import json
import os

import pytest
from twisted.internet import defer

from diskguard import CorePlugin
from diskguard import core as core_module
from diskguard.core import Core

pytestmark = pytest.mark.skipif(
    os.environ.get("DISKGUARD_REAL_DELUGE") == "1",
    reason="stub-only component registry",
)


def test_plugin_entry_point():
    plugin = CorePlugin("DiskGuard")
    assert plugin.plugin.plugin_name == "DiskGuard"


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

    assert captured["func"] is core_module.storage.build_disks
    assert captured["kwargs"] == {"thresholds": {}}
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

    with caplog.at_level("WARNING"):
        core._apply_payload((disks, []))
    assert core._state.low_mounts == {"/mnt/x"}
    assert sum("low" in record.getMessage() for record in caplog.records) == 1

    caplog.clear()
    with caplog.at_level("WARNING"):
        core._apply_payload((disks, []))
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
