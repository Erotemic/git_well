"""Git repository operations used while staging source archives."""

from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Iterable
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, cast

from ._common import BranchRefInventory, PathLike, SubmoduleStatus, _Logger

if TYPE_CHECKING:  # pragma: no cover
    import git

def _coerce_repo(repo_dpath: PathLike) -> 'git.Repo':
    import git

    path = Path(repo_dpath).expanduser()
    try:
        repo = git.Repo(path, search_parent_directories=True)
    except (git.InvalidGitRepositoryError, git.NoSuchPathError) as ex:
        raise RuntimeError(f'not inside a Git repository: {path}') from ex
    if repo.working_tree_dir is None:
        raise RuntimeError(f'not a non-bare Git working tree: {path}')
    return repo


def _logical_cwd() -> Path:
    """Return the shell's working directory without dereferencing symlinks."""
    physical_cwd = Path.cwd()
    pwd = os.environ.get('PWD')
    if pwd:
        logical_cwd = Path(pwd).expanduser()
        if logical_cwd.is_absolute():
            try:
                if logical_cwd.resolve() == physical_cwd:
                    return logical_cwd
            except OSError:
                pass
    return physical_cwd


def _logical_repo_root(repo_dpath: PathLike, repo_root: Path) -> Path:
    """
    Recover the lexical repository root used to enter the working tree.

    GitPython reports ``working_tree_dir`` as a canonical path, which is right
    for repository operations but loses a symlink basename chosen by the user.
    Walk upward from the unresolved invocation path and return the first path
    that resolves to the canonical repository root.
    """
    invocation_path = Path(repo_dpath).expanduser()
    if not invocation_path.is_absolute():
        invocation_path = _logical_cwd() / invocation_path
    invocation_path = Path(os.path.normpath(os.fspath(invocation_path)))

    for candidate in (invocation_path, *invocation_path.parents):
        try:
            if candidate.resolve() == repo_root:
                return candidate
        except OSError:
            continue
    return repo_root


def _assert_has_head(repo: 'git.Repo') -> None:
    try:
        _ = repo.head.commit.hexsha
    except ValueError as ex:
        raise RuntimeError('repository has no HEAD commit to archive') from ex

def _submodule_status(repo: 'git.Repo') -> list[SubmoduleStatus]:
    infos: list[SubmoduleStatus] = []
    repo_root = Path(cast(str, repo.working_tree_dir)).resolve()
    _collect_committed_submodules(
        repo=repo,
        treeish='HEAD',
        superproject_root=repo_root,
        prefix='',
        infos=infos,
    )
    return infos


def _collect_committed_submodules(
    repo: 'git.Repo',
    treeish: str,
    superproject_root: Path,
    prefix: str,
    infos: list[SubmoduleStatus],
) -> None:
    """Recursively enumerate gitlinks from committed trees, not the index."""
    gitlinks = _committed_gitlinks(repo, treeish)
    if not gitlinks:
        return

    mapped_paths = _committed_gitmodule_paths(repo, treeish)
    missing_mappings = [
        path for _sha, path in gitlinks if path not in mapped_paths
    ]
    if missing_mappings:
        rendered = ', '.join(repr(path) for path in missing_mappings)
        raise RuntimeError(
            f'committed gitlink has no .gitmodules path mapping: {rendered}'
        )

    for sha, relative_path in gitlinks:
        full_path = (
            PurePosixPath(prefix, relative_path).as_posix()
            if prefix
            else PurePosixPath(relative_path).as_posix()
        )
        local_dpath = superproject_root.joinpath(
            *PurePosixPath(full_path).parts
        )
        sub_repo = _open_exact_repo(local_dpath)
        if sub_repo is None:
            status = '-'
        else:
            try:
                current_sha = sub_repo.head.commit.hexsha
            except ValueError:
                status = '-'
            else:
                status = ' ' if current_sha == sha else '+'

        line = f'{status}{sha} {full_path}'
        infos.append(
            SubmoduleStatus(
                status=status,
                sha=sha,
                path=full_path,
                line=line,
            )
        )

        if sub_repo is not None and _repo_has_commit(sub_repo, sha):
            _collect_committed_submodules(
                repo=sub_repo,
                treeish=sha,
                superproject_root=superproject_root,
                prefix=full_path,
                infos=infos,
            )


