# git-well kwconf migration and IPFS name fallback overlay

This overlay supersedes the previous kwconf migration overlay and keeps the
same end state while also fixing generated IPFS pin names when a repository has
no usable `origin` remote.

Changes:

- Replace `scriptconfig` imports with `kwconf`.
- Replace `scfg.DataConfig` bases with `kw.Config`.
- Keep existing `kw.Value`, `kw.Flag`, and `kw.ModalCLI` definitions using the kwconf public API.
- Rename remaining `type=str` `Value` metadata to `parser=str` to avoid deprecated kwconf compatibility aliases.
- Replace the runtime dependency on `scriptconfig` with `kwconf>=0.10.0`.
- Update the Sphinx intersphinx mapping from scriptconfig to kwconf.
- Teach generated IPFS pin names to fall back to safe `pkg:generic/<repo>` names when `remote.origin.url` is missing, local-only, or unparseable.
- Preserve existing forge-derived names for GitHub, GitLab, Bitbucket, and other network remotes.
- Add regression tests for no-origin repositories, local-origin repositories, `ipfs add --dry-run`, and `ipfs export`.

Validation performed in this sandbox:

- Installed local `kwconf` source and the migrated `git_well` package into a fresh virtual environment.
- `python -m py_compile git_well/ipfs.py tests/test_ipfs.py`
- `pytest -q tests/test_ipfs.py` -> 12 passed.
- `python -m py_compile $(find git_well tests -name '*.py' -print)`
- `pytest -q tests` -> 26 passed, 1 skipped.
