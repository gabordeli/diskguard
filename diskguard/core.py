"""Deluge core plugin: storage discovery and guard configuration."""

from __future__ import annotations

import logging
from pathlib import Path

from deluge.configmanager import ConfigManager
from deluge.plugins.pluginbase import CorePluginBase

from . import guard, storage

log = logging.getLogger(__name__)

CONFIG_FILE = "diskguard.conf"
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

    def enable(self):
        self.config = ConfigManager(CONFIG_FILE, defaults=DEFAULT_PREFS)
        log.info("DiskGuard enabled")

    def disable(self):
        log.info("DiskGuard disabled")

    def update(self):
        # The guard loop (M2) will refresh state; reads are on demand.
        pass

    def mounts(self):
        try:
            text = Path("/proc/self/mounts").read_text(encoding="utf-8")
        except OSError:
            return []
        return storage.parse_mounts(text)

    def get_storage(self):
        from deluge import component

        core = component.get("Core")
        try:
            label_plugin = component.get("CorePlugin.Label")
        except KeyError:
            label_plugin = None

        uses = storage.collect_paths(core, label_plugin)
        mounts = self.mounts()
        default, per_mount = guard.settings_from_config(self.config or {})
        thresholds = {
            mount.mountpoint: guard.settings_for(
                mount.mountpoint, default, per_mount
            ).min_free_bytes
            for mount in mounts
        }
        disks, unknown = storage.build_disks(
            uses, mounts, thresholds=thresholds
        )
        return {"disks": disks, "unknown": unknown}

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
        return self.get_guard_config()

    def get_guard_state(self):
        return {
            "low_mounts": sorted(self._state.low_mounts),
            "paused_torrents": sorted(self._state.paused_torrents),
        }