def _committed_gitlinks(
    repo: 'git.Repo', treeish: str
) -> list[tuple[str, str]]:
    """Return ``(sha, path)`` gitlinks from one committed tree."""
    stdout = repo.git.ls_tree('-r', '-z', treeish)
    gitlinks = []
    for record in stdout.split('\0'):
        if not record:
            continue
        try:
            header, path = record.split('\t', 1)
            mode, object_type, sha = header.split(' ', 2)
        except ValueError as ex:
            raise RuntimeError(
                f'could not parse git ls-tree record: {record!r}'
            ) from ex
        if mode == '160000':
            if object_type != 'commit':
                raise RuntimeError(
                    'invalid gitlink tree entry: '
                    f'{mode} {object_type} {sha} {path!r}'
                )
            gitlinks.append((sha, path))
    return gitlinks


def _committed_gitmodule_paths(repo: 'git.Repo', treeish: str) -> set[str]:
    """Read submodule path mappings from the committed ``.gitmodules``."""
    import git

    blob = f'{treeish}:.gitmodules'
    try:
        repo.git.show(blob)
    except git.GitCommandError:
        return set()

    try:
        stdout = repo.git.config(
            '-z',
            f'--blob={blob}',
            '--get-regexp',
            r'^submodule\..*\.path$',
        )
    except git.GitCommandError as ex:
        raise RuntimeError(
            f'could not parse committed .gitmodules at {treeish}'
        ) from ex

    paths = set()
    for record in stdout.split('\0'):
        if not record:
            continue
        try:
            _key, path = record.split('\n', 1)
        except ValueError as ex:
            raise RuntimeError(
                f'could not parse committed .gitmodules record: {record!r}'
            ) from ex
        paths.add(path)
    return paths


def _open_exact_repo(path: Path) -> 'git.Repo | None':
    """Open a repository rooted at ``path`` without climbing to a parent."""
    import git

    try:
        repo = git.Repo(path, search_parent_directories=False)
    except (git.InvalidGitRepositoryError, git.NoSuchPathError):
        return None
    if repo.working_tree_dir is None:
        return None
    return repo


def _repo_has_commit(repo: 'git.Repo', commit: str) -> bool:
    import git

    try:
        repo.git.cat_file('-e', f'{commit}^{{commit}}')
    except git.GitCommandError:
        return False
    return True


def _branch_ref_inventory(repo: 'git.Repo') -> BranchRefInventory:
    """Return branch-like refs already present in ``repo`` without fetching."""

    def _names(namespace: str) -> tuple[str, ...]:
        stdout = repo.git.for_each_ref('--format=%(refname:strip=2)', namespace)
        return tuple(sorted(line for line in stdout.splitlines() if line))

    return BranchRefInventory(
        local_branches=_names('refs/heads'),
        remote_tracking_branches=_names('refs/remotes'),
    )


def _clone_options_for_depth(clone_depth: int | None) -> list[str]:
    options = ['--quiet', '--no-local', '--single-branch', '--no-checkout']
    if clone_depth is not None:
        options += ['--depth', str(clone_depth)]
    return options


def _configure_archive_git_portability(repo: 'git.Repo') -> None:
    """Make an archived Git checkout portable to deep Windows paths.

    Git for Windows keeps long-path support disabled by default. Source archives
    commonly gain a long extraction prefix before reaching
    ``.git/objects/pack/pack-<hash>.pack``, so a repository that is valid while
    staged can become unreadable after extraction even though its refs remain
    accessible. Keep this repository-local: the archive carries the setting and
    the user's global Git configuration is never changed.
    """
    repo.git.config('--local', 'core.longpaths', 'true')


