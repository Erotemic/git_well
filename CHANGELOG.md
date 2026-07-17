# Changelog

We are currently working on porting this changelog to the specifications in
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Version 0.3.2 - Unreleased

### Added

* Add `archive_source --submodule-depth` YAML specs for source-only, shallow, full, default, and globbed per-submodule depth policies.
* Add `archive_source --exclude-submodule` and `--no-submodules` controls for omitting large submodule working trees from source archives.
* Add `archive_source --redact-local-paths` to omit local source/output paths and generated local clone origins from distributable archives.

### Changed

* Replace the verbose `SOURCE_ARCHIVE_MANIFEST.txt` with a concise `GIT_WELL_ARCHIVE_INFO.txt` receipt that records archive paths, commits, history depth, and intentional pruning without embedding `git status` output.
* Resolve recursive submodules from committed Git trees instead of the current index, including support for valid paths containing spaces.

### Fixed

* Make `branch_cleanup --remove-merged` opt-in instead of deleting all merged branches unconditionally.
* Make `sync` stage untracked files, propagate commit-hook failures, and check out the intended remote branch before pulling or resetting it.
* Confine IPFS sidecar pulls to the enclosing worktree by default and atomically replace destinations so stale files cannot survive a CID update.
* Restore the original branch after `squash`, including in-place operation, and permit an excluded root commit as the squash boundary.
* Update only remote URL config keys in `remote_protocol`, while supporting nested groups, SCP-style users, local URLs, and SSH ports.
* Inspect rebase conflicts with NUL-delimited Git plumbing instead of parsing human-readable `git status`.
* Report `discover_remote` cross-drive path errors without referencing an uninitialized variable, and gate the network upstream doctest behind `NETWORK==1`.
* Refuse to overwrite a repository-owned archive information path, including symlinks and dangling symlinks.
* Make parent submodule exclusions apply to all nested descendants.
* Report malformed committed gitlinks instead of silently treating submodule discovery failures as an empty submodule set.
* Keep mixed superproject/submodule history descriptions accurate, avoid duplicate `.git/info/exclude` entries, preserve the caller's working directory, and honor the documented `format=auto` fallback.


## Version 0.3.1 - Released 2026-05-16


## Version 0.3.0 - Released 2026-05-16

### Added

* Added patchdir save / apply tools
* Added new `git-well squash` tool with the goal of making a more useful squash-streaks
* Add `git-well archive_source` tool
* Add `git-well ipfs` tool

### Removed
* Drop Python 3.7, 3.8, and 3.9 support

### Changed
* git-well discover-remote now works with submodules


## Version 0.2.4 - Released 2025-02-27

### Added
* New CLI command: `url` which lets you access components of a git url

### Changed
* The `repo_name` item (which previously ended with .git) has been changed to `repo_endpoint`, and the `repo_name` no longer will contain a .git suffix.


## [Version 0.2.3] - 

## [Version 0.2.2] - 

### Added
* Add email option to gpg autoconf

### Changed
* modified default in git-well permit

## [Version 0.2.1] - 

### Fixed
* Fixed error in git-rebase-add-continue parsing submodule status
* git-well squash-streaks now works from modal CLI

### Added
* autoconf-gpg for auto-configuring which gpg key to sign commits with
* more options to discover ssh remote

### Changed
* git track-upstream will now ask which remote to use when it is ambiguous.
* Improved `git-remote-protocol`
* `find_git_root` uses `absolute` instead of `resolve` so logical pathing is preserved.

## [Version 0.2.0] - Released 2023-08-09

### Added
* Add `git_rebase_add_continue`
* Add `git_remote_protocol` (i.e. git permit)

## [Version 0.1.1] - Released 2023-06-22

### Added
* Initial version with `branch_upgrade`,`squash_streaks`,`sync`,`branch_cleanup`,`track_upstream`
