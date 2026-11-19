"""GTK UI plugin: standalone storage page and guard settings."""

from __future__ import annotations

import logging
import math

from deluge.plugins.pluginbase import Gtk3PluginBase

log = logging.getLogger(__name__)

PAGE_NAME = "DiskGuard"
MENU_LABEL = "DiskGuard Storage"
CSS = """
.diskguard-title { font-size: 20px; font-weight: 700; }
.diskguard-subtitle { font-size: 11px; opacity: 0.65; }
.diskguard-section { font-size: 13px; font-weight: 700; opacity: 0.9; }
.diskguard-card {
    background-color: alpha(@theme_fg_color, 0.055);
    border: 1px solid alpha(@theme_fg_color, 0.10);
    border-radius: 10px;
    padding: 12px;
}
.diskguard-card-low { border-left: 4px solid #e53935; }
.diskguard-stat {
    background-color: alpha(@theme_fg_color, 0.045);
    border: 1px solid alpha(@theme_fg_color, 0.08);
    border-radius: 8px;
    padding: 10px;
}
.diskguard-stat-value { font-size: 18px; font-weight: 700; }
.diskguard-stat-label { font-size: 10px; opacity: 0.65; }
.diskguard-low-value { color: #e53935; }
.diskguard-dirty { color: #e5a50a; font-weight: 600; }
.diskguard-column { font-size: 10px; font-weight: 700; opacity: 0.60; }
.diskguard-row-mount { font-weight: 600; }
.diskguard-mount { font-size: 14px; font-weight: 700; }
.diskguard-meta { font-size: 11px; opacity: 0.7; }
.diskguard-badge {
    border-radius: 9px;
    font-size: 10px;
    font-weight: 700;
    padding: 2px 9px;
}
.diskguard-badge-low { background-color: #c62828; color: #ffffff; }
.diskguard-badge-ok { background-color: #2e7d32; color: #ffffff; }
"""


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


def disk_used_fraction(disk: dict) -> float:
    total = float(disk.get("total") or 0)
    if total <= 0:
        return 0.0
    used = disk.get("used")
    if used is None:
        used = total - float(disk.get("free") or 0)
    return min(1.0, max(0.0, float(used) / total))


def disk_summary(payload: dict) -> dict:
    disks = payload.get("disks") or []
    deluge_disks = [disk for disk in disks if disk.get("deluge")]
    total = sum(float(disk.get("total") or 0) for disk in deluge_disks)
    free = sum(float(disk.get("free") or 0) for disk in deluge_disks)
    return {
        "disks": len(disks),
        "low": sum(1 for disk in disks if disk.get("low")),
        "free": free,
        "total": total,
        "used": max(0.0, total - free),
        "unknown": len(payload.get("unknown") or []),
    }


def disk_meta(disk: dict) -> str:
    return (
        f"{disk.get('fs') or 'unknown fs'} · "
        f"{format_bytes(disk.get('free'))} free of "
        f"{format_bytes(disk.get('total'))} · "
        f"{format_pct(disk_used_fraction(disk))} used"
    )


def disk_labels(disk: dict) -> str:
    labels = [str(label) for label in disk.get("labels") or []]
    return ", ".join(labels) if labels else "No labels"


