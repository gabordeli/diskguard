import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("DISKGUARD_REAL_DELUGE") != "1",
    reason="real Deluge only",
)


def test_core_is_a_deluge_core_plugin():
    from deluge.plugins.pluginbase import CorePluginBase

    from diskguard.core import Core

    assert issubclass(Core, CorePluginBase)
