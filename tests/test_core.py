import json
import os

import pytest

from diskguard import CorePlugin
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
