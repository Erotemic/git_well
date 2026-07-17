# git-well kwconf migration and IPFS pin-name overlay

This overlay supersedes the previous kwconf/IPFS overlays. It keeps the kwconf
migration, the safe generated-name fallback behavior, and the direct Kubo 0.39+
`ipfs add --pin-name=...` path, then adds the latest requested cleanup.

Changes:

- Replace `scriptconfig` imports with `kwconf`.
- Replace `DataConfig` bases with `kwconf.Config`.
- Use `import kwconf` and fully qualified `kwconf.Value`, `kwconf.Flag`, `kwconf.Config`, and `kwconf.ModalCLI` references instead of a `kw` alias.
- Correct existing config-field annotations that incorrectly used `Value`; annotations now describe the parsed field data, and unannotated fields remain unannotated.
- Rename remaining `type=str` `Value` metadata to `parser=str` to avoid deprecated kwconf compatibility aliases.
- Replace the runtime dependency on `scriptconfig` with `kwconf>=0.10.0`.
- Update the Sphinx intersphinx mapping from scriptconfig to kwconf.
- Teach generated IPFS pin names to fall back to safe `pkg:generic/<repo>` names when `remote.origin.url` is missing, local-only, or unparseable.
- Preserve existing forge-derived names for GitHub, GitLab, Bitbucket, and other network remotes.
- Assume Kubo 0.39+ and use direct `ipfs add --pin-name=...` for named adds instead of a post-add `ipfs pin add` compatibility step.
- Validate Kubo pin names and deterministically shorten names over the 255-byte UTF-8 limit with a stable `~gw-<sha256-prefix>` suffix.
- Persist effective pin names in sidecars as `pin_name` / `pin_name_source`, with shortening audit metadata when applicable.
- After `git-well ipfs add`, print a copy/paste `ipfs pin add --name=...` command for pinning the CID on another machine.
- Add regression tests for no-origin repositories, local-origin repositories, dry-run generated names, sidecar-persisted names, name shortening, direct `--pin-name`, and the printed remote pin command.
- Fix `ty check git_well` diagnostics reported after v4: nullable field annotations for `None` defaults, safe branch-name narrowing, integer trust-level comparison, and the sidecar pin-name narrowing.
- Prefer PEP 604 `T | None` syntax in `git_archive_source.py` and remove the old `Optional[...]` spellings there.

Validation performed in this sandbox:

- `PYTHONPATH=/mnt/data/kwconf_src:. uv tool run ty check git_well` -> passed.
- `PYTHONPATH=/mnt/data/kwconf_src:. python -m py_compile $(find git_well tests -name '*.py' -print)` -> passed.
- `PYTHONPATH=/mnt/data/kwconf_src:. python -m pytest -q -o addopts='' tests/test_ipfs.py` -> 17 passed.
- `PYTHONPATH=/mnt/data/kwconf_src:. python -m pytest -q tests` -> 31 passed, 1 skipped.
