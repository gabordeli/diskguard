# Install

DiskGuard is a standalone Deluge 2.x egg plugin: a core plugin for `deluged`
and a GTK UI plugin for `deluge-gtk`. It does not require `webui2` and does
not start an HTTP server.

## Build

From the repository checkout:

```bash
python3 -m venv .venv
.venv/bin/pip install pytest ruff build twisted wheel setuptools
.venv/bin/python setup.py bdist_egg
```

The egg is written to `dist/DiskGuard-<version>-py3.x.egg`.

## Install

Install the egg in the Deluge config/data directory of the `deluged` user. The
exact path is environment-specific (`$DELUGE_CONFIG_DIR` in deployments):

```bash
install -d -m 755 "$DELUGE_CONFIG_DIR/plugins"
install -m 644 dist/DiskGuard-*.egg "$DELUGE_CONFIG_DIR/plugins/"
```

For the GTK UI, install the same egg on the machine running `deluge-gtk` (it can
be the same host or a thin client), in that user's Deluge config directory.

Enable the plugin in Deluge:

- core: `DiskGuard` in `enabled_plugins` of `core.conf` (or the GTK Plugins
  preferences when connected to the daemon);
- GTK: install the egg before starting `deluge-gtk`, then enable `DiskGuard`
  on the GTK Plugins page and restart the client.

The GTK plugin is loaded when `deluge-gtk` starts. If the Plugins page lists
`DiskGuard` (that list comes from the daemon) but no DiskGuard page appears,
the running client started before the egg was installed: restart it.

Restarting `deluged` and `deluge-gtk` is a separate operational step; confirm
it on the host.

## Config

`diskguard.conf` is created with defaults on enable:

```json
{
  "enabled": true,
  "default_min_free_gb": 10.0,
  "resume_margin_gb": 5.0,
  "per_mount": {
    "/mnt/usbhdd2": {
      "min_free_gb": 20,
      "resume_margin_gb": 5,
      "action": "pause"
    }
  }
}
```

- `default_min_free_gb`: threshold for mounts without an override.
- `resume_margin_gb`: hysteresis; resume only above
  `min_free_gb + resume_margin_gb`.
- `per_mount`: optional overrides keyed by mountpoint. Each entry supports
  `min_free_gb`, `resume_margin_gb` and `action`.
- `action`: `pause` (pause affected downloads) or `notify` (event only);
  the per-mount value wins over the default.

Validation rules (enforced by `set_guard_config`):

- `enabled` must be a boolean;
- numbers must be finite and `>= 0`;
- unknown top-level or per-mount keys are rejected.

## Verify

Open `deluge-gtk` → `View` → `DiskGuard Storage` (or `Preferences` →
`DiskGuard`). The view should list every real mount and mark the Deluge-used
ones with `deluge: true`. The guard state and thresholds can be edited there.

## Release smoke checklist

1. Build the egg and install it on the daemon and the GTK client.
2. Enable `DiskGuard`, restart `deluged`/`deluge-gtk`, open `View` →
   `DiskGuard Storage`; the disk list and defaults must appear.
3. Set a high threshold on a test mount, add a torrent there and confirm the
   guard pauses only the affected active torrents; restore the threshold.
4. Resume the torrent manually while the disk is still low; the guard must not
   undo a manual pause on the next `ok` transition.
5. Set the threshold low again and confirm a `LowTargetEvent` warning appears
   once for a new/moved torrent path.

The guard state is in memory only in 0.1.0; no data is moved or deleted.

## GTK smoke without a daemon

With Deluge/GTK installed, build the real page under Xvfb:

```bash
PYTHONPATH=. xvfb-run -a python3 scripts/gtk_smoke.py
```

It uses the storage fixture and a fake RPC client, and checks that the
Preferences page and the main-window menu entry can be created and removed. It
does not connect to `deluged`.

## Rollback

1. Disable `DiskGuard` in the GTK Plugins page and in `core.conf`.
2. Remove the egg from `$DELUGE_CONFIG_DIR/plugins/` on the daemon and the GTK
   client.
3. Restart `deluged` and `deluge-gtk`.
