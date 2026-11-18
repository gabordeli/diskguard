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

    def _collect_uses(self):
        from deluge import component

        core = component.get("Core")
        try:
            label_plugin = component.get("CorePlugin.Label")
        except KeyError:
            label_plugin = None
        return storage.collect_paths(core, label_plugin)

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
            storage.build_disks, uses, mounts, thresholds=thresholds
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

    def _apply_payload(self, payload):
        disks, unknown = payload
        self._cache = {"disks": disks, "unknown": unknown}
        default, per_mount = guard.settings_from_config(self.config or {})
        for event in guard.evaluate(disks, default, per_mount, self._state):
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
        return payload

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
