"""Worktree and Git-object pruning for source archives."""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, cast

from ._common import PromisorPruneResult, _Logger
from ._repo import _source_remote_urls

if TYPE_CHECKING:  # pragma: no cover
    import git


def _tracked_blob_paths(repo: 'git.Repo', treeish: str) -> list[str]:
    """Return tracked blob/symlink paths, excluding submodule gitlinks."""
    stdout = repo.git.ls_tree('-r', '-z', treeish)
    paths = []
    for record in stdout.split('\0'):
        if not record:
            continue
        try:
            header, path = record.split('\t', 1)
            mode, object_type, _sha = header.split(' ', 2)
        except ValueError as ex:
            raise RuntimeError(
                f'could not parse git ls-tree record: {record!r}'
            ) from ex
        if mode == '160000':
            continue
        if object_type == 'blob':
            paths.append(path)
    return paths


def _archive_path_selector_matches(path: str, selector: str) -> bool:
    """Match one archive-root-relative path against an exclusion selector."""
    import fnmatch

    selector = selector.rstrip('/')
    if not selector:
        return False
    if any(ch in selector for ch in '*?['):
        return fnmatch.fnmatchcase(path, selector)
    return path == selector or path.startswith(selector + '/')


def _sparse_literal_pattern(path: str) -> str:
    """Encode a repository-relative path as a literal sparse-checkout rule."""
    if '\n' in path or '\r' in path or '\x00' in path:
        raise ValueError(
            'cannot encode newline/NUL-containing Git path in sparse checkout'
        )
    escaped = []
    for char in path:
        if char in r'\\*?[' or char == ' ':
            escaped.append('\\')
        escaped.append(char)
    return '!/' + ''.join(escaped)


def _configure_sparse_worktree_exclusions(
    repo_root: Path, local_paths: list[str]
) -> None:
    """Hide exact tracked paths while preserving a clean, restorable checkout."""
    import git

    repo = git.Repo(repo_root, search_parent_directories=False)
    repo.git.sparse_checkout('init', '--no-cone')
    sparse_path = Path(repo.git_dir) / 'info' / 'sparse-checkout'
    rules = ['/*']
    rules.extend(_sparse_literal_pattern(path) for path in sorted(local_paths))
    sparse_path.write_text('\n'.join(rules) + '\n')
    repo.git.read_tree('-mu', 'HEAD')


def _remove_source_only_paths(repo_root: Path, local_paths: list[str]) -> None:
    """Remove selected files from a source-only staged repository tree."""
    for relpath in local_paths:
        path = repo_root.joinpath(*PurePosixPath(relpath).parts)
        try:
            path.unlink()
        except FileNotFoundError:
            continue
        parent = path.parent
        while parent != repo_root:
            try:
                parent.rmdir()
            except OSError:
                break
            parent = parent.parent


def _apply_worktree_path_exclusions(
    units: Iterable[tuple[str, 'git.Repo', str, Path, bool]],
    selectors: list[str],
    log: '_Logger',
) -> tuple[tuple[str, ...], tuple[str, ...], int]:
    """
    Omit selected tracked paths without rewriting any repository history.

    Args:
        units:
            Tuples of ``(archive_prefix, source_repo, treeish, staged_root,
            history_bearing)`` for the superproject and included submodules.
    """
    if not selectors:
        return (), (), 0

    selector_hits = {selector: False for selector in selectors}
    omitted_paths: list[str] = []
    omitted_bytes = 0

    for prefix, source_repo, treeish, staged_root, history_bearing in units:
        local_matches = []
        for local_path in _tracked_blob_paths(source_repo, treeish):
            archive_path = (
                PurePosixPath(prefix, local_path).as_posix()
                if prefix
                else PurePosixPath(local_path).as_posix()
            )
            matched = False
            for selector in selectors:
                if _archive_path_selector_matches(archive_path, selector):
                    selector_hits[selector] = True
                    matched = True
            if not matched:
                continue

            staged_path = staged_root.joinpath(
                *PurePosixPath(local_path).parts
            )
            if os.path.lexists(staged_path):
                try:
                    omitted_bytes += staged_path.lstat().st_size
                except OSError:
                    pass
                local_matches.append(local_path)
                omitted_paths.append(archive_path)

        if not local_matches:
            continue
        if history_bearing:
            _configure_sparse_worktree_exclusions(staged_root, local_matches)
        else:
            _remove_source_only_paths(staged_root, local_matches)

    unmatched = tuple(
        selector for selector, was_hit in selector_hits.items() if not was_hit
    )
    for selector in unmatched:
        log.warning(
            '[source-archive] WARNING: --exclude-path selector matched no '
            f'tracked path in the materialized archive: {selector}'
        )
    if omitted_paths:
        log(
            '[source-archive] worktree pruning: omitted '
            f'{len(omitted_paths)} tracked path(s), {omitted_bytes} raw bytes; '
            'Git history was not rewritten'
        )
    return tuple(sorted(omitted_paths)), unmatched, omitted_bytes


