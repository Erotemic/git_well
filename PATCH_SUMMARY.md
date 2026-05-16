# git-ipfs hardening overlay v5

This follow-up fixes a regression in the v4 integration test: using
`scriptconfig.ModalCLI.register` directly as a decorator can replace the
command class name with `None` in some scriptconfig versions.

Changes:

- Adds `_register_modal(parent)` helper that registers a modal command and
  returns the original class object.
- Switches `IPFSDoctor`, `IPFSPeers`, `IPFSPush`, `IPFSAdd`, `IPFSPull`,
  `IPFSStatus`, `IPFSExportPins`, `IPFSPinAdd`, and `IPFSCheckCID` to the
  preserving decorator.
- Adds a regression test that imports `IPFSAdd`, `IPFSPull`, and `IPFSDoctor`
  and verifies they remain class objects.

Suggested checks:

```bash
ty check git_well
python -m pytest -q tests/test_ipfs.py
bash -n dev/ipfs_dogfood_smoke.sh
```


## v6

- Broadened internal config helper annotations so `scriptconfig.Config` objects accepted by `cli()` satisfy `ty`.
- Avoid mutating parsed `scriptconfig.Config` in `IPFSPinAdd`; use a local `pin_name` instead.

## v7 Windows fake-IPFS test fix

- Makes the fake Kubo test helper install a shared Python implementation plus
  POSIX and Windows launchers.
- Adds `ipfs.bat` and `ipfs.cmd` launchers so `shutil.which('ipfs')` succeeds
  on Windows through PATHEXT during the fake-IPFS integration test.
- Keeps the fake implementation single-sourced so POSIX and Windows exercise
  the same behavior.
