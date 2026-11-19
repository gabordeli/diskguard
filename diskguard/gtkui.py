"""GTK UI plugin: standalone storage page and guard settings."""

from __future__ import annotations

import logging

from deluge.plugins.pluginbase import Gtk3PluginBase

log = logging.getLogger(__name__)

PAGE_NAME = "DiskGuard"
MENU_LABEL = "DiskGuard Storage"
DISK_COLUMNS = (
    "Mount",
    "FS",
    "Free",
    "Total",
    "Used",
    "Torrents",
    "Labels",
    "Deluge",
    "State",
)


def format_bytes(value) -> str:
    size = float(value or 0)
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    for unit in units:
        if abs(size) < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} B"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TiB"


def format_pct(value) -> str:
    pct = min(1.0, max(0.0, float(value or 0))) * 100
    return f"{pct:.1f}%"


def disk_rows(payload: dict) -> list[list[str]]:
    return [
        [
            str(disk.get("mount", "")),
            str(disk.get("fs", "")),
            format_bytes(disk.get("free")),
            format_bytes(disk.get("total")),
            format_pct(disk.get("used_pct")),
            str(disk.get("torrents", 0)),
            ", ".join(disk.get("labels") or []),
            "yes" if disk.get("deluge") else "no",
            "LOW" if disk.get("low") else "OK",
        ]
        for disk in payload.get("disks", [])
    ]


def mount_choices(payload: dict, config: dict) -> list[str]:
    names = {
        str(disk["mount"])
        for disk in payload.get("disks", [])
        if disk.get("mount")
    }
    names.update((config.get("per_mount") or {}).keys())
    return sorted(names)


def defaults_payload(enabled: bool, min_free_gb, margin_gb) -> dict:
    return {
        "enabled": bool(enabled),
        "default_min_free_gb": float(min_free_gb),
        "resume_margin_gb": float(margin_gb),
    }


def mount_payload(mount: str, min_free_gb, margin_gb, action: str) -> dict:
    return {
        "per_mount": {
            mount: {
                "min_free_gb": float(min_free_gb),
                "resume_margin_gb": float(margin_gb),
                "action": action,
            }
        }
    }


def clear_mount_payload(mount: str) -> dict:
    return {"per_mount": {mount: {}}}


