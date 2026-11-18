"""Deluge core plugin: storage discovery and guard configuration."""

from __future__ import annotations

import logging
from pathlib import Path

from deluge.configmanager import ConfigManager
from deluge.plugins.pluginbase import CorePluginBase
from twisted.internet import task, threads

from . import guard, storage

log = logging.getLogger(__name__)

CONFIG_FILE = "diskguard.conf"
GUARD_POLL_SECONDS = 30
DEFAULT_PREFS = {
    "enabled": True,
    "default_min_free_gb": 10.0,
    "resume_margin_gb": 5.0,
    "per_mount": {},
}


class Core(CorePluginBase):
    def __init__(self, plugin_name):
        super().__init__(plugin_name)
        self.config = None
        self._state = guard.GuardState()
        self._loop = None
        self._polling = False
        self._cache = None

    def enable(self):
        self.config = ConfigManager(CONFIG_FILE, defaults=DEFAULT_PREFS)
        self._cache = None
        self._start_guard()
        log.info("DiskGuard enabled")

    def disable(self):
        self._stop_guard()
        log.info("DiskGuard disabled")

    def _start_guard(self):
        if self._loop is not None:
            return
        self._loop = task.LoopingCall(self._poll)
        self._loop.start(GUARD_POLL_SECONDS, now=False)

    def _stop_guard(self):
        if self._loop is None:
            return
        self._loop.stop()
        self._loop = None

    def update(self):
        # The guard loop refreshes state; reads use the cached payload.
        pass

    def _core(self):
        from deluge import component

        return component.get("Core")

    def _label_plugin(self):
        from deluge import component

        try:
            return component.get("CorePlugin.Label")
        except KeyError:
            return None

    def _collect_uses(self):
        return storage.collect_paths(self._core(), self._label_plugin())

    def _thresholds(self, mounts):
        default, per_mount = guard.settings_from_config(self.config or {})
        return {
            mount.mountpoint: guard.settings_for(
                mount.mountpoint, default, per_mount
            ).min_free_bytes
            for mount in mounts
        }

    def _enabled(self):
        return bool((self.config or {}).get("enabled", True))

    def _poll(self):
        if self._polling or not self._enabled():
            return None
        self._polling = True
        try:
            uses = self._collect_uses()
            mounts = self.mounts()
            thresholds = self._thresholds(mounts)
        except Exception:
            self._polling = False
            log.exception("DiskGuard poll setup failed")
            return None

        deferred = threads.deferToThread(
            self._build_payload, uses, mounts, thresholds
        )
        deferred.addCallback(self._apply_payload)
        deferred.addErrback(self._poll_failed)
        deferred.addBoth(self._poll_done)
        return deferred

    def _poll_failed(self, failure):
        log.warning("DiskGuard poll failed: %s", failure.getErrorMessage())
        return None

    def _poll_done(self, result):
        self._polling = False
        return result

    def _build_payload(self, uses, mounts, thresholds):
        disks, unknown = storage.build_disks(
            uses, mounts, thresholds=thresholds
        )
        return uses, mounts, storage.mount_aliases(mounts), disks, unknown

    def _apply_payload(self, payload):
        uses, mounts, aliases, disks, unknown = payload
        self._cache = {"disks": disks, "unknown": unknown}
        default, per_mount = guard.settings_from_config(self.config or {})
        events = guard.evaluate(disks, default, per_mount, self._state)
        for event in events:
            if event["event"] == "low":
                log.warning(
                    "DiskGuard: %s low, %.1f GiB free",
                    event["mount"],
                    event["free"] / guard.GIB,
                )
            else:
                log.info(
                    "DiskGuard: %s ok, %.1f GiB free",
                    event["mount"],
                    event["free"] / guard.GIB,
                )
        self._apply_events(events, uses, mounts, aliases)
        return payload

    def _apply_events(self, events, uses, mounts, aliases=None):
        if not events or not self._enabled():
            return
        aliases = aliases or {}
        try:
            core = self._core()
        except KeyError:
            log.warning("DiskGuard: Deluge core unavailable")
            return
        torrents = getattr(getattr(core, "torrentmanager", None), "torrents", {})
        if not isinstance(torrents, dict):
            torrents = {}
        existing = set(torrents)
        state = self._state
        state.paused_torrents &= existing
        for mount in list(state.paused_by_mount):
            state.paused_by_mount[mount] &= existing
            if not state.paused_by_mount[mount]:
                del state.paused_by_mount[mount]

        for event in events:
            mount = event["mount"]
            if event["event"] == "low" and event.get("action") == "pause":
                ids = guard.torrents_to_pause(
                    uses,
                    mounts,
                    mount,
                    state.paused_torrents,
                    aliases.get(mount, {mount}),
                ) & existing
                ids = self._active_ids(torrents, ids)
                if not ids:
                    continue
                paused = self._per_torrent(
                    core.pause_torrents, ids, "pause"
                )
                if not paused:
                    continue
                state.paused_torrents.update(paused)
                state.paused_by_mount.setdefault(mount, set()).update(paused)
                log.warning(
                    "DiskGuard: paused %d torrent(s) on %s",
                    len(paused),
                    mount,
                )
            elif event["event"] == "ok":
                ids = guard.torrents_to_resume(
                    state, mount, existing
                ) & state.paused_torrents
                resumed = self._per_torrent(
                    core.resume_torrents, ids, "resume"
                )
                state.paused_torrents.difference_update(resumed)
                failed = ids - resumed
                if failed:
                    state.paused_by_mount[mount] = failed
                else:
                    state.paused_by_mount.pop(mount, None)
                if resumed:
                    log.info(
                        "DiskGuard: resumed %d torrent(s) on %s",
                        len(resumed),
                        mount,
                    )

    @staticmethod
    def _per_torrent(action, ids, verb):
        done = set()
        for torrent_id in sorted(ids):
            try:
                action([torrent_id])
            except Exception:
                log.exception(
                    "DiskGuard: %s failed for %s", verb, torrent_id
                )
            else:
                done.add(torrent_id)
        return done

    @staticmethod
    def _active_ids(torrents, ids):
        active = set()
        for torrent_id in ids:
            torrent = torrents.get(torrent_id)
            if torrent is None:
                continue
            try:
                status = torrent.get_status(["state"], update=False)
            except Exception:
                continue
            if status and status.get("state") in storage.ACTIVE_STATES:
                active.add(torrent_id)
        return active

    def mounts(self):
        try:
            text = Path("/proc/self/mounts").read_text(encoding="utf-8")
        except OSError:
            return []
        return storage.parse_mounts(text)

    def get_storage(self):
        if self._cache is not None:
            return self._cache
        uses = self._collect_uses()
        mounts = self.mounts()
        disks, unknown = storage.build_disks(
            uses, mounts, thresholds=self._thresholds(mounts)
        )
        self._cache = {"disks": disks, "unknown": unknown}
        return self._cache

    def get_guard_config(self):
        config = self.config or {}
        return {
            "enabled": bool(config.get("enabled", True)),
            "default_min_free_gb": float(
                config.get("default_min_free_gb", 10.0)
            ),
            "resume_margin_gb": float(config.get("resume_margin_gb", 5.0)),
            "per_mount": dict(config.get("per_mount") or {}),
        }

    def set_guard_config(self, payload):
        clean = guard.validate_guard_config(payload)
        for key, value in clean.items():
            self.config[key] = value
        self.config.save()
        self._cache = None
        return self.get_guard_config()

    def get_guard_state(self):
        return {
            "low_mounts": sorted(self._state.low_mounts),
            "paused_torrents": sorted(self._state.paused_torrents),
        }
