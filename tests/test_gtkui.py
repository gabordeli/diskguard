from diskguard import GtkUIPlugin
from diskguard.gtkui import GtkUI


def test_gtk_plugin_entry_point_and_disable():
    plugin = GtkUIPlugin("DiskGuard")

    assert isinstance(plugin.plugin, GtkUI)
    plugin.plugin.disable()
