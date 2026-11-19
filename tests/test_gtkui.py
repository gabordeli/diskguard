import json
import sys
from pathlib import Path
from types import ModuleType

import pytest
from twisted.internet import defer

from diskguard import GtkUIPlugin
from diskguard.gtkui import (
    GtkUI,
    disk_labels,
    disk_meta,
    disk_summary,
    disk_used_fraction,
    format_bytes,
    format_pct,
    mount_choices,
)

FIXTURE = Path(__file__).parent / "fixtures" / "storage_payload.json"


class FakeStyleContext:
    providers = []

    def __init__(self):
        self.classes = set()

    def add_class(self, name):
        self.classes.add(name)

    def remove_class(self, name):
        self.classes.discard(name)

    @classmethod
    def add_provider_for_screen(cls, screen, provider, priority):
        cls.providers.append((screen, provider, priority))

    @classmethod
    def remove_provider_for_screen(cls, screen, provider):
        cls.providers = [
            item for item in cls.providers if item[1] is not provider
        ]


class FakeCssProvider:
    def __init__(self):
        self.data = None

    def load_from_data(self, data):
        self.data = data


class FakeWidget:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.children = []
        self.callbacks = {}
        self.shown = False
        self.destroyed = False
        self.style_context = FakeStyleContext()

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
        self.shown = True
        for child in self.children:
            show_all = getattr(child, "show_all", None)
            if show_all is not None:
                show_all()

    def set_tooltip_text(self, text):
        self.tooltip = text

    def connect(self, signal, callback, *args, **kwargs):
        self.callbacks[signal] = callback

    def get_children(self):
        return list(self.children)

    def remove(self, child):
        self.children.remove(child)

    def destroy(self):
        self.destroyed = True

    def get_style_context(self):
        return self.style_context

    def set_margin_start(self, value):
        self.margin_start = value

    def set_margin_end(self, value):
        self.margin_end = value

    def set_margin_top(self, value):
        self.margin_top = value

    def set_margin_bottom(self, value):
        self.margin_bottom = value

    def set_halign(self, value):
        self.halign = value

    def set_valign(self, value):
        self.valign = value

    def set_hexpand(self, value):
        self.hexpand = value

    def set_vexpand(self, value):
        self.vexpand = value

    def set_size_request(self, width, height):
        self.size_request = (width, height)

    def set_line_wrap(self, value):
        self.line_wrap = value

    def set_xalign(self, value):
        self.xalign = value

    def set_opacity(self, value):
        self.opacity = value

    def set_sensitive(self, value):
        self.sensitive = bool(value)

    def get_sensitive(self):
        return getattr(self, "sensitive", True)


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

    def set_increments(self, step, page):
        self.increments = (step, page)

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


class FakeGrid(FakeWidget):
    def attach(self, child, left, top, width, height):
        self.children.append(child)


class FakeImage(FakeWidget):
    @classmethod
    def new_from_icon_name(cls, name, size):
        image = cls(icon_name=name, icon_size=size)
        image.icon_name = name
        image.icon_size = size
        return image


