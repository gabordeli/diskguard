# API

DiskGuard is standalone: the GTK UI calls exported core methods through
Deluge RPC (`client.diskguard.*`). The methods return Twisted Deferreds; there
are no HTTP endpoints. The core plugin methods are decorated with `@export`.

## `get_storage()`

Read-only. Returns the per-filesystem view.

```json
{
  "disks": [
    {
      "mount": "/mnt/usbhdd2",
      "fs": "ext4",
      "total": 4000000000000,
      "free": 80000000000,
      "used": 3920000000000,
      "used_pct": 0.98,
      "paths": ["/mnt/usbhdd2/downloads"],
      "torrents": 42,
      "labels": ["film", "sorozat"],
      "deluge": true,
      "low": true
    }
  ],
  "unknown": ["/mnt/missing/path"]
}
```

- `disks[]` is sorted low-first, then by mountpoint.
- `deluge` is true when at least one Deluge path maps to the filesystem.
- `low` is true when the mount has a threshold and is below it; thresholds
  are applied only to Deluge-used mounts, so backup/system mounts stay OK.
- `torrents` is a count; `paths`/`labels` are unique values.
- `unknown[]` lists paths that could not be mapped or statted.
- `used_pct` is a 0..1 fraction, rounded to 4 decimals.

The canonical payload fixture is
[`tests/fixtures/storage_payload.json`](../tests/fixtures/storage_payload.json).
It is asserted against `build_disks()` in the unit tests, so the GTK UI and
tests can use it without drifting from the backend contract.

## `get_guard_config()`

Returns the guard config:

```json
{
  "enabled": true,
  "default_min_free_gb": 10.0,
  "resume_margin_gb": 5.0,
  "per_mount": {
    "/mnt/usbhdd2": {"min_free_gb": 20, "action": "pause"}
  }
}
```

## `set_guard_config(payload)`

Accepts any subset of:

```json
{
  "enabled": true,
  "default_min_free_gb": 10,
  "resume_margin_gb": 5,
  "per_mount": {
    "/mnt/usbhdd2": {
      "min_free_gb": 20,
      "resume_margin_gb": 5,
      "action": "pause"
    }
  }
}
```

Validation:

- `enabled` must be a boolean;
- numbers must be finite and `>= 0`;
- `action` is `pause` or `notify`;
- unknown top-level or per-mount keys are rejected;
- the response is the same shape as `get_guard_config()`.

Invalid input raises `ValueError`, which Deluge returns as an RPC error. The
GTK UI maps it to a dialog/message.

## `get_guard_state()`

Returns the in-memory transition state:

```json
{
  "low_mounts": ["/mnt/usbhdd2"],
  "paused_torrents": ["<torrent-id>"]
}
```

`paused_torrents` contains only torrents paused by the guard; a manual user
pause is never undone automatically.

## Events

`LowTargetEvent(torrent_id, path, mount)` is emitted when a torrent path
first appears on a low disk after the initial guard snapshot. The GTK UI
registers it with `client.register_event_handler('LowTargetEvent', ...)` and
shows one non-blocking warning per `(torrent_id, path, mount)`.

## Auth

The GTK UI uses the existing Deluge RPC session. DiskGuard does not add a
token, HTTP endpoint or second auth layer.

## Errors

- `ValueError`: invalid guard config (RPC error).
- Missing torrents/paths: reported in `unknown[]` or skipped, not raised.