def mount_choices(payload: dict, config: dict) -> list[str]:
    names = {
        str(disk["mount"])
        for disk in payload.get("disks", [])
        if disk.get("mount")
    }
    names.update((config.get("per_mount") or {}).keys())
    return sorted(names)


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
        self._mount_grid = None
        self._mount_rows = {}
        self._mount_names = []
        self._settings_status = None
        self._save_button = None
        self._dirty = False
        self._loading = False
        self._warned_targets = set()
        self._menu_item = None
        self._menu_parent = None
        self._css_provider = None
        self._screen = None
        self._disk_count_label = None
        self._low_count_label = None
        self._free_total_label = None
        self._unknown_label = None
        self._capacity_bar = None
        self._capacity_label = None
        self._status_item = None
        self._status_timer = None
        self._glib = None

    def enable(self):
        if self._page is not None:
            return

        import gi

        gi.require_version("Gtk", "3.0")
        from deluge import component
        from deluge.ui.client import client

        self._client = client
        self._warned_targets.clear()
        client.register_event_handler(
            "LowTargetEvent", self._on_low_target_event
        )
        page = self._build_page()
        component.get("Preferences").add_page(PAGE_NAME, page)
        self._add_menu_entry()
        self._page = page
        self._add_status_item()
        self._refresh()
        log.info("DiskGuard GTK page enabled")

    def disable(self):
        if self._page is None:
            return
        from deluge import component

        self._remove_status_item()
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
            self._remove_css()
            self._page = None
            self._client = None
        log.info("DiskGuard GTK page disabled")

    def update(self):
        # The page refreshes itself on enable and through the Refresh button.
        pass

    def _add_menu_entry(self):
        from deluge import component

        if self._gtk is None:
            return
        gtk = self._gtk
        try:
            builder = component.get("MainWindow").get_builder()
            submenu = builder.get_object("submenu_view")
        except Exception:
            log.exception("DiskGuard: main window is not available")
            return
        if submenu is None:
            log.warning("DiskGuard: View menu not found")
            return

        item = gtk.MenuItem.new_with_label(MENU_LABEL)
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

    def _add_status_item(self):
        from deluge import component
        from gi.repository import GLib

        self._glib = GLib
        try:
            statusbar = component.get("StatusBar")
            self._status_item = statusbar.add_item(
                icon="drive-harddisk-symbolic",
                text="",
                tooltip="DiskGuard storage",
                callback=self._on_status_item_clicked,
            )
        except Exception:
            log.exception("DiskGuard: status bar item unavailable")
            self._status_item = None
            return
        self._status_item.set_markup("<small>DiskGuard</small>")
        self._status_timer = GLib.timeout_add_seconds(
            30, self._refresh_status_item
        )
        log.info("DiskGuard status bar item added")

    def _remove_status_item(self):
        item = self._status_item
        self._status_item = None
        if item is not None:
            try:
                from deluge import component

                component.get("StatusBar").remove_item(item)
            except Exception:
                log.exception("DiskGuard: status bar item removal failed")
        if self._status_timer is not None and self._glib is not None:
            self._glib.source_remove(self._status_timer)
        self._status_timer = None
        self._glib = None

    def _on_status_item_clicked(self, widget, event):
        from deluge import component

        component.get("Preferences").show(PAGE_NAME)

    def _refresh_status_item(self):
        if self._client is None or self._status_item is None:
            return False
        try:
            deferred = self._client.diskguard.get_storage()
        except Exception:
            return True
        deferred.addCallback(self._update_status_item)
        deferred.addErrback(self._ignore_status_error)
        return True

    def _ignore_status_error(self, failure):
        log.debug("DiskGuard: status bar refresh failed: %s", failure)
        # A non-Failure return marks the errback as handled.
        return failure.getErrorMessage()

    def _update_status_item(self, payload):
        if self._status_item is None:
            return payload
        summary = disk_summary(payload or {})
        free = format_bytes(summary["free"])
        if summary["low"]:
            icon = "dialog-warning-symbolic"
            markup = (
                f'<span foreground="#e53935"><b>{summary["low"]} low</b>'
                f"</span> · <small>{free} free</small>"
            )
        else:
            icon = "drive-harddisk-symbolic"
            markup = (
                f'<small>{summary["disks"]} disks · {free} free</small>'
            )
        self._status_item.set_image_from_icon(icon)
        self._status_item.set_markup(markup)
        self._status_item.set_tooltip(
            f"DiskGuard: {summary['disks']} filesystems, "
            f"{summary['low']} low, {free} free; click for details."
        )
        return summary

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

    def _install_css(self):
        try:
            from gi.repository import Gdk

            gtk = self._gtk
            provider = gtk.CssProvider()
            provider.load_from_data(CSS.encode("utf-8"))
            screen = Gdk.Screen.get_default()
            if screen is None:
                return
            gtk.StyleContext.add_provider_for_screen(
                screen, provider, gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
            self._css_provider = provider
            self._screen = screen
        except Exception:
            log.exception("DiskGuard: unable to install CSS")

    def _remove_css(self):
        if (
            self._css_provider is None
            or self._screen is None
            or self._gtk is None
        ):
            return
        try:
            self._gtk.StyleContext.remove_provider_for_screen(
                self._screen, self._css_provider
            )
        except Exception:
            log.exception("DiskGuard: unable to remove CSS")
        finally:
            self._css_provider = None
            self._screen = None

    def _style(self, widget, name):
        widget.get_style_context().add_class(name)

    def _label(self, text, css_class=None):
        label = self._gtk.Label(label=text)
        label.set_xalign(0)
        if css_class:
            self._style(label, css_class)
        return label

    def _spin_button(self):
        spin = self._gtk.SpinButton()
        spin.set_range(0, 1000000)
        spin.set_digits(1)
        spin.set_increments(1, 10)
        return spin

    def _build_page(self):
        from gi.repository import Gtk

        self._gtk = Gtk
        gtk = self._gtk
        self._install_css()

        page = gtk.Box(orientation=gtk.Orientation.VERTICAL, spacing=14)
        page.set_margin_start(18)
        page.set_margin_end(18)
        page.set_margin_top(18)
        page.set_margin_bottom(18)

        self._build_header(page)
        self._build_summary(page)
        self._build_filesystems(page)
        self._build_settings(page)

        self._status = self._label("", "diskguard-meta")
        self._status.set_line_wrap(True)
        page.pack_start(self._status, False, False, 0)

        page.show_all()
        return page

    def _build_header(self, page):
        gtk = self._gtk
        header = gtk.Box(orientation=gtk.Orientation.HORIZONTAL, spacing=12)

        icon = gtk.Image.new_from_icon_name(
            "drive-harddisk", gtk.IconSize.DIALOG
        )
        icon.set_valign(gtk.Align.START)
        header.pack_start(icon, False, False, 0)

        titles = gtk.Box(orientation=gtk.Orientation.VERTICAL, spacing=2)
        titles.pack_start(
            self._label("DiskGuard", "diskguard-title"),
            False,
            False,
            0,
        )
        titles.pack_start(
            self._label(
                "Per-filesystem free space and guard",
                "diskguard-subtitle",
            ),
            False,
            False,
            0,
        )
        header.pack_start(titles, True, True, 0)

        refresh = gtk.Button(label="Refresh disks")
        refresh.connect("clicked", self._on_refresh)
        refresh.set_tooltip_text(
            "Reload the storage view; guard settings are saved separately."
        )
        refresh.set_valign(gtk.Align.CENTER)
        header.pack_end(refresh, False, False, 0)

        page.pack_start(header, False, False, 0)

    def _stat_box(self, parent, caption):
        gtk = self._gtk
        box = gtk.Box(orientation=gtk.Orientation.VERTICAL, spacing=2)
        self._style(box, "diskguard-stat")
        value = self._label("—", "diskguard-stat-value")
        box.pack_start(value, False, False, 0)
        box.pack_start(
            self._label(caption, "diskguard-stat-label"),
            False,
            False,
            0,
        )
        parent.pack_start(box, True, True, 0)
        return value

    def _build_summary(self, page):
        gtk = self._gtk
        summary = gtk.Box(orientation=gtk.Orientation.HORIZONTAL, spacing=10)
        self._disk_count_label = self._stat_box(summary, "Filesystems")
        self._low_count_label = self._stat_box(summary, "Low disks")
        self._free_total_label = self._stat_box(summary, "Free (Deluge)")
        self._capacity_box(summary)
        page.pack_start(summary, False, False, 0)

    def _build_filesystems(self, page):
        gtk = self._gtk
        heading = gtk.Box(orientation=gtk.Orientation.HORIZONTAL, spacing=8)
        heading.pack_start(
            self._label("Filesystems", "diskguard-section"),
            False,
            False,
            0,
        )
        hint = self._label(
            "Only Deluge-used disks are guarded", "diskguard-meta"
        )
        hint.set_xalign(1)
        heading.pack_end(hint, False, False, 0)
        page.pack_start(heading, False, False, 0)

        self._rows_box = gtk.Box(
            orientation=gtk.Orientation.VERTICAL, spacing=8
        )
        page.pack_start(self._rows_box, False, False, 0)

    def _build_settings(self, page):
        gtk = self._gtk
        settings = gtk.Box(orientation=gtk.Orientation.VERTICAL, spacing=10)
        self._style(settings, "diskguard-card")

        header = gtk.Box(orientation=gtk.Orientation.HORIZONTAL, spacing=8)
        header.pack_start(
            self._label("Guard settings", "diskguard-section"),
            False,
            False,
            0,
        )
        self._save_button = gtk.Button(label="Save guard settings")
        self._save_button.get_style_context().add_class("suggested-action")
        self._save_button.connect("clicked", self._on_save)
        header.pack_end(self._save_button, False, False, 0)
        reload_button = gtk.Button(label="Reload")
        reload_button.connect("clicked", self._on_reload)
        header.pack_end(reload_button, False, False, 0)
        settings.pack_start(header, False, False, 0)

        hint = self._label(
            "Changes stay on this page until you click Save guard settings. "
            "Reload discards them and loads the daemon config.",
            "diskguard-meta",
        )
        hint.set_line_wrap(True)
        settings.pack_start(hint, False, False, 0)

        defaults = gtk.Grid(column_spacing=10, row_spacing=8)
        self._enabled = gtk.CheckButton(label="Guard enabled")
        self._enabled.connect("toggled", self._mark_dirty)
        defaults.attach(self._enabled, 0, 0, 6, 1)
        self._default_min = self._spin_button()
        self._default_min.connect("value-changed", self._on_default_changed)
        self._default_margin = self._spin_button()
        self._default_margin.connect(
            "value-changed", self._on_default_changed
        )
        defaults.attach(self._label("Minimum free"), 0, 1, 1, 1)
        defaults.attach(self._default_min, 1, 1, 1, 1)
        defaults.attach(self._label("GB"), 2, 1, 1, 1)
        defaults.attach(self._label("Resume margin"), 3, 1, 1, 1)
        defaults.attach(self._default_margin, 4, 1, 1, 1)
        defaults.attach(self._label("GB"), 5, 1, 1, 1)
        settings.pack_start(defaults, False, False, 0)

        settings.pack_start(gtk.Separator(), False, False, 0)
        settings.pack_start(
            self._label("Per-mount overrides", "diskguard-section"),
            False,
            False,
            0,
        )
        override_hint = self._label(
            "Check Override on a disk row to edit that disk. Unchecked = "
            "uses the defaults. 'not guarded' = no current Deluge path "
            "uses that disk. One save writes every row.",
            "diskguard-meta",
        )
        override_hint.set_line_wrap(True)
        settings.pack_start(override_hint, False, False, 0)

        self._mount_grid = gtk.Grid(column_spacing=10, row_spacing=6)
        settings.pack_start(self._mount_grid, False, False, 0)

        self._settings_status = self._label(
            "Loading guard settings…", "diskguard-meta"
        )
        self._settings_status.set_line_wrap(True)
        settings.pack_start(self._settings_status, False, False, 0)

        page.pack_start(settings, False, False, 0)

    def _rounded_rect(self, cr, x, y, width, height, radius):
        if width <= 0 or height <= 0:
            return
        radius = min(radius, width / 2, height / 2)
        cr.new_sub_path()
        cr.arc(x + width - radius, y + radius, radius, -math.pi / 2, 0)
        cr.arc(
            x + width - radius,
            y + height - radius,
            radius,
            0,
            math.pi / 2,
        )
        cr.arc(
            x + radius,
            y + height - radius,
            radius,
            math.pi / 2,
            math.pi,
        )
        cr.arc(x + radius, y + radius, radius, math.pi, 3 * math.pi / 2)
        cr.close_path()

    def _draw_usage_bar(self, widget, cr):
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        radius = height / 2
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.20)
        self._rounded_rect(cr, 0, 0, width, height, radius)
        cr.fill()

        fraction = float(getattr(widget, "fraction", 0.0))
        if fraction <= 0:
            return
        fill_width = min(width, max(height, width * fraction))
        if getattr(widget, "low", False):
            cr.set_source_rgba(0.90, 0.20, 0.20, 0.95)
        else:
            cr.set_source_rgba(0.18, 0.65, 0.30, 0.95)
        self._rounded_rect(cr, 0, 0, fill_width, height, radius)
        cr.fill()

    def _usage_bar(self, fraction, low):
        bar = self._gtk.DrawingArea()
        bar.set_size_request(-1, 12)
        bar.set_hexpand(True)
        bar.fraction = fraction
        bar.low = low
        bar.connect("draw", self._draw_usage_bar)
        bar.set_tooltip_text(f"{format_pct(fraction)} used")
        return bar

    def _draw_capacity(self, widget, cr):
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        radius = max(1.0, min(width, height) / 2 - 6)
        center_x = width / 2
        center_y = height / 2
        cr.set_line_width(8)
        cr.set_line_cap(1)
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.20)
        cr.arc(center_x, center_y, radius, 0, 2 * math.pi)
        cr.stroke()

        fraction = float(getattr(widget, "fraction", 0.0))
        if fraction <= 0:
            return
        if getattr(widget, "low", False):
            cr.set_source_rgba(0.90, 0.20, 0.20, 0.95)
        else:
            cr.set_source_rgba(0.21, 0.52, 0.89, 0.95)
        cr.arc(
            center_x,
            center_y,
            radius,
            -math.pi / 2,
            -math.pi / 2 + 2 * math.pi * fraction,
        )
        cr.stroke()

    def _capacity_box(self, parent):
        gtk = self._gtk
        box = gtk.Box(orientation=gtk.Orientation.VERTICAL, spacing=2)
        self._style(box, "diskguard-stat")
        ring = gtk.DrawingArea()
        ring.set_size_request(64, 64)
        ring.set_halign(gtk.Align.CENTER)
        ring.set_valign(gtk.Align.CENTER)
        ring.fraction = 0.0
        ring.low = False
        ring.connect("draw", self._draw_capacity)
        box.pack_start(ring, False, False, 0)
        self._capacity_bar = ring
        self._capacity_label = self._label(
            "— used", "diskguard-stat-value"
        )
        self._capacity_label.set_xalign(0.5)
        box.pack_start(self._capacity_label, False, False, 0)
        caption = self._label("Deluge capacity", "diskguard-stat-label")
        caption.set_xalign(0.5)
        box.pack_start(caption, False, False, 0)
        parent.pack_start(box, True, True, 0)

    def _build_disk_card(self, disk):
        gtk = self._gtk
        low = bool(disk.get("low"))
        card = gtk.Box(orientation=gtk.Orientation.VERTICAL, spacing=8)
        self._style(card, "diskguard-card")
        if low:
            self._style(card, "diskguard-card-low")

        top = gtk.Box(orientation=gtk.Orientation.HORIZONTAL, spacing=10)
        icon = gtk.Image.new_from_icon_name(
            "drive-harddisk-symbolic", gtk.IconSize.BUTTON
        )
        icon.set_valign(gtk.Align.START)
        top.pack_start(icon, False, False, 0)

        info = gtk.Box(orientation=gtk.Orientation.VERTICAL, spacing=2)
        mount = self._label(
            str(disk.get("mount", "")), "diskguard-mount"
        )
        mount.set_line_wrap(True)
        info.pack_start(mount, False, False, 0)
        meta = self._label(disk_meta(disk), "diskguard-meta")
        meta.set_line_wrap(True)
        info.pack_start(meta, False, False, 0)
        labels = self._label(
            f"Labels: {disk_labels(disk)}", "diskguard-meta"
        )
        labels.set_line_wrap(True)
        info.pack_start(labels, False, False, 0)
        top.pack_start(info, True, True, 0)

        badge = self._label("LOW" if low else "OK")
        self._style(badge, "diskguard-badge")
        self._style(
            badge, "diskguard-badge-low" if low else "diskguard-badge-ok"
        )
        badge.set_valign(gtk.Align.START)
        top.pack_end(badge, False, False, 0)
        card.pack_start(top, False, False, 0)

        fraction = disk_used_fraction(disk)
        card.pack_start(self._usage_bar(fraction, low), False, False, 0)

        footer = gtk.Box(
            orientation=gtk.Orientation.HORIZONTAL, spacing=12
        )
        footer.pack_start(
            self._label(
                f"Torrents: {int(disk.get('torrents') or 0)}",
                "diskguard-meta",
            ),
            False,
            False,
            0,
        )
        footer.pack_end(
            self._label(
                "Deluge" if disk.get("deluge") else "Not used by Deluge",
                "diskguard-meta",
            ),
            False,
            False,
            0,
        )
        card.pack_start(footer, False, False, 0)

        paths = disk.get("paths") or []
        if paths:
            card.set_tooltip_text("\n".join(str(path) for path in paths))
        return card

    def _build_unknown_card(self, unknown):
        gtk = self._gtk
        card = gtk.Box(orientation=gtk.Orientation.HORIZONTAL, spacing=10)
        self._style(card, "diskguard-card")
        icon = gtk.Image.new_from_icon_name(
            "dialog-warning-symbolic", gtk.IconSize.BUTTON
        )
        icon.set_valign(gtk.Align.START)
        card.pack_start(icon, False, False, 0)
        label = self._label(
            "Unknown paths: " + ", ".join(str(path) for path in unknown),
            "diskguard-meta",
        )
        label.set_line_wrap(True)
        card.pack_start(label, True, True, 0)
        self._unknown_label = label
        return card

    def _refresh(self, include_config=True):
        if self._client is None:
            return
        try:
            storage = self._client.diskguard.get_storage()
            config = (
                self._client.diskguard.get_guard_config()
                if include_config
                else None
            )
        except Exception as ex:
            self._set_status(str(ex))
            return
        storage.addCallback(self._render_storage).addErrback(self._on_error)
        if config is not None:
            config.addCallback(self._render_config).addErrback(
                self._on_error
            )

    def _render_storage(self, payload):
        self._storage = payload or {"disks": [], "unknown": []}
        for child in self._rows_box.get_children():
            self._rows_box.remove(child)
        self._unknown_label = None
        for disk in self._storage.get("disks") or []:
            self._rows_box.pack_start(
                self._build_disk_card(disk), False, False, 0
            )
        unknown = self._storage.get("unknown") or []
        if unknown:
            self._rows_box.pack_start(
                self._build_unknown_card(unknown),
                False,
                False,
                0,
            )
        self._rows_box.show_all()

        summary = disk_summary(self._storage)
        self._disk_count_label.set_text(str(summary["disks"]))
        self._low_count_label.set_text(str(summary["low"]))
        low_context = self._low_count_label.get_style_context()
        if summary["low"]:
            low_context.add_class("diskguard-low-value")
        else:
            low_context.remove_class("diskguard-low-value")
        self._free_total_label.set_text(format_bytes(summary["free"]))
        if summary["total"] > 0:
            capacity = summary["used"] / summary["total"]
            self._capacity_label.set_text(f"{format_pct(capacity)} used")
        else:
            capacity = 0.0
            self._capacity_label.set_text("— used")
        self._capacity_bar.fraction = capacity
        self._capacity_bar.low = summary["low"] > 0
        self._capacity_bar.queue_draw()
        self._render_mount_rows()
        filesystems = "filesystem" if summary["disks"] == 1 else "filesystems"
        unknown = "unknown path" if summary["unknown"] == 1 else "unknown paths"
        self._set_status(
            f"{summary['disks']} {filesystems} · "
            f"{summary['low']} low · "
            f"{summary['unknown']} {unknown}"
        )
        self._update_status_item(self._storage)

    def _render_config(self, config):
        self._config = config or {}
        self._loading = True
        try:
            self._enabled.set_active(bool(self._config.get("enabled", True)))
            self._default_min.set_value(
                float(self._config.get("default_min_free_gb", 10.0))
            )
            self._default_margin.set_value(
                float(self._config.get("resume_margin_gb", 5.0))
            )
        finally:
            self._loading = False
        if not self._dirty:
            self._render_mount_rows(force=True)
            overrides = len(self._config.get("per_mount") or {})
            override_word = "override" if overrides == 1 else "overrides"
            mounts = len(self._mount_names)
            mount_word = "mount" if mounts == 1 else "mounts"
            self._set_settings_status(
                f"Loaded from daemon: {mounts} {mount_word}, "
                f"{overrides} {override_word}."
            )

    def _set_row_sensitive(self, row, active):
        row["min"].set_sensitive(active)
        row["margin"].set_sensitive(active)
        row["action"].set_sensitive(active)

    def _add_mount_headers(self):
        headers = (
            "Override",
            "Mount",
            "Min free (GB)",
            "Margin (GB)",
            "Action",
        )
        for column, text in enumerate(headers):
            self._mount_grid.attach(
                self._label(text, "diskguard-column"),
                column,
                0,
                1,
                1,
            )

    def _mount_row(
        self, mount, options, default_min, default_margin, guarded
    ):
        gtk = self._gtk
        override = gtk.CheckButton()
        override.set_tooltip_text(f"Use a custom threshold for {mount}")
        override.set_active(options is not None)
        override.connect("toggled", self._on_override_toggled, mount)
        mount_text = mount
        if mount not in guarded:
            mount_text += "  ·  not guarded"
        mount_label = self._label(mount_text, "diskguard-row-mount")
        mount_label.set_hexpand(True)
        min_spin = self._spin_button()
        margin_spin = self._spin_button()
        action = gtk.ComboBoxText()
        action.append_text("pause")
        action.append_text("notify")
        if options:
            min_spin.set_value(
                float(options.get("min_free_gb", default_min))
            )
            margin_spin.set_value(
                float(options.get("resume_margin_gb", default_margin))
            )
            action.set_active(
                0 if options.get("action", "pause") == "pause" else 1
            )
        else:
            min_spin.set_value(default_min)
            margin_spin.set_value(default_margin)
            action.set_active(0)
        min_spin.connect("value-changed", self._mark_dirty)
        margin_spin.connect("value-changed", self._mark_dirty)
        action.connect("changed", self._mark_dirty)
        row = {
            "override": override,
            "min": min_spin,
            "margin": margin_spin,
            "action": action,
        }
        self._set_row_sensitive(row, options is not None)
        return (override, mount_label, min_spin, margin_spin, action), row

    def _render_mount_rows(self, force=False):
        if self._mount_grid is None:
            return
        if self._dirty and not force:
            return
        mounts = mount_choices(self._storage, self._config)
        if not force and self._mount_rows and mounts == self._mount_names:
            return
        self._loading = True
        try:
            for child in self._mount_grid.get_children():
                self._mount_grid.remove(child)
            self._add_mount_headers()
            self._mount_rows = {}
            default_min = float(
                self._config.get("default_min_free_gb", 10.0)
            )
            default_margin = float(
                self._config.get("resume_margin_gb", 5.0)
            )
            per_mount = self._config.get("per_mount") or {}
            guarded = {
                str(disk.get("mount"))
                for disk in self._storage.get("disks") or []
                if disk.get("deluge")
            }
            for row_index, mount in enumerate(mounts, start=1):
                options = per_mount.get(mount)
                widgets, row = self._mount_row(
                    mount, options, default_min, default_margin, guarded
                )
                for column, widget in enumerate(widgets):
                    self._mount_grid.attach(
                        widget, column, row_index, 1, 1
                    )
                self._mount_rows[mount] = row
            self._mount_names = mounts
            self._mount_grid.show_all()
        finally:
            self._loading = False

    def _on_override_toggled(self, widget, mount):
        row = self._mount_rows.get(mount)
        if row is not None:
            active = widget.get_active()
            self._set_row_sensitive(row, active)
            if not active:
                self._loading = True
                try:
                    row["min"].set_value(self._default_min.get_value())
                    row["margin"].set_value(
                        self._default_margin.get_value()
                    )
                    row["action"].set_active(0)
                finally:
                    self._loading = False
        self._mark_dirty()

    def _on_default_changed(self, *args):
        if self._loading:
            return
        self._loading = True
        try:
            for row in self._mount_rows.values():
                if not row["override"].get_active():
                    row["min"].set_value(self._default_min.get_value())
                    row["margin"].set_value(
                        self._default_margin.get_value()
                    )
        finally:
            self._loading = False
        self._mark_dirty()

    def _mark_dirty(self, *args):
        if self._loading:
            return
        self._dirty = True
        self._set_settings_status(
            "Unsaved changes — click Save guard settings."
        )

    def _set_settings_status(self, message):
        if self._settings_status is None:
            return
        self._settings_status.set_text(message)
        context = self._settings_status.get_style_context()
        if self._dirty:
            context.add_class("diskguard-dirty")
        else:
            context.remove_class("diskguard-dirty")

    def _on_save(self, widget=None):
        per_mount = {}
        for mount, row in self._mount_rows.items():
            if row["override"].get_active():
                per_mount[mount] = {
                    "min_free_gb": float(row["min"].get_value()),
                    "resume_margin_gb": float(
                        row["margin"].get_value()
                    ),
                    "action": row["action"].get_active_text()
                    or "pause",
                }
        payload = {
            "enabled": self._enabled.get_active(),
            "default_min_free_gb": float(
                self._default_min.get_value()
            ),
            "resume_margin_gb": float(
                self._default_margin.get_value()
            ),
            "per_mount": per_mount,
        }
        self._save(payload, "Guard settings saved")

    def _on_reload(self, widget=None):
        self._dirty = False
        self._set_settings_status("Reloading from daemon…")
        self._refresh()

    def _on_refresh(self, widget=None):
        self._refresh(include_config=not self._dirty)

    def _save(self, payload, message):
        if self._client is None:
            return
        deferred = self._client.diskguard.set_guard_config(payload)
        deferred.addCallback(
            lambda _result: self._on_saved(message)
        ).addErrback(self._on_error)

    def _on_saved(self, message):
        self._dirty = False
        self._set_status(message)
        self._set_settings_status(message + "; reloading from daemon…")
        self._refresh()
        return message

    def _on_error(self, failure):
        message = str(getattr(failure, "value", failure))
        self._set_status(message)
        self._set_settings_status(message)
        return None

    def _set_status(self, message):
        if self._status is not None:
            self._status.set_text(message)
