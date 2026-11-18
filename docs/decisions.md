# Decisions

## ADR-001: Headless plugin, UI in webui2

DiskGuard runs as a Deluge core plugin and exposes Python functions/resources;
`webui2` mounts the HTTP endpoints and owns auth, SSE and Web Push. A second
HTTP server would duplicate those concerns for no user benefit.

## ADR-002: Per-filesystem guard, not a global pause

A global session pause stops healthy disks too. The guard maps torrents to the
filesystem of their `download_location` and pauses only the affected active
downloads.

## ADR-003: Group by `st_dev`, label by longest mountpoint

Path strings are not a filesystem identity: bind mounts and symlinks can point
to the same data. `st_dev` groups them; `/proc/self/mounts` gives the display
mountpoint via the longest prefix.

## ADR-004: Never pause seeding

Seeding does not consume free space, and pausing it can create HnR risk. The
guard only pauses `Allocating`, `Checking`, `Downloading`, `Moving` and
`Queued` torrents on the low mount.

## ADR-005: Track guard-paused torrents separately

The guard must not resume a torrent the user paused by hand. `GuardState` keeps
the IDs it paused; only those are candidates for automatic resume.

## ADR-006: No auto-remove

A full disk with completed data needs a human cleanup decision. The guard emits
an event and the UI can show the largest torrents; it never deletes.

## ADR-007: Show all mounts, guard only Deluge mounts

Operators want to see backup/USB disks too. The payload includes every real
mount with `deluge: false`; only filesystems with Deluge paths are guarded.

## ADR-008: Hysteresis in GB

Thresholds are configured in GB (not percent) because the download use case is
about absolute headroom for the next file. Resume requires
`min_free + margin`, so a disk at the threshold does not flap every poll.

## ADR-009: The stock 8112 UI stays untouched

The multi-disk view is a `webui2` feature. Changing `deluge-web` on 8112 would
expand the compatibility surface and contradict the existing deployment rule.
