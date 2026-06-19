# git-well kwconf migration overlay

This overlay migrates git-well from `scriptconfig` to `kwconf`.

Changes:

- Replace `scriptconfig` imports with `kwconf`.
- Replace `scfg.DataConfig` bases with `kw.Config`.
- Keep existing `kw.Value`, `kw.Flag`, and `kw.ModalCLI` definitions using the kwconf public API.
- Rename remaining `type=str` `Value` metadata to `parser=str` to avoid deprecated kwconf compatibility aliases.
- Replace the runtime dependency on `scriptconfig` with `kwconf>=0.10.0`.
- Update the Sphinx intersphinx mapping from scriptconfig to kwconf.

Validation performed in this sandbox:

- Installed local `kwconf` source and the migrated `git_well` package into a fresh virtual environment.
- `python -m py_compile $(find git_well tests -name '*.py' -print)`
- `pytest -q tests` -> 22 passed, 1 skipped.
- `pytest -q` ran the package tests and doctests; the only failure was an existing network-dependent doctest in `git_track_upstream.py` at `git fetch origin`.
