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
    view_menu = Gtk.Menu()
    main_window = FakeMainWindow(view_menu)

    def get_component(name):
        if name == "MainWindow":
            return main_window
        return preferences

    component.get = get_component
    client.diskguard = FakeDiskGuard(payload)

    from diskguard.gtkui import GtkUI

    ui = GtkUI("DiskGuard")
    ui.enable()
    assert list(preferences.pages) == ["DiskGuard"]
    menu_items = view_menu.get_children()
    assert len(menu_items) == 1
    assert menu_items[0].get_label() == "DiskGuard Storage"
    menu_items[0].activate()
    assert preferences.shown == ["DiskGuard"]
    ui.disable()
    assert preferences.pages == {}
    assert view_menu.get_children() == []
    print("GTK smoke ok")


if __name__ == "__main__":
    main()
