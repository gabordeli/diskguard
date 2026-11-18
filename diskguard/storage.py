"""Mount discovery and Deluge path-to-filesystem mapping.

Pure Python: the Deluge adapter in :mod:`diskguard.core` feeds ``PathUse``
objects in, and tests can feed fakes. No ``deluge.*`` imports here.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field

PSEUDO_FS = frozenset(
    {
        "autofs",
        "binfmt_misc",
        "bpf",
        "cgroup",
        "cgroup2",
        "configfs",
        "debugfs",
        "devpts",
        "devtmpfs",
        "efivarfs",
        "fusectl",
        "hugetlbfs",
        "mqueue",
        "nsfs",
        "overlay",
        "pstore",
        "proc",
        "ramfs",
        "rootfs",
        "rpc_pipefs",
        "securityfs",
        "squashfs",
        "sysfs",
        "tmpfs",
        "tracefs",
    }
)

ACTIVE_STATES = frozenset(
    {"Allocating", "Checking", "Downloading", "Moving", "Queued"}
)


@dataclass(frozen=True)
class Mount:
    device: str
    mountpoint: str
    fstype: str
    options: tuple[str, ...] = ()


@dataclass(frozen=True)
class DiskStat:
    total: int
    free: int
    used: int

    @property
    def used_pct(self) -> float:
        if self.total <= 0:
            return 0.0
        return min(1.0, max(0.0, self.used / self.total))


@dataclass(frozen=True)
class PathUse:
    path: str
    torrent_id: str | None = None
    label: str | None = None
    active: bool = False
    size: int = 0
    kind: str = "torrent"


@dataclass
class Disk:
    mount: str
    fstype: str
    stat: DiskStat
    paths: list[str] = field(default_factory=list)
    torrents: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    deluge: bool = False
    low: bool = False

    def as_dict(self) -> dict:
        return {
            "mount": self.mount,
            "fs": self.fstype,
            "total": self.stat.total,
            "free": self.stat.free,
            "used": self.stat.used,
            "used_pct": round(self.stat.used_pct, 4),
            "paths": self.paths,
            "torrents": len(self.torrents),
            "labels": self.labels,
            "deluge": self.deluge,
            "low": self.low,
        }


def _unescape_mount(value: str) -> str:
    return (
        value.replace("\\040", " ")
        .replace("\\011", "\t")
        .replace("\\134", "\\")
    )


def _normalise_mount(value: str) -> str:
    return value.rstrip("/") or "/"


def parse_mounts(text: str) -> list[Mount]:
    """Parse the kernel mount table (``/proc/self/mounts`` format)."""
    mounts: list[Mount] = []
    for line in text.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        options = tuple(fields[3].split(",")) if len(fields) > 3 else ()
        mounts.append(
            Mount(
                device=fields[0],
                mountpoint=_normalise_mount(_unescape_mount(fields[1])),
                fstype=fields[2],
                options=options,
            )
        )
    return mounts


def real_mounts(mounts: Sequence[Mount]) -> list[Mount]:
    """Mounts worth showing in a storage panel (pseudo filesystems removed)."""
    return [mount for mount in mounts if mount.fstype not in PSEUDO_FS]


def canonical_mount(path: str, mounts: Sequence[Mount]) -> Mount | None:
    """Return the longest mountpoint containing ``path``."""
    if not path:
        return None
    resolved = os.path.realpath(path)
    best: Mount | None = None
    best_len = -1
    for mount in mounts:
        root = _normalise_mount(mount.mountpoint)
        if root == "/":
            matches = resolved.startswith("/")
        else:
            matches = resolved == root or resolved.startswith(root + os.sep)
        if matches and len(root) > best_len:
            best = mount
            best_len = len(root)
    return best


def disk_stat(path: str) -> DiskStat | None:
    """Return filesystem totals, or ``None`` for a missing/unreadable path."""
    try:
        st = os.statvfs(path)
    except OSError:
        return None
    block = st.f_frsize or st.f_bsize
    total = st.f_blocks * block
    free = st.f_bavail * block
    used = (st.f_blocks - st.f_bfree) * block
    return DiskStat(total=total, free=free, used=used)


def device_id(path: str) -> int | None:
    """Return the filesystem device id (``st_dev``), or ``None``."""
    try:
        return os.stat(path).st_dev
    except OSError:
        return None


def collect_paths(core, label_plugin=None) -> list[PathUse]:
    """Collect paths Deluge can write to.

    The adapter is duck-typed so tests can pass a fake ``core`` without
    importing Deluge. Paths are deduplicated by ``(path, torrent_id)``.
    """
    uses: list[PathUse] = []
    seen: set[tuple[str, str | None]] = set()

    def add(use: PathUse) -> None:
        key = (use.path, use.torrent_id)
        if key not in seen:
            seen.add(key)
            uses.append(use)

    try:
        config = core.get_config_values(
            ["download_location", "move_completed", "move_completed_path"]
        )
    except Exception:
        config = {}
    default = config.get("download_location")
    if default:
        add(PathUse(path=default, kind="session"))
    move_target = config.get("move_completed_path")
    if config.get("move_completed") and move_target:
        add(PathUse(path=move_target, kind="move_completed"))

    label_map = getattr(label_plugin, "torrent_labels", {}) or {}
    label_options: dict[str, dict] = {}
    torrents = getattr(getattr(core, "torrentmanager", None), "torrents", {}) or {}

    for torrent_id, torrent in torrents.items():
        try:
            status = torrent.get_status(
                [
                    "download_location",
                    "save_path",
                    "state",
                    "total_size",
                    "move_completed",
                    "move_completed_path",
                ],
                update=False,
            )
        except Exception:
            continue
        label = label_map.get(torrent_id)
        active = status.get("state") in ACTIVE_STATES
        for path in {
            status.get("download_location"),
            status.get("save_path"),
        }:
            if path:
                add(
                    PathUse(
                        path=path,
                        torrent_id=torrent_id,
                        label=label,
                        active=active,
                        size=int(status.get("total_size") or 0),
                    )
                )
        if status.get("move_completed") and status.get("move_completed_path"):
            add(
                PathUse(
                    path=status["move_completed_path"],
                    torrent_id=torrent_id,
                    label=label,
                    size=int(status.get("total_size") or 0),
                    kind="move_completed",
                )
            )
        if label and label_plugin is not None and label not in label_options:
            try:
                label_options[label] = label_plugin.get_options(label) or {}
            except Exception:
                label_options[label] = {}

    for label, options in label_options.items():
        if options.get("apply_move_completed") and options.get("move_completed"):
            target = options.get("move_completed_path")
            if target:
                add(PathUse(path=target, label=label, kind="move_completed"))

    return uses


def build_disks(
    uses: Iterable[PathUse],
    mounts: Sequence[Mount],
    *,
    include_all: bool = True,
    thresholds: Mapping[str, int] | None = None,
    stat_fn: Callable[[str], DiskStat | None] = disk_stat,
    dev_fn: Callable[[str], int | None] = device_id,
) -> tuple[list[dict], list[str]]:
    """Group paths by filesystem and build the storage payload.

    Returns ``(disks, unknown_paths)``. ``thresholds`` maps a mountpoint to
    the minimum free bytes; a disk below it is marked ``low``.
    """
    thresholds = thresholds or {}
    groups: dict[int, dict] = {}
    unknown: list[str] = []

    for use in uses:
        mount = canonical_mount(use.path, mounts)
        if mount is None:
            unknown.append(use.path)
            continue
        dev = dev_fn(use.path)
        if dev is None:
            unknown.append(use.path)
            continue
        group = groups.setdefault(
            dev,
            {
                "mount": mount,
                "paths": set(),
                "torrents": set(),
                "labels": set(),
            },
        )
        if len(mount.mountpoint) > len(group["mount"].mountpoint):
            group["mount"] = mount
        group["paths"].add(use.path)
        if use.torrent_id:
            group["torrents"].add(use.torrent_id)
        if use.label:
            group["labels"].add(use.label)

    disks: list[Disk] = []
    for group in groups.values():
        mount = group["mount"]
        stat = stat_fn(mount.mountpoint)
        if stat is None:
            unknown.extend(sorted(group["paths"]))
            continue
        disks.append(
            Disk(
                mount=mount.mountpoint,
                fstype=mount.fstype,
                stat=stat,
                paths=sorted(group["paths"]),
                torrents=sorted(group["torrents"]),
                labels=sorted(group["labels"]),
                deluge=True,
            )
        )

    if include_all:
        used_devs = set(groups)
        for mount in real_mounts(mounts):
            dev = dev_fn(mount.mountpoint)
            if dev is None or dev in used_devs:
                continue
            stat = stat_fn(mount.mountpoint)
            if stat is None:
                continue
            disks.append(Disk(mount=mount.mountpoint, fstype=mount.fstype, stat=stat))
            used_devs.add(dev)

    for disk in disks:
        threshold = thresholds.get(disk.mount)
        disk.low = threshold is not None and disk.stat.free < threshold
    disks.sort(key=lambda disk: (not disk.low, disk.mount))
    return [disk.as_dict() for disk in disks], sorted(set(unknown))
