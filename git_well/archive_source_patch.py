"""
Incremental transport for :mod:`git_well.git_archive_source`.

Patch archives intentionally support one clear case: a full source archive
whose superproject contains ``.git`` is advanced to a descendant commit with
the same superproject history/branch policy. Git bundles carry new repository
objects; a residual filesystem overlay carries generated hook payloads and any
other staged differences that Git does not reconstruct.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Callable, Iterable

if TYPE_CHECKING:  # pragma: no cover
    from git_well.git_archive_source import ResolvedArchiveFormat

from git_well.source_patch_apply import SourcePatchError, apply_source_patch

PathLike = str | os.PathLike[str]
_PATCH_MANIFEST_FNAME = 'GIT_WELL_SOURCE_PATCH.json'
_PATCH_README_FNAME = 'README.txt'
_PATCH_APPLY_FNAME = 'APPLY_SOURCE_PATCH.py'
_REGISTRY_SCHEMA = 1
_PATCH_SCHEMA = 1


@dataclass(frozen=True)
class SourceArchiveInfo:
    path: Path
    sha256: str
    root_name: str
    head_sha: str
    short_sha: str
    has_git: bool
    history_depth: int | None
    all_branches: bool
    generated_timestamp: str | None


@dataclass(frozen=True)
class _Entry:
    kind: str
    mode: int
    digest: str | None = None
    link_target: str | None = None


def _run(
    args: Iterable[PathLike],
    *,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    cmd = [os.fspath(part) for part in args]
    proc = subprocess.run(
        cmd,
        cwd=os.fspath(cwd) if cwd is not None else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and proc.returncode != 0:
        rendered = ' '.join(cmd)
        raise SourcePatchError(
            f'command failed with code {proc.returncode}: {rendered}\n'
            f'stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}'
        )
    return proc


def _git_command(repo: Path, *args: PathLike) -> list[PathLike]:
    # Source archives may be unpacked by a different uid than the one recorded
    # in the tar metadata (notably in agent/container handoffs). Trust only the
    # exact repository this operation was explicitly given; never mutate the
    # user's global safe.directory configuration.
    return [
        'git',
        '-c',
        f'safe.directory={repo.resolve()}',
        '-C',
        repo,
        *args,
    ]


def _git(repo: Path, *args: str, check: bool = True) -> str:
    return _run(_git_command(repo, *args), check=check).stdout.rstrip('\n')


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open('rb') as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def _git_common_dir(repo_root: Path) -> Path:
    text = _git(repo_root, 'rev-parse', '--git-common-dir')
    path = Path(text)
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


def _registry_path(repo_root: Path) -> Path:
    return _git_common_dir(repo_root) / 'git-well' / 'archive-source' / 'archives.jsonl'


def register_full_archive(
    *,
    repo_root: Path,
    archive_path: Path,
    head_sha: str,
    archive_root_name: str,
    include_git_history: bool,
    normalized_depth: int | None,
    all_branches: bool,
    timestamp: str,
    archive_format: str,
) -> None:
    """Record one successfully-written full archive for future ``patch=auto``."""
    archive_path = archive_path.resolve()
    record = {
        'schema_version': _REGISTRY_SCHEMA,
        'path': os.fspath(archive_path),
        'sha256': _sha256_file(archive_path),
        'head_sha': head_sha,
        'archive_root_name': archive_root_name,
        'has_git': bool(include_git_history),
        'history_depth': normalized_depth,
        'all_branches': bool(all_branches),
        'timestamp': timestamp,
        'archive_format': archive_format,
    }
    registry = _registry_path(repo_root)
    registry.parent.mkdir(parents=True, exist_ok=True)
    with registry.open('a', encoding='utf8') as file:
        file.write(json.dumps(record, sort_keys=True) + '\n')


def _parse_history_depth(value: str) -> int | None:
    value = value.strip()
    if value == 'full':
        return None
    if value.startswith('shallow (depth ') and value.endswith(')'):
        return int(value[len('shallow (depth ') : -1])
    if value == 'source-only (depth 0)':
        return 0
    raise SourcePatchError(f'unrecognized source archive history policy: {value!r}')


def _parse_archive_manifest(text: str) -> dict[str, Any]:
    values: dict[str, str] = {}
    for line in text.splitlines():
        if ': ' in line:
            key, value = line.split(': ', 1)
            values[key] = value
    required = [
        'Archive prefix',
        'Superproject commit',
        'Superproject short commit',
        'Superproject history',
        'Superproject branches',
    ]
    missing = [key for key in required if key not in values]
    if missing:
        raise SourcePatchError(
            'archive information file is missing required fields: '
            + ', '.join(missing)
        )
    return {
        'root_name': values['Archive prefix'],
        'head_sha': values['Superproject commit'],
        'short_sha': values['Superproject short commit'],
        'history_depth': _parse_history_depth(values['Superproject history']),
        'all_branches': values['Superproject branches'].startswith('all '),
        'generated_timestamp': values.get('Generated timestamp'),
    }


def inspect_source_archive(path: PathLike) -> SourceArchiveInfo:
    """Inspect a full archive without extracting it."""
    path = Path(path).expanduser().resolve()
    if not path.is_file():
        raise SourcePatchError(f'base source archive does not exist: {path}')

    import tarfile
    import zipfile

    manifest_name: str | None = None
    manifest_bytes: bytes | None = None
    names: list[str]
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path, 'r') as zfile:
            names = zfile.namelist()
            candidates = [
                name
                for name in names
                if len(PurePosixPath(name).parts) == 2
                and PurePosixPath(name).name == 'GIT_WELL_ARCHIVE_INFO.txt'
            ]
            if len(candidates) == 1:
                manifest_name = candidates[0]
                manifest_bytes = zfile.read(manifest_name)
    else:
        try:
            with tarfile.open(path, 'r:*') as tar:
                members = tar.getmembers()
                names = [member.name for member in members]
                candidates = [
                    member
                    for member in members
                    if len(PurePosixPath(member.name).parts) == 2
                    and PurePosixPath(member.name).name
                    == 'GIT_WELL_ARCHIVE_INFO.txt'
                ]
                if len(candidates) == 1:
                    manifest_name = candidates[0].name
                    extracted = tar.extractfile(candidates[0])
                    if extracted is not None:
                        manifest_bytes = extracted.read()
        except tarfile.TarError as ex:
            raise SourcePatchError(f'unsupported or invalid source archive: {path}') from ex

    if manifest_name is None or manifest_bytes is None:
        raise SourcePatchError(
            'patch bases must be full git-well source archives containing a '
            'top-level GIT_WELL_ARCHIVE_INFO.txt'
        )
    parsed = _parse_archive_manifest(manifest_bytes.decode('utf8'))
    root_name = str(parsed['root_name'])
    git_prefix = f'{root_name}/.git'
    has_git = any(name == git_prefix or name.startswith(git_prefix + '/') for name in names)
    return SourceArchiveInfo(
        path=path,
        sha256=_sha256_file(path),
        root_name=root_name,
        head_sha=str(parsed['head_sha']),
        short_sha=str(parsed['short_sha']),
        has_git=has_git,
        history_depth=parsed['history_depth'],
        all_branches=bool(parsed['all_branches']),
        generated_timestamp=parsed['generated_timestamp'],
    )


def _load_registry(repo_root: Path) -> list[dict[str, Any]]:
    registry = _registry_path(repo_root)
    if not registry.exists():
        return []
    records = []
    for line in registry.read_text(encoding='utf8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _is_ancestor(repo_root: Path, ancestor: str, descendant: str) -> bool:
    proc = _run(
        _git_command(repo_root, 'merge-base', '--is-ancestor', ancestor, descendant),
        check=False,
    )
    return proc.returncode == 0


def _commit_distance(repo_root: Path, base: str, target: str) -> int:
    text = _git(repo_root, 'rev-list', '--count', f'{base}..{target}')
    return int(text)


def _validate_base_compatibility(
    info: SourceArchiveInfo,
    *,
    target_depth: int | None,
    target_all_branches: bool,
) -> None:
    if not info.has_git or info.history_depth == 0:
        raise SourcePatchError(
            'patch mode requires a base archive whose superproject contains .git'
        )
    if info.history_depth != target_depth:
        base_label = 'full' if info.history_depth is None else str(info.history_depth)
        target_label = 'full' if target_depth is None else str(target_depth)
        raise SourcePatchError(
            'patch mode currently requires the same superproject history policy; '
            f'base={base_label}, target={target_label}'
        )
    if info.all_branches != target_all_branches:
        raise SourcePatchError(
            'patch mode currently requires the same --all-branches policy in '
            'the base and target archives'
        )


def resolve_patch_base(
    *,
    repo_root: Path,
    target_head: str,
    target_depth: int | None,
    target_all_branches: bool,
    patch: PathLike,
) -> SourceArchiveInfo:
    """Resolve ``patch='auto'`` or an explicit full archive path."""
    patch_text = os.fspath(patch)
    if patch_text != 'auto':
        candidate = Path(patch_text).expanduser()
        if not candidate.is_absolute():
            candidate = repo_root / candidate
        info = inspect_source_archive(candidate)
        _validate_base_compatibility(
            info,
            target_depth=target_depth,
            target_all_branches=target_all_branches,
        )
        if not _is_ancestor(repo_root, info.head_sha, target_head):
            raise SourcePatchError(
                'patch base HEAD is not an ancestor of the target HEAD; '
                'v1 patch mode only supports descendant updates'
            )
        distance = _commit_distance(repo_root, info.head_sha, target_head)
        if target_depth is not None and distance >= target_depth:
            raise SourcePatchError(
                'patch base HEAD falls outside the target shallow-history '
                f'window (distance={distance}, depth={target_depth}); create a '
                'newer full archive or increase --depth'
            )
        return info

    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for record in _load_registry(repo_root):
        if not record.get('has_git'):
            continue
        if record.get('history_depth') != target_depth:
            continue
        if bool(record.get('all_branches')) != target_all_branches:
            continue
        path_text = record.get('path')
        head_sha = record.get('head_sha')
        if not isinstance(path_text, str) or not isinstance(head_sha, str):
            continue
        path = Path(path_text)
        if not path.is_file():
            continue
        if not _is_ancestor(repo_root, head_sha, target_head):
            continue
        try:
            distance = _commit_distance(repo_root, head_sha, target_head)
        except (ValueError, SourcePatchError):
            continue
        if target_depth is not None and distance >= target_depth:
            # The staged target cannot contain this prerequisite with the
            # requested shallow depth, so it cannot anchor a bundle delta.
            continue
        timestamp = str(record.get('timestamp') or '')
        ranked.append((distance, timestamp, record))

    # Smallest descendant distance is the most useful base. Prefer newer
    # archive receipts when the same commit was archived more than once.
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=False)
    by_distance: dict[int, list[tuple[str, dict[str, Any]]]] = {}
    for distance, timestamp, record in ranked:
        by_distance.setdefault(distance, []).append((timestamp, record))
    for distance in sorted(by_distance):
        group = sorted(by_distance[distance], key=lambda item: item[0], reverse=True)
        for _timestamp, record in group:
            try:
                info = inspect_source_archive(str(record['path']))
            except SourcePatchError:
                continue
            if info.sha256 != record.get('sha256'):
                continue
            if info.head_sha != record.get('head_sha'):
                continue
            try:
                _validate_base_compatibility(
                    info,
                    target_depth=target_depth,
                    target_all_branches=target_all_branches,
                )
            except SourcePatchError:
                continue
            return info

    raise SourcePatchError(
        'no compatible Git-bearing full source archive is known for patch=auto; '
        'create a full archive first or pass an explicit base archive path'
    )


def _safe_target(root: Path, relpath: str) -> Path:
    pure = PurePosixPath(relpath)
    if pure.is_absolute() or any(part in {'', '.', '..'} for part in pure.parts):
        raise SourcePatchError(f'unsafe patch path: {relpath!r}')
    path = root.joinpath(*pure.parts)
    resolved_parent = path.parent.resolve()
    root_resolved = root.resolve()
    if resolved_parent != root_resolved and root_resolved not in resolved_parent.parents:
        raise SourcePatchError(f'patch path escapes target root: {relpath!r}')
    return path


def _safe_extract_tar(tar: Any, dst: Path) -> None:
    for member in tar.getmembers():
        pure = PurePosixPath(member.name)
        if pure.is_absolute() or '..' in pure.parts:
            raise SourcePatchError(f'unsafe archive member: {member.name!r}')
    try:
        tar.extractall(dst, filter='fully_trusted')
    except TypeError:
        tar.extractall(dst)


def _extract_archive(path: Path, dst: Path) -> None:
    import tarfile
    import zipfile

    dst.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path, 'r') as zfile:
            for info in zfile.infolist():
                pure = PurePosixPath(info.filename)
                if pure.is_absolute() or '..' in pure.parts:
                    raise SourcePatchError(
                        f'unsafe archive member: {info.filename!r}'
                    )
                target = dst.joinpath(*pure.parts)
                mode = (info.external_attr >> 16) & 0xFFFF
                if info.is_dir() or stat.S_ISDIR(mode):
                    target.mkdir(parents=True, exist_ok=True)
                    if mode:
                        os.chmod(target, stat.S_IMODE(mode))
                elif stat.S_ISLNK(mode):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if os.path.lexists(target):
                        _remove_path(target)
                    os.symlink(zfile.read(info).decode(), target)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zfile.open(info, 'r') as src, target.open('wb') as file:
                        shutil.copyfileobj(src, file)
                    if mode:
                        os.chmod(target, stat.S_IMODE(mode))
    else:
        with tarfile.open(path, 'r:*') as tar:
            _safe_extract_tar(tar, dst)


def _repo_head(repo: Path) -> str:
    return _git(repo, 'rev-parse', 'HEAD')


def _repo_has_commit(repo: Path, commit: str) -> bool:
    proc = _run(
        _git_command(repo, 'cat-file', '-e', f'{commit}^{{commit}}'),
        check=False,
    )
    return proc.returncode == 0


def _create_bundle(
    *,
    target_repo: Path,
    base_repo: Path,
    base_head: str,
    target_head: str,
    bundle_path: Path,
    all_refs: bool = False,
) -> bool:
    refs_changed = all_refs and _git_refs(base_repo) != _git_refs(target_repo)
    if base_head == target_head and not refs_changed:
        return False
    if not _repo_has_commit(target_repo, base_head):
        raise SourcePatchError(
            f'base commit {base_head} is outside the target staged history at '
            f'{target_repo}; create a newer full archive or increase history depth'
        )
    if not _is_ancestor(target_repo, base_head, target_head):
        raise SourcePatchError(
            f'base commit {base_head} is not an ancestor of target {target_head} '
            f'in staged repository {target_repo}'
        )
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    positives = ['--all'] if all_refs else ['HEAD']
    prerequisite_heads = {base_head}
    if all_refs:
        for line in _git(base_repo, 'for-each-ref', '--format=%(objectname)').splitlines():
            sha = line.strip()
            if sha and _repo_has_commit(target_repo, sha):
                prerequisite_heads.add(sha)
    command = _git_command(
        target_repo,
        'bundle',
        'create',
        bundle_path,
        *positives,
        *[f'^{sha}' for sha in sorted(prerequisite_heads)],
    )
    proc = _run(command, check=False)
    if proc.returncode != 0:
        if 'Refusing to create empty bundle' in proc.stderr:
            bundle_path.unlink(missing_ok=True)
            return False
        raise SourcePatchError(
            'failed to create Git bundle:\n'
            f'stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}'
        )
    return True


def _apply_git_delta(repo: Path, bundle: Path | None, target_head: str) -> None:
    if bundle is not None:
        _run(_git_command(repo, 'bundle', 'unbundle', bundle))
    _run(_git_command(repo, 'reset', '--hard', '--quiet', target_head))


def _entry_for(path: Path) -> _Entry:
    st = path.lstat()
    mode = stat.S_IMODE(st.st_mode)
    if stat.S_ISLNK(st.st_mode):
        return _Entry('symlink', mode, link_target=os.readlink(path))
    if stat.S_ISDIR(st.st_mode):
        return _Entry('dir', mode)
    if stat.S_ISREG(st.st_mode):
        hasher = hashlib.sha256()
        with path.open('rb') as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b''):
                hasher.update(chunk)
        return _Entry('file', mode, digest=hasher.hexdigest())
    raise SourcePatchError(f'unsupported filesystem entry in source archive: {path}')


def _path_is_excluded(rel: PurePosixPath, excluded: tuple[PurePosixPath, ...]) -> bool:
    for prefix in excluded:
        if rel == prefix or prefix in rel.parents:
            return True
    return False


def _tree_entries(
    root: Path, *, excluded: tuple[PurePosixPath, ...]
) -> dict[str, _Entry]:
    entries: dict[str, _Entry] = {}

    def walk(path: Path, rel: PurePosixPath) -> None:
        if rel.parts and _path_is_excluded(rel, excluded):
            return
        if rel.parts:
            entries[rel.as_posix()] = _entry_for(path)
        st = path.lstat()
        if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode):
            return
        for child in sorted(path.iterdir(), key=lambda item: item.name):
            child_rel = rel / child.name if rel.parts else PurePosixPath(child.name)
            walk(child, child_rel)

    walk(root, PurePosixPath())
    return entries


def _remove_path(path: Path) -> None:
    if not os.path.lexists(path):
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _copy_entry(src: Path, dst: Path, entry: _Entry) -> None:
    if entry.kind == 'dir':
        if os.path.lexists(dst) and (dst.is_symlink() or not dst.is_dir()):
            _remove_path(dst)
        dst.mkdir(parents=True, exist_ok=True)
        os.chmod(dst, entry.mode)
    elif entry.kind == 'symlink':
        if os.path.lexists(dst):
            _remove_path(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(os.readlink(src), dst)
    elif entry.kind == 'file':
        if os.path.lexists(dst) and (dst.is_symlink() or dst.is_dir()):
            _remove_path(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        os.chmod(dst, entry.mode)
    else:  # pragma: no cover
        raise AssertionError(entry.kind)


def _build_residual_overlay(
    *,
    reconstructed_root: Path,
    target_root: Path,
    patch_root: Path,
    excluded: tuple[PurePosixPath, ...],
) -> tuple[list[str], list[str]]:
    before = _tree_entries(reconstructed_root, excluded=excluded)
    after = _tree_entries(target_root, excluded=excluded)
    deletions = sorted(
        [path for path in before if path not in after or before[path].kind != after[path].kind],
        key=lambda value: (value.count('/'), value),
        reverse=True,
    )
    overlay_paths = sorted(
        [path for path, entry in after.items() if before.get(path) != entry],
        key=lambda value: (value.count('/'), value),
    )

    overlay_root = patch_root / 'overlay'
    overlay_root.mkdir(parents=True, exist_ok=True)
    for relpath in overlay_paths:
        src = _safe_target(target_root, relpath)
        dst = _safe_target(overlay_root, relpath)
        _copy_entry(src, dst, after[relpath])
    (patch_root / 'deletions.json').write_text(
        json.dumps(deletions, indent=2) + '\n', encoding='utf8'
    )
    return deletions, overlay_paths


def _apply_deletions(root: Path, deletions: Iterable[str]) -> None:
    for relpath in sorted(
        deletions, key=lambda value: (value.count('/'), value), reverse=True
    ):
        _remove_path(_safe_target(root, relpath))


def _apply_overlay(
    overlay_root: Path,
    target_root: Path,
    *,
    paths: Iterable[str] | None = None,
) -> None:
    if not overlay_root.exists():
        return
    entries = _tree_entries(overlay_root, excluded=())
    if paths is None:
        relpaths = list(entries)
    else:
        relpaths = list(paths)
    for relpath in sorted(
        relpaths, key=lambda value: (value.count('/'), value)
    ):
        entry = entries.get(relpath)
        if entry is None:
            raise SourcePatchError(
                f'patch overlay entry is missing: {relpath!r}'
            )
        src = _safe_target(overlay_root, relpath)
        dst = _safe_target(target_root, relpath)
        _copy_entry(src, dst, entry)


def _verify_non_object_tree(
    *,
    actual: Path,
    expected: Path,
    excluded: tuple[PurePosixPath, ...],
) -> None:
    actual_entries = _tree_entries(actual, excluded=excluded)
    expected_entries = _tree_entries(expected, excluded=excluded)
    if actual_entries != expected_entries:
        only_actual = sorted(set(actual_entries) - set(expected_entries))[:20]
        only_expected = sorted(set(expected_entries) - set(actual_entries))[:20]
        changed = sorted(
            path
            for path in set(actual_entries) & set(expected_entries)
            if actual_entries[path] != expected_entries[path]
        )[:20]
        raise SourcePatchError(
            'patch self-verification failed outside Git object stores; '
            f'only_actual={only_actual}, only_expected={only_expected}, '
            f'changed={changed}'
        )


def _git_refs(repo: Path) -> str:
    return _git(repo, 'for-each-ref', '--format=%(refname) %(objectname)')


def _verify_git_repo(actual: Path, expected: Path) -> None:
    if _repo_head(actual) != _repo_head(expected):
        raise SourcePatchError(f'patch self-verification HEAD mismatch at {actual}')
    if _git_refs(actual) != _git_refs(expected):
        raise SourcePatchError(f'patch self-verification ref mismatch at {actual}')
    actual_reachable = _git(actual, 'rev-list', '--all').splitlines()
    expected_reachable = _git(expected, 'rev-list', '--all').splitlines()
    if set(actual_reachable) != set(expected_reachable):
        raise SourcePatchError(
            f'patch self-verification reachable-history mismatch at {actual}'
        )


def _patch_root_name(repo_name: str, base_short: str, target_short: str) -> str:
    return (
        f'{repo_name}-source-patch-'
        f'{base_short[:12]}-to-{target_short[:12]}'
    )



def _write_standalone_apply_script(patch_root: Path) -> None:
    """Copy the dependency-free applier used by git-well into the patch."""
    from git_well import source_patch_apply

    source_path = Path(source_patch_apply.__file__).resolve()
    source = source_path.read_text(encoding='utf8')
    target = patch_root / _PATCH_APPLY_FNAME
    target.write_text(source, encoding='utf8')
    target.chmod(0o755)


def _write_patch_readme(patch_root: Path, manifest: dict[str, Any]) -> None:
    base = manifest['base']
    target = manifest['target']
    lines = [
        'git-well incremental source patch',
        '=================================',
        '',
        'This is not a standalone source archive. It requires the exact full',
        'base archive named below.',
        '',
        f'Required base archive: {base["archive_name"]}',
        f'Required base archive SHA-256: {base["archive_sha256"]}',
        f'Base commit: {base["head_sha"]}',
        f'Target commit: {target["head_sha"]}',
        '',
        'Standalone application (git-well is NOT required)',
        '-------------------------------------------------',
        '',
        'Requirements:',
        '  - Python 3.10 or newer',
        '  - git on PATH',
        '',
        '1. Extract this patch archive.',
        '2. From the extracted patch directory, run:',
        '',
        f'   python {_PATCH_APPLY_FNAME} /path/to/{base["archive_name"]} /path/to/output',
        '',
        'The output directory must be empty or not yet exist. The script verifies',
        'the exact base SHA-256 and every Git repository HEAD before and after',
        'application, including Git-bearing submodules represented by this patch.',
        'It prints the reconstructed target source root on success.',
        '',
        'Installed git-well API',
        '----------------------',
        '',
        'The same application implementation is available as:',
        '',
        '   git_well.archive_source_patch.apply_source_patch(',
        '       base_archive, patch_archive, output_directory)',
        '',
        'Do not manually merge overlay/ into the base archive. Use the script or',
        'the API so base identity, Git bundles, deletions, and submodules are all',
        'handled and verified together.',
        '',
    ]
    (patch_root / _PATCH_README_FNAME).write_text('\n'.join(lines), encoding='utf8')


def build_source_patch(
    *,
    context: Any,
    patch: PathLike,
    write_archive: Callable[[Path, str, Path, ResolvedArchiveFormat], None],
) -> Path:
    """Build an incremental archive from a fully prepared/validated context."""
    if not context.include_git_history:
        raise SourcePatchError(
            'archive_source patch mode requires Git history; depth=0 is not supported'
        )

    base = resolve_patch_base(
        repo_root=context.repo_root,
        target_head=context.head_sha,
        target_depth=context.normalized_depth,
        target_all_branches=context.all_branches,
        patch=patch,
    )
    if context.archive_path.resolve() == base.path.resolve():
        raise SourcePatchError('patch output path must differ from the base archive path')

    work = Path(
        tempfile.mkdtemp(
            prefix=f'{context.repo_name}-source-patch.',
            dir=os.environ.get('TMPDIR', None),
        )
    )
    try:
        base_extract = work / 'base'
        _extract_archive(base.path, base_extract)
        reconstructed_root = base_extract / base.root_name
        if not reconstructed_root.is_dir():
            raise SourcePatchError(
                f'base archive root is missing after extraction: {base.root_name}'
            )
        if not (reconstructed_root / '.git').is_dir():
            raise SourcePatchError('base archive superproject does not contain .git')
        if _repo_head(reconstructed_root) != base.head_sha:
            raise SourcePatchError('base archive HEAD does not match its archive manifest')

        patch_stage = work / 'patch-stage'
        patch_stage.mkdir(parents=True, exist_ok=True)
        patch_root_name = _patch_root_name(
            context.repo_name, base.short_sha, context.short_sha
        )
        patch_root = patch_stage / patch_root_name
        patch_root.mkdir(parents=True)
        git_root = patch_root / 'git'

        git_entries: list[dict[str, Any]] = []
        managed_repos: list[tuple[Path, Path]] = []
        excluded_objects: list[PurePosixPath] = []

        super_bundle = git_root / 'superproject.bundle'
        has_super_bundle = _create_bundle(
            target_repo=context.archive_root,
            base_repo=reconstructed_root,
            base_head=base.head_sha,
            target_head=context.head_sha,
            bundle_path=super_bundle,
            all_refs=context.all_branches,
        )
        _apply_git_delta(
            reconstructed_root,
            super_bundle if has_super_bundle else None,
            context.head_sha,
        )
        git_entries.append(
            {
                'path': '.',
                'base_head': base.head_sha,
                'target_head': context.head_sha,
                'bundle': 'git/superproject.bundle' if has_super_bundle else None,
            }
        )
        managed_repos.append((reconstructed_root, context.archive_root))
        excluded_objects.append(PurePosixPath('.git/objects'))

        sub_index = 0
        for decision in context.submodule_decisions:
            if decision.omitted or decision.depth == 0:
                continue
            relpath = decision.info.path
            target_repo = context.archive_root / relpath
            base_repo = reconstructed_root / relpath
            if not (target_repo / '.git').is_dir() or not (base_repo / '.git').is_dir():
                continue
            try:
                base_head = _repo_head(base_repo)
                target_head = _repo_head(target_repo)
            except SourcePatchError:
                continue
            if not _repo_has_commit(target_repo, base_head):
                continue
            if not _is_ancestor(target_repo, base_head, target_head):
                continue
            bundle_rel: str | None = None
            bundle_path: Path | None = None
            if base_head != target_head:
                sub_index += 1
                bundle_rel = f'git/submodule-{sub_index:03d}.bundle'
                bundle_path = patch_root / bundle_rel
                try:
                    made = _create_bundle(
                        target_repo=target_repo,
                        base_repo=base_repo,
                        base_head=base_head,
                        target_head=target_head,
                        bundle_path=bundle_path,
                    )
                except SourcePatchError:
                    if bundle_path.exists():
                        bundle_path.unlink()
                    continue
                if not made:
                    bundle_rel = None
                    bundle_path = None
            _apply_git_delta(base_repo, bundle_path, target_head)
            git_entries.append(
                {
                    'path': relpath,
                    'base_head': base_head,
                    'target_head': target_head,
                    'bundle': bundle_rel,
                }
            )
            managed_repos.append((base_repo, target_repo))
            excluded_objects.append(PurePosixPath(relpath) / '.git' / 'objects')

        excluded = tuple(excluded_objects)
        deletions, overlay_paths = _build_residual_overlay(
            reconstructed_root=reconstructed_root,
            target_root=context.archive_root,
            patch_root=patch_root,
            excluded=excluded,
        )

        manifest: dict[str, Any] = {
            'schema_version': _PATCH_SCHEMA,
            'kind': 'git-well-source-patch',
            'base': {
                'archive_name': base.path.name,
                'archive_sha256': base.sha256,
                'root_name': base.root_name,
                'head_sha': base.head_sha,
                'short_sha': base.short_sha,
            },
            'target': {
                'root_name': context.archive_root_name,
                'head_sha': context.head_sha,
                'short_sha': context.short_sha,
                'history_depth': context.normalized_depth,
                'all_branches': context.all_branches,
            },
            'git_repositories': git_entries,
            'deletions_file': 'deletions.json',
            'overlay_root': 'overlay',
            'overlay_entry_count': len(overlay_paths),
            'overlay_paths': overlay_paths,
            'apply_script': _PATCH_APPLY_FNAME,
        }
        (patch_root / _PATCH_MANIFEST_FNAME).write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + '\n', encoding='utf8'
        )
        _write_standalone_apply_script(patch_root)
        _write_patch_readme(patch_root, manifest)

        # Apply the residual to the reconstructed base and prove that the patch
        # reaches the prepared target before emitting an artifact.
        _apply_deletions(reconstructed_root, deletions)
        _apply_overlay(
            patch_root / 'overlay',
            reconstructed_root,
            paths=overlay_paths,
        )
        _verify_non_object_tree(
            actual=reconstructed_root,
            expected=context.archive_root,
            excluded=excluded,
        )
        for actual_repo, expected_repo in managed_repos:
            _verify_git_repo(actual_repo, expected_repo)

        write_archive(
            patch_stage,
            patch_root_name,
            context.archive_path,
            context.archive_format,
        )
        context._log(f'[source-archive] wrote patch: {context.archive_path}')
        context._log(
            '[source-archive] patch base: '
            f'{base.path.name} ({base.short_sha[:12]})'
        )
        return context.archive_path
    finally:
        shutil.rmtree(work, ignore_errors=True)
