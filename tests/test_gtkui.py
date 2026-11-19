import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
from twisted.internet import defer

from diskguard import GtkUIPlugin
from diskguard.gtkui import (
    GtkUI,
    clear_mount_payload,
    defaults_payload,
    disk_rows,
    format_bytes,
    format_pct,
    mount_choices,
    mount_payload,
)

FIXTURE = Path(__file__).parent / "fixtures" / "storage_payload.json"


class FakeWidget:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.children = []
        self.callbacks = {}
        self.shown = False
        self.destroyed = False

    def pack_start(self, child, expand=False, fill=False, padding=0):
        self.children.append(child)

    def pack_end(self, child, expand=False, fill=False, padding=0):
        self.children.append(child)

    def add(self, child):
        self.children.append(child)

    def append(self, child):
        self.children.append(child)

    def show(self):
        self.shown = True

    def show_all(self):
        pass

    def set_tooltip_text(self, text):
        self.tooltip = text

    def connect(self, signal, callback):
        self.callbacks[signal] = callback

    def get_children(self):
        return list(self.children)

    def remove(self, child):
        self.children.remove(child)

    def destroy(self):
        self.destroyed = True


class FakeMenuItem(FakeWidget):
    @classmethod
    def new_with_label(cls, label):
        item = cls(label=label)
        item.label = label
        return item


class FakeLabel(FakeWidget):
    def __init__(self, label="", **kwargs):
        super().__init__(**kwargs)
        self.text = label

    def set_text(self, text):
        self.text = text


class FakeButton(FakeWidget):
    pass


class FakeCheckButton(FakeWidget):
    def __init__(self, label="", **kwargs):
        super().__init__(**kwargs)
        self.active = False

    def set_active(self, active):
        self.active = bool(active)

    def get_active(self):
        return self.active


