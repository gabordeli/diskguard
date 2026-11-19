import json
import os
from types import SimpleNamespace

import pytest
from twisted.internet import defer

from diskguard import CorePlugin
from diskguard import core as core_module
from diskguard.core import Core, GuardEvent, LowTargetEvent
from diskguard.storage import Mount, PathUse

pytestmark = pytest.mark.skipif(
    os.environ.get("DISKGUARD_REAL_DELUGE") == "1",
    reason="stub-only component registry",
)


def test_plugin_entry_point():
    plugin = CorePlugin("DiskGuard")
    assert plugin.plugin.plugin_name == "DiskGuard"


def test_core_rpc_methods_are_exported():
    for name in (
        "get_storage",
        "get_guard_config",
        "set_guard_config",
        "get_guard_state",
        "get_last_events",
    ):
        assert getattr(Core, name)._rpcserver_export is True


def test_enable_sets_defaults():
    core = Core("DiskGuard")
    core.enable()
    assert core.config["enabled"] is True
    assert core.config["default_min_free_gb"] == 10.0
    assert core.config["per_mount"] == {}


def test_set_guard_config_round_trips():
    core = Core("DiskGuard")
    core.enable()
    result = core.set_guard_config(
        {
            "default_min_free_gb": 20,
            "resume_margin_gb": 4,
            "per_mount": {
                "/mnt/usbhdd2": {"min_free_gb": 30, "action": "notify"}
            },
        }
    )
    assert result["default_min_free_gb"] == 20.0
    assert result["per_mount"]["/mnt/usbhdd2"]["min_free_gb"] == 30.0


def test_set_guard_config_replaces_per_mount_map():
    core = Core("DiskGuard")
    core.enable()
    core.set_guard_config(
        {
            "per_mount": {
                "/mnt/one": {"min_free_gb": 10},
                "/mnt/two": {"min_free_gb": 20},
            }
        }
    )

    result = core.set_guard_config(
        {"per_mount": {"/mnt/one": {"min_free_gb": 30}}}
    )

    assert result["per_mount"] == {"/mnt/one": {"min_free_gb": 30.0}}


def test_set_guard_config_rejects_unknown_key():
    core = Core("DiskGuard")
    core.enable()
    with pytest.raises(ValueError, match="unknown config keys"):
        core.set_guard_config({"nope": 1})


def test_get_guard_config_defaults():
    core = Core("DiskGuard")
    core.enable()

    assert core.get_guard_config() == {
        "enabled": True,
        "default_min_free_gb": 10.0,
        "resume_margin_gb": 5.0,
        "per_mount": {},
    }


def test_set_guard_config_updates_enabled_and_is_json_serialisable():
    core = Core("DiskGuard")
    core.enable()

    result = core.set_guard_config(
        {
            "enabled": False,
            "per_mount": {
                "/mnt/usbhdd2": {
                    "min_free_gb": 30,
                    "resume_margin_gb": 6,
                    "action": "notify",
                }
            },
        }
    )

    assert result["enabled"] is False
    assert result["per_mount"]["/mnt/usbhdd2"] == {
        "min_free_gb": 30.0,
        "resume_margin_gb": 6.0,
        "action": "notify",
    }
    json.dumps(result)


def test_set_guard_config_persists(monkeypatch):
    core = Core("DiskGuard")
    core.enable()
    calls = []
    monkeypatch.setattr(core.config, "save", lambda: calls.append(True))

    core.set_guard_config({"default_min_free_gb": 12})

    assert calls == [True]
    assert core.get_guard_config()["default_min_free_gb"] == 12.0


@pytest.mark.parametrize(
    "payload",
    [
        {"enabled": "yes"},
        {"default_min_free_gb": -1},
        {"per_mount": {"/mnt/usbhdd2": {"nope": 1}}},
        {"per_mount": {"/mnt/usbhdd2": {"action": "drop"}}},
    ],
)
def test_set_guard_config_rejects_invalid_values(payload):
    core = Core("DiskGuard")
    core.enable()

    with pytest.raises(ValueError):
        core.set_guard_config(payload)


class FakeLoopingCall:
    def __init__(self, func):
        self.func = func
        self.started = []
        self.stops = 0

    def start(self, interval, now=True):
        self.started.append((interval, now))

    def stop(self):
        self.stops += 1


def _enabled_core(monkeypatch):
    core = Core("DiskGuard")
    monkeypatch.setattr(core, "_start_guard", lambda: None)
    core.enable()
    return core