def _clone_committed_checkout(
    src: 'git.Repo',
    dst: PathLike,
    commit: str,
    label: str,
    clone_depth: int | None,
    branch_refs: BranchRefInventory | None,
    redact_local_paths: bool,
    log: '_Logger',
) -> None:
    import shutil

    import git

    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst)

    src_root = Path(cast(str, src.working_tree_dir)).resolve()
    clone_command = [
        'git',
        'clone',
        *_clone_options_for_depth(clone_depth),
        str(src_root),
        str(dst),
    ]
    git.Git(str(src_root.parent)).execute(clone_command)
    cloned = git.Repo(dst)
    _configure_archive_git_portability(cloned)
    _checkout_commit(
        cloned,
        commit,
        label,
        clone_depth,
        log,
        source_repo=src,
    )

    # Compact only the initial clone. Cached branch refs and any additional
    # shallow boundaries are final archive state, so install them after Git
    # maintenance. In particular, this prevents ``git gc`` / ``pack-refs``
    # from changing the representation of refs that archive_source promises
    # to preserve.
    try:
        cloned.git.reflog(
            'expire', '--expire=now', '--expire-unreachable=now', '--all'
        )
    except git.GitCommandError:
        pass
    try:
        cloned.git.gc('--prune=now', '--quiet')
    except git.GitCommandError:
        pass

    if branch_refs is not None:
        _copy_cached_branch_refs(
            src=src,
            cloned=cloned,
            clone_depth=clone_depth,
            branch_refs=branch_refs,
            log=log,
        )

    if redact_local_paths:
        _remove_remote_configs_preserving_refs(cloned)

    if branch_refs is not None:
        _verify_cached_branch_refs(
            src=src,
            cloned=cloned,
            clone_depth=clone_depth,
            branch_refs=branch_refs,
        )


def _copy_cached_branch_refs(
    src: 'git.Repo',
    cloned: 'git.Repo',
    clone_depth: int | None,
    branch_refs: BranchRefInventory,
    log: '_Logger',
) -> None:
    """Copy cached branch refs by importing local objects, never by fetching."""
    import git

    src_root = Path(cast(str, src.working_tree_dir)).resolve()

    # The initial clone creates synthetic ``origin/*`` refs for the local
    # source repository. Remove that generated remote before copying the
    # source repository's actual cached remote-tracking refs into place.
    if 'origin' in [remote.name for remote in cloned.remotes]:
        cloned.git.remote('remove', 'origin')
        # ``git remote remove`` deletes the generated ``origin/<branch>`` ref
        # but can leave ``refs/remotes/origin/HEAD`` behind as a dangling
        # symbolic ref.  That renders an otherwise valid archive repository
        # invalid to ``git fsck`` (the symref resolves to the zero OID).
        # Remove only the synthetic symref here; if the source repository has
        # a real cached ``origin/HEAD`` entry, the ref-copy loop below recreates
        # it from the source inventory.
        try:
            cloned.git.symbolic_ref('--delete', 'refs/remotes/origin/HEAD')
        except git.GitCommandError:
            pass

    # Local cached refs are local state, so keep them entirely out of Git's
    # transport layer. This uses the same direct object-database import as
    # unadvertised submodule commits; no URL/refspec parsing is involved.
    source_to_dest = [
        (f'refs/heads/{name}', f'refs/heads/{name}')
        for name in branch_refs.local_branches
    ]
    source_to_dest.extend(
        (f'refs/remotes/{name}', f'refs/remotes/{name}')
        for name in branch_refs.remote_tracking_branches
    )
    ref_targets = [
        (dest_ref, src.git.rev_parse('--verify', source_ref).strip())
        for source_ref, dest_ref in source_to_dest
    ]
    if ref_targets:
        _import_local_history_from_object_database(
            repo=cloned,
            source_repo=src,
            commits=(oid for _dest_ref, oid in ref_targets),
            clone_depth=clone_depth,
        )
        for dest_ref, oid in ref_targets:
            cloned.git.update_ref(dest_ref, oid)

    cloned.git.remote('add', 'origin', str(src_root))
    log(
        '[source-archive] copied locally cached branch refs: '
        f'{len(branch_refs.local_branches)} local, '
        f'{len(branch_refs.remote_tracking_branches)} remote-tracking'
    )


