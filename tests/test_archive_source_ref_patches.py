"""Patch regressions involving Git refs copied from source checkouts.

These tests exercise the patch transport without GitPython or CLI dependencies.
They deliberately construct legacy Git-bearing source archives whose pinned
submodule commit differs from the source checkout's moving main branch.
"""

from __future__ import annotations

import subprocess
import sys
import tarfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from git_well.archive_source.patch import build_source_patch
from git_well.archive_source.patch_apply import apply_source_patch


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(
        ['git', '-C', str(repo), *args],
        capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0, f'{args}: {proc.stderr}'
    return proc.stdout.strip()


def _commit(repo: Path, filename: str, content: str) -> str:
    (repo / filename).write_text(content, encoding='utf8')
    _git(repo, 'add', filename)
    _git(repo, 'commit', '-qm', f'update {filename}')
    return _git(repo, 'rev-parse', 'HEAD')


def _new_repo(path: Path) -> Path:
    path.mkdir(parents=True)
    _git(path, 'init', '-q', '-b', 'main')
    _git(path, 'config', 'user.name', 'Test')
    _git(path, 'config', 'user.email', 'test@example.org')
    return path


def _clone_at(source: Path, target: Path, commit: str) -> Path:
    _git(target.parent, 'clone', '-q', '--single-branch', '--no-checkout',
         str(source), str(target))
    _git(target, 'checkout', '-q', '--detach', commit)
    return target


def _finish_clone(repo: Path) -> None:
    """Match the archive builder's post-checkout reflog expiry/compaction."""
    _git(repo, 'reflog', 'expire', '--expire=now', '--expire-unreachable=now', '--all')
    _git(repo, 'gc', '--prune=now', '--quiet')


def _write_archive(directory: Path, name: str, path: Path, format: str) -> None:
    assert format == 'tar.gz'
    with tarfile.open(path, 'w:gz') as archive:
        archive.add(directory / name, arcname=name)


def test_head_only_clone_cleanup_drops_refs_not_origin(tmp_path, monkeypatch):
    """The ref-cleanup helper operates on a detached Git clone only."""
    from git_well.archive_source._repo import _drop_cloned_branch_refs

    class GitCommandError(Exception):
        pass

    class GitFacade:
        """Small GitPython-compatible facade to test without extra packages."""

        def __init__(self, path: Path):
            self.path = path

        def for_each_ref(self, *args: str) -> str:
            return _git(self.path, 'for-each-ref', *args)

        def update_ref(self, *args: str) -> str:
            return _git(self.path, 'update-ref', *args)

        def symbolic_ref(self, *args: str) -> str:
            proc = subprocess.run(
                ['git', '-C', str(self.path), 'symbolic-ref', *args],
                capture_output=True, text=True, check=False,
            )
            if proc.returncode:
                raise GitCommandError(proc.stderr)
            return proc.stdout.strip()

    monkeypatch.setitem(
        sys.modules, 'git', SimpleNamespace(GitCommandError=GitCommandError),
    )
    source = _new_repo(tmp_path / 'src')
    pinned = _commit(source, 'first.txt', 'first\n')
    _commit(source, 'second.txt', 'second\n')
    cloned = _clone_at(source, tmp_path / 'cloned', pinned)
    assert _git(cloned, 'for-each-ref', '--format=%(refname)')

    _drop_cloned_branch_refs(SimpleNamespace(git=GitFacade(cloned)))
    _finish_clone(cloned)
    assert _git(cloned, 'rev-parse', 'HEAD') == pinned
    assert _git(cloned, 'for-each-ref', '--format=%(refname)') == ''
    assert _git(cloned, 'remote', 'get-url', 'origin') == str(source)
    _git(cloned, 'fsck', '--full')


@pytest.mark.parametrize('history_blobs', ['full', 'sparse'])
@pytest.mark.parametrize('prune_target_refs', [False, True])
def test_patch_existing_base_with_moving_submodule_branch(
    tmp_path: Path, prune_target_refs: bool, history_blobs: str,
) -> None:
    """Advance a superproject but not its gitlink, with a moving submodule main.

    Legacy bases retain synthetic refs; `main` might point at a later commit
    than the exported HEAD. A patch must never copy that new ref without its
    objects, nor require a newly generated full archive base to recover.
    """
    sub_src = _new_repo(tmp_path / 'renderer-source')
    pinned_sub_head = _commit(sub_src, 'sprite.svg', 'first version\n')

    super_src = _new_repo(tmp_path / 'super-source')
    (super_src / '.gitmodules').write_text(
        '[submodule "tools/renderer"]\n'
        '\tpath = tools/renderer\n'
        f'\turl = {sub_src.as_uri()}\n',
        encoding='utf8',
    )
    _git(super_src, 'add', '.gitmodules')
    _git(super_src, 'update-index', '--add', '--cacheinfo',
         f'160000,{pinned_sub_head},tools/renderer')
    _git(super_src, 'commit', '-qm', 'pin renderer')
    base_super_head = _git(super_src, 'rev-parse', 'HEAD')

    base_root_name = 'super-source-base'
    base_root = _clone_at(super_src, tmp_path / base_root_name, base_super_head)
    (base_root / 'tools').mkdir(exist_ok=True)
    base_sub = _clone_at(sub_src, base_root / 'tools' / 'renderer', pinned_sub_head)
    _finish_clone(base_sub)
    _finish_clone(base_root)
    (base_root / 'GIT_WELL_ARCHIVE_INFO.txt').write_text(
        'Git Well Source Archive\n'
        f'Archive prefix: {base_root_name}\n'
        f'Superproject commit: {base_super_head}\n'
        f'Superproject short commit: {base_super_head[:12]}\n'
        'Superproject history: full\n'
        'Superproject branches: current HEAD history only\n'
        f'History blob retention: {history_blobs}\n',
        encoding='utf8',
    )
    base_archive = tmp_path / 'base.tar.gz'
    _write_archive(tmp_path, base_root_name, base_archive, 'tar.gz')

    # The source checkout moves but the parent continues to pin the old sha.
    new_sub_tip = _commit(sub_src, 'sprite.svg', 'second version\n')
    target_super_head = _commit(super_src, 'main.rs', 'root update\n')
    assert _git(super_src, 'rev-parse', 'HEAD:tools/renderer') == pinned_sub_head

    target_name = 'super-source-target'
    target_root = _clone_at(super_src, tmp_path / target_name, target_super_head)
    (target_root / 'tools').mkdir(exist_ok=True)
    target_sub = _clone_at(sub_src, target_root / 'tools' / 'renderer', pinned_sub_head)
    assert _git(target_sub, 'rev-parse', 'refs/heads/main') == new_sub_tip
    if prune_target_refs:
        # New HEAD-only archives will not preserve these implicit refs. Old
        # bases might still have them, so patching must remove them safely.
        for name in _git(
            target_sub, 'for-each-ref', '--format=%(refname)',
            'refs/heads', 'refs/remotes',
        ).splitlines():
            _git(target_sub, 'update-ref', '--no-deref', '-d', name)
    _finish_clone(target_sub)
    _finish_clone(target_root)

    context = SimpleNamespace(
        include_git_history=True,
        repo_root=super_src,
        head_sha=target_super_head,
        short_sha=target_super_head[:12],
        archive_root=target_root,
        archive_root_name=target_name,
        archive_path=tmp_path / 'update.tar.gz',
        repo_name='super-source',
        archive_format='tar.gz',
        normalized_depth=None,
        all_branches=False,
        branches=(),
        history_blobs=history_blobs,
        exclude_path_selectors=(),
        submodule_decisions=(
            SimpleNamespace(
                omitted=False,
                depth=25,
                info=SimpleNamespace(path='tools/renderer'),
            ),
        ),
        _log=lambda *args: None,
    )
    patch_archive = build_source_patch(
        context=context, patch=base_archive, write_archive=_write_archive,
    )
    assert patch_archive.is_file()
    # The self-check inside build_source_patch caught this bug before the fix.
    output = tmp_path / 'applied'
    patched_root = apply_source_patch(base_archive, patch_archive, output)
    patched_sub = patched_root / 'tools' / 'renderer'
    assert _git(patched_sub, 'rev-parse', 'HEAD') == pinned_sub_head
    assert (patched_sub / 'sprite.svg').read_text() == 'first version\n'
    refs = _git(patched_sub, 'for-each-ref', '--format=%(refname)')
    if prune_target_refs:
        assert refs == ''
    else:
        assert _git(patched_sub, 'rev-parse', 'refs/heads/main') == new_sub_tip
        assert _git(patched_sub, 'cat-file', '-t', new_sub_tip) == 'commit'
    _git(patched_sub, 'rev-list', '--all', 'HEAD')
    _git(patched_sub, 'fsck', '--full')
    _git(patched_root, 'fsck', '--full')


@pytest.mark.parametrize('history_blobs', ['full', 'sparse'])
def test_patch_transports_superproject_refs_outside_head(
    tmp_path: Path, history_blobs: str,
) -> None:
    """The residual overlay cannot install a ref without its object graph."""
    source = _new_repo(tmp_path / 'main-source')
    base_head = _commit(source, 'tracked.txt', 'base\n')
    base_name = 'main-base'
    base_root = _clone_at(source, tmp_path / base_name, base_head)
    _finish_clone(base_root)
    (base_root / 'GIT_WELL_ARCHIVE_INFO.txt').write_text(
        'Git Well Source Archive\n'
        f'Archive prefix: {base_name}\n'
        f'Superproject commit: {base_head}\n'
        f'Superproject short commit: {base_head[:12]}\n'
        'Superproject history: full\n'
        'Superproject branches: current HEAD history only\n'
        f'History blob retention: {history_blobs}\n',
        encoding='utf8',
    )
    base_archive = tmp_path / 'base.tar.gz'
    _write_archive(tmp_path, base_name, base_archive, 'tar.gz')

    target_head = _commit(source, 'tracked.txt', 'target\n')
    target_name = 'main-target'
    target = _clone_at(source, tmp_path / target_name, target_head)
    # Simulate an extra staged ref (e.g. from a historical archive or hook).
    _git(target, 'config', 'user.name', 'Test')
    _git(target, 'config', 'user.email', 'test@example.org')
    _git(target, 'checkout', '-qb', 'topic')
    topic_head = _commit(target, 'topic.txt', 'topic history\n')
    _git(target, 'checkout', '-q', '--detach', target_head)
    _finish_clone(target)

    context = SimpleNamespace(
        include_git_history=True,
        repo_root=source,
        head_sha=target_head,
        short_sha=target_head[:12],
        archive_root=target,
        archive_root_name=target_name,
        archive_path=tmp_path / 'update.tar.gz',
        repo_name='main',
        archive_format='tar.gz',
        normalized_depth=None,
        all_branches=False,
        branches=(),
        history_blobs=history_blobs,
        exclude_path_selectors=(),
        submodule_decisions=(),
        _log=lambda *args: None,
    )
    patch_archive = build_source_patch(
        context=context, patch=base_archive, write_archive=_write_archive,
    )
    applied = apply_source_patch(
        base_archive, patch_archive, tmp_path / 'applied',
    )
    assert _git(applied, 'rev-parse', 'HEAD') == target_head
    assert _git(applied, 'rev-parse', 'refs/heads/topic') == topic_head
    assert _git(applied, 'show', 'topic:topic.txt') == 'topic history'
    _git(applied, 'fsck', '--full')