class FakeSpinButton(FakeWidget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.value = 0.0

    def set_range(self, minimum, maximum):
        self.range = (minimum, maximum)

    def set_digits(self, digits):
        self.digits = digits

    def set_value(self, value):
        self.value = float(value)

    def get_value(self):
        return self.value


class FakeComboBoxText(FakeWidget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.items = []
        self.active_index = None

    def append_text(self, text):
        self.items.append(text)

    def remove_all(self):
        self.items = []
        self.active_index = None

    def set_active(self, index):
        self.active_index = index

    def get_active_text(self):
        if self.active_index is None:
            return None
        return self.items[self.active_index]


class FakeGtk:
    class Orientation:
        VERTICAL = 1
        HORIZONTAL = 2

    class MessageType:
        WARNING = 1

    class ButtonsType:
        OK = 1

    Box = FakeWidget
    Label = FakeLabel
    Button = FakeButton
    CheckButton = FakeCheckButton
    SpinButton = FakeSpinButton
    ComboBoxText = FakeComboBoxText
    Frame = FakeWidget
    MenuItem = FakeMenuItem
    MessageDialog = None


class FakeMessageDialog:
    instances = []

    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.secondary = None
        self.shown = False
        self.destroyed = False
        FakeMessageDialog.instances.append(self)

    def format_secondary_text(self, text):
        self.secondary = text

    def connect(self, signal, callback):
        self.callback = callback

    def show(self):
        self.shown = True

    def destroy(self):
        self.destroyed = True


FakeGtk.MessageDialog = FakeMessageDialog


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
    def __init__(self, payload, config):
        self.payload = payload
        self.config = config
        self.saved = []

    def get_storage(self):
        return defer.succeed(self.payload)

    def get_guard_config(self):
        return defer.succeed(self.config)

    def set_guard_config(self, payload):
        self.saved.append(payload)
        return defer.succeed(payload)


@pytest.fixture(scope="module")
def gtk_ui():
    plugin = GtkUIPlugin("DiskGuard")
    yield plugin.plugin


def test_gtk_plugin_entry_point(gtk_ui):
    assert isinstance(gtk_ui, GtkUI)


def test_format_helpers():
    assert format_bytes(0) == "0 B"
    assert format_bytes(1024) == "1.0 KiB"
    assert format_bytes(1024**3) == "1.0 GiB"
    assert format_pct(0.987) == "98.7%"
    assert format_pct(1.5) == "100.0%"
    assert format_pct(-1) == "0.0%"


def test_disk_rows_preserve_order_and_units():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    rows = disk_rows(payload)

    assert rows[0][0] == "/mnt/usbhdd2"
    assert rows[0][2] == "100 B"
    assert rows[0][4] == "90.0%"
    assert rows[0][-1] == "LOW"
    assert rows[-1][0] == "/mnt/backup"
    assert rows[-1][-1] == "OK"


def test_mount_choices_include_overrides():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    choices = mount_choices(
        payload, {"per_mount": {"/mnt/override": {"min_free_gb": 1}}}
    )

    assert choices == ["/", "/mnt/backup", "/mnt/override", "/mnt/usbhdd2"]


def test_empty_payload_helpers():
    payload = {"disks": [], "unknown": []}

    assert disk_rows(payload) == []
    assert mount_choices(payload, {"per_mount": {}}) == []


def test_payload_builders():
    assert defaults_payload(False, 20, 6) == {
        "enabled": False,
        "default_min_free_gb": 20.0,
        "resume_margin_gb": 6.0,
    }
    assert mount_payload("/mnt/x", 20, 6, "notify") == {
        "per_mount": {
            "/mnt/x": {
                "min_free_gb": 20.0,
                "resume_margin_gb": 6.0,
                "action": "notify",
            }
        }
    }
    assert clear_mount_payload("/mnt/x") == {"per_mount": {"/mnt/x": {}}}


def test_gtkui_enable_refresh_apply_and_disable(gtk_ui, monkeypatch):
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
    config = {
        "enabled": True,
        "default_min_free_gb": 10.0,
        "resume_margin_gb": 5.0,
        "per_mount": {
            "/mnt/usbhdd2": {
                "min_free_gb": 20.0,
                "resume_margin_gb": 5.0,
                "action": "pause",
            }
        },
    }
    fake_core = FakeDiskGuard(payload, config)
    preferences = FakePreferences()
    view_menu = FakeWidget()
    main_window = FakeMainWindow(view_menu)

    gi = ModuleType("gi")
    gi.require_version = lambda *args: None
    repository = ModuleType("gi.repository")
    repository.Gtk = FakeGtk
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repository)

    from deluge import component
    from deluge.ui.client import client

    def get_component(name):
        if name == "MainWindow":
            return main_window
        return preferences

    monkeypatch.setattr(component, "get", get_component)
    monkeypatch.setattr(client, "diskguard", fake_core, raising=False)
    events = {}

    def register(name, handler):
        events.setdefault(name, []).append(handler)

    def deregister(name, handler):
        events[name].remove(handler)

    monkeypatch.setattr(
        client, "register_event_handler", register, raising=False
    )
    monkeypatch.setattr(
        client, "deregister_event_handler", deregister, raising=False
    )
    FakeMessageDialog.instances = []

    gtk_ui.enable()
    assert preferences.pages["DiskGuard"] is gtk_ui._page
    assert len(events["LowTargetEvent"]) == 1
    assert len(view_menu.children) == 1
    menu_item = view_menu.children[0]
    assert menu_item.label == "DiskGuard Storage"
    assert menu_item.shown is True
    assert menu_item.callbacks["activate"]
    menu_item.callbacks["activate"](menu_item)
    assert preferences.shown == ["DiskGuard"]
    rows = gtk_ui._rows_box.get_children()
    assert len(rows) == 4
    assert rows[-1].text.startswith("Unknown:")
    assert gtk_ui._default_min.get_value() == 10.0
    assert gtk_ui._mount_combo.get_active_text() == "/"

    gtk_ui._mount_combo.set_active(2)
    gtk_ui._on_mount_changed()
    assert gtk_ui._mount_min.get_value() == 20.0

    gtk_ui._on_low_target_event(
        "t1", "/mnt/usbhdd2/downloads", "/mnt/usbhdd2"
    )
    gtk_ui._on_low_target_event(
        "t1", "/mnt/usbhdd2/downloads", "/mnt/usbhdd2"
    )
    assert len(FakeMessageDialog.instances) == 1
    assert FakeMessageDialog.instances[0].shown is True

    gtk_ui.enable()
    assert list(preferences.pages) == ["DiskGuard"]

    gtk_ui._enabled.set_active(False)
    gtk_ui._default_min.set_value(12)
    gtk_ui._default_margin.set_value(6)
    gtk_ui._on_apply_defaults()
    assert fake_core.saved[-1] == {
        "enabled": False,
        "default_min_free_gb": 12.0,
        "resume_margin_gb": 6.0,
    }

    gtk_ui._mount_min.set_value(25)
    gtk_ui._mount_margin.set_value(7)
    gtk_ui._mount_action.set_active(1)
    gtk_ui._on_apply_mount()
    assert fake_core.saved[-1] == mount_payload(
        "/mnt/usbhdd2", 25, 7, "notify"
    )

    gtk_ui._on_clear_mount()
    assert fake_core.saved[-1] == clear_mount_payload("/mnt/usbhdd2")

    gtk_ui.disable()
    assert preferences.pages == {}
    assert view_menu.children == []
    assert menu_item.destroyed is True
    assert events["LowTargetEvent"] == []
    gtk_ui.disable()
