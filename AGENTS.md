# AGENTS.md

DiskGuard is a **headless Deluge 2.x core plugin + Python library** for
multi-filesystem free-space awareness and a per-disk guard. The mobile UI/API
lives in `webui2`; DiskGuard does not start its own HTTP server.

## Layout

- `diskguard/core.py` – Deluge plugin, config, `get_storage()`
- `diskguard/storage.py` – mount discovery, `statvfs`, path mapping (pure)
- `diskguard/guard.py` – thresholds, hysteresis, pause selection (pure)
- `tests/` – pytest; `tests/stubs` for unit tests without Deluge
- `docs/` – design, API, install, decisions

## Commands

```bash
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/python setup.py bdist_egg
```

## Principles

- **Per filesystem, not global:** a full disk pauses only the torrents writing
  to it; other disks keep running.
- **Do not pause seeding:** seeding does not consume free space and a pause can
  hurt HnR. Pause only downloading/queued/checking/moving torrents.
- **The reactor must not block:** local `statvfs` is cheap, but network mounts
  can hang. Run stats in a thread/cache for the guard loop; reads stay cached.
- **Hysteresis:** low below the threshold, OK only above threshold + margin;
  never flap on every poll.
- **User pause is sacred:** track guard-paused torrent IDs; never resume a
  torrent the user paused by hand.
- **No auto-remove:** a full disk with completed data needs a cleanup decision.
- **Do not duplicate Deluge:** only model what the stock daemon/UI does not.
  The 8112 `deluge-web` stays untouched.
- **Deluge 2.x:** every new `deluge.*` import must pass the CI
  `deluge-compat` matrix (2.0.3 / 2.1.1 / 2.2.0); update `tests/stubs` too.
- **Secrets/IPs:** no host/IP/token in the repo; docs stay environment-agnostic.
- **Authz:** endpoints are mounted by `webui2`; every mutating one needs
  read-only (403) and normal (200) tests there.

## Rules

- Git operations (commit/push/merge) require explicit approval.
- Changing the Deluge host (install/restart) requires separate approval.
- One step = one issue, followed by a short status; do not leave half-finished
  files.

Details: [README.md](README.md), [docs/design.md](docs/design.md),
[docs/api.md](docs/api.md), [docs/decisions.md](docs/decisions.md).
