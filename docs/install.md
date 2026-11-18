# Install

DiskGuard is a Deluge 2.x egg plugin. The mobile UI/API is provided by
`webui2`; install that separately if you want the Storage sheet.

## Build

From the repository checkout:

```bash
python3 -m venv .venv
.venv/bin/pip install pytest ruff build twisted wheel setuptools
.venv/bin/python setup.py bdist_egg
```

The egg is written to `dist/DiskGuard-<version>-py3.x.egg`.

## Install on the Deluge host

Use the Deluge config/data directory of the `deluged` user; the exact path is
environment-specific (`$DELUGE_CONFIG_DIR` in deployments):

```bash
install -d -m 755 "$DELUGE_CONFIG_DIR/plugins"
install -m 644 dist/DiskGuard-*.egg "$DELUGE_CONFIG_DIR/plugins/"
```

Add `DiskGuard` to `enabled_plugins` in `core.conf`, then restart `deluged`.
Restarting the daemon is a separate operational step; confirm it on the host.

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

With `webui2` installed:

```bash
curl -s -H "Authorization: Bearer <token>" \
  https://<deluge-host>:8113/api/storage
```

The response should list every real mount and mark the Deluge-used ones with
`"deluge": true`.

## Rollback

1. Remove the egg from `$DELUGE_CONFIG_DIR/plugins/`.
2. Remove `DiskGuard` from `enabled_plugins` in `core.conf`.
3. Restart `deluged`.

The guard state is in memory only in M1/M2; no data is moved or deleted.
