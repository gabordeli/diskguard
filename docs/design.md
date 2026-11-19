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
- Expose a stable `disks[]` payload for the GTK UI.
- Guard per filesystem: pause only the torrents writing to a low disk;
  resume them after hysteresis.
- Keep the reactor responsive.
- Ship a GTK UI page/dialog for the storage view, guard settings and
  free-space warnings.
- Use the existing Deluge RPC session; do not add a second auth or HTTP layer.

## Non-goals

- A standalone SPA or another HTTP server.
- Changing the stock 8112 `deluge-web`.
- Auto-removing torrents to free space.
- Tracker logic, RSS or notifications beyond a guard event payload.
- Editing/moving data outside Deluge's own paths.

## Architecture

```text
deluge-gtk GTK UI
   │  client.diskguard.get_storage()
   │  client.diskguard.get_guard_config() / set_guard_config()
   ▼  Deluge RPC
diskguard.core (core plugin: config, state, exported methods)
   │
   ├── storage.py   pure: mounts, statvfs, path mapping
   └── guard.py     pure: thresholds, hysteresis, pause selection
```

One egg provides both the core plugin and the GTK UI plugin. The core runs in
`deluged` so the guard can query the torrent manager and pause/resume torrents.
The GTK UI runs in `deluge-gtk` and calls only exported core methods; it does
not start an HTTP server or duplicate Deluge's RPC authentication.

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
  `Moving`, `Queued`). Mountpoints sharing the target disk's `st_dev` are
  treated as aliases, so a torrent under a shorter bind mount is still paused.

Seeding is never paused by the guard. A completed torrent does not write, and
pausing it can hurt HnR. `paused_torrents` tracks only guard-paused IDs, and
`paused_by_mount` keeps the per-disk subset, so an `ok` event only resumes
torrents the guard paused for that disk. The current state of every candidate
is re-read before pausing, so a torrent the user paused after the snapshot is
left alone.

The guard loop lives in `core.py`: a low-frequency `LoopingCall` runs
`statvfs` in a thread for network mounts and applies transitions through the
Deluge core's torrent manager.

## GTK UI

`diskguard/gtkui.py` is a `Gtk3PluginBase` plugin. It adds a Preferences
page (`Preferences` → `DiskGuard`) and a `View` → `DiskGuard Storage` entry
that selects the same page. The page renders each filesystem as a padded card
with a drive icon, a low/OK badge, a rounded usage bar and a summary tile
strip plus an aggregate capacity ring; the CSS is scoped to `diskguard-*`
classes so it does not restyle the stock Deluge widgets. The page lists
`disks[]`, shows low/OK state, and edits the guard config through
`client.diskguard.*`. Settings use one table and one **Save guard settings**
action; **Reload** discards edits and an unchecked Override row inherits the
defaults. A compact status-bar item shows `N disks · X free` (red when a disk
is low) and opens the page on click. The core guard loop detects a torrent path
that first appears on a low disk and emits `LowTargetEvent(torrent_id, path,
mount)`; the GTK UI shows one non-blocking dialog per
`(torrent, path, mount)`. This covers add and move paths. The stock GTK dialogs
are not replaced; DiskGuard adds a page of its own. GTK imports stay inside the
module so the headless core tests do not need PyGObject.

## Performance and guard loop

`os.statvfs` is cheap on local disks; a hung NFS/CIFS mount can block.

`Core.enable()` starts a Twisted `LoopingCall` (`GUARD_POLL_SECONDS = 30`);
`disable()` stops it idempotently. Path and mount collection runs on the
reactor using cached torrent status. `storage.build_disks()` (`st_dev` +
`statvfs`) runs in `deferToThread`; the result is applied back on the reactor,
where `guard.evaluate()` updates `GuardState` and the payload cache. A hung
network mount therefore cannot block `deluged`; one failed poll keeps the
previous cached payload, resets the in-flight flag and the next tick retries.
Only disks with `deluge: true` are evaluated, so backup disks are visible but
not guarded.

Each low/ok transition records one event with `mount`, `event`, `free`,
`min_free`, `action` and the affected `torrents` count. The core emits a
Deluge `GuardEvent` and calls optional in-process sinks; repeated polls at the
same state do not duplicate it. There is no HTTP or second auth layer.

## Security

- Read-only storage payload; no paths are mutated.
- The GTK UI uses the existing Deluge RPC session; DiskGuard adds no token or
  HTTP auth layer.
- No token, passkey or host/IP in logs or docs.
