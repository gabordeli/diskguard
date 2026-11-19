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

    def add_page(self, name, widget):
        self.pages[name] = widget

    def remove_page(self, name):
        self.pages.pop(name)


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
    preferences = FakePreferences()
    component.get = lambda name: preferences
    client.diskguard = FakeDiskGuard(payload)

    from diskguard.gtkui import GtkUI

    ui = GtkUI("DiskGuard")
    ui.enable()
    assert list(preferences.pages) == ["DiskGuard"]
    ui.disable()
    assert preferences.pages == {}
    print("GTK smoke ok")


if __name__ == "__main__":
    main()
