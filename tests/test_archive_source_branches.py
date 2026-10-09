"""Branch-selection integration tests for ``git archive-source``."""

from __future__ import annotations

import json
import subprocess
import tarfile
from pathlib import Path

import pytest


def _git(repo: Path, *args: str, check: bool = True) -> str:
    proc = subprocess.run(
        ['git', '-C', str(repo), *args],
        check=check,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    return proc.stdout.strip()


def _repo(root: Path) -> Path:
    repo = root / 'source'
    repo.mkdir()
    _git(repo, 'init', '-q')
    _git(repo, 'config', 'user.name', 'Test User')
    _git(repo, 'config', 'user.email', 'test@example.com')
    _commit(repo, 'initial.txt', 'initial')
    _git(repo, 'branch', '-M', 'main')
    return repo


def _commit(repo: Path, name: str, content: str) -> str:
    (repo / name).write_text(content + '\n', encoding='utf8')
    _git(repo, 'add', name)
    _git(repo, 'commit', '-qm', f'update {name}')
    return _git(repo, 'rev-parse', 'HEAD')


def _unpack(archive: Path, destination: Path) -> Path:
    destination.mkdir()
    with tarfile.open(archive, 'r:gz') as tar:
        tar.extractall(destination)
    roots = [path for path in destination.iterdir() if path.is_dir()]
    assert len(roots) == 1
    return roots[0]


def _refs(repo: Path) -> set[str]:
    return set(
        _git(repo, 'for-each-ref', '--format=%(refname)',
             'refs/heads', 'refs/remotes').splitlines()
    )


def _selected_repo(root: Path) -> tuple[Path, str, str]:
    repo = _repo(root)
    head_sha = _git(repo, 'rev-parse', 'HEAD')
    _git(repo, 'checkout', '-qb', 'keep')
    _commit(repo, 'keep.txt', 'keep branch')
    _git(repo, 'checkout', '-q', 'main')
    _git(repo, 'checkout', '-qb', 'discard')
    _commit(repo, 'discard.txt', 'discard branch')
    _git(repo, 'checkout', '-q', 'main')
    _git(repo, 'checkout', '-qb', 'temporary')
    remote_sha = _commit(repo, 'remote.txt', 'cached remote branch')
    _git(repo, 'update-ref', 'refs/remotes/contributor/pr/123', remote_sha)
    _git(repo, 'checkout', '-q', 'main')
    _git(repo, 'branch', '-D', 'temporary')
    return repo, head_sha, remote_sha


def test_archive_source_branches_exact_cli_and_python(tmp_path):
    from git_well.archive_source import archive_source
    from git_well.git_archive_source import ArchiveSourceCLI

    repo, head_sha, remote_sha = _selected_repo(tmp_path)
    _git(repo, 'remote', 'add', 'contributor', str(tmp_path / 'missing-remote'))

    config = ArchiveSourceCLI.cli(
        argv=['--branches', 'keep', 'contributor/pr/123',
              '--output', 'selected.tar.gz'], strict=True
    )
    assert config.branches == ['keep', 'contributor/pr/123']

    archive = ArchiveSourceCLI.main(
        argv=[str(repo), '--branches', 'keep', 'contributor/pr/123',
              '--output', str(tmp_path / 'selected.tar.gz')]
    )
    unpacked = _unpack(archive, tmp_path / 'extracted')
    assert _refs(unpacked) == {
        'refs/heads/keep', 'refs/remotes/contributor/pr/123'
    }
    assert _git(unpacked, 'rev-parse', 'HEAD') == head_sha
    assert _git(unpacked, 'symbolic-ref', '-q', 'HEAD', check=False) == ''
    assert _git(unpacked, 'rev-parse', 'refs/remotes/contributor/pr/123') == remote_sha
    assert _git(unpacked, 'show', 'keep:keep.txt') == 'keep branch'
    assert _git(unpacked, 'show', 'contributor/pr/123:remote.txt') == 'cached remote branch'
    _git(unpacked, 'fsck', '--full')

    manifest = (unpacked / 'GIT_WELL_ARCHIVE_INFO.txt').read_text()
    prefix = 'Selected superproject branches JSON: '
    selected = next(line[len(prefix):] for line in manifest.splitlines()
                    if line.startswith(prefix))
    assert json.loads(selected) == [
        'refs/heads/keep', 'refs/remotes/contributor/pr/123'
    ]

    # The Python API accepts a single branch name without requiring a list.
    single = archive_source(
        repo_dpath=repo, branches='keep',
        output=tmp_path / 'single.tar.gz', verbose=0,
    )
    assert _refs(_unpack(single, tmp_path / 'single-unpacked')) == {'refs/heads/keep'}


def test_archive_source_branches_validation_and_ambiguity(tmp_path):
    from git_well.archive_source import archive_source

    repo = _repo(tmp_path)
    _git(repo, 'checkout', '-qb', 'origin/topic')
    local_tip = _commit(repo, 'local.txt', 'local tip')
    _git(repo, 'checkout', '-q', 'main')
    remote_tip = _commit(repo, 'remote.txt', 'remote tip')
    _git(repo, 'update-ref', 'refs/remotes/origin/topic', remote_tip)

    with pytest.raises(ValueError, match='ambiguous branch'):
        archive_source(repo_dpath=repo, branches=['origin/topic'], verbose=0)
    with pytest.raises(ValueError, match='not found'):
        archive_source(repo_dpath=repo, branches=['missing'], verbose=0)
    with pytest.raises(ValueError, match='at least one'):
        archive_source(repo_dpath=repo, branches=[], verbose=0)
    with pytest.raises(ValueError, match='mutually exclusive'):
        archive_source(repo_dpath=repo, branches=['main'], all_branches=True, verbose=0)
    with pytest.raises(ValueError, match='requires Git history'):
        archive_source(repo_dpath=repo, branches=['main'], depth=0, verbose=0)

    for branch, expected_ref, expected_tip in [
        ('refs/heads/origin/topic', 'refs/heads/origin/topic', local_tip),
        ('refs/remotes/origin/topic', 'refs/remotes/origin/topic', remote_tip),
    ]:
        out = tmp_path / (branch.replace('/', '_') + '.tar.gz')
        archive = archive_source(
            repo_dpath=repo, output=out, branches=[branch], verbose=0,
        )
        unpacked = _unpack(archive, tmp_path / (out.stem + '-unpacked'))
        assert _refs(unpacked) == {expected_ref}
        assert _git(unpacked, 'rev-parse', expected_ref) == expected_tip
        _git(unpacked, 'fsck', '--full')


def test_archive_source_branches_shallow_preserves_each_tip(tmp_path):
    from git_well.archive_source import archive_source

    repo = _repo(tmp_path)
    for i in range(3):
        _commit(repo, 'main.txt', f'main {i}')
    head_sha = _git(repo, 'rev-parse', 'HEAD')
    _git(repo, 'checkout', '-qb', 'topic', 'HEAD~2')
    for i in range(4):
        _commit(repo, 'topic.txt', f'topic {i}')
    _git(repo, 'checkout', '-q', 'main')

    archive = archive_source(
        repo_dpath=repo, output=tmp_path / 'shallow.tar.gz',
        branches=['topic'], depth=2, verbose=0,
    )
    unpacked = _unpack(archive, tmp_path / 'shallow-unpacked')
    assert _refs(unpacked) == {'refs/heads/topic'}
    assert _git(unpacked, 'rev-parse', 'HEAD') == head_sha
    assert _git(unpacked, 'rev-list', '--count', 'HEAD') == '2'
    assert _git(unpacked, 'rev-list', '--count', 'topic') == '2'
    _git(unpacked, 'fsck', '--full')


def test_archive_source_branches_patch_roundtrip_with_detached_head(tmp_path):
    from git_well.archive_source import apply_source_patch, archive_source
    from git_well.archive_source.patch import inspect_source_archive
    from git_well.archive_source.patch_apply import SourcePatchError

    repo = _repo(tmp_path)
    _git(repo, 'checkout', '-qb', 'topic')
    _commit(repo, 'topic.txt', 'topic base')
    _git(repo, 'checkout', '-q', 'main')
    base = archive_source(
        repo_dpath=repo, output=tmp_path / 'base.tar.gz',
        branches=['topic'], verbose=0,
    )
    assert inspect_source_archive(base).branches == ('refs/heads/topic',)

    # Advance both a selected branch and the excluded branch containing HEAD.
    _git(repo, 'checkout', '-q', 'topic')
    topic_tip = _commit(repo, 'topic.txt', 'topic next')
    _git(repo, 'checkout', '-q', 'main')
    head_tip = _commit(repo, 'main.txt', 'main next')

    with pytest.raises(SourcePatchError, match='branch-selection policy'):
        archive_source(
            repo_dpath=repo, output=tmp_path / 'wrong-policy.tar.gz',
            branches=['main'], patch=base, verbose=0,
        )

    patch = archive_source(
        repo_dpath=repo, output=tmp_path / 'incremental.tar.gz',
        branches=['topic'], patch=base, verbose=0,
    )
    patched = apply_source_patch(base, patch, tmp_path / 'applied')
    assert _refs(patched) == {'refs/heads/topic'}
    assert _git(patched, 'rev-parse', 'HEAD') == head_tip
    assert _git(patched, 'rev-parse', 'topic') == topic_tip
    assert _git(patched, 'show', 'HEAD:main.txt') == 'main next'
    assert _git(patched, 'show', 'topic:topic.txt') == 'topic next'
    _git(patched, 'fsck', '--full')
