"""GTK UI plugin for DiskGuard.

The real Storage page lives in M3 #9; this module keeps GTK imports lazy so
headless core tests do not need PyGObject.
"""

from __future__ import annotations

import logging

from deluge.plugins.pluginbase import Gtk3PluginBase

log = logging.getLogger(__name__)


class GtkUI(Gtk3PluginBase):
    def __init__(self, plugin_name):
        super().__init__(plugin_name)
        self._page = None

    def enable(self):
        if self._page is not None:
            return

        import gi

        gi.require_version("Gtk", "3.0")
        from deluge import component
        from gi.repository import Gtk

        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        page.pack_start(
            Gtk.Label(label="DiskGuard storage"), False, False, 0
        )
        page.show_all()
        component.get("Preferences").add_page("DiskGuard", page)
        self._page = page
        log.info("DiskGuard GTK page enabled")

    def disable(self):
        if self._page is None:
            return
        from deluge import component

        try:
            component.get("Preferences").remove_page("DiskGuard")
        finally:
            self._page = None
        log.info("DiskGuard GTK page disabled")

    def update(self):
        pass
