"""Per-filesystem guard rules.

Pure decisions: no Deluge calls, no sleeping. The runner in ``core.py`` applies
the returned transitions to torrents and notifications.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .storage import Mount, PathUse, canonical_mount

GIB = 1024**3
ACTIONS = frozenset({"notify", "pause"})


@dataclass(frozen=True)
class GuardSettings:
    min_free_bytes: int
    resume_margin_bytes: int = 0
    action: str = "pause"


@dataclass
class GuardState:
    low_mounts: set[str] = field(default_factory=set)
    paused_torrents: set[str] = field(default_factory=set)


def _gb(value, fallback: int) -> int:
    try:
        return max(0, int(float(value) * GIB))
    except (TypeError, ValueError):
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

    per_mount: dict[str, GuardSettings] = {}
    for mount, raw in (config.get("per_mount") or {}).items():
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
            events.append({"mount": mount, "event": "ok", "free": free})
    return events


def torrents_to_pause(
    uses: list[PathUse],
    mounts: list[Mount],
    mount: str,
    already_paused: set[str],
) -> set[str]:
    """Torrents that are actively writing to ``mount`` and not yet guarded."""
    ids: set[str] = set()
    for use in uses:
        if not use.torrent_id or not use.active:
            continue
        if use.torrent_id in already_paused:
            continue
        mapped = canonical_mount(use.path, mounts)
        if mapped is not None and mapped.mountpoint == mount:
            ids.add(use.torrent_id)
    return ids
