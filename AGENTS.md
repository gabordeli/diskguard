# AGENTS.md

DiskGuard is a **standalone Deluge 2.x plugin** (core + GTK UI) for
multi-filesystem free-space awareness and a per-disk guard. The core runs in
`deluged`; the GTK UI runs in `deluge-gtk` and talks to it through Deluge RPC.
There is no `webui2` dependency and no separate HTTP server.

## Layout

- `diskguard/core.py` – Deluge core plugin, config, exported RPC methods
- `diskguard/storage.py` – mount discovery, `statvfs`, path mapping (pure)
- `diskguard/guard.py` – thresholds, hysteresis, pause selection (pure)
- `diskguard/gtkui.py` – GTK UI plugin (Storage page/dialog, warnings)
- `tests/` – pytest; `tests/stubs` for unit tests without Deluge/GTK
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
  The stock GTK and 8112 `deluge-web` UIs stay untouched; DiskGuard adds its
  own GTK page/dialog.
- **Core/GTK split:** the GTK UI calls only exported core methods through
  `client.diskguard.*`; core methods need `@export`. Keep GTK imports lazy so
  headless core tests do not need PyGObject.
- **Deluge 2.x:** every new `deluge.*` import must pass the CI
  `deluge-compat` matrix (2.0.3 / 2.1.1 / 2.2.0); update `tests/stubs` too.
  GTK imports also need stubs or a separate GTK job.
- **Secrets/IPs:** no host/IP/token in the repo; docs stay environment-agnostic.
- **RPC auth:** the GTK UI uses the existing Deluge RPC session; DiskGuard does
  not add a second token/auth layer.

## Rules

- Git operations (commit/push/merge) require explicit approval.
- Changing the Deluge host (install/restart) requires separate approval.
- One step = one issue, followed by a short status; do not leave half-finished
  files.

Details: [README.md](README.md), [docs/design.md](docs/design.md),
[docs/api.md](docs/api.md), [docs/decisions.md](docs/decisions.md).