def test_thresholds_only_include_deluge_used_mounts(monkeypatch):
    core = _enabled_core(monkeypatch)
    mounts = [
        Mount("/dev/sda1", "/mnt/used", "ext4"),
        Mount("/dev/sdb1", "/mnt/unused", "ext4"),
    ]
    uses = [PathUse("/mnt/used/a", "t1")]

    thresholds = core._thresholds(mounts, uses)

    assert set(thresholds) == {"/mnt/used"}


def test_guard_loop_start_stop_is_idempotent(monkeypatch):
    loops = []

    def factory(func):
        loop = FakeLoopingCall(func)
        loops.append(loop)
        return loop

    monkeypatch.setattr(core_module.task, "LoopingCall", factory)
    core = Core("DiskGuard")

    core.enable()
    core.enable()
    assert len(loops) == 1
    assert loops[0].started == [(core_module.GUARD_POLL_SECONDS, False)]

    core.disable()
    core.disable()
    assert loops[0].stops == 1
    assert core._loop is None


def test_poll_runs_build_disks_off_reactor(monkeypatch):
    core = _enabled_core(monkeypatch)
    monkeypatch.setattr(core, "_collect_uses", lambda: [])
    monkeypatch.setattr(core, "mounts", lambda: [])
    captured = {}

    def fake_defer(func, *args, **kwargs):
        captured["func"] = func
        captured["args"] = args
        captured["kwargs"] = kwargs
        return defer.succeed(func(*args, **kwargs))

    monkeypatch.setattr(core_module.threads, "deferToThread", fake_defer)

    core._poll()

    assert captured["func"].__name__ == "_build_payload"
    assert captured["args"] == ([], [], {})
    assert captured["kwargs"] == {}
    assert core._cache == {"disks": [], "unknown": []}
    assert core._polling is False


def test_poll_resets_flag_on_setup_error(monkeypatch):
    core = _enabled_core(monkeypatch)

    def boom():
        raise RuntimeError("setup failed")

    monkeypatch.setattr(core, "_collect_uses", boom)

    assert core._poll() is None
    assert core._polling is False


def test_poll_resets_flag_on_build_error(monkeypatch):
    core = _enabled_core(monkeypatch)
    monkeypatch.setattr(core, "_collect_uses", lambda: [])
    monkeypatch.setattr(core, "mounts", lambda: [])
    monkeypatch.setattr(
        core_module.threads,
        "deferToThread",
        lambda *args, **kwargs: defer.fail(RuntimeError("build failed")),
    )

    core._poll()

    assert core._polling is False
    assert core._cache is None


def test_poll_skips_when_disabled(monkeypatch):
    core = _enabled_core(monkeypatch)
    core.config["enabled"] = False

    assert core._poll() is None
    assert core._polling is False


def test_apply_payload_emits_low_once(monkeypatch, caplog):
    core = _enabled_core(monkeypatch)
    disks = [{"mount": "/mnt/x", "free": 1, "deluge": True}]
    monkeypatch.setattr(
        core,
        "_apply_events",
        lambda events, uses, mounts, aliases: None,
    )

    with caplog.at_level("WARNING"):
        core._apply_payload(([], [], {}, disks, []))
    assert core._state.low_mounts == {"/mnt/x"}
    assert sum("low" in record.getMessage() for record in caplog.records) == 1

    caplog.clear()
    with caplog.at_level("WARNING"):
        core._apply_payload(([], [], {}, disks, []))
    assert caplog.records == []


def test_get_storage_uses_cache(monkeypatch):
    core = _enabled_core(monkeypatch)
    cached = {"disks": [{"mount": "/mnt/x"}], "unknown": []}
    core._cache = cached

    def boom():
        raise AssertionError("should not rebuild storage")

    monkeypatch.setattr(core, "_collect_uses", boom)

    assert core.get_storage() is cached


def test_set_guard_config_invalidates_cache(monkeypatch):
    core = _enabled_core(monkeypatch)
    core._cache = {"disks": [], "unknown": []}

    core.set_guard_config({"default_min_free_gb": 12})

    assert core._cache is None


class FakeActionTorrent:
    def __init__(self, state):
        self.state = state

    def get_status(self, keys, update=False):
        assert update is False
        return {"state": self.state}


class FakeActionCore:
    def __init__(self, states):
        torrents = {
            torrent_id: FakeActionTorrent(state)
            for torrent_id, state in states.items()
        }
        self.torrentmanager = SimpleNamespace(torrents=torrents)
        self.paused = []
        self.resumed = []

    def pause_torrents(self, ids):
        self.paused.append(list(ids))

    def resume_torrents(self, ids):
        self.resumed.append(list(ids))


