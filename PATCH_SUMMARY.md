# git ipfs hardening overlay v8

This overlay fixes the Windows fake-IPFS integration test by avoiding platform
PATH / PATHEXT lookup for the test double.

Changes:

- Add `GIT_WELL_IPFS_COMMAND_JSON`, a test/user override for the logical `ipfs`
  executable. It accepts a JSON argv prefix, e.g. `["python", "fake_ipfs.py"]`.
- Keep `GIT_WELL_IPFS_EXE` as a simpler single-executable override.
- Rewrite logical `ipfs ...` commands through the configured prefix inside
  `_run`, while still using the logical argv for user-facing diagnostics.
- Update `git ipfs doctor` to report configured IPFS command prefixes.
- Update the fake-IPFS test fixture to set `GIT_WELL_IPFS_COMMAND_JSON` to
  `[sys.executable, _fake_ipfs.py]`, so Windows does not need to discover
  `ipfs`, `ipfs.cmd`, or `ipfs.bat` via `shutil.which`.

Validation performed in this sandbox:

- `python -m py_compile git_well/ipfs.py tests/test_ipfs.py`

The sandbox does not have the runtime test dependency `scriptconfig`, so the
full pytest suite was not run here.