class GtkUI(Gtk3PluginBase):
    def __init__(self, plugin_name):
        super().__init__(plugin_name)
        self._page = None
        self._client = None
        self._gtk = None
        self._storage = {"disks": [], "unknown": []}
        self._config = {}
        self._rows_box = None
        self._status = None
        self._enabled = None
        self._default_min = None
        self._default_margin = None
        self._mount_combo = None
        self._mount_min = None
        self._mount_margin = None
        self._mount_action = None
        self._warned_targets = set()
        self._menu_item = None
        self._menu_parent = None

    def enable(self):
        if self._page is not None:
            return

        import gi

        gi.require_version("Gtk", "3.0")
        from deluge import component
        from deluge.ui.client import client
        from gi.repository import Gtk

        self._client = client
        self._warned_targets.clear()
        client.register_event_handler(
            "LowTargetEvent", self._on_low_target_event
        )
        page = self._build_page(Gtk)
        component.get("Preferences").add_page(PAGE_NAME, page)
        self._add_menu_entry(Gtk)
        self._page = page
        self._refresh()
        log.info("DiskGuard GTK page enabled")

    def disable(self):
        if self._page is None:
            return
        from deluge import component

        self._remove_menu_entry()
        try:
            component.get("Preferences").remove_page(PAGE_NAME)
        finally:
            if self._client is not None:
                try:
                    self._client.deregister_event_handler(
                        "LowTargetEvent", self._on_low_target_event
                    )
                except Exception:
                    log.exception("DiskGuard: event handler removal failed")
            self._page = None
            self._client = None
        log.info("DiskGuard GTK page disabled")

    def update(self):
        pass

    def _add_menu_entry(self, Gtk):
        from deluge import component

        try:
            builder = component.get("MainWindow").get_builder()
            submenu = builder.get_object("submenu_view")
        except Exception:
            log.exception("DiskGuard: main window is not available")
            return
        if submenu is None:
            log.warning("DiskGuard: View menu not found")
            return

        item = Gtk.MenuItem.new_with_label(MENU_LABEL)
        item.set_tooltip_text("Open the DiskGuard storage and guard settings")
        item.connect("activate", self._on_menu_activate)
        submenu.append(item)
        item.show()
        self._menu_item = item
        self._menu_parent = submenu
        log.info("DiskGuard main-window entry added")

    def _remove_menu_entry(self):
        item = self._menu_item
        parent = self._menu_parent
        self._menu_item = None
        self._menu_parent = None
        if item is None or parent is None:
            return
        try:
            parent.remove(item)
            item.destroy()
        except Exception:
            log.exception("DiskGuard: menu entry removal failed")

    def _on_menu_activate(self, widget=None):
        from deluge import component

        try:
            component.get("Preferences").show(PAGE_NAME)
        except Exception:
            log.exception("DiskGuard: unable to open the storage page")

    def _on_low_target_event(self, torrent_id, path, mount):
        key = (torrent_id, path, mount)
        if key in self._warned_targets:
            return
        self._warned_targets.add(key)
        self._set_status(f"Low target: {mount} ({path})")
        if self._gtk is None:
            return
        dialog = self._gtk.MessageDialog(
            message_type=self._gtk.MessageType.WARNING,
            buttons=self._gtk.ButtonsType.OK,
            text=f"Torrent {torrent_id} targets a low disk",
        )
        dialog.format_secondary_text(f"{path} on {mount}")
        dialog.connect(
            "response", lambda widget, response: widget.destroy()
        )
        dialog.show()

    def _build_page(self, Gtk):
        self._gtk = Gtk
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        header.pack_start(Gtk.Label(label="Storage"), False, False, 0)
        refresh = Gtk.Button(label="Refresh")
        refresh.connect("clicked", self._on_refresh)
        header.pack_end(refresh, False, False, 0)
        page.pack_start(header, False, False, 0)

        self._status = Gtk.Label(label="")
        page.pack_start(self._status, False, False, 0)

        header_row = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6
        )
        for title in DISK_COLUMNS:
            header_row.pack_start(
                Gtk.Label(label=title), False, False, 0
            )
        page.pack_start(header_row, False, False, 0)

        self._rows_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=2
        )
        page.pack_start(self._rows_box, True, True, 0)

        defaults = Gtk.Frame(label="Defaults")
        defaults_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6
        )
        self._enabled = Gtk.CheckButton(label="Guard enabled")
        defaults_box.pack_start(self._enabled, False, False, 0)
        defaults_box.pack_start(Gtk.Label(label="Min free (GB)"), False, False, 0)
        self._default_min = Gtk.SpinButton()
        self._default_min.set_range(0, 1000000)
        self._default_min.set_digits(1)
        defaults_box.pack_start(self._default_min, False, False, 0)
        defaults_box.pack_start(Gtk.Label(label="Margin (GB)"), False, False, 0)
        self._default_margin = Gtk.SpinButton()
        self._default_margin.set_range(0, 1000000)
        self._default_margin.set_digits(1)
        defaults_box.pack_start(self._default_margin, False, False, 0)
        apply_defaults = Gtk.Button(label="Apply defaults")
        apply_defaults.connect("clicked", self._on_apply_defaults)
        defaults_box.pack_start(apply_defaults, False, False, 0)
        defaults.add(defaults_box)
        page.pack_start(defaults, False, False, 0)

        mount_frame = Gtk.Frame(label="Per-mount override")
        mount_box = Gtk.Box(
            orientation=Gtk.Orientation.HORIZONTAL, spacing=6
        )
        self._mount_combo = Gtk.ComboBoxText()
        self._mount_combo.connect("changed", self._on_mount_changed)
        mount_box.pack_start(self._mount_combo, False, False, 0)
        mount_box.pack_start(Gtk.Label(label="Min (GB)"), False, False, 0)
        self._mount_min = Gtk.SpinButton()
        self._mount_min.set_range(0, 1000000)
        self._mount_min.set_digits(1)
        mount_box.pack_start(self._mount_min, False, False, 0)
        mount_box.pack_start(Gtk.Label(label="Margin (GB)"), False, False, 0)
        self._mount_margin = Gtk.SpinButton()
        self._mount_margin.set_range(0, 1000000)
        self._mount_margin.set_digits(1)
        mount_box.pack_start(self._mount_margin, False, False, 0)
        self._mount_action = Gtk.ComboBoxText()
        self._mount_action.append_text("pause")
        self._mount_action.append_text("notify")
        self._mount_action.set_active(0)
        mount_box.pack_start(self._mount_action, False, False, 0)
        apply_mount = Gtk.Button(label="Apply mount")
        apply_mount.connect("clicked", self._on_apply_mount)
        mount_box.pack_start(apply_mount, False, False, 0)
        clear_mount = Gtk.Button(label="Clear override")
        clear_mount.connect("clicked", self._on_clear_mount)
        mount_box.pack_start(clear_mount, False, False, 0)
        mount_frame.add(mount_box)
        page.pack_start(mount_frame, False, False, 0)

        page.show_all()
        return page

    def _refresh(self):
        if self._client is None:
            return
        try:
            storage = self._client.diskguard.get_storage()
            config = self._client.diskguard.get_guard_config()
        except Exception as ex:
            self._set_status(str(ex))
            return
        storage.addCallback(self._render_storage).addErrback(self._on_error)
        config.addCallback(self._render_config).addErrback(self._on_error)

    def _render_storage(self, payload):
        self._storage = payload or {"disks": [], "unknown": []}
        for child in list(self._rows_box.get_children()):
            self._rows_box.remove(child)
        for row in disk_rows(self._storage):
            line = self._gtk.Box(
                orientation=self._gtk.Orientation.HORIZONTAL, spacing=6
            )
            for value in row:
                line.pack_start(
                    self._gtk.Label(label=value), False, False, 0
                )
            line.show_all()
            self._rows_box.pack_start(line, False, False, 0)
        unknown = self._storage.get("unknown") or []
        if unknown:
            self._rows_box.pack_start(
                self._gtk.Label(label="Unknown: " + ", ".join(unknown)),
                False,
                False,
                0,
            )
        self._refresh_mounts()
        self._set_status(
            f"{len(self._storage.get('disks', []))} disk(s), "
            f"{len(self._storage.get('unknown', []))} unknown path(s)"
        )

    def _render_config(self, config):
        self._config = config or {}
        self._enabled.set_active(bool(self._config.get("enabled", True)))
        self._default_min.set_value(
            float(self._config.get("default_min_free_gb", 10.0))
        )
        self._default_margin.set_value(
            float(self._config.get("resume_margin_gb", 5.0))
        )
        self._refresh_mounts()
        self._on_mount_changed(self._mount_combo)

    def _refresh_mounts(self):
        current = self._mount_combo.get_active_text()
        mounts = mount_choices(self._storage, self._config)
        self._mount_combo.remove_all()
        for mount in mounts:
            self._mount_combo.append_text(mount)
        if current in mounts:
            self._mount_combo.set_active(mounts.index(current))
        elif mounts:
            self._mount_combo.set_active(0)

    def _on_refresh(self, widget=None):
        self._refresh()

    def _on_mount_changed(self, widget=None):
        mount = self._mount_combo.get_active_text()
        if not mount:
            return
        options = (self._config.get("per_mount") or {}).get(mount, {})
        self._mount_min.set_value(
            float(
                options.get(
                    "min_free_gb",
                    self._config.get("default_min_free_gb", 10.0),
                )
            )
        )
        self._mount_margin.set_value(
            float(
                options.get(
                    "resume_margin_gb",
                    self._config.get("resume_margin_gb", 5.0),
                )
            )
        )
        action = options.get("action", "pause")
        self._mount_action.set_active(0 if action == "pause" else 1)

    def _on_apply_defaults(self, widget=None):
        payload = defaults_payload(
            self._enabled.get_active(),
            self._default_min.get_value(),
            self._default_margin.get_value(),
        )
        self._save(payload, "Defaults saved")

    def _on_apply_mount(self, widget=None):
        mount = self._mount_combo.get_active_text()
        if not mount:
            return
        action = self._mount_action.get_active_text() or "pause"
        payload = mount_payload(
            mount,
            self._mount_min.get_value(),
            self._mount_margin.get_value(),
            action,
        )
        self._save(payload, f"Override saved for {mount}")

    def _on_clear_mount(self, widget=None):
        mount = self._mount_combo.get_active_text()
        if not mount:
            return
        self._save(clear_mount_payload(mount), f"Override cleared for {mount}")

    def _save(self, payload, message):
        if self._client is None:
            return
        deferred = self._client.diskguard.set_guard_config(payload)
        deferred.addCallback(
            lambda _result: self._on_saved(message)
        ).addErrback(self._on_error)

    def _on_saved(self, message):
        self._set_status(message)
        self._refresh()
        return message

    def _on_error(self, failure):
        self._set_status(str(getattr(failure, "value", failure)))
        return None

    def _set_status(self, message):
        if self._status is not None:
            self._status.set_text(message)
