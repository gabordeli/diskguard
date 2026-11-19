# Changelog

## [Unreleased]

### Added

- `View` → `DiskGuard Storage` entry in the main window; it opens the
  Preferences → DiskGuard page directly.
- Styled GTK page: padded cards, summary tiles, disk icons, low/OK badges,
  rounded usage bars, an aggregate capacity ring and a themed layout that
  works in light and dark themes.
- Per-mount settings table with one **Save guard settings** action, explicit
  **Reload** and an unsaved-changes indicator; unchecked Override rows inherit
  the defaults.
- Deluge plugin metadata now exposes the author, email and homepage; the
  plugin description is a short 1–2 sentence text instead of the README.

## [0.1.0] - 2026-11-19

### Added

- Standalone Deluge 2.x plugin: core daemon plugin plus a GTK UI page.
- Per-filesystem storage model: mount discovery, `statvfs`, `st_dev`
  grouping, bind-mount aliases and `unknown[]` paths.
- Session/torrent/Label `move_completed_path` mapping.
- Per-disk guard with hysteresis, per-mount thresholds, guard-paused tracking
  and manual-pause protection.
- GTK Storage page with disk list, unknown paths and default/per-mount guard
  settings.
- `LowTargetEvent` warnings for add/move targets on a low disk.
- Exported core RPC methods for the GTK client (`client.diskguard.*`).
- CI: lint, tests, Deluge 2.0.3/2.1.1/2.2.0 compatibility and entry-point
  checks.

### Changed

- The UI target is a standalone GTK plugin; no `webui2` dependency and no
  separate HTTP server.
- The plugin egg declares no runtime pip dependencies; it uses the Twisted
  provided by Deluge. This keeps the Deluge plugin loader from resolving
  unrelated distributions.
