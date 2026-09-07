# Changelog

We are currently working on porting this changelog to the specifications in
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Version 0.3.3 - Unreleased

### Added

* Add `git epoch` / `git-epoch` for verifiable bounded-history checkpoints, exact archival epochs, recursive submodule rollover, reconstruction, and resumable prepare/publish transactions.
* Add `git epoch sandbox` to rehearse epoch rollover against contained local worktrees, bare publication remotes, and a local history store before touching production remotes.
* Add `git epoch stats` and `git epoch sandbox stats` to report shared history-store size, per-epoch reachable/exclusive object sizes, standalone bundle sizes, active-history size, recursive fresh-clone size, and optional `archive_source` package size.
* Add committed `.git-epoch.yaml` public history locators and `git epoch attach` so fresh clones can discover, inspect, validate, and reconstruct split history without machine-local configuration.
* Add explicit `--retire-extra-branches` checkpoint planning to archive auxiliary branch tips under their original names and remove them atomically from active remotes after archival verification.
* Add human-browsable history-store views: a generated `main` landing branch, `archive/...` branch/tag mirrors, and idempotent `git epoch history-sync` repair/backfill.
* Add `git epoch compact` to prune locally unreachable retired-epoch objects from verified published checkouts while reporting before/after Git-directory sizes.
* Add `archive_source --all-branches` to preserve every local branch and every locally cached remote-tracking branch without contacting configured remotes.
* Add programmatic `prepare` and `validate` hooks for repository-specific archive enrichment and policy checks.
* Add `stage_source_archive()` and `ArchiveSourceContext` for direct control of staged archive contents, generated-path exclusions, metadata finalization, serialization, and retained-stage debugging.

### Changed

* Report elapsed stage/repository progress for epoch apply, publish, and deep verification on stderr, with `--quiet` for machine-oriented runs.
* Batch archive publication per repository and batch deep-verification fetches, avoiding one network round trip per archived ref and redundant deep `fsck` work.
* Refuse unignored checkpoint-plan output paths inside the managed worktree so planning cannot make its own subsequent apply fail the clean-tree gate.

### Fixed

* Store `core.longpaths=true` in every history-bearing source archive checkout so Git for Windows can read packed objects after extraction beneath long directory prefixes without requiring global Git configuration.
* Make the Git epoch subprocess boundary encode all string stdin as bytes before invoking Git, so line-oriented plumbing cannot acquire CRLF terminators on Windows regardless of the caller.
* Keep Git epoch batched local ref publication compatible with Git for Windows by using the portable `update-ref --stdin` batch form.
* Materialize cached archive-source branch refs by direct local object import and local ref creation, removing Git transport/refspec parsing from local branch preservation on every platform.
* Finalize cached archive-source refs after clone maintenance and verify their exact OIDs and shallow traversal before serialization, so maintenance cannot invalidate the archive ref contract.
* Recover unadvertised local archive-source commits by streaming Git objects directly from the source object database instead of constructing an alternates-backed temporary fetch remote.
* Make sandbox verification generation-scoped so a locked pack file from an earlier Windows verification run cannot block a later run.
* Compose recursive sandbox verification from independent fresh clones with flat separate Git admin directories, preventing nested `.git/modules/...` path growth on Windows.
* Resolve `ty` diagnostics in recursive epoch planning, successor-tree translation typing, and repository command forwarding.
* Make repeated `git epoch init` setup calls idempotently reconcile equivalent local configuration, recreate a missing public locator, and tolerate only an in-progress locator edit while still rejecting real configuration conflicts or unrelated dirty state.
* Preserve explicit logical history-store IDs when creating the first remote archive manifest instead of deriving the manifest ID from the remote URL basename.
* Make sandbox verification perform a real recursive fresh clone through contained `file://` remotes, prove every submodule initializes at the translated gitlink without retired commits leaking through local-clone optimization, and report idempotent `sandbox run` phases as explicit skips.
* Let `archive_source` omit uninitialized submodules with a warning instead of aborting the entire archive; record each omission in `GIT_WELL_ARCHIVE_INFO.txt`.


## Version 0.3.2 - Released 2026-07-17

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
* Write archive information with deterministic LF newlines on every platform.
* Resolve `ty` and mypy diagnostics in command-output handling, Git URL metadata, CLI registration, and configuration annotations.
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