class FakeDrawingArea(FakeWidget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.fraction = 0.0
        self.low = False
        self.queued = False

    def queue_draw(self):
        self.queued = True


class FakeProgressBar(FakeWidget):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.fraction = 0.0
        self.show_text = False
        self.text = ""

    def set_fraction(self, fraction):
        self.fraction = fraction

    def set_show_text(self, show):
        self.show_text = show

    def set_text(self, text):
        self.text = text


class FakeSeparator(FakeWidget):
    pass


class FakeGdk:
    class Screen:
        @staticmethod
        def get_default():
            return "screen"


class FakeGtk:
    class Orientation:
        VERTICAL = 1
        HORIZONTAL = 2

    class Align:
        START = 1
        CENTER = 2
        END = 3
        FILL = 4

    class IconSize:
        BUTTON = 1
        DIALOG = 2

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
    Grid = FakeGrid
    Image = FakeImage
    DrawingArea = FakeDrawingArea
    ProgressBar = FakeProgressBar
    Separator = FakeSeparator
    MenuItem = FakeMenuItem
    CssProvider = FakeCssProvider
    StyleContext = FakeStyleContext
    STYLE_PROVIDER_PRIORITY_APPLICATION = 800
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


class FakeStatusItem:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.image = kwargs.get("icon")
        self.markup = kwargs.get("text", "")
        self.tooltip = kwargs.get("tooltip", "")
        self.callback = kwargs.get("callback")

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


class FakeGLib:
    timeouts = {}
    next_id = 1

    @classmethod
    def timeout_add_seconds(cls, seconds, callback):
        source_id = cls.next_id
        cls.next_id += 1
        cls.timeouts[source_id] = (seconds, callback)
        return source_id

    @classmethod
    def source_remove(cls, source_id):
        cls.timeouts.pop(source_id, None)


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
        self.config.update(payload)
        return defer.succeed(self.config)


@pytest.fixture(scope="module")
def gtk_ui():
    plugin = GtkUIPlugin("DiskGuard")
    return plugin.plugin


def test_gtk_plugin_entry_point(gtk_ui):
    assert isinstance(gtk_ui, GtkUI)


def test_format_helpers():
    assert format_bytes(0) == "0 B"
    assert format_bytes(1024) == "1.0 KiB"
    assert format_bytes(1024**3) == "1.0 GiB"
    assert format_pct(0.987) == "98.7%"
    assert format_pct(1.5) == "100.0%"
    assert format_pct(-1) == "0.0%"


def test_disk_helpers_preserve_order_and_units():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    summary = disk_summary(payload)

    assert summary == {
        "disks": 3,
        "low": 1,
        "free": 100,
        "total": 1000,
        "used": 900,
        "unknown": 1,
    }
    assert disk_used_fraction(payload["disks"][0]) == 0.9
    assert disk_used_fraction({"total": 0, "free": 0}) == 0.0
    assert disk_meta(payload["disks"][0]).startswith("ext4 · 100 B free")
    assert disk_meta(payload["disks"][0]).endswith("90.0% used")
    assert disk_labels(payload["disks"][0]) == "film"
    assert disk_labels({"labels": []}) == "No labels"


def test_mount_choices_include_overrides():
    payload = json.loads(FIXTURE.read_text(encoding="utf-8"))

    choices = mount_choices(
        payload, {"per_mount": {"/mnt/override": {"min_free_gb": 1}}}
    )

    assert choices == ["/", "/mnt/backup", "/mnt/override", "/mnt/usbhdd2"]


def test_empty_payload_helpers():
    payload = {"disks": [], "unknown": []}

    assert disk_summary(payload) == {
        "disks": 0,
        "low": 0,
        "free": 0,
        "total": 0,
        "used": 0,
        "unknown": 0,
    }
    assert mount_choices(payload, {"per_mount": {}}) == []


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
    statusbar = FakeStatusBar()
    monkeypatch.setattr(FakeGLib, "timeouts", {})
    monkeypatch.setattr(FakeGLib, "next_id", 1)
    view_menu = FakeWidget()
    main_window = FakeMainWindow(view_menu)

    gi = ModuleType("gi")
    gi.require_version = lambda *args: None
    repository = ModuleType("gi.repository")
    repository.Gtk = FakeGtk
    repository.Gdk = FakeGdk
    repository.GLib = FakeGLib
    monkeypatch.setitem(sys.modules, "gi", gi)
    monkeypatch.setitem(sys.modules, "gi.repository", repository)

    from deluge import component
    from deluge.ui.client import client

    def get_component(name):
        if name == "MainWindow":
            return main_window
        if name == "StatusBar":
            return statusbar
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
    monkeypatch.setattr(FakeMessageDialog, "instances", [])

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
    assert len(statusbar.items) == 1
    status_item = statusbar.items[0]
    assert status_item.image == "dialog-warning-symbolic"
    assert "1 low" in status_item.markup
    assert "100 B free" in status_item.markup
    assert "3 filesystems" in status_item.tooltip
    status_item.callback(status_item, None)
    assert preferences.shown[-1] == "DiskGuard"
    assert len(FakeGLib.timeouts) == 1
    rows = gtk_ui._rows_box.get_children()
    assert len(rows) == 4
    assert all(row.shown for row in rows)
    assert "diskguard-card" in rows[0].style_context.classes
    assert "diskguard-card-low" in rows[0].style_context.classes
    assert rows[0].children[1].fraction == 0.9
    assert rows[0].children[1].low is True
    assert rows[0].children[1].tooltip == "90.0% used"
    assert gtk_ui._disk_count_label.text == "3"
    assert gtk_ui._low_count_label.text == "1"
    assert gtk_ui._free_total_label.text == "100 B"
    assert gtk_ui._capacity_label.text == "90.0% used"
    assert gtk_ui._capacity_bar.fraction == 0.9
    assert gtk_ui._capacity_bar.low is True
    assert gtk_ui._unknown_label.text.startswith("Unknown paths:")
    assert gtk_ui._page.margin_start == 18
    assert gtk_ui._default_min.get_value() == 10.0
    assert set(gtk_ui._mount_rows) == {
        "/",
        "/mnt/backup",
        "/mnt/usbhdd2",
    }
    disk_row = gtk_ui._mount_rows["/mnt/usbhdd2"]
    assert disk_row["override"].get_active() is True
    assert disk_row["override"].shown is True
    assert disk_row["min"].get_value() == 20.0
    assert disk_row["min"].get_sensitive() is True
    root_row = gtk_ui._mount_rows["/"]
    assert root_row["override"].get_active() is False
    assert root_row["min"].get_value() == 10.0
    assert root_row["min"].get_sensitive() is False
    assert gtk_ui._settings_status.text.startswith("Loaded from daemon")

    gtk_ui._default_min.set_value(12)
    gtk_ui._on_default_changed()
    assert root_row["min"].get_value() == 12.0
    assert disk_row["min"].get_value() == 20.0
    gtk_ui._default_min.set_value(10)
    gtk_ui._on_default_changed()

    gtk_ui._render_storage({"disks": [], "unknown": []})
    assert gtk_ui._rows_box.get_children() == []
    assert gtk_ui._disk_count_label.text == "0"
    assert gtk_ui._low_count_label.text == "0"
    assert gtk_ui._capacity_label.text == "— used"
    assert gtk_ui._capacity_bar.fraction == 0.0
    assert gtk_ui._status_item.image == "drive-harddisk-symbolic"
    assert "0 disks" in gtk_ui._status_item.markup
    assert "diskguard-low-value" not in (
        gtk_ui._low_count_label.style_context.classes
    )
    gtk_ui._render_storage(payload)

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

    root_row = gtk_ui._mount_rows["/"]
    root_row["override"].set_active(True)
    gtk_ui._on_override_toggled(root_row["override"], "/")
    root_row["min"].set_value(30)
    root_row["margin"].set_value(6)
    root_row["action"].set_active(1)
    gtk_ui._enabled.set_active(False)
    gtk_ui._default_min.set_value(12)
    gtk_ui._default_margin.set_value(6)
    gtk_ui._mark_dirty()
    assert gtk_ui._dirty is True
    assert "Unsaved changes" in gtk_ui._settings_status.text

    gtk_ui._on_save()
    assert fake_core.saved[-1] == {
        "enabled": False,
        "default_min_free_gb": 12.0,
        "resume_margin_gb": 6.0,
        "per_mount": {
            "/": {
                "min_free_gb": 30.0,
                "resume_margin_gb": 6.0,
                "action": "notify",
            },
            "/mnt/usbhdd2": {
                "min_free_gb": 20.0,
                "resume_margin_gb": 5.0,
                "action": "pause",
            },
        },
    }
    assert gtk_ui._dirty is False
    assert gtk_ui._mount_rows["/"]["min"].get_value() == 30.0
    assert gtk_ui._settings_status.text.startswith("Loaded from daemon")

    gtk_ui._mount_rows["/"]["min"].set_value(99)
    gtk_ui._mark_dirty()
    assert "Unsaved changes" in gtk_ui._settings_status.text
    gtk_ui._on_reload()
    assert gtk_ui._mount_rows["/"]["min"].get_value() == 30.0
    assert gtk_ui._dirty is False

    gtk_ui.disable()
    assert preferences.pages == {}
    assert view_menu.children == []
    assert menu_item.destroyed is True
    assert statusbar.removed == [status_item]
    assert FakeGLib.timeouts == {}
    assert events["LowTargetEvent"] == []
    gtk_ui.disable()
