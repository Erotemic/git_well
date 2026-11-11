"""Source archive cleanup on Windows where Git objects are read-only."""

from __future__ import annotations

import errno
import os
import stat
from pathlib import Path

import pytest

from git_well.archive_source.patch_apply import _remove_path, _remove_tree


def test_archive_removes_readonly_git_objects(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulate WinError 5 on any host to exercise the retry logic."""
    root = tmp_path / 'staged-repo'
    pack_dir = root / '.git' / 'objects' / 'pack'
    pack_dir.mkdir(parents=True)
    readonly = pack_dir / 'pack-test.idx'
    readonly.write_bytes(b'git pack index')
    readonly.chmod(stat.S_IREAD)

    real_unlink = os.unlink
    blocked_attempts: list[str] = []

    def windows_unlink(path, *args, **kwargs):
        if Path(path).name == readonly.name and not (
            readonly.stat().st_mode & stat.S_IWUSR
        ):
            blocked_attempts.append(str(path))
            raise PermissionError(errno.EACCES, 'Access is denied', str(path))
        return real_unlink(path, *args, **kwargs)

    with monkeypatch.context() as patcher:
        patcher.setattr(os, 'unlink', windows_unlink)
        _remove_tree(root)

    assert blocked_attempts, 'the Windows-like error path was not exercised'
    assert not root.exists()


def test_archive_removal_does_not_mask_other_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / 'staged-repo'
    root.mkdir()
    (root / 'blocked').write_text('data')
    real_unlink = os.unlink

    def failed_unlink(path, *args, **kwargs):
        if Path(path).name == 'blocked':
            raise OSError(errno.EIO, 'unrelated filesystem failure')
        return real_unlink(path, *args, **kwargs)

    with monkeypatch.context() as patcher:
        patcher.setattr(os, 'unlink', failed_unlink)
        with pytest.raises(OSError, match='unrelated filesystem failure'):
            _remove_tree(root)


def test_archive_removal_preserves_symlink_targets(tmp_path: Path) -> None:
    target = tmp_path / 'outside.txt'
    target.write_text('leave me alone')
    root = tmp_path / 'staged-repo'
    root.mkdir()
    link = root / 'external.txt'
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError):
        pytest.skip('creating symlinks requires privileges on this platform')
    _remove_tree(root)
    assert target.read_text() == 'leave me alone'


def test_patch_removes_readonly_individual_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Patch deletion also retries when a read-only Git object is removed."""
    readonly = tmp_path / 'loose-object'
    readonly.write_bytes(b'object')
    readonly.chmod(stat.S_IREAD)
    real_unlink = os.unlink
    refused: list[str] = []

    def windows_unlink(path, *args, **kwargs):
        if Path(path) == readonly and not (readonly.stat().st_mode & stat.S_IWUSR):
            refused.append(str(path))
            raise PermissionError(errno.EACCES, 'Access is denied', str(path))
        return real_unlink(path, *args, **kwargs)

    with monkeypatch.context() as patcher:
        patcher.setattr(os, 'unlink', windows_unlink)
        _remove_path(readonly)

    assert refused
    assert not readonly.exists()
