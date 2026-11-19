import sys
from types import ModuleType, SimpleNamespace

import pytest

from diskguard import GtkUIPlugin
from diskguard.gtkui import GtkUI


@pytest.fixture(scope="module")
def gtk_ui():
    plugin = GtkUIPlugin("DiskGuard")
    yield plugin.plugin


def test_gtk_plugin_entry_point(gtk_ui):
    assert isinstance(gtk_ui, GtkUI)


def test_gtkui_enable_disable_is_idempotent(gtk_ui, monkeypatch):
    class FakeWidget:
        def pack_start(self, *args, **kwargs):
            pass

        def show_all(self):
            pass

    class FakeGtk:
        Orientation = SimpleNamespace(VERTICAL=1)

        @staticmethod
        def Box(**kwargs):
            return FakeWidget()

        @staticmethod
        def Label(**kwargs):
            return FakeWidget()

    gi = ModuleType("gi")
    gi.require_version = lambda *args: None
    repository = ModuleType("gi.repository")
    repository.Gtk = FakeGtk
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repository)

    from deluge import component

    class FakePreferences:
        def __init__(self):
            self.pages = {}

        def add_page(self, name, widget):
            self.pages[name] = widget

        def remove_page(self, name):
            self.pages.pop(name)

    preferences = FakePreferences()
    monkeypatch.setattr(component, "get", lambda name: preferences)

    gtk_ui.enable()
    assert preferences.pages["DiskGuard"] is gtk_ui._page

    gtk_ui.enable()
    assert list(preferences.pages) == ["DiskGuard"]

    gtk_ui.disable()
    assert preferences.pages == {}

    gtk_ui.disable()
