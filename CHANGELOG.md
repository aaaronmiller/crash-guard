# Changelog

## [Unreleased]

### Added
- Added terminal backend restore support for tmux, Ghostty, kitty, Windows Terminal, and existing WezTerm behavior.
- Added `crash-guard restore --terminal` to force a restore backend when auto-detection is not desired.
- Added MIT license and repository ignore rules for generated/local files.
- Added Kilo continue restore support.
- Added `cgr-archive` alias for retrying archived restore records through Ghostty/tmux.
- Added append-only durable session history with grouped recovery via `crash-guard history`.
- Added `crash-guard restore --group` and `--item` for restoring older groups or individual sessions.
- Added `crash-guard excise` dry-run cleanup with explicit confirmation for old history removal.
- Added `cgh` alias for history.
- Added no-argument `crash-guard` restore picker behavior.
- Added OS boot-period browsing to `crash-guard history`.
- Added `crash-guard restore --boot` for restoring groups from older boot periods.
- Added colorized terminal headings and an ASCII project title for interactive recovery views.

### Changed
- Reworked README structure around install, restore workflow, terminal backends, config, and limitations.
- Changed Ghostty restore behavior to use tmux windows in the current Ghostty tab instead of opening Ghostty OS windows.
- Clarified that tmux restore uses tmux windows, not split panes.
- Changed default restore to derive targets from durable recovery groups instead of only currently crashed live sentinels.
- Changed `history` from a flat group list into a boot-period browser with number/arrow-key selection.
- Changed interactive restore into a two-step arrow-key picker for recovery group and session selection.
- Moved executable, shell integration, and installer into `bin/`, `shell/`, and `scripts/` for a cleaner repository root.
- Changed `scripts/install.sh` to install files only and leave shell rc edits to documented setup steps.

### Fixed
- Fixed restore planning for wrapper-keyed sentinels such as `rtk` by inferring the concrete tool from recorded argv.
- Fixed default config merging so existing user configs receive new built-in program entries without overwriting local entries.
- Added `restore --from-archive` so sentinels archived by a failed restore can be recovered.
- Fixed archived failed-restore sessions being split apart by preserved file mtimes; archive filename timestamps now group them correctly.
