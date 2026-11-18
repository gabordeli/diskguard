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
