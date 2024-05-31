# Changelog

We are currently working on porting this changelog to the specifications in
[Keep a Changelog](https://keepachangelog.com/en/1.0.0/).
This project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Version 0.2.4 - Unreleased


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