def _history_paths_matching_selectors(
    repo_root: Path,
    archive_prefix: str,
    selectors: list[str],
) -> list[str]:
    """Return repository-relative paths matching selectors anywhere in history."""
    if not selectors:
        return []
    proc = subprocess.run(
        [
            'git',
            'log',
            '--all',
            '--format=',
            '--name-only',
            '-z',
            '--no-renames',
        ],
        cwd=repo_root,
        env={**os.environ, 'GIT_NO_LAZY_FETCH': '1'},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode:
        error = proc.stderr.decode(errors='replace').strip()
        raise RuntimeError(
            f'could not enumerate historical paths for promisor pruning: {error}'
        )
    paths = set()
    for raw_path in proc.stdout.split(b'\0'):
        if not raw_path:
            continue
        local_path = raw_path.decode(errors='surrogateescape')
        archive_path = (
            PurePosixPath(archive_prefix, local_path).as_posix()
            if archive_prefix
            else PurePosixPath(local_path).as_posix()
        )
        if any(
            _archive_path_selector_matches(archive_path, selector)
            for selector in selectors
        ):
            paths.add(local_path)
    return sorted(paths)


def _write_sparse_filter_blob(repo_root: Path, local_paths: list[str]) -> tuple[str, str]:
    """Create a temporary sparse specification object for Git object filtering."""
    rules = ['/*']
    rules.extend(_sparse_literal_pattern(path) for path in sorted(local_paths))
    spec_text = '\n'.join(rules) + '\n'
    proc = subprocess.run(
        ['git', 'hash-object', '-w', '--stdin'],
        cwd=repo_root,
        input=spec_text.encode(errors='surrogateescape'),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode:
        error = proc.stderr.decode(errors='replace').strip()
        raise RuntimeError(f'could not create sparse object filter: {error}')
    return proc.stdout.decode().strip(), spec_text


def _filtered_blob_oids(repo_root: Path, sparse_oid: str) -> tuple[str, ...]:
    """Return blobs that a sparse object filter would intentionally omit."""
    proc = subprocess.run(
        [
            'git',
            'rev-list',
            '--objects',
            '--all',
            f'--filter=sparse:oid={sparse_oid}',
            '--filter-print-omitted',
        ],
        cwd=repo_root,
        env={**os.environ, 'GIT_NO_LAZY_FETCH': '1'},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode:
        error = proc.stderr.decode(errors='replace').strip()
        raise RuntimeError(f'could not resolve sparse object filter: {error}')
    oids = []
    for line in proc.stdout.decode().splitlines():
        if line.startswith('~'):
            oid = line[1:].strip()
            if oid:
                oids.append(oid)
    return tuple(sorted(set(oids)))


def _blob_sizes(repo_root: Path, oids: tuple[str, ...]) -> dict[str, int]:
    """Resolve object sizes in one batch before filtered objects are removed."""
    if not oids:
        return {}
    proc = subprocess.run(
        ['git', 'cat-file', '--batch-check=%(objectname) %(objecttype) %(objectsize)'],
        cwd=repo_root,
        input=('\n'.join(oids) + '\n').encode(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if proc.returncode:
        error = proc.stderr.decode(errors='replace').strip()
        raise RuntimeError(f'could not size filtered Git objects: {error}')
    sizes = {}
    for line in proc.stdout.decode().splitlines():
        oid, object_type, size_text = line.split()
        if object_type != 'blob':
            raise RuntimeError(
                f'sparse object filter unexpectedly selected {object_type} {oid}'
            )
        sizes[oid] = int(size_text)
    return sizes


def _cached_remote_contains_commit(
    source_repo: 'git.Repo', remote_name: str, commit: str
) -> bool:
    """Conservatively prove a commit is reachable from a cached remote ref."""
    refs = source_repo.git.for_each_ref(
        '--format=%(objectname)', f'refs/remotes/{remote_name}'
    ).splitlines()
    for ref_oid in refs:
        proc = subprocess.run(
            ['git', 'merge-base', '--is-ancestor', commit, ref_oid],
            cwd=cast(str, source_repo.working_tree_dir),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if proc.returncode == 0:
            return True
    return False


def _looks_like_local_remote_url(url: str) -> bool:
    """Return whether a Git URL is tied to a local filesystem path."""
    import re

    if url.startswith('file://'):
        return True
    if re.match(r'^[A-Za-z]:[\\/]', url):
        return True
    if '://' in url:
        return False
    if re.match(r'^(?:[^/@:]+@)?[^/:]+:.+', url):
        # SCP-like Git transport, e.g. git@example.com:owner/repo.git.
        return False
    # Absolute, ./../~/ relative, and plain relative paths are all local.
    return True


def _choose_promisor_remote_url(
    source_repo: 'git.Repo',
    commit: str,
    *,
    redact_local_paths: bool,
    force_local_source: bool,
) -> str:
    """Choose a remote that can credibly promise omitted source objects."""
    source_root = Path(cast(str, source_repo.working_tree_dir)).resolve()
    if not force_local_source:
        candidates = _source_remote_urls(source_repo)
        candidates.sort(key=lambda item: (item[0] != 'origin', item[0], item[1]))
        for remote_name, url in candidates:
            if redact_local_paths and _looks_like_local_remote_url(url):
                continue
            if _cached_remote_contains_commit(source_repo, remote_name, commit):
                return url
    if not redact_local_paths:
        # This is always a truthful promisor while the source checkout exists.
        # It also mirrors archive_source's normal non-redacted origin behavior.
        return os.fspath(source_root)
    raise RuntimeError(
        'history_blobs="sparse" needs a promisor remote for omitted blobs, '
        f'but no non-local configured remote is known to contain {commit[:12]}. '
        'Publish the commit, disable --redact-local-paths, or use '
        'history_blobs="full".'
    )


def _remove_loose_object(repo_root: Path, oid: str) -> None:
    git_dir_text = subprocess.run(
        ['git', 'rev-parse', '--git-dir'],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout.strip()
    git_dir = Path(git_dir_text)
    if not git_dir.is_absolute():
        git_dir = repo_root / git_dir
    (git_dir / 'objects' / oid[:2] / oid[2:]).unlink(missing_ok=True)


def _mark_reachable_packs_promisor(repo_root: Path) -> None:
    git_dir_text = subprocess.run(
        ['git', 'rev-parse', '--git-dir'],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout.strip()
    git_dir = Path(git_dir_text)
    if not git_dir.is_absolute():
        git_dir = repo_root / git_dir
    for pack_path in (git_dir / 'objects' / 'pack').glob('*.pack'):
        pack_path.with_suffix('.promisor').touch()


def _configure_promisor_remote(repo_root: Path, remote_url: str) -> str:
    """Install repository-local partial-clone metadata without fetching."""
    remote_name = 'git-well-promisor'
    subprocess.run(
        ['git', 'remote', 'remove', remote_name],
        cwd=repo_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    subprocess.run(
        ['git', 'remote', 'add', remote_name, remote_url],
        cwd=repo_root,
        check=True,
    )
    version_proc = subprocess.run(
        ['git', 'config', '--local', '--get', 'core.repositoryformatversion'],
        cwd=repo_root,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    try:
        repository_format = int(version_proc.stdout.strip())
    except ValueError:
        repository_format = 0
    if repository_format < 1:
        subprocess.run(
            ['git', 'config', '--local', 'core.repositoryformatversion', '1'],
            cwd=repo_root,
            check=True,
        )
    subprocess.run(
        ['git', 'config', '--local', 'extensions.partialClone', remote_name],
        cwd=repo_root,
        check=True,
    )
    subprocess.run(
        ['git', 'config', '--local', f'remote.{remote_name}.promisor', 'true'],
        cwd=repo_root,
        check=True,
    )
    return remote_name


def _prune_sparse_history_blobs(
    *,
    archive_prefix: str,
    source_repo: 'git.Repo',
    treeish: str,
    staged_root: Path,
    selectors: list[str],
    redact_local_paths: bool,
    force_local_source: bool,
    log: '_Logger',
) -> PromisorPruneResult | None:
    """Drop excluded historical blobs while preserving Git object identity."""
    history_paths = _history_paths_matching_selectors(
        staged_root, archive_prefix, selectors
    )
    if not history_paths:
        return None

    sparse_oid, _spec_text = _write_sparse_filter_blob(staged_root, history_paths)
    omitted_oids = _filtered_blob_oids(staged_root, sparse_oid)
    if not omitted_oids:
        _remove_loose_object(staged_root, sparse_oid)
        return None
    sizes = _blob_sizes(staged_root, omitted_oids)

    remote_url = _choose_promisor_remote_url(
        source_repo,
        treeish,
        redact_local_paths=redact_local_paths,
        force_local_source=force_local_source,
    )

    git_dir_text = subprocess.run(
        ['git', 'rev-parse', '--git-dir'],
        cwd=staged_root,
        text=True,
        stdout=subprocess.PIPE,
        check=True,
    ).stdout.strip()
    git_dir = Path(git_dir_text)
    if not git_dir.is_absolute():
        git_dir = staged_root / git_dir
    index_path = git_dir / 'index'

    with tempfile.TemporaryDirectory(prefix='git-well-promisor-') as temp_text:
        temp_root = Path(temp_text)
        held_index = temp_root / 'index'
        had_index = index_path.exists()
        old_bare_proc = subprocess.run(
            ['git', 'config', '--local', '--get', 'core.bare'],
            cwd=staged_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        old_bare = old_bare_proc.stdout.strip() if old_bare_proc.returncode == 0 else None
        try:
            if had_index:
                os.replace(index_path, held_index)
            subprocess.run(
                ['git', 'config', '--local', 'core.bare', 'true'],
                cwd=staged_root,
                check=True,
            )
            proc = subprocess.run(
                [
                    'git',
                    'repack',
                    '-a',
                    '-d',
                    '--no-write-bitmap-index',
                    f'--filter=sparse:oid={sparse_oid}',
                    f'--filter-to={temp_root / "filtered-pack"}',
                ],
                cwd=staged_root,
                env={**os.environ, 'GIT_NO_LAZY_FETCH': '1'},
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
            if proc.returncode:
                error = proc.stderr.decode(errors='replace').strip()
                raise RuntimeError(
                    'Git cannot repack sparse promisor history with this '
                    f'installation: {error}'
                )
        finally:
            try:
                if old_bare is None:
                    subprocess.run(
                        ['git', 'config', '--local', '--unset', 'core.bare'],
                        cwd=staged_root,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        check=False,
                    )
                else:
                    subprocess.run(
                        ['git', 'config', '--local', 'core.bare', old_bare],
                        cwd=staged_root,
                        check=True,
                    )
            finally:
                if had_index and held_index.exists():
                    os.replace(held_index, index_path)

    _remove_loose_object(staged_root, sparse_oid)
    # ``git repack`` filters packed reachable objects, but a duplicate loose
    # copy can survive repacking. Remove any such copies before validating
    # that every promised blob is genuinely absent from local storage.
    for oid in omitted_oids:
        _remove_loose_object(staged_root, oid)
    _mark_reachable_packs_promisor(staged_root)
    remote_name = _configure_promisor_remote(staged_root, remote_url)

    env = {**os.environ, 'GIT_NO_LAZY_FETCH': '1'}
    for oid in omitted_oids:
        probe = subprocess.run(
            ['git', 'cat-file', '-e', oid],
            cwd=staged_root,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        if probe.returncode == 0:
            raise RuntimeError(
                f'sparse promisor pruning retained excluded blob {oid}'
            )
    fsck = subprocess.run(
        ['git', 'fsck', '--full'],
        cwd=staged_root,
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if fsck.returncode:
        error = fsck.stderr.decode(errors='replace').strip()
        raise RuntimeError(
            f'promisor repository failed git fsck after pruning: {error}'
        )

    raw_bytes = sum(sizes.values())
    label = archive_prefix or '.'
    log(
        '[source-archive] promisor history pruning: '
        f'{label}: omitted {len(omitted_oids)} blob(s), {raw_bytes} raw bytes '
        f'across {len(history_paths)} path(s)'
    )
    return PromisorPruneResult(
        archive_prefix=archive_prefix,
        matched_history_paths=tuple(history_paths),
        omitted_blob_oids=omitted_oids,
        omitted_blob_bytes=raw_bytes,
        promisor_remote_name=remote_name,
        promisor_remote_url=remote_url,
    )


def _apply_promisor_history_pruning(
    units: Iterable[tuple[str, 'git.Repo', str, Path, bool]],
    selectors: list[str],
    *,
    redact_local_paths: bool,
    all_branches: bool,
    log: '_Logger',
) -> tuple[PromisorPruneResult, ...]:
    """Apply sparse partial-clone semantics to history-bearing archive units."""
    results: list[PromisorPruneResult] = []
    for prefix, source_repo, treeish, staged_root, history_bearing in units:
        if not history_bearing:
            continue
        result = _prune_sparse_history_blobs(
            archive_prefix=prefix,
            source_repo=source_repo,
            treeish=treeish,
            staged_root=staged_root,
            selectors=selectors,
            redact_local_paths=redact_local_paths,
            force_local_source=bool(all_branches and not prefix),
            log=log,
        )
        if result is not None:
            results.append(result)
    return tuple(results)
