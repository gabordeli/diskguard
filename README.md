# DiskGuard

Multi-filesystem free-space awareness and guard for **Deluge 2.x**.

The stock Deluge status bar asks for the free space of the *default download
location* only. On a server with several HDDs that hides every other filesystem,
and a full disk can silently stop downloads. DiskGuard models each filesystem
separately and can pause only the torrents that are writing to the full disk.

**Status:** bootstrap / M1 (storage model). No release yet.

## Why

Deluge 2.x core has `get_free_space(path)` but no low-disk gate: `add()` does
not check free space, and there is no `max_disk_space` setting. The stock UI
shows one value, for one path. DiskGuard fills that gap:

- discovers real mounts and filesystem stats;
- maps the session default, torrent paths, and session/torrent/Label
  `move_completed_path` targets to filesystems;
- exposes a stable `disks[]` payload;
- runs a per-filesystem guard with hysteresis and notifications.

It is **headless**: the mobile UI/API lives in the existing `webui2` plugin.
DiskGuard does not start another HTTP server, so it does not duplicate auth,
SSE or Web Push. The stock `deluge-web` on 8112 stays untouched.

## Data model

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

Paths are grouped by `st_dev`, so bind mounts of the same filesystem do not
appear twice. The mount label is the longest matching mountpoint from
`/proc/self/mounts`.

## Guard semantics

- Low: `free < min_free_bytes` for that mount.
- OK: `free >= min_free_bytes + resume_margin_bytes` (hysteresis).
- `pause` action pauses **only downloading/queued/checking torrents on that
  filesystem**. Seeding is left alone (it does not write, and pausing it would
  hurt HnR).
- Torrents paused by the guard are tracked separately, so the guard never
  resumes a torrent the user paused by hand.
- `notify` action emits an event without pausing.
- No auto-remove: a full disk with completed data needs a cleanup decision,
  not an automatic delete.

## Layout

```text
diskguard/          Deluge core plugin + pure storage/guard logic
  core.py           enable/disable, config, get_storage()
  storage.py        mount discovery, statvfs, path mapping
  guard.py          thresholds, hysteresis, pause selection
tests/              pytest; Deluge stubs for unit tests
docs/               design, API, install, decisions
```

## Commands

```bash
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/python setup.py bdist_egg
```

The normal unit-test run uses `tests/stubs`; the CI compatibility job sets
`DISKGUARD_REAL_DELUGE=1` to import real Deluge 2.x.

## Documentation

- [docs/design.md](docs/design.md) – model and guard semantics
- [docs/api.md](docs/api.md) – payload and webui2 endpoints
- [docs/install.md](docs/install.md) – egg install, config, rollback
- [docs/decisions.md](docs/decisions.md) – decisions and constraints
- [AGENTS.md](AGENTS.md) – working notes for agents
