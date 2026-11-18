from diskguard.storage import (
    DiskStat,
    Mount,
    PathUse,
    build_disks,
    canonical_mount,
    collect_paths,
    device_id,
    disk_stat,
    parse_mounts,
    real_mounts,
)


def test_parse_mounts_unescapes_spaces():
    text = (
        "/dev/sda1 /mnt/usb\\040hdd ext4 rw,relatime 0 0\n"
        "proc /proc proc rw 0 0\n"
    )
    mounts = parse_mounts(text)
    assert mounts[0].mountpoint == "/mnt/usb hdd"
    assert mounts[0].fstype == "ext4"
    assert mounts[1].fstype == "proc"


def test_parse_mounts_unescapes_tabs_and_backslashes():
    text = (
        "/dev/sda1 /mnt/usb\\011hdd ext4 rw 0 0\n"
        "/dev/sdb1 /mnt/back\\134slash ext4 rw 0 0\n"
    )
    mounts = parse_mounts(text)
    assert mounts[0].mountpoint == "/mnt/usb\thdd"
    assert mounts[1].mountpoint == "/mnt/back\\slash"


def test_real_mounts_filters_pseudo_filesystems():
    mounts = [
        Mount("proc", "/proc", "proc"),
        Mount("sysfs", "/sys", "sysfs"),
        Mount("overlay", "/", "overlay"),
        Mount("tmpfs", "/run", "tmpfs"),
        Mount("/dev/sda1", "/mnt/data", "ext4"),
    ]
    assert [mount.mountpoint for mount in real_mounts(mounts)] == ["/mnt/data"]


def test_canonical_mount_uses_longest_prefix():
    mounts = [
        Mount("/dev/sda1", "/", "ext4"),
        Mount("/dev/sdb1", "/mnt/usbhdd2", "ext4"),
    ]
    assert canonical_mount("/mnt/usbhdd2/downloads", mounts).mountpoint == (
        "/mnt/usbhdd2"
    )
    assert canonical_mount("/mnt/usbhdd2-other/file", mounts).mountpoint == "/"


def test_build_disks_groups_by_device_and_marks_low():
    mounts = [
        Mount("/dev/sda1", "/", "ext4"),
        Mount("/dev/sdb1", "/mnt/usbhdd2", "ext4"),
        Mount("/dev/sdc1", "/mnt/usbhdd3", "ext4"),
    ]
    uses = [
        PathUse("/mnt/usbhdd2/a", "t1", "film", active=True),
        PathUse("/mnt/usbhdd2/b", "t2", "film"),
        PathUse("/mnt/usbhdd3/c", "t3"),
    ]
    stats = {
        "/mnt/usbhdd2": DiskStat(100, 5, 95),
        "/mnt/usbhdd3": DiskStat(100, 50, 50),
        "/": DiskStat(100, 80, 20),
    }
    devs = {
        "/mnt/usbhdd2": 2,
        "/mnt/usbhdd2/a": 2,
        "/mnt/usbhdd2/b": 2,
        "/mnt/usbhdd3": 3,
        "/mnt/usbhdd3/c": 3,
    }
    disks, unknown = build_disks(
        uses,
        mounts,
        include_all=False,
        thresholds={"/mnt/usbhdd2": 10},
        stat_fn=stats.get,
        dev_fn=devs.get,
    )
    assert unknown == []
    assert len(disks) == 2
    low_disk = disks[0]
    assert low_disk["mount"] == "/mnt/usbhdd2"
    assert low_disk["low"] is True
    assert low_disk["torrents"] == 2
    assert low_disk["labels"] == ["film"]
    assert disks[1]["mount"] == "/mnt/usbhdd3"


def test_build_disks_includes_unused_mounts():
    mounts = [
        Mount("/dev/sda1", "/", "ext4"),
        Mount("/dev/sdc1", "/mnt/usbhdd3", "ext4"),
        Mount("proc", "/proc", "proc"),
    ]
    stats = {
        "/": DiskStat(100, 80, 20),
        "/mnt/usbhdd3": DiskStat(100, 50, 50),
    }
    devs = {"/": 1, "/mnt/usbhdd3": 3}
    disks, unknown = build_disks(
        [],
        mounts,
        include_all=True,
        stat_fn=stats.get,
        dev_fn=devs.get,
    )
    assert unknown == []
    assert [disk["mount"] for disk in disks] == ["/", "/mnt/usbhdd3"]
    assert all(disk["deluge"] is False for disk in disks)


