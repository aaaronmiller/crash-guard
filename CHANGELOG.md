# Changelog

## [Unreleased]

### Added
- Added terminal backend restore support for tmux, Ghostty, kitty, Windows Terminal, and existing WezTerm behavior.
- Added `crash-guard restore --terminal` to force a restore backend when auto-detection is not desired.
- Added MIT license and repository ignore rules for generated/local files.
- Added Kilo continue restore support.

### Changed
- Reworked README structure around install, restore workflow, terminal backends, config, and limitations.

### Fixed
- Fixed restore planning for wrapper-keyed sentinels such as `rtk` by inferring the concrete tool from recorded argv.
- Fixed default config merging so existing user configs receive new built-in program entries without overwriting local entries.
