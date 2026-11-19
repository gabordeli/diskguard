"""Per-filesystem guard rules.

Pure decisions: no Deluge calls, no sleeping. The runner in ``core.py`` applies
the returned transitions to torrents and notifications.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .storage import Mount, PathUse, canonical_mount

GIB = 1024**3
ACTIONS = frozenset({"notify", "pause"})
CONFIG_KEYS = frozenset(
    {"enabled", "default_min_free_gb", "resume_margin_gb", "per_mount"}
)
MOUNT_KEYS = frozenset({"min_free_gb", "resume_margin_gb", "action"})


@dataclass(frozen=True)
class GuardSettings:
    min_free_bytes: int
    resume_margin_bytes: int = 0
    action: str = "pause"


@dataclass
class GuardState:
    low_mounts: set[str] = field(default_factory=set)
    paused_torrents: set[str] = field(default_factory=set)
    paused_by_mount: dict[str, set[str]] = field(default_factory=dict)


def _non_negative_float(value, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as ex:
        raise ValueError(f"{name} must be a number") from ex
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    if number < 0:
        raise ValueError(f"{name} must be >= 0")
    return number


def _reject_unknown(payload: dict, allowed: frozenset[str], message: str) -> None:
    unknown = set(payload) - allowed
    if unknown:
        raise ValueError(
            message.format(keys=", ".join(sorted(map(str, unknown))))
        )


def _validate_per_mount_entry(mount: str, raw: dict) -> dict:
    if not isinstance(mount, str) or not mount:
        raise ValueError("per_mount keys must be non-empty mountpoints")
    if not isinstance(raw, dict):
        raise ValueError(f"per_mount[{mount}] must be an object")
    _reject_unknown(
        raw,
        MOUNT_KEYS,
        f"unknown per_mount keys for {mount}: {{keys}}",
    )
    entry: dict = {}
    for key in ("min_free_gb", "resume_margin_gb"):
        if key in raw:
            entry[key] = _non_negative_float(
                raw[key], f"per_mount[{mount}].{key}"
            )
    if "action" in raw:
        if raw["action"] not in ACTIONS:
            raise ValueError(
                f"per_mount[{mount}].action must be notify or pause"
            )
        entry["action"] = raw["action"]
    return entry


def _validate_per_mount(value: dict) -> dict[str, dict]:
    if not isinstance(value, dict):
        raise ValueError("per_mount must be an object")
    return {
        mount: _validate_per_mount_entry(mount, raw)
        for mount, raw in value.items()
    }


def validate_guard_config(payload: dict) -> dict:
    """Validate a partial guard config and return normalised known keys.

    The returned dict can be merged into the plugin config. ``ValueError``
    carries a client-safe message; the webui2 layer maps it to HTTP 400.
    """
    if not isinstance(payload, dict):
        raise ValueError("config must be an object")
    _reject_unknown(payload, CONFIG_KEYS, "unknown config keys: {keys}")

    clean: dict = {}
    if "enabled" in payload:
        if not isinstance(payload["enabled"], bool):
            raise ValueError("enabled must be a boolean")
        clean["enabled"] = payload["enabled"]
    for key in ("default_min_free_gb", "resume_margin_gb"):
        if key in payload:
            clean[key] = _non_negative_float(payload[key], key)
    if "per_mount" in payload:
        clean["per_mount"] = _validate_per_mount(payload["per_mount"])
    return clean


def _gb(value, fallback: int) -> int:
    try:
        return max(0, int(float(value) * GIB))
    except (TypeError, ValueError, OverflowError):
        return fallback


def settings_from_config(
    config: dict,
) -> tuple[GuardSettings, dict[str, GuardSettings]]:
    """Build the default and per-mount settings from plugin config."""
    default_min_gb = config.get("default_min_free_gb", 10.0)
    default_margin_gb = config.get("resume_margin_gb", 5.0)
    default_min = _gb(default_min_gb, 10 * GIB)
    default_margin = _gb(default_margin_gb, 5 * GIB)
    default = GuardSettings(default_min, default_margin, "pause")

    raw_per_mount = config.get("per_mount") or {}
    if not isinstance(raw_per_mount, dict):
        raw_per_mount = {}
    per_mount: dict[str, GuardSettings] = {}
    for mount, raw in raw_per_mount.items():
        if not isinstance(raw, dict):
            continue
        action = raw.get("action", "pause")
        if action not in ACTIONS:
            action = "pause"
        per_mount[mount] = GuardSettings(
            min_free_bytes=_gb(
                raw.get("min_free_gb", default_min_gb), default_min
            ),
            resume_margin_bytes=_gb(
                raw.get("resume_margin_gb", default_margin_gb), default_margin
            ),
            action=action,
        )
    return default, per_mount


def settings_for(
    mount: str,
    default: GuardSettings,
    per_mount: dict[str, GuardSettings],
) -> GuardSettings:
    return per_mount.get(mount, default)


def evaluate(
    disks: list[dict],
    default: GuardSettings,
    per_mount: dict[str, GuardSettings],
    state: GuardState,
) -> list[dict]:
    """Return low/ok transitions, updating ``state.low_mounts``.

    Hysteresis: a low disk only returns to ``ok`` after
    ``min_free_bytes + resume_margin_bytes``.
    """
    events: list[dict] = []
    for disk in disks:
        if not disk.get("deluge", True):
            continue
        mount = disk["mount"]
        settings = settings_for(mount, default, per_mount)
        free = int(disk.get("free") or 0)
        if mount not in state.low_mounts and free < settings.min_free_bytes:
            state.low_mounts.add(mount)
            events.append(
                {
                    "mount": mount,
                    "event": "low",
                    "free": free,
                    "min_free": settings.min_free_bytes,
                    "action": settings.action,
                }
            )
        elif mount in state.low_mounts and free >= (
            settings.min_free_bytes + settings.resume_margin_bytes
        ):
            state.low_mounts.remove(mount)
            events.append(
                {
                    "mount": mount,
                    "event": "ok",
                    "free": free,
                    "min_free": settings.min_free_bytes,
                    "action": settings.action,
                }
            )
    return events


def torrents_to_pause(
    uses: list[PathUse],
    mounts: list[Mount],
    mount: str,
    already_paused: set[str],
    mountpoints: set[str] | None = None,
) -> set[str]:
    """Torrents that are actively writing to ``mount`` and not yet guarded."""
    allowed = mountpoints or {mount}
    ids: set[str] = set()
    for use in uses:
        if not use.torrent_id or not use.active:
            continue
        if use.torrent_id in already_paused:
            continue
        mapped = canonical_mount(use.path, mounts)
        if mapped is not None and mapped.mountpoint in allowed:
            ids.add(use.torrent_id)
    return ids


def torrents_to_resume(
    state: GuardState,
    mount: str,
    existing: set[str],
) -> set[str]:
    """Guard-paused torrents for ``mount`` that still exist."""
    return set(state.paused_by_mount.get(mount, set())) & existing