def test_collect_paths_includes_label_move_completed():
    class Torrent:
        def get_status(self, keys, update=False):
            assert update is False
            return {
                "download_location": "/mnt/usbhdd2/incomplete",
                "save_path": "/mnt/usbhdd2/final",
                "state": "Downloading",
                "total_size": 123,
            }

    class TorrentManager:
        torrents = {"t1": Torrent()}

    class Core:
        torrentmanager = TorrentManager()

        def get_config_values(self, keys):
            return {"download_location": "/mnt/usbhdd2/downloads"}

    class Label:
        torrent_labels = {"t1": "film"}

        def get_options(self, label):
            assert label == "film"
            return {
                "apply_move_completed": True,
                "move_completed": True,
                "move_completed_path": "/mnt/usbhdd3/film",
            }

    uses = collect_paths(Core(), Label())
    paths = {use.path for use in uses}
    assert "/mnt/usbhdd2/downloads" in paths
    assert "/mnt/usbhdd2/final" in paths
    assert "/mnt/usbhdd3/film" in paths
    assert any(use.active and use.torrent_id == "t1" for use in uses)


def test_build_disks_collapses_bind_mounts_by_device():
    mounts = [
        Mount("/dev/sdb1", "/mnt/usbhdd2", "ext4"),
        Mount("/dev/sdb1", "/mnt/usbhdd2/bind", "ext4"),
        Mount("/dev/sdc1", "/mnt/usbhdd3", "ext4"),
    ]
    uses = [
        PathUse("/mnt/usbhdd2/bind/a", "t1", active=True),
        PathUse("/mnt/usbhdd2/b", "t2"),
        PathUse("/mnt/usbhdd3/c", "t3"),
    ]
    stats = {
        "/mnt/usbhdd2/bind": DiskStat(100, 5, 95),
        "/mnt/usbhdd3": DiskStat(100, 50, 50),
    }
    devs = {
        "/mnt/usbhdd2/bind/a": 2,
        "/mnt/usbhdd2/b": 2,
        "/mnt/usbhdd3/c": 3,
    }

    disks, unknown = build_disks(
        uses,
        mounts,
        include_all=False,
        stat_fn=stats.get,
        dev_fn=devs.get,
    )

    assert unknown == []
    assert [disk["mount"] for disk in disks] == [
        "/mnt/usbhdd2/bind",
        "/mnt/usbhdd3",
    ]
    assert disks[0]["torrents"] == 2
    assert disks[0]["paths"] == ["/mnt/usbhdd2/b", "/mnt/usbhdd2/bind/a"]


def test_build_disks_missing_path_without_mount_is_unknown():
    disks, unknown = build_disks(
        [PathUse("/gone/missing")],
        [],
        include_all=False,
        stat_fn=lambda path: DiskStat(100, 50, 50),
        dev_fn=lambda path: None,
    )
    assert disks == []
    assert unknown == ["/gone/missing"]


def test_build_disks_unstattable_path_is_unknown():
    mounts = [Mount("/dev/sda1", "/", "ext4")]

    disks, unknown = build_disks(
        [PathUse("/diskguard/definitely-missing-path")],
        mounts,
        include_all=False,
        stat_fn=lambda path: DiskStat(100, 50, 50),
        dev_fn=device_id,
    )

    assert disks == []
    assert unknown == ["/diskguard/definitely-missing-path"]


def test_build_disks_drops_disk_when_mount_stat_fails():
    mounts = [Mount("/dev/sda1", "/mnt/usbhdd2", "ext4")]

    disks, unknown = build_disks(
        [PathUse("/mnt/usbhdd2/a", "t1")],
        mounts,
        include_all=False,
        stat_fn=lambda path: None,
        dev_fn=lambda path: 2,
    )

    assert disks == []
    assert unknown == ["/mnt/usbhdd2/a"]


def test_build_disks_marks_deluge_used_and_unused_mounts():
    mounts = [
        Mount("/dev/sda1", "/", "ext4"),
        Mount("/dev/sdb1", "/mnt/usbhdd2", "ext4"),
        Mount("/dev/sdc1", "/mnt/usbhdd3", "ext4"),
    ]
    stats = {
        "/": DiskStat(100, 80, 20),
        "/mnt/usbhdd2": DiskStat(100, 50, 50),
        "/mnt/usbhdd3": DiskStat(100, 50, 50),
    }
    devs = {
        "/": 1,
        "/mnt/usbhdd2": 2,
        "/mnt/usbhdd2/a": 2,
        "/mnt/usbhdd3": 3,
    }

    disks, unknown = build_disks(
        [PathUse("/mnt/usbhdd2/a", "t1")],
        mounts,
        include_all=True,
        stat_fn=stats.get,
        dev_fn=devs.get,
    )

    assert unknown == []
    assert [(disk["mount"], disk["deluge"]) for disk in disks] == [
        ("/", False),
        ("/mnt/usbhdd2", True),
        ("/mnt/usbhdd3", False),
    ]


def test_disk_stat_missing_path_returns_none():
    assert disk_stat("/diskguard/definitely-missing-path") is None
