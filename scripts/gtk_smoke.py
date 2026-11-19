"""Real-GTK page smoke test, without a running daemon.

Run from the repo root with the Deluge/GTK packages installed:

    PYTHONPATH=. xvfb-run -a python3 scripts/gtk_smoke.py
"""

import json
from pathlib import Path

from deluge import component
from deluge.ui.client import client
from twisted.internet import defer

ROOT = Path(__file__).resolve().parent.parent


class FakePreferences:
    def __init__(self):
        self.pages = {}
        self.shown = []

    def add_page(self, name, widget):
        self.pages[name] = widget

    def remove_page(self, name):
        self.pages.pop(name)

    def show(self, page=None):
        self.shown.append(page)


class FakeBuilder:
    def __init__(self, objects):
        self.objects = objects

    def get_object(self, name):
        return self.objects.get(name)


class FakeMainWindow:
    def __init__(self, submenu):
        self.builder = FakeBuilder({"submenu_view": submenu})

    def get_builder(self):
        return self.builder


class FakeStatusItem:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.image = kwargs.get("icon")
        self.markup = kwargs.get("text", "")
        self.tooltip = kwargs.get("tooltip", "")

    def set_image_from_icon(self, icon):
        self.image = icon

    def set_markup(self, markup):
        self.markup = markup

    def set_tooltip(self, tooltip):
        self.tooltip = tooltip


class FakeStatusBar:
    def __init__(self):
        self.items = []
        self.removed = []

    def add_item(self, **kwargs):
        item = FakeStatusItem(**kwargs)
        self.items.append(item)
        return item

    def remove_item(self, item):
        self.removed.append(item)


class FakeDiskGuard:
    def __init__(self, payload):
        self.payload = payload

    def get_storage(self):
        return defer.succeed(self.payload)

    def get_guard_config(self):
        return defer.succeed(
            {
                "enabled": True,
                "default_min_free_gb": 10.0,
                "resume_margin_gb": 5.0,
                "per_mount": {},
            }
        )

    def set_guard_config(self, payload):
        return defer.succeed(payload)


def main():
    payload = json.loads(
        (ROOT / "tests" / "fixtures" / "storage_payload.json").read_text(
            encoding="utf-8"
        )
    )
    import gi

    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    preferences = FakePreferences()
    statusbar = FakeStatusBar()
    view_menu = Gtk.Menu()
    main_window = FakeMainWindow(view_menu)

    def get_component(name):
        if name == "MainWindow":
            return main_window
        if name == "StatusBar":
            return statusbar
        return preferences

    component.get = get_component
    client.diskguard = FakeDiskGuard(payload)

    from diskguard.gtkui import GtkUI

    ui = GtkUI("DiskGuard")
    ui.enable()
    assert list(preferences.pages) == ["DiskGuard"]
    assert len(statusbar.items) == 1
    assert "1 low" in statusbar.items[0].markup
    menu_items = view_menu.get_children()
    assert len(menu_items) == 1
    assert menu_items[0].get_label() == "DiskGuard Storage"
    menu_items[0].activate()
    assert preferences.shown == ["DiskGuard"]
    ui.disable()
    assert preferences.pages == {}
    assert view_menu.get_children() == []
    assert statusbar.removed == [statusbar.items[0]]
    print("GTK smoke ok")


if __name__ == "__main__":
    main()
