# git-well kwconf migration and IPFS pin-name overlay

This overlay supersedes the previous kwconf and IPFS name-fallback overlays. It
keeps the same end state and adds Kubo 0.39-era named-pin behavior for
`git-well ipfs add`.

Changes:

- Replace `scriptconfig` imports with `kwconf`.
- Replace `scfg.DataConfig` bases with `kw.Config`.
- Keep existing `kw.Value`, `kw.Flag`, and `kw.ModalCLI` definitions using the kwconf public API.
- Rename remaining `type=str` `Value` metadata to `parser=str` to avoid deprecated kwconf compatibility aliases.
- Replace the runtime dependency on `scriptconfig` with `kwconf>=0.10.0`.
- Update the Sphinx intersphinx mapping from scriptconfig to kwconf.
- Teach generated IPFS pin names to fall back to safe `pkg:generic/<repo>` names when `remote.origin.url` is missing, local-only, or unparseable.
- Preserve existing forge-derived names for GitHub, GitLab, Bitbucket, and other network remotes.
- Assume Kubo 0.39.0 or newer and pass named pins directly to `ipfs add` via `--pin-name=<name>` instead of running a second `ipfs pin add --name ...` command.
- Validate IPFS pin names before they reach Kubo, rejecting empty names and control characters that are unsafe for sidecars / CLI display.
- Deterministically shorten names longer than Kubo's 255-byte UTF-8 limit using a stable `~gw-<sha256-prefix>` suffix.
- Persist the effective pin name in sidecars with `pin_name` and `pin_name_source`, while leaving `add_config.name` as the original explicit CLI option.
- Persist shortening audit metadata in sidecars when a generated or explicit pin name must be shortened.
- Prefer persisted sidecar `pin_name` during later `ipfs export` and `ipfs pin add` operations, falling back to older `add_config.name` and then generated names.
- Add regression tests for no-origin repositories, local-origin repositories, direct `ipfs add --pin-name`, sidecar pin-name persistence, name validation/shortening, and `ipfs export`.

Validation performed in this sandbox:

- Installed the local `kwconf` source.
- `python -m ruff check git_well/ipfs.py tests/test_ipfs.py` -> passed.
- `python -m py_compile $(find git_well tests -name '*.py' -print)` -> passed.
- `pytest -q -o addopts='' tests/test_ipfs.py` -> 16 passed.
- `pytest -q tests` -> 30 passed, 1 skipped.
