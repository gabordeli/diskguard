# Design

## Problem

Deluge 2.x exposes `Core.get_free_space(path)`, but the stock status bar asks
for one path: the session `download_location`. On a multi-HDD server every
other filesystem is invisible, and the daemon has no low-disk gate:

- `torrentmanager.add()` does not check free space;
- there is no `max_disk_space` setting;
- the stock UI cannot show per-filesystem usage.

A full disk therefore shows up late, as a write error or a stalled torrent.

## Goals

- Discover real mounted filesystems and their total/free/used values.
- Map every path Deluge can write to a filesystem:
  - session `download_location`;
  - per-torrent `download_location` (active) and `save_path` (completed);
  - session and per-torrent `move_completed_path` when `move_completed` is on;
  - Label `move_completed_path` when both `apply_move_completed` and
    `move_completed` are on.
- Expose a stable `disks[]` payload for the mobile UI.
- Guard per filesystem: pause only the torrents writing to a low disk;
  resume them after hysteresis.
- Keep the reactor responsive.
- Do not duplicate auth, SSE or Web Push: the UI/API lives in `webui2`.

## Non-goals

- A standalone SPA or another HTTP server.
- Changing the stock 8112 `deluge-web`.
- Auto-removing torrents to free space.
- Tracker logic, RSS or notifications beyond a guard event payload.
- Editing/moving data outside Deluge's own paths.

## Architecture

```text
webui2 SPA
   │  GET /api/storage, PATCH /api/storage/guard
   ▼
webui2 HTTP resources
   │  import diskguard.api / diskguard.storage / diskguard.guard
   ▼
diskguard.core (Deluge plugin: config, state, get_storage)
   │
   ├── storage.py   pure: mounts, statvfs, path mapping
   └── guard.py     pure: thresholds, hysteresis, pause selection
```

`diskguard` is a Deluge core plugin so the guard can query the torrent manager
and pause/resume torrents. The plugin does not serve HTTP itself; `webui2`
mounts the resources and keeps the single auth/push/SSE boundary.

## Path model

Every `PathUse` carries `path`, optional `torrent_id`, `label`, `active`,
`size` and `kind`. The adapter deduplicates by `(path, torrent_id)`.

Move targets use `kind="move_completed"` and `active=False`: they are visible
in the storage payload and can trigger a low-disk notification, but they do not
make the guard pause downloads that are still writing to another disk.

Grouping is by `st_dev`, not by path string:

- bind mounts of the same filesystem collapse into one disk;
- the display mount is the longest matching mountpoint from
  `/proc/self/mounts`;
- missing paths go to `unknown[]` instead of failing the request.

`include_all` adds every real mount (pseudo filesystems filtered) with
`deluge: false`, so backup disks are visible without being guarded.

Inside a Docker container `/proc/self/mounts` shows the container's mount
namespace: the `overlay` root is filtered as a pseudo filesystem, while
bind-mounted host disks appear normally. A download directory that lives only
on the container's overlay layer is therefore not listed; mount it as a volume
if it should be visible to DiskGuard.

## Guard semantics

For each mount with a threshold:

- **low** when `free < min_free_bytes`;
- **ok** when `free >= min_free_bytes + resume_margin_bytes`;
- only the transition emits an event, so a poll loop does not spam.

Actions:

- `notify`: emit an event, no torrent changes.
- `pause`: pause torrents whose `download_location` maps to the low mount and
  whose state is in `ACTIVE_STATES` (`Allocating`, `Checking`, `Downloading`,
  `Moving`, `Queued`).

Seeding is never paused by the guard. A completed torrent does not write, and
pausing it can hurt HnR. `paused_torrents` tracks only guard-paused IDs, so a
manual user pause is never undone by an automatic resume.

The guard loop belongs in `core.py` (M2). It will poll at a low frequency,
run `statvfs` in a thread for network mounts, and apply transitions through
the torrent manager.

## Performance

`os.statvfs` is cheap on local disks; a hung NFS/CIFS mount can block.

## Guard loop

`Core.enable()` starts a Twisted `LoopingCall` (`GUARD_POLL_SECONDS = 30`);
`disable()` stops it idempotently. Path and mount collection runs on the
reactor using cached torrent status. `storage.build_disks()` (`st_dev` +
`statvfs`) runs in `deferToThread`; the result is applied back on the reactor,
where `guard.evaluate()` updates `GuardState` and the payload cache. A hung
network mount therefore cannot block `deluged`; one failed poll keeps the
previous cached payload, resets the in-flight flag and the next tick retries.
Only disks with `deluge: true` are evaluated, so backup disks are visible but
not guarded.

## Security

- Read-only storage payload; no paths are mutated.
- The mutating guard config endpoint is mounted by `webui2` and follows its
  authz levels: read-only → 403, normal → 200.
- No token, passkey or host/IP in logs or docs.