def _verify_cached_branch_refs(
    src: 'git.Repo',
    cloned: 'git.Repo',
    clone_depth: int | None,
    branch_refs: BranchRefInventory,
) -> None:
    """Verify the exact cached-ref contract before archive serialization."""
    import git

    source_to_dest = [
        (f'refs/heads/{name}', f'refs/heads/{name}')
        for name in branch_refs.local_branches
    ]
    source_to_dest.extend(
        (f'refs/remotes/{name}', f'refs/remotes/{name}')
        for name in branch_refs.remote_tracking_branches
    )
    for source_ref, dest_ref in source_to_dest:
        expected = src.git.rev_parse('--verify', source_ref).strip()
        try:
            actual = cloned.git.rev_parse('--verify', dest_ref).strip()
        except git.GitCommandError as ex:
            raise RuntimeError(
                f'archive checkout lost cached ref {dest_ref}'
            ) from ex
        if actual != expected:
            raise RuntimeError(
                f'archive checkout cached ref mismatch for {dest_ref}: '
                f'{actual} != {expected}'
            )
        if clone_depth is not None:
            try:
                cloned.git.rev_list('--count', dest_ref)
            except git.GitCommandError as ex:
                raise RuntimeError(
                    f'archive checkout cannot traverse shallow ref {dest_ref}'
                ) from ex


def _remove_remote_configs_preserving_refs(repo: 'git.Repo') -> None:
    """Remove local fetch paths without deleting remote-tracking refs."""
    import git

    for remote in list(repo.remotes):
        try:
            repo.git.config('--remove-section', f'remote.{remote.name}')
        except git.GitCommandError:
            pass
    git_dir = Path(repo.git_dir)
    (git_dir / 'FETCH_HEAD').unlink(missing_ok=True)


def _checkout_commit(
    repo: 'git.Repo',
    commit: str,
    label: str,
    clone_depth: int | None,
    log: '_Logger',
    source_repo: 'git.Repo',
) -> None:
    import git

    try:
        repo.git.checkout('-q', '--detach', commit)
        return
    except git.GitCommandError:
        log(
            f'[source-archive] checkout of {label} failed after clone; '
            'recovering exact commit'
        )

    recovery_errors: list[str] = []
    source_has_commit = _repo_has_commit(source_repo, commit)

    if source_has_commit:
        try:
            log(
                f'[source-archive] recovering {label} commit {commit[:12]} '
                'from local object database'
            )
            _import_local_history_from_object_database(
                repo=repo,
                source_repo=source_repo,
                commits=(commit,),
                clone_depth=clone_depth,
            )
            repo.git.checkout('-q', '--detach', commit)
            return
        except (git.GitCommandError, RuntimeError):
            recovery_errors.append('local object database recovery failed')

    remote_names = []
    for remote_name, remote_url in _source_remote_urls(source_repo):
        remote_names.append(remote_name)
        try:
            log(
                f'[source-archive] recovering {label} commit {commit[:12]} '
                f'from source remote {remote_name}'
            )
            _fetch_exact_commit(
                repo=repo,
                source=remote_url,
                commit=commit,
                clone_depth=clone_depth,
            )
            repo.git.checkout('-q', '--detach', commit)
            return
        except git.GitCommandError:
            recovery_errors.append(
                f'source remote {remote_name} did not provide the commit'
            )

    source_head = source_repo.head.commit.hexsha
    tried = ', '.join(remote_names) if remote_names else '(none configured)'
    local_state = 'present but recovery failed' if source_has_commit else 'absent'
    details = '\n'.join(f'  - {item}' for item in recovery_errors)
    if details:
        details = '\nRecovery failures:\n' + details
    raise RuntimeError(
        f'cannot materialize {label} commit {commit}; source checkout HEAD is '
        f'{source_head}. Required commit in local object database: '
        f'{local_state}. Configured source remotes tried: {tried}. The '
        'committed gitlink cannot be reconstructed; publish the referenced '
        'commit, update the superproject gitlink, or explicitly exclude the '
        f'submodule from the archive.{details}'
    )