def _action_core(monkeypatch, states):
    core = _enabled_core(monkeypatch)
    fake = FakeActionCore(states)
    monkeypatch.setattr(core, "_core", lambda: fake)
    return core, fake


def test_apply_events_pauses_only_affected_active_torrents(monkeypatch):
    core, fake = _action_core(
        monkeypatch,
        {"t1": "Downloading", "t2": "Downloading", "t3": "Seeding"},
    )
    mounts = [
        Mount("/dev/sda1", "/mnt/a", "ext4"),
        Mount("/dev/sdb1", "/mnt/b", "ext4"),
    ]
    uses = [
        PathUse("/mnt/a/one", "t1", active=True),
        PathUse("/mnt/b/two", "t2", active=True),
        PathUse("/mnt/a/seed", "t3", active=False),
    ]

    core._apply_events(
        [{"mount": "/mnt/a", "event": "low", "action": "pause"}],
        uses,
        mounts,
    )

    assert fake.paused == [["t1"]]
    assert core._state.paused_torrents == {"t1"}
    assert core._state.paused_by_mount == {"/mnt/a": {"t1"}}


def test_apply_events_pauses_bind_mount_aliases(monkeypatch):
    core, fake = _action_core(monkeypatch, {"t1": "Downloading"})
    mounts = [
        Mount("/dev/sda1", "/mnt/a", "ext4"),
        Mount("/dev/sda1", "/mnt/a/bind", "ext4"),
    ]
    uses = [PathUse("/mnt/a/one", "t1", active=True)]

    core._apply_events(
        [{"mount": "/mnt/a/bind", "event": "low", "action": "pause"}],
        uses,
        mounts,
        {"/mnt/a/bind": {"/mnt/a", "/mnt/a/bind"}},
    )

    assert fake.paused == [["t1"]]


def test_apply_events_continues_when_one_pause_fails(monkeypatch):
    core, fake = _action_core(
        monkeypatch, {"t1": "Downloading", "t2": "Downloading"}
    )
    mounts = [Mount("/dev/sda1", "/mnt/a", "ext4")]
    uses = [
        PathUse("/mnt/a/one", "t1", active=True),
        PathUse("/mnt/a/two", "t2", active=True),
    ]

    def pause(ids):
        if ids == ["t2"]:
            raise KeyError("torrent disappeared")
        fake.paused.append(list(ids))

    fake.pause_torrents = pause

    core._apply_events(
        [{"mount": "/mnt/a", "event": "low", "action": "pause"}],
        uses,
        mounts,
    )

    assert fake.paused == [["t1"]]
    assert core._state.paused_torrents == {"t1"}


def test_apply_events_respects_manual_pause_after_snapshot(monkeypatch):
    core, fake = _action_core(monkeypatch, {"t1": "Paused"})
    mounts = [Mount("/dev/sda1", "/mnt/a", "ext4")]
    uses = [PathUse("/mnt/a/one", "t1", active=True)]

    core._apply_events(
        [{"mount": "/mnt/a", "event": "low", "action": "pause"}],
        uses,
        mounts,
    )

    assert fake.paused == []
    assert core._state.paused_torrents == set()


def test_apply_events_resumes_only_guard_paused_torrents(monkeypatch):
    core, fake = _action_core(
        monkeypatch, {"t1": "Paused", "manual": "Paused"}
    )
    core._state.paused_torrents = {"t1", "manual"}
    core._state.paused_by_mount = {"/mnt/a": {"t1"}}

    core._apply_events([{"mount": "/mnt/a", "event": "ok"}], [], [])

    assert fake.resumed == [["t1"]]
    assert core._state.paused_torrents == {"manual"}
    assert core._state.paused_by_mount == {}


def test_apply_events_handles_disappeared_torrent(monkeypatch):
    core, fake = _action_core(monkeypatch, {})
    core._state.paused_torrents = {"gone"}
    core._state.paused_by_mount = {"/mnt/a": {"gone"}}

    core._apply_events([{"mount": "/mnt/a", "event": "ok"}], [], [])

    assert fake.resumed == []
    assert core._state.paused_torrents == set()
    assert core._state.paused_by_mount == {}


