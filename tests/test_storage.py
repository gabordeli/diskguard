import pytest

from diskguard.storage import (
    ACTIVE_STATES,
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


class FakeTorrent:
    def __init__(self, status, error=None):
        self._status = status
        self._error = error
        self.calls = []

    def get_status(self, keys, update=False):
        self.calls.append((tuple(keys), update))
        if self._error:
            raise self._error
        return self._status


class FakeTorrentManager:
    def __init__(self, torrents):
        self.torrents = torrents


class FakeCore:
    def __init__(self, torrents=None, config=None, config_error=None):
        self.torrentmanager = FakeTorrentManager(torrents or {})
        self._config = (
            config
            if config is not None
            else {"download_location": "/downloads"}
        )
        self._config_error = config_error

    def get_config_values(self, keys):
        if self._config_error:
            raise self._config_error
        return {key: self._config[key] for key in keys if key in self._config}


class FakeLabel:
    def __init__(self, torrent_labels=None, options=None, error=None):
        self.torrent_labels = torrent_labels or {}
        self.labels = options or {}
        self._error = error

    def get_options(self, label):
        if self._error:
            raise self._error
        return self.labels[label]


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


def test_collect_paths_session_default_only():
    uses = collect_paths(FakeCore())

    assert [use.path for use in uses] == ["/downloads"]
    assert uses[0].kind == "session"
    assert uses[0].torrent_id is None
    assert uses[0].active is False


def test_collect_paths_session_move_completed_target():
    core = FakeCore(
        config={
            "download_location": "/downloads",
            "move_completed": True,
            "move_completed_path": "/archive",
        }
    )

    moved = [use for use in collect_paths(core) if use.kind == "move_completed"]

    assert [use.path for use in moved] == ["/archive"]


def test_collect_paths_session_move_completed_requires_flag():
    core = FakeCore(
        config={
            "download_location": "/downloads",
            "move_completed": False,
            "move_completed_path": "/archive",
        }
    )

    assert all(use.path != "/archive" for use in collect_paths(core))


def test_collect_paths_torrent_paths_are_active_and_sized():
    status = {
        "download_location": "/incomplete/t1",
        "save_path": "/incomplete/t1",
        "state": "Downloading",
        "total_size": 123,
        "move_completed": False,
        "move_completed_path": "",
    }
    torrent = FakeTorrent(status)

    uses = collect_paths(FakeCore({"t1": torrent}))
    torrent_uses = [use for use in uses if use.torrent_id == "t1"]

    assert len(torrent_uses) == 1
    assert torrent_uses[0].path == "/incomplete/t1"
    assert torrent_uses[0].active is True
    assert torrent_uses[0].size == 123
    assert torrent.calls[0][1] is False


def test_collect_paths_torrent_move_completed_target():
    status = {
        "download_location": "/incomplete/t1",
        "save_path": "/incomplete/t1",
        "state": "Downloading",
        "total_size": 123,
        "move_completed": True,
        "move_completed_path": "/archive/t1",
    }

    uses = collect_paths(FakeCore({"t1": FakeTorrent(status)}))
    moved = [use for use in uses if use.kind == "move_completed"]

    assert [(use.path, use.torrent_id, use.size) for use in moved] == [
        ("/archive/t1", "t1", 123)
    ]
    assert moved[0].active is False


def test_collect_paths_torrent_move_completed_requires_flag():
    status = {
        "download_location": "/incomplete/t1",
        "state": "Downloading",
        "move_completed": False,
        "move_completed_path": "/archive/t1",
    }

    uses = collect_paths(FakeCore({"t1": FakeTorrent(status)}))

    assert all(use.kind != "move_completed" for use in uses)


@pytest.mark.parametrize(
    ("state", "expected"),
    [
        *((state, True) for state in sorted(ACTIVE_STATES)),
        ("Seeding", False),
        ("Paused", False),
        ("Error", False),
    ],
)
def test_collect_paths_active_state(state, expected):
    status = {"download_location": "/data", "state": state}

    uses = collect_paths(FakeCore({"t1": FakeTorrent(status)}))
    torrent_use = next(use for use in uses if use.torrent_id == "t1")

    assert torrent_use.active is expected


def test_collect_paths_tolerates_missing_label_plugin():
    status = {"download_location": "/data", "state": "Downloading"}

    uses = collect_paths(FakeCore({"t1": FakeTorrent(status)}), None)

    torrent_use = next(use for use in uses if use.torrent_id == "t1")
    assert torrent_use.label is None


def test_collect_paths_tolerates_label_option_errors():
    status = {"download_location": "/data", "state": "Downloading"}
    label = FakeLabel({"t1": "film"}, error=KeyError("film"))

    uses = collect_paths(FakeCore({"t1": FakeTorrent(status)}), label)

    assert any(use.torrent_id == "t1" for use in uses)
    assert all(use.kind != "move_completed" for use in uses)


def test_collect_paths_tolerates_missing_label_options():
    status = {"download_location": "/data", "state": "Downloading"}
    label = FakeLabel({"t1": "film"}, {"film": None})

    uses = collect_paths(FakeCore({"t1": FakeTorrent(status)}), label)

    assert any(use.torrent_id == "t1" for use in uses)
    assert all(use.kind != "move_completed" for use in uses)


@pytest.mark.parametrize(
    ("options", "expected"),
    [
        (
            {
                "apply_move_completed": True,
                "move_completed": True,
                "move_completed_path": "/moved/film",
            },
            "/moved/film",
        ),
        (
            {
                "apply_move_completed": True,
                "move_completed": False,
                "move_completed_path": "/moved/film",
            },
            None,
        ),
        (
            {
                "apply_move_completed": False,
                "move_completed": True,
                "move_completed_path": "/moved/film",
            },
            None,
        ),
        (
            {
                "apply_move_completed": True,
                "move_completed": True,
                "move_completed_path": "",
            },
            None,
        ),
    ],
)
def test_collect_paths_label_move_completed_requires_both_flags(
    options, expected
):
    status = {
        "download_location": "/data",
        "state": "Seeding",
        "move_completed": False,
    }
    label = FakeLabel({"t1": "film"}, {"film": options})

    moved = [
        use
        for use in collect_paths(FakeCore({"t1": FakeTorrent(status)}), label)
        if use.kind == "move_completed"
    ]

    assert [use.path for use in moved] == ([expected] if expected else [])
    if expected:
        assert moved[0].label == "film"


def test_collect_paths_deduplicates_path_per_torrent():
    status = {"download_location": "/same", "save_path": "/same"}

    uses = collect_paths(FakeCore({"t1": FakeTorrent(status)}))

    assert len([use for use in uses if use.torrent_id == "t1"]) == 1


def test_collect_paths_keeps_same_path_for_different_torrents():
    status = {"download_location": "/same"}

    uses = collect_paths(
        FakeCore({"t1": FakeTorrent(status), "t2": FakeTorrent(status)})
    )

    assert len([use for use in uses if use.path == "/same"]) == 2


def test_collect_paths_tolerates_config_and_torrent_errors():
    core = FakeCore(
        {
            "bad": FakeTorrent({}, error=RuntimeError("boom")),
            "good": FakeTorrent({"download_location": "/good"}),
        },
        config_error=RuntimeError("config"),
    )

    uses = collect_paths(core)

    assert [use.path for use in uses] == ["/good"]


def test_collect_paths_ignores_empty_paths():
    core = FakeCore(
        {
            "t1": FakeTorrent(
                {"download_location": "", "save_path": None, "state": "Queued"}
            )
        },
        config={"download_location": ""},
    )

    assert collect_paths(core) == []
