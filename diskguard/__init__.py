"""DiskGuard: multi-filesystem free-space guard for Deluge 2.x."""

from deluge.plugins.init import PluginInitBase

from .version import __version__

__all__ = ["CorePlugin", "GtkUIPlugin", "__version__"]


class CorePlugin(PluginInitBase):
    def __init__(self, plugin_name):
        from .core import Core as _plugin_cls

        self._plugin_cls = _plugin_cls
        super().__init__(plugin_name)


class GtkUIPlugin(PluginInitBase):
    def __init__(self, plugin_name):
        from .gtkui import GtkUI as _plugin_cls

        self._plugin_cls = _plugin_cls
        super().__init__(plugin_name)