def _fetch_exact_commit(
    repo: 'git.Repo',
    source: str,
    commit: str,
    clone_depth: int | None,
) -> None:
    """Fetch one exact commit from a source that is willing to advertise it."""
    fetch_args = ['--quiet', '--no-auto-maintenance']
    if clone_depth is not None:
        fetch_args += ['--depth', str(clone_depth)]
    repo.git.fetch(*fetch_args, source, commit)


def _import_local_history_from_object_database(
    repo: 'git.Repo',
    source_repo: 'git.Repo',
    commits: Iterable[str],
    clone_depth: int | None,
) -> None:
    """
    Import locally present commit histories without using Git transport.

    The source repository already owns the required objects. Treating local
    state as a fetch remote adds avoidable failure surfaces: ref advertisement,
    temporary refs, URL/refspec parsing, alternates syntax, and platform path
    rules. Instead, resolve each requested history slice in the source object
    database, union the object set, and stream one pack directly into the
    destination object database.

    This path has no shell, no remote, no URL, and no filesystem path embedded
    in a Git protocol. Every process boundary is a binary pipe.
    """
    commit_list = tuple(dict.fromkeys(commits))
    if not commit_list:
        return

    source_root_text = cast(str | None, source_repo.working_tree_dir)
    destination_root_text = cast(str | None, repo.working_tree_dir)
    if source_root_text is None or destination_root_text is None:
        raise RuntimeError('local object recovery requires working-tree repositories')
    source_root = Path(source_root_text).resolve()
    destination_root = Path(destination_root_text).resolve()

    if clone_depth is None:
        selected_commits: tuple[str, ...] | None = None
        shallow_boundaries: tuple[str, ...] = ()
    else:
        selected: list[str] = []
        selected_seen: set[str] = set()
        boundaries: list[str] = []
        boundary_seen: set[str] = set()
        for commit in commit_list:
            commit_slice, commit_boundaries = _local_history_slice(
                source_repo,
                commit,
                clone_depth,
            )
            assert commit_slice is not None
            for oid in commit_slice:
                if oid not in selected_seen:
                    selected_seen.add(oid)
                    selected.append(oid)
            for oid in commit_boundaries:
                if oid not in boundary_seen:
                    boundary_seen.add(oid)
                    boundaries.append(oid)
        selected_commits = tuple(selected)
        shallow_boundaries = tuple(boundaries)

    rev_args = [
        'git',
        'rev-list',
        '--objects',
        '--no-object-names',
    ]
    rev_input: bytes | None
    if selected_commits is None:
        rev_args.extend(commit_list)
        rev_input = None
    else:
        rev_args.extend(['--no-walk', '--stdin'])
        rev_input = ('\n'.join(selected_commits) + '\n').encode()

    with tempfile.TemporaryFile() as rev_stderr_file, tempfile.TemporaryFile() as pack_stderr_file:
        rev_proc = subprocess.Popen(
            rev_args,
            cwd=source_root,
            stdin=subprocess.PIPE if rev_input is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=rev_stderr_file,
        )
        assert rev_proc.stdout is not None
        pack_proc = subprocess.Popen(
            ['git', 'pack-objects', '--stdout'],
            cwd=source_root,
            stdin=rev_proc.stdout,
            stdout=subprocess.PIPE,
            stderr=pack_stderr_file,
        )
        rev_proc.stdout.close()
        assert pack_proc.stdout is not None
        index_proc = subprocess.Popen(
            ['git', 'index-pack', '--stdin'],
            cwd=destination_root,
            stdin=pack_proc.stdout,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        pack_proc.stdout.close()

        if rev_input is not None:
            assert rev_proc.stdin is not None
            try:
                rev_proc.stdin.write(rev_input)
            except BrokenPipeError:
                # Preserve the real rev-list diagnostic below.
                pass
            finally:
                rev_proc.stdin.close()

        _index_stdout, index_stderr = index_proc.communicate()
        pack_returncode = pack_proc.wait()
        rev_returncode = rev_proc.wait()

        if rev_returncode or pack_returncode or index_proc.returncode:
            rev_stderr_file.seek(0)
            pack_stderr_file.seek(0)
            rev_stderr = rev_stderr_file.read().decode(errors='replace').strip()
            pack_stderr = pack_stderr_file.read().decode(errors='replace').strip()
            index_error = (index_stderr or b'').decode(errors='replace').strip()
            raise RuntimeError(
                'local Git object transfer failed: '
                f'rev-list={rev_returncode} {rev_stderr!r}; '
                f'pack-objects={pack_returncode} {pack_stderr!r}; '
                f'index-pack={index_proc.returncode} {index_error!r}'
            )

    missing_commits = [
        commit for commit in commit_list if not _repo_has_commit(repo, commit)
    ]
    if missing_commits:
        raise RuntimeError(
            'local Git object transfer completed without importing commits: '
            f'{missing_commits}'
        )
    if shallow_boundaries:
        _record_shallow_boundaries(repo, shallow_boundaries)


def _local_history_slice(
    source_repo: 'git.Repo',
    commit: str,
    clone_depth: int | None,
) -> tuple[tuple[str, ...] | None, tuple[str, ...]]:
    """Resolve exact commit generations for local object import.

    ``None`` means full reachable history.  A finite depth is computed by
    parent generations rather than by command-output count, so merge histories
    retain the same depth interpretation as a shallow clone.
    """
    if clone_depth is None:
        return None, ()
    if clone_depth <= 0:
        raise ValueError(f'clone_depth must be positive or None, got {clone_depth!r}')

    selected: list[str] = []
    selected_set: set[str] = set()
    parent_map: dict[str, tuple[str, ...]] = {}
    frontier = [commit]
    for _generation in range(clone_depth):
        current: list[str] = []
        for oid in frontier:
            if oid not in selected_set:
                selected_set.add(oid)
                selected.append(oid)
                current.append(oid)
        if not current:
            break

        source_root = cast(str | None, source_repo.working_tree_dir)
        if source_root is None:
            raise RuntimeError('local history slicing requires a working tree')
        parent_input = ('\n'.join(current) + '\n').encode('ascii')
        parent_proc = subprocess.run(
            ['git', 'rev-list', '--parents', '--no-walk', '--stdin'],
            cwd=source_root,
            input=parent_input,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        if parent_proc.returncode:
            error = parent_proc.stderr.decode(errors='replace').strip()
            raise RuntimeError(
                f'could not resolve local commit parents: {error}'
            )
        text = parent_proc.stdout.decode()
        next_frontier: list[str] = []
        for line in text.splitlines():
            parts = line.split()
            if not parts:
                continue
            oid, *parents = parts
            parent_map[oid] = tuple(parents)
            next_frontier.extend(parents)
        missing = [oid for oid in current if oid not in parent_map]
        if missing:
            raise RuntimeError(
                f'could not resolve parent metadata for local commits: {missing}'
            )
        frontier = next_frontier

    boundaries = tuple(
        oid
        for oid in selected
        if any(parent not in selected_set for parent in parent_map.get(oid, ()))
    )
    return tuple(selected), boundaries


def _record_shallow_boundaries(repo: 'git.Repo', commits: Iterable[str]) -> None:
    """Merge imported shallow roots into the destination's shallow file."""
    raw = repo.git.rev_parse('--git-path', 'shallow').strip()
    path = Path(raw)
    if not path.is_absolute():
        working_tree = cast(str | None, repo.working_tree_dir)
        if working_tree is None:
            raise RuntimeError('cannot resolve shallow file for a bare repository')
        path = Path(working_tree) / path
    existing = set(path.read_text().splitlines()) if path.exists() else set()
    existing.update(commits)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(('\n'.join(sorted(existing)) + '\n').encode())


def _source_remote_urls(repo: 'git.Repo') -> list[tuple[str, str]]:
    """Return unique configured fetch URLs from the source repository."""
    import git

    pairs: list[tuple[str, str]] = []
    seen_urls: set[str] = set()
    for remote in repo.remotes:
        try:
            stdout = repo.git.remote('get-url', '--all', remote.name)
        except git.GitCommandError:
            continue
        for url in stdout.splitlines():
            url = url.strip()
            if url and url not in seen_urls:
                seen_urls.add(url)
                pairs.append((remote.name, url))
    return pairs
