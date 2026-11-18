"""Source archive cleanup on Windows where Git objects are read-only."""

from __future__ import annotations

import errno
import os
import stat
import subprocess
from pathlib import Path

import pytest

from git_well.archive_source._prune import _remove_loose_object
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


@pytest.mark.parametrize('removal', ['patch', 'prune'])
def test_patch_removes_readonly_individual_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, removal: str
) -> None:
    """Patch deletion and sparse pruning retry read-only Git object removal."""
    oid = 'a' * 40
    if removal == 'prune':
        subprocess.run(['git', 'init', str(tmp_path)], check=True)
        readonly = tmp_path / '.git' / 'objects' / oid[:2] / oid[2:]
        readonly.parent.mkdir()
    else:
        readonly = tmp_path / 'loose-object'
    readonly.write_bytes(b'object')
    readonly.chmod(stat.S_IREAD)
    real_unlink = Path.unlink
    refused: list[str] = []

    def windows_unlink(path, *args, **kwargs):
        if Path(path) == readonly and not (readonly.stat().st_mode & stat.S_IWUSR):
            refused.append(str(path))
            raise PermissionError(errno.EACCES, 'Access is denied', str(path))
        return real_unlink(path, *args, **kwargs)

    with monkeypatch.context() as patcher:
        # Python 3.10's pathlib caches os.unlink in its accessor, so patch the
        # Path method directly to intercept deletion on every supported Python.
        patcher.setattr(Path, 'unlink', windows_unlink)
        if removal == 'prune':
            _remove_loose_object(tmp_path, oid)
        else:
            _remove_path(readonly)

    assert refused
    assert not readonly.exists()
    if removal == 'prune':
        # Missing objects are harmless (the old unlink used missing_ok=True).
        _remove_loose_object(tmp_path, oid)


@pytest.mark.parametrize('failure', [None, 'prepare', 'validate', 'early'])
def test_archive_closes_gitpython_repositories(tmp_path, monkeypatch, failure):
    """Persistent Git processes must not keep Windows source directories busy."""
    import git

    from git_well.archive_source import ArchiveSourceHookError, archive_source

    def run_git(repo, *args):
        return subprocess.run(
            ['git', '-C', str(repo), *args], check=True, capture_output=True
        )

    source = tmp_path / 'source'
    submodule = tmp_path / 'submodule'
    for path in (source, submodule):
        path.mkdir()
        run_git(path, 'init')
        run_git(path, 'config', 'user.name', 'Test User')
        run_git(path, 'config', 'user.email', 'test@example.com')
        (path / 'keep.txt').write_text('keep\n')
        (path / 'large.bin').write_bytes(b'x' * 1000)
        run_git(path, 'add', '.')
        run_git(path, 'commit', '-m', 'initial')
    run_git(
        source, '-c', 'protocol.file.allow=always',
        'submodule', 'add', str(submodule), 'child',
    )
    run_git(source, 'commit', '-am', 'add child')

    # Keep strong references so delayed collection cannot conceal missing close
    # calls. Observe real GitPython objects and their actual process caches.
    opened = []
    closed = set()
    real_init = git.Repo.__init__
    real_close = git.Repo.close

    def record_init(repo, *args, **kwargs):
        real_init(repo, *args, **kwargs)
        opened.append(repo)

    def record_close(repo):
        closed.add(id(repo))
        real_close(repo)

    def fail_hook(context):
        raise RuntimeError('intentional hook failure')

    with monkeypatch.context() as patcher:
        patcher.setattr(git.Repo, '__init__', record_init)
        patcher.setattr(git.Repo, 'close', record_close)
        options = dict(
            repo_dpath=source, output=tmp_path / 'source.tar.gz',
            exclude_path=['large.bin', 'child/large.bin'],
            history_blobs='sparse', verbose=0,
        )
        try:
            if failure == 'early':
                with pytest.raises(ValueError, match='requires Git history'):
                    archive_source(**options, depth=0, all_branches=True)
            elif failure:
                with pytest.raises(ArchiveSourceHookError):
                    archive_source(**options, **{failure: fail_hook})
            else:
                archive_source(**options)

            assert opened
            assert all(id(repo) in closed for repo in opened)
            assert all(repo.git.cat_file_header is None for repo in opened)
            assert all(repo.git.cat_file_all is None for repo in opened)
            source.rename(tmp_path / 'source.offline')
        finally:
            for repo in opened:
                real_close(repo)
