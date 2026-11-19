"""Deluge core plugin: storage discovery and guard configuration."""

from __future__ import annotations

import logging
from pathlib import Path

from deluge.configmanager import ConfigManager
from deluge.core.rpcserver import export
from deluge.event import DelugeEvent
from deluge.plugins.pluginbase import CorePluginBase
from twisted.internet import task, threads

from . import guard, storage

log = logging.getLogger(__name__)

CONFIG_FILE = "diskguard.conf"
GUARD_POLL_SECONDS = 30


class LowTargetEvent(DelugeEvent):
    """A torrent path appeared on a low disk."""

    def __init__(self, torrent_id, path, mount):
        self._args = [torrent_id, path, mount]


class GuardEvent(DelugeEvent):
    """A low/ok guard transition for one mount."""

    def __init__(self, mount, event, free, min_free, action, torrents):
        self._args = [mount, event, free, min_free, action, torrents]


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
        self._torrent_paths = None
        self._last_events = []
        self._sinks = []

    def enable(self):
        self.config = ConfigManager(CONFIG_FILE, defaults=DEFAULT_PREFS)
        self._cache = None
        self._torrent_paths = None
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

    def _thresholds(self, mounts, uses):
        default, per_mount = guard.settings_from_config(self.config or {})
        used = set()
        for use in uses:
            mount = storage.canonical_mount(use.path, mounts)
            if mount is not None:
                used.add(mount.mountpoint)
        return {
            mount.mountpoint: guard.settings_for(
                mount.mountpoint, default, per_mount
            ).min_free_bytes
            for mount in mounts
            if mount.mountpoint in used
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
            thresholds = self._thresholds(mounts, uses)
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
            event["torrents"] = self._event_torrent_count(
                event, uses, mounts, aliases
            )
        self._record_events(events)
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
        self._emit_low_targets(uses, mounts, aliases, disks)
        return payload

    def _prune_state(self, existing):
        state = self._state
        state.paused_torrents &= existing
        for mount in tuple(state.paused_by_mount):
            state.paused_by_mount[mount] &= existing
            if not state.paused_by_mount[mount]:
                del state.paused_by_mount[mount]

    def _pause_for_event(
        self, event, uses, mounts, aliases, torrents, existing, core
    ):
        mount = event["mount"]
        state = self._state
        ids = guard.torrents_to_pause(
            uses,
            mounts,
            mount,
            state.paused_torrents,
            aliases.get(mount, {mount}),
        ) & existing
        ids = self._active_ids(torrents, ids)
        if not ids:
            return
        paused = self._per_torrent(core.pause_torrents, ids, "pause")
        if not paused:
            return
        state.paused_torrents.update(paused)
        state.paused_by_mount.setdefault(mount, set()).update(paused)
        log.warning(
            "DiskGuard: paused %d torrent(s) on %s",
            len(paused),
            mount,
        )

    def _resume_for_event(self, event, existing, core):
        mount = event["mount"]
        state = self._state
        ids = guard.torrents_to_resume(
            state, mount, existing
        ) & state.paused_torrents
        resumed = self._per_torrent(core.resume_torrents, ids, "resume")
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
        self._prune_state(existing)

        for event in events:
            if event["event"] == "low" and event.get("action") == "pause":
                self._pause_for_event(
                    event, uses, mounts, aliases, torrents, existing, core
                )
            elif event["event"] == "ok":
                self._resume_for_event(event, existing, core)

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

    @staticmethod
    def _current_torrent_paths(uses) -> dict[str, set[str]]:
        current: dict[str, set[str]] = {}
        for use in uses:
            if use.torrent_id and use.path:
                current.setdefault(use.torrent_id, set()).add(use.path)
        return current

    def _emit_low_target(
        self, manager, torrent_id, path, mounts, aliases, disks
    ) -> None:
        disk = self._low_disk_for_path(path, mounts, aliases, disks)
        if disk is None:
            return
        try:
            manager.emit(LowTargetEvent(torrent_id, path, disk["mount"]))
        except Exception:
            log.exception(
                "DiskGuard: could not emit low-target event for %s",
                torrent_id,
            )

    def _emit_low_targets(self, uses, mounts, aliases, disks):
        if not self._enabled():
            return
        current = self._current_torrent_paths(uses)
        previous = self._torrent_paths
        self._torrent_paths = current
        if previous is None:
            return
        try:
            manager = self._event_manager()
        except KeyError:
            return
        for torrent_id, paths in current.items():
            for path in sorted(paths - previous.get(torrent_id, set())):
                self._emit_low_target(
                    manager, torrent_id, path, mounts, aliases, disks
                )

    def add_event_sink(self, sink):
        if sink not in self._sinks:
            self._sinks.append(sink)

    def remove_event_sink(self, sink):
        if sink in self._sinks:
            self._sinks.remove(sink)

    @export
    def get_last_events(self):
        return [dict(event) for event in self._last_events]

    def _event_torrent_count(self, event, uses, mounts, aliases):
        mount = event["mount"]
        if event["event"] == "low":
            ids = guard.torrents_to_pause(
                uses,
                mounts,
                mount,
                self._state.paused_torrents,
                aliases.get(mount, {mount}),
            )
            return len(ids)
        return len(self._state.paused_by_mount.get(mount, set()))

    def _record_events(self, events):
        if not events:
            return
        self._last_events.extend(events)
        self._last_events = self._last_events[-50:]
        for event in events:
            snapshot = dict(event)
            for sink in tuple(self._sinks):
                try:
                    sink(dict(snapshot))
                except Exception:
                    log.exception("DiskGuard: event sink failed")
            try:
                manager = self._event_manager()
            except KeyError:
                continue
            try:
                manager.emit(GuardEvent(**snapshot))
            except Exception:
                log.exception("DiskGuard: could not emit guard event")

    def _event_manager(self):
        from deluge import component

        return component.get("EventManager")

    @staticmethod
    def _low_disk_for_path(path, mounts, aliases, disks):
        try:
            mount = storage.canonical_mount(path, mounts)
        except Exception:
            return None
        if mount is None:
            return None
        names = aliases.get(mount.mountpoint, {mount.mountpoint})
        for disk in disks:
            if disk.get("mount") in names and disk.get("low"):
                return disk
        return None

    def mounts(self):
        try:
            text = Path("/proc/self/mounts").read_text(encoding="utf-8")
        except OSError:
            return []
        return storage.parse_mounts(text)

    @export
    def get_storage(self):
        if self._cache is not None:
            return self._cache
        uses = self._collect_uses()
        mounts = self.mounts()
        disks, unknown = storage.build_disks(
            uses, mounts, thresholds=self._thresholds(mounts, uses)
        )
        self._cache = {"disks": disks, "unknown": unknown}
        return self._cache

    @export
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

    @export
    def set_guard_config(self, payload):
        clean = guard.validate_guard_config(payload)
        for key, value in clean.items():
            self.config[key] = value
        self.config.save()
        self._cache = None
        return self.get_guard_config()

    @export
    def get_guard_state(self):
        return {
            "low_mounts": sorted(self._state.low_mounts),
            "paused_torrents": sorted(self._state.paused_torrents),
        }