def test_apply_events_is_idempotent(monkeypatch):
    core, fake = _action_core(monkeypatch, {"t1": "Downloading"})
    mounts = [Mount("/dev/sda1", "/mnt/a", "ext4")]
    uses = [PathUse("/mnt/a/one", "t1", active=True)]
    events = [{"mount": "/mnt/a", "event": "low", "action": "pause"}]

    core._apply_events(events, uses, mounts)
    core._apply_events(events, uses, mounts)

    assert fake.paused == [["t1"]]
    assert core._state.paused_torrents == {"t1"}


def test_apply_events_notify_action_does_not_pause(monkeypatch):
    core, fake = _action_core(monkeypatch, {"t1": "Downloading"})
    mounts = [Mount("/dev/sda1", "/mnt/a", "ext4")]
    uses = [PathUse("/mnt/a/one", "t1", active=True)]

    core._apply_events(
        [{"mount": "/mnt/a", "event": "low", "action": "notify"}],
        uses,
        mounts,
    )

    assert fake.paused == []
    assert core._state.paused_torrents == set()


class FakeEventManager:
    def __init__(self):
        self.events = []

    def emit(self, event):
        self.events.append(event)


def test_emit_low_targets_only_for_new_paths(monkeypatch):
    core = _enabled_core(monkeypatch)
    manager = FakeEventManager()
    monkeypatch.setattr(core, "_event_manager", lambda: manager)
    mounts = [
        Mount("/dev/sda1", "/mnt/low", "ext4"),
        Mount("/dev/sdb1", "/mnt/ok", "ext4"),
    ]
    aliases = {"/mnt/low": {"/mnt/low"}, "/mnt/ok": {"/mnt/ok"}}
    disks = [
        {"mount": "/mnt/low", "low": True, "deluge": True},
        {"mount": "/mnt/ok", "low": False, "deluge": True},
    ]

    core._emit_low_targets(
        [PathUse("/mnt/low/old", "t1")], mounts, aliases, disks
    )
    assert manager.events == []

    core._emit_low_targets(
        [PathUse("/mnt/low/new", "t1")], mounts, aliases, disks
    )
    assert len(manager.events) == 1
    assert isinstance(manager.events[0], LowTargetEvent)
    assert manager.events[0]._args == ["t1", "/mnt/low/new", "/mnt/low"]

    core._emit_low_targets(
        [PathUse("/mnt/ok/new", "t1")], mounts, aliases, disks
    )
    assert len(manager.events) == 1


def test_emit_low_targets_new_torrent_on_low_disk(monkeypatch):
    core = _enabled_core(monkeypatch)
    manager = FakeEventManager()
    monkeypatch.setattr(core, "_event_manager", lambda: manager)
    mounts = [Mount("/dev/sda1", "/mnt/low", "ext4")]
    aliases = {"/mnt/low": {"/mnt/low"}}
    disks = [{"mount": "/mnt/low", "low": True, "deluge": True}]

    core._emit_low_targets([], mounts, aliases, disks)
    core._emit_low_targets(
        [PathUse("/mnt/low/a", "t2")], mounts, aliases, disks
    )

    assert len(manager.events) == 1
    assert manager.events[0]._args == ["t2", "/mnt/low/a", "/mnt/low"]


def test_apply_payload_records_guard_event_and_sink(monkeypatch):
    core = _enabled_core(monkeypatch)
    monkeypatch.setattr(core, "_apply_events", lambda *args: None)
    manager = FakeEventManager()
    monkeypatch.setattr(core, "_event_manager", lambda: manager)
    seen = []
    core.add_event_sink(seen.append)
    mounts = [Mount("/dev/sda1", "/mnt/x", "ext4")]
    aliases = {"/mnt/x": {"/mnt/x"}}
    uses = [PathUse("/mnt/x/a", "t1", active=True)]
    disks = [{"mount": "/mnt/x", "free": 1, "deluge": True}]
    expected = {
        "mount": "/mnt/x",
        "event": "low",
        "free": 1,
        "min_free": 10 * 1024**3,
        "action": "pause",
        "torrents": 1,
    }

    core._apply_payload((uses, mounts, aliases, disks, []))

    assert core.get_last_events() == [expected]
    assert seen == [expected]
    assert len(manager.events) == 1
    assert isinstance(manager.events[0], GuardEvent)
    assert manager.events[0]._args == [
        "/mnt/x",
        "low",
        1,
        10 * 1024**3,
        "pause",
        1,
    ]

    core._apply_payload((uses, mounts, aliases, disks, []))

    assert core.get_last_events() == [expected]
    assert seen == [expected]
    assert len(manager.events) == 1
