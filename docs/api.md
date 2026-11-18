# API

DiskGuard is headless. The endpoints below are mounted by the `webui2` plugin;
DiskGuard itself does not start an HTTP server.

## `GET /api/storage`

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
- `low` is true when the mount has a threshold and is below it.
- `torrents` is a count; `paths`/`labels` are unique values.
- `unknown[]` lists paths that could not be mapped or statted.
- `used_pct` is a 0..1 fraction, rounded to 4 decimals.

The canonical payload fixture is
[`tests/fixtures/storage_payload.json`](../tests/fixtures/storage_payload.json).
It is asserted against `build_disks()` in the unit tests, so `webui2`
endpoint tests can load it as a mock `GET /api/storage` response without
drifting from the backend contract.

## `GET /api/storage/guard`

Returns the guard config and the current transition state.

```json
{
  "enabled": true,
  "default_min_free_gb": 10.0,
  "resume_margin_gb": 5.0,
  "per_mount": {
    "/mnt/usbhdd2": {"min_free_gb": 20, "action": "pause"}
  },
  "low_mounts": ["/mnt/usbhdd2"],
  "paused_torrents": ["<torrent-id>"]
}
```

## `PATCH /api/storage/guard`

Normal auth level. Accepts any subset of:

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

- numbers must be `>= 0`;
- `action` is `pause` or `notify`;
- unknown keys are rejected with `400`;
- the response is the same shape as `GET /api/storage/guard`.

## Authz

- `GET /api/storage` and `GET /api/storage/guard`: read-only level.
- `PATCH /api/storage/guard`: normal level; read-only gets `403`.
- `webui2` owns the token/session handling; DiskGuard does not log it.

## Errors

- `400`: invalid config or payload.
- `403`: read-only token on a mutation.
- `200`: successful read/update.

The payload is stable; new fields are additive.
