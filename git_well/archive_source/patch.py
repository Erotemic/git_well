"""
Incremental transport for :mod:`git_well.archive_source`.

Patch archives advance a compatible Git-bearing source archive to a
descendant commit without rebuilding the whole archive. Full-history blob
archives use Git bundles for repository-object deltas. Sparse promisor archives
use filtered object packs so deliberately omitted blobs remain promised and
absent. A residual filesystem overlay carries generated hook payloads and other
staged differences that Git does not reconstruct.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:  # pragma: no cover
    from ._common import ResolvedArchiveFormat

from .patch_apply import (
    PATCH_MANIFEST_FNAME as _PATCH_MANIFEST_FNAME,
    PATCH_SCHEMA as _PATCH_SCHEMA,
    PathLike,
    SourcePatchError,
    _apply_deletions,
    _apply_git_delta,
    _apply_overlay,
    _copy_entry,
    _extract_archive,
    _git,
    _git_command,
    _repo_has_commit,
    _repo_head,
    _run,
    _safe_target,
    _sha256_file,
    _tree_entries,
)

_PATCH_README_FNAME = 'README.txt'
_PATCH_APPLY_FNAME = 'APPLY_SOURCE_PATCH.py'
_REGISTRY_SCHEMA = 2


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
    history_blobs: str
    exclude_path_selectors: tuple[str, ...]
    generated_timestamp: str | None


def _git_common_dir(repo_root: Path) -> Path:
    text = _git(repo_root, 'rev-parse', '--git-common-dir')
    path = Path(text)
    if not path.is_absolute():
        path = repo_root / path
    return path.resolve()


def _registry_path(repo_root: Path) -> Path:
    return _git_common_dir(repo_root) / 'git-well' / 'archive-source' / 'archives.jsonl'


def register_source_archive(
    *,
    repo_root: Path,
    archive_path: Path,
    head_sha: str,
    archive_root_name: str,
    include_git_history: bool,
    normalized_depth: int | None,
    all_branches: bool,
    history_blobs: str,
    exclude_path_selectors: tuple[str, ...],
    timestamp: str,
    archive_format: str,
) -> None:
    """Record one patch-capable archive for future ``patch=auto``."""
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
        'history_blobs': history_blobs,
        'exclude_path_selectors': list(exclude_path_selectors),
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


def _parse_manifest_exclude_path_selectors(text: str) -> tuple[str, ...]:
    """Read exact exclusion policy from new or legacy human-readable manifests."""
    prefix = 'Worktree exclusion selectors JSON: '
    for line in text.splitlines():
        if line.startswith(prefix):
            try:
                value = json.loads(line[len(prefix):])
            except json.JSONDecodeError as ex:
                raise SourcePatchError(
                    'invalid Worktree exclusion selectors JSON in source archive'
                ) from ex
            if not isinstance(value, list) or not all(
                isinstance(item, str) for item in value
            ):
                raise SourcePatchError(
                    'invalid Worktree exclusion selectors JSON in source archive'
                )
            return tuple(value)

    lines = text.splitlines()
    try:
        section = lines.index('Worktree path exclusions:')
    except ValueError:
        return ()
    try:
        selector_line = lines.index('Selectors:', section + 1)
    except ValueError:
        return ()
    selectors = []
    for line in lines[selector_line + 1:]:
        if line.startswith('- '):
            selectors.append(line[2:])
            continue
        if line == 'Unmatched selectors:':
            break
        if line.strip() == '':
            break
        # A new top-level section starts without indentation/bullet syntax.
        if not line.startswith(' '):
            break
    return tuple(selectors)


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
        'history_blobs': values.get('History blob retention', 'full'),
        'exclude_path_selectors': _parse_manifest_exclude_path_selectors(text),
        'generated_timestamp': values.get('Generated timestamp'),
    }


def inspect_source_archive(path: PathLike) -> SourceArchiveInfo:
    """Inspect a source archive without extracting it."""
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
            'patch bases must be Git-bearing git-well source archives containing a '
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
        history_blobs=str(parsed['history_blobs']),
        exclude_path_selectors=tuple(parsed['exclude_path_selectors']),
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
    target_history_blobs: str,
    target_exclude_path_selectors: tuple[str, ...],
) -> None:
    if not info.has_git or info.history_depth == 0:
        raise SourcePatchError(
            'patch mode requires a base archive whose superproject contains .git'
        )
    if info.history_blobs != target_history_blobs:
        raise SourcePatchError(
            'patch mode requires the same Git blob retention policy in the '
            f'base and target; base={info.history_blobs!r}, '
            f'target={target_history_blobs!r}'
        )
    if target_history_blobs == 'sparse':
        base_selectors = tuple(sorted(set(info.exclude_path_selectors)))
        target_selectors = tuple(sorted(set(target_exclude_path_selectors)))
        if base_selectors != target_selectors:
            raise SourcePatchError(
                'sparse promisor patch mode requires the same --exclude-path '
                'policy in the base and target; create a new source archive '
                'base when changing sparse exclusions'
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
    target_history_blobs: str,
    target_exclude_path_selectors: tuple[str, ...],
    patch: PathLike,
) -> SourceArchiveInfo:
    """Resolve ``patch='auto'`` or an explicit compatible archive path."""
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
            target_history_blobs=target_history_blobs,
            target_exclude_path_selectors=target_exclude_path_selectors,
        )
        if not _is_ancestor(repo_root, info.head_sha, target_head):
            raise SourcePatchError(
                'patch base HEAD is not an ancestor of the target HEAD; '
                'patch mode only supports descendant updates'
            )
        distance = _commit_distance(repo_root, info.head_sha, target_head)
        if target_depth is not None and distance >= target_depth:
            raise SourcePatchError(
                'patch base HEAD falls outside the target shallow-history '
                f'window (distance={distance}, depth={target_depth}); create a '
                'newer compatible archive or increase --depth'
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
        if record.get('history_blobs', 'full') != target_history_blobs:
            continue
        if target_history_blobs == 'sparse':
            record_selectors = record.get('exclude_path_selectors', [])
            if not isinstance(record_selectors, list):
                continue
            if tuple(sorted(set(record_selectors))) != tuple(
                sorted(set(target_exclude_path_selectors))
            ):
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
                    target_history_blobs=target_history_blobs,
                    target_exclude_path_selectors=target_exclude_path_selectors,
                )
            except SourcePatchError:
                continue
            return info

    raise SourcePatchError(
        'no compatible Git-bearing source archive is known for patch=auto; '
        'create a compatible base archive first or pass an explicit base archive path'
    )




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
            f'{target_repo}; create a newer compatible archive or increase history depth'
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





def _read_sparse_checkout(repo: Path) -> str | None:
    enabled = _git(
        repo, 'config', '--bool', '--get', 'core.sparseCheckout', check=False
    ).strip()
    if enabled.lower() not in {'true', 'yes', 'on', '1'}:
        return None
    path_text = _git(repo, 'rev-parse', '--git-path', 'info/sparse-checkout')
    path = Path(path_text)
    if not path.is_absolute():
        path = repo / path
    if not path.is_file():
        return None
    return path.read_text(encoding='utf8')


def _read_promisor_config(repo: Path) -> dict[str, str] | None:
    remote_name = _git(
        repo, 'config', '--local', '--get', 'extensions.partialClone', check=False
    ).strip()
    if not remote_name:
        return None
    remote_url = _git(
        repo,
        'config',
        '--local',
        '--get',
        f'remote.{remote_name}.url',
        check=False,
    ).strip()
    if not remote_url:
        raise SourcePatchError(
            f'partial-clone repository {repo} has no URL for promisor remote '
            f'{remote_name!r}'
        )
    return {'name': remote_name, 'url': remote_url}


def _delta_revision_args(
    *,
    target_repo: Path,
    base_repo: Path,
    base_head: str,
    target_head: str,
    all_refs: bool,
) -> list[str]:
    if not _repo_has_commit(target_repo, base_head):
        raise SourcePatchError(
            f'base commit {base_head} is outside the target staged history at '
            f'{target_repo}; create a newer base archive or increase history depth'
        )
    if not _is_ancestor(target_repo, base_head, target_head):
        raise SourcePatchError(
            f'base commit {base_head} is not an ancestor of target {target_head} '
            f'in staged repository {target_repo}'
        )
    if not all_refs:
        return [target_head, f'^{base_head}']
    args = ['--all']
    prerequisite_heads = {base_head}
    for line in _git(base_repo, 'for-each-ref', '--format=%(objectname)').splitlines():
        sha = line.strip()
        if sha and _repo_has_commit(target_repo, sha):
            prerequisite_heads.add(sha)
    args.extend(f'^{sha}' for sha in sorted(prerequisite_heads))
    return args


def _locally_missing_oids(repo: Path, oids: list[str]) -> list[str]:
    """Return ``oids`` that are not available without a promisor fetch."""
    if not oids:
        return []
    env = {**os.environ, 'GIT_NO_LAZY_FETCH': '1'}
    proc = subprocess.run(
        [
            os.fspath(part)
            for part in _git_command(
                repo,
                'cat-file',
                '--batch-check=%(objectname) %(objecttype)',
            )
        ],
        input=''.join(f'{oid}\n' for oid in oids),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    if proc.returncode:
        raise SourcePatchError(
            'failed to inspect base Git object availability:\n'
            f'stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}'
        )
    lines = proc.stdout.splitlines()
    if len(lines) != len(oids):
        raise SourcePatchError(
            'git cat-file returned an unexpected number of object probes; '
            f'expected {len(oids)}, got {len(lines)}'
        )
    missing: list[str] = []
    for expected_oid, line in zip(oids, lines):
        fields = line.split()
        if len(fields) != 2 or fields[0] != expected_oid:
            raise SourcePatchError(
                'could not parse git cat-file object availability result: '
                f'{line!r}'
            )
        if fields[1] == 'missing':
            missing.append(expected_oid)
    return missing


def _enumerate_revision_objects(
    repo: Path, revisions: list[str]
) -> tuple[list[str], int]:
    """Enumerate locally-present reachable objects without lazy fetching."""
    env = {**os.environ, 'GIT_NO_LAZY_FETCH': '1'}
    proc = subprocess.run(
        [
            os.fspath(part)
            for part in _git_command(
                repo,
                'rev-list',
                '--objects',
                '--missing=print',
                *revisions,
            )
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    if proc.returncode:
        raise SourcePatchError(
            'failed to enumerate sparse Git objects:\n'
            f'stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}'
        )
    present_oids: list[str] = []
    missing_count = 0
    seen_present: set[str] = set()
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        if line.startswith('?'):
            missing_count += 1
            continue
        oid = line.split(' ', 1)[0]
        if oid not in seen_present:
            seen_present.add(oid)
            present_oids.append(oid)
    return present_oids, missing_count


def _create_object_pack(
    *,
    target_repo: Path,
    base_repo: Path,
    base_head: str,
    target_head: str,
    pack_path: Path,
    all_refs: bool = False,
    require_descendant: bool = True,
) -> tuple[bool, int]:
    """Create a locally-present object delta for a sparse patch.

    A normal revision delta is almost sufficient for descendant updates, but
    partial clones add one important case: an object can be reachable from the
    base commit yet intentionally absent in the base object database. If an
    unchanged exclusion policy later makes that same object locally required
    (for example, a file moves from an excluded path to an included path), the
    patch must backfill it even though its object ID predates the base.

    Descendant updates therefore combine the normal revision delta with the
    small set of base-promised objects that have become locally present in the
    target. Unrelated sparse submodule updates use an exact target-vs-base local
    availability comparison because no revision subtraction is possible.
    """
    refs_changed = all_refs and _git_refs(base_repo) != _git_refs(target_repo)
    if base_head == target_head and not refs_changed:
        return False, len(_missing_promisor_oids(target_repo))

    if require_descendant:
        revisions = _delta_revision_args(
            target_repo=target_repo,
            base_repo=base_repo,
            base_head=base_head,
            target_head=target_head,
            all_refs=all_refs,
        )
        delta_present, missing_count = _enumerate_revision_objects(
            target_repo, revisions
        )
        pack_oids = _locally_missing_oids(base_repo, delta_present)

        # Revision subtraction deliberately removes objects reachable from the
        # base graph. A promisor base may not actually have some of those
        # objects. If the fresh target now materialized one, carry it too.
        base_missing = sorted(_missing_promisor_oids(base_repo))
        if base_missing:
            still_missing_in_target = set(
                _locally_missing_oids(target_repo, base_missing)
            )
            seen = set(pack_oids)
            for oid in base_missing:
                if oid not in still_missing_in_target and oid not in seen:
                    pack_oids.append(oid)
                    seen.add(oid)
    else:
        revisions = ['--all'] if all_refs else [target_head]
        target_present, missing_count = _enumerate_revision_objects(
            target_repo, revisions
        )
        pack_oids = _locally_missing_oids(base_repo, target_present)

    if not pack_oids:
        return False, missing_count
    env = {**os.environ, 'GIT_NO_LAZY_FETCH': '1'}
    pack_path.parent.mkdir(parents=True, exist_ok=True)
    pack_proc = subprocess.run(
        [os.fspath(part) for part in _git_command(target_repo, 'pack-objects', '--stdout')],
        input=('\n'.join(pack_oids) + '\n').encode(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    if pack_proc.returncode:
        raise SourcePatchError(
            'failed to create sparse Git object pack:\n'
            f'stderr:\n{pack_proc.stderr.decode(errors="replace")}'
        )
    pack_path.write_bytes(pack_proc.stdout)
    return True, missing_count


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


def _oid_set_sha256(oids: set[str]) -> str:
    payload = ''.join(f'{oid}\n' for oid in sorted(oids)).encode()
    return hashlib.sha256(payload).hexdigest()


def _missing_promisor_oids(repo: Path) -> set[str]:
    env = {**os.environ, 'GIT_NO_LAZY_FETCH': '1'}
    proc = subprocess.run(
        [
            os.fspath(part)
            for part in _git_command(
                repo, 'rev-list', '--objects', '--all', '--missing=print'
            )
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    if proc.returncode:
        raise SourcePatchError(
            'patch self-verification could not inspect promisor objects at '
            f'{repo}: {proc.stderr.strip()}'
        )
    return {
        line[1:].strip()
        for line in proc.stdout.splitlines()
        if line.startswith('?') and line[1:].strip()
    }


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
    actual_missing = _missing_promisor_oids(actual)
    expected_missing = _missing_promisor_oids(expected)
    if actual_missing != expected_missing:
        raise SourcePatchError(
            'patch self-verification missing-object mismatch at '
            f'{actual}; extra_missing={sorted(actual_missing - expected_missing)[:20]}, '
            f'unexpectedly_present={sorted(expected_missing - actual_missing)[:20]}'
        )
    env = {**os.environ, 'GIT_NO_LAZY_FETCH': '1'}
    proc = subprocess.run(
        [os.fspath(part) for part in _git_command(actual, 'fsck', '--full')],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=False,
    )
    if proc.returncode:
        raise SourcePatchError(
            'patch self-verification offline fsck failed at '
            f'{actual}: {proc.stderr.strip()}'
        )


def _patch_root_name(repo_name: str, base_short: str, target_short: str) -> str:
    return (
        f'{repo_name}-source-patch-'
        f'{base_short[:12]}-to-{target_short[:12]}'
    )



def _write_standalone_apply_script(patch_root: Path) -> None:
    """Copy the dependency-free applier used by git-well into the patch."""
    from . import patch_apply

    source_path = Path(patch_apply.__file__).resolve()
    target = patch_root / _PATCH_APPLY_FNAME
    # This file is part of the patch transport protocol: copy bytes rather than
    # round-tripping through text mode, which rewrites LF to CRLF on Windows.
    target.write_bytes(source_path.read_bytes())
    target.chmod(0o755)


def _write_patch_readme(patch_root: Path, manifest: dict[str, Any]) -> None:
    base = manifest['base']
    target = manifest['target']
    lines = [
        'git-well incremental source patch',
        '=================================',
        '',
        'This is not a standalone source archive. It requires the exact compatible',
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
        '   git_well.archive_source.apply_source_patch(',
        '       base_archive, patch_archive, output_directory)',
        '',
        'Do not manually merge overlay/ into the base archive. Use the script or',
        'the API so base identity, Git object deltas, deletions, and submodules are all',
        'handled and verified together.',
        '',
    ]
    (patch_root / _PATCH_README_FNAME).write_text('\n'.join(lines), encoding='utf8')



def _prepare_git_delta(
    *,
    target_repo: Path,
    base_repo: Path,
    base_head: str,
    target_head: str,
    patch_root: Path,
    transport_stem: str,
    history_blobs: str,
    all_refs: bool = False,
    require_descendant: bool = True,
) -> dict[str, Any]:
    """Create and self-apply one repository delta for a source patch."""
    bundle_rel: str | None = None
    bundle_path: Path | None = None
    object_pack_rel: str | None = None
    object_pack_path: Path | None = None
    object_pack_promisor = False
    missing_promisor_objects = 0
    sparse_checkout = None
    promisor = None

    if history_blobs == 'sparse':
        sparse_checkout = _read_sparse_checkout(target_repo)
        promisor = _read_promisor_config(target_repo)
        object_pack_rel = f'git/{transport_stem}.pack'
        object_pack_path = patch_root / object_pack_rel
        made, _enumerated_missing = _create_object_pack(
            target_repo=target_repo,
            base_repo=base_repo,
            base_head=base_head,
            target_head=target_head,
            pack_path=object_pack_path,
            all_refs=all_refs,
            require_descendant=require_descendant,
        )
        if not made:
            object_pack_rel = None
            object_pack_path = None
        missing_oids = _missing_promisor_oids(target_repo)
        missing_promisor_objects = len(missing_oids)
        missing_promisor_oid_sha256 = _oid_set_sha256(missing_oids)
        if missing_promisor_objects and promisor is None:
            raise SourcePatchError(
                'sparse patch target has missing objects but no configured '
                f'promisor remote: {target_repo}'
            )
        object_pack_promisor = promisor is not None
    else:
        bundle_rel = f'git/{transport_stem}.bundle'
        bundle_path = patch_root / bundle_rel
        made = _create_bundle(
            target_repo=target_repo,
            base_repo=base_repo,
            base_head=base_head,
            target_head=target_head,
            bundle_path=bundle_path,
            all_refs=all_refs,
        )
        if not made:
            bundle_rel = None
            bundle_path = None

    _apply_git_delta(
        base_repo,
        bundle_path,
        target_head,
        object_pack=object_pack_path,
        object_pack_promisor=object_pack_promisor,
        sparse_checkout=sparse_checkout,
        promisor=promisor,
    )
    entry: dict[str, Any] = {
        'base_head': base_head,
        'target_head': target_head,
        'bundle': bundle_rel,
    }
    if history_blobs == 'sparse':
        entry.update(
            {
                'object_pack': object_pack_rel,
                'object_pack_promisor': object_pack_promisor,
                'missing_promisor_objects': missing_promisor_objects,
                'missing_promisor_oid_sha256': missing_promisor_oid_sha256,
                'sparse_checkout': sparse_checkout,
                'promisor': promisor,
            }
        )
    return entry


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
        target_history_blobs=context.history_blobs,
        target_exclude_path_selectors=context.exclude_path_selectors,
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
        git_entries: list[dict[str, Any]] = []
        managed_repos: list[tuple[Path, Path]] = []
        excluded_objects: list[PurePosixPath] = []

        super_entry = _prepare_git_delta(
            target_repo=context.archive_root,
            base_repo=reconstructed_root,
            base_head=base.head_sha,
            target_head=context.head_sha,
            patch_root=patch_root,
            transport_stem='superproject',
            history_blobs=context.history_blobs,
            all_refs=context.all_branches,
        )
        super_entry['path'] = '.'
        git_entries.append(super_entry)
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
            sparse_submodule = context.history_blobs == 'sparse'
            if not sparse_submodule:
                if not _repo_has_commit(target_repo, base_head):
                    continue
                if not _is_ancestor(target_repo, base_head, target_head):
                    continue
            sub_index += 1
            try:
                sub_entry = _prepare_git_delta(
                    target_repo=target_repo,
                    base_repo=base_repo,
                    base_head=base_head,
                    target_head=target_head,
                    patch_root=patch_root,
                    transport_stem=f'submodule-{sub_index:03d}',
                    history_blobs=context.history_blobs,
                    require_descendant=not sparse_submodule,
                )
            except SourcePatchError:
                if sparse_submodule:
                    # Never hide a sparse-object transport failure by copying
                    # the complete staged submodule object store into the
                    # residual overlay. That would preserve correctness while
                    # silently destroying the requested patch-size semantics.
                    raise
                continue
            sub_entry['path'] = relpath
            git_entries.append(sub_entry)
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
                'history_blobs': context.history_blobs,
                'exclude_path_selectors': list(context.exclude_path_selectors),
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
