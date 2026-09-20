"""Archive naming, metadata, extraction, and serialization helpers."""

from __future__ import annotations

import os
from collections.abc import Iterable, Sequence
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Literal, cast

from ._common import (
    ArchiveFormatArg,
    BranchRefInventory,
    PathLike,
    ResolvedArchiveFormat,
    SubmoduleArchiveDecision,
    _ARCHIVE_INFO_FNAME,
    _FORMAT_ALIASES,
    _FORMAT_TO_EXTENSION,
    _FORMAT_TO_TAR_MODE,
)

if TYPE_CHECKING:  # pragma: no cover
    import git
    import tarfile
    import zipfile

def _normalize_archive_root_name(name: str) -> str:
    text = os.fspath(name)
    if not text or text in {'.', '..'}:
        raise ValueError('archive_root_name must be a non-empty basename')
    if '\x00' in text or '/' in text or '\\' in text:
        raise ValueError(
            'archive_root_name must be a basename without path separators'
        )
    return text

def _normalize_format(format: str) -> ResolvedArchiveFormat:
    raw_format = str(format).lower()
    normalized = _FORMAT_ALIASES.get(raw_format, raw_format)
    if normalized not in _FORMAT_TO_EXTENSION:
        valid = ', '.join(['auto', *_FORMAT_TO_EXTENSION, *_FORMAT_ALIASES])
        raise ValueError(
            f'unknown archive format {format!r}; expected one of: {valid}'
        )
    return cast(ResolvedArchiveFormat, normalized)


def _infer_format_from_output(output: PathLike) -> ResolvedArchiveFormat:
    name = Path(output).name.lower()
    if name.endswith(('.tar.gz', '.tgz')):
        return 'tar.gz'
    if name.endswith(('.tar.bz2', '.tbz2')):
        return 'tar.bz2'
    if name.endswith(('.tar.xz', '.txz')):
        return 'tar.xz'
    if name.endswith('.tar'):
        return 'tar'
    if name.endswith('.zip'):
        return 'zip'
    raise ValueError(
        f'cannot infer archive format from output path {output!r}; '
        'specify --format explicitly'
    )


def _resolve_archive_format(
    output: PathLike | None,
    format: ArchiveFormatArg,
) -> ResolvedArchiveFormat:
    if str(format).lower() == 'auto':
        if output is None:
            return 'tar.gz'
        try:
            return _infer_format_from_output(output)
        except ValueError:
            return 'tar.gz'
    return _normalize_format(str(format))


def _resolve_output(
    repo_root: Path,
    output: PathLike | None,
    prefix: str,
    archive_format: ResolvedArchiveFormat,
) -> Path:
    if output is None:
        archive_path = (
            repo_root / f'{prefix}{_FORMAT_TO_EXTENSION[archive_format]}'
        )
    else:
        archive_path = Path(output).expanduser()
        if not archive_path.is_absolute():
            archive_path = repo_root / archive_path
    return archive_path.resolve()

def _extract_git_archive(
    repo: 'git.Repo', treeish: str, dst: Path, prefix: str
) -> None:
    import hashlib
    import tarfile

    digest = hashlib.sha1(f'{prefix}\0{treeish}'.encode()).hexdigest()[0:16]
    archive_fpath = dst / f'git-archive-{digest}.tar'
    with archive_fpath.open('wb') as file:
        repo.archive(
            file,
            treeish=treeish,
            prefix=f'{prefix.rstrip("/")}/',
            format='tar',
        )
    try:
        with tarfile.open(archive_fpath, mode='r:') as tar:
            _safe_extractall(tar, dst)
    finally:
        archive_fpath.unlink(missing_ok=True)


def _safe_extractall(tar: 'tarfile.TarFile', dst: Path) -> None:
    try:
        tar.extractall(path=str(dst), filter='fully_trusted')
    except TypeError:
        tar.extractall(path=str(dst))


def _normalize_generated_exclude(path: str) -> str:
    text = os.fspath(path).replace('\\', '/')
    if not text or '\x00' in text or '\n' in text or '\r' in text:
        raise ValueError(f'invalid generated exclude path: {path!r}')
    directory_rule = text.endswith('/')
    text = text.strip('/')
    pure = PurePosixPath(text)
    if not text or any(part in {'.', '..'} for part in pure.parts):
        raise ValueError(
            'generated excludes must be archive-root-relative paths without '
            f'traversal: {path!r}'
        )
    normalized = '/' + pure.as_posix()
    if directory_rule:
        normalized += '/'
    return normalized


def _append_generated_excludes(
    repo_dpath: PathLike, rules: Iterable[str]
) -> None:
    info = Path(repo_dpath) / '.git' / 'info'
    if info.exists():
        exclude = info / 'exclude'
        existing = exclude.read_text() if exclude.exists() else ''
        existing_rules = set(existing.splitlines())
        missing = [rule for rule in rules if rule not in existing_rules]
        if missing:
            block = (
                '\n# Added by git-well archive_source for generated metadata.\n'
                + ''.join(f'{rule}\n' for rule in missing)
            )
            with exclude.open('a') as file:
                file.write(block)


def _assert_archive_info_path_available(manifest: Path) -> None:
    """Refuse to overwrite or follow any repository-owned path."""
    if os.path.lexists(manifest):
        import stat

        mode = manifest.lstat().st_mode
        if stat.S_ISLNK(mode):
            kind = 'symlink'
        elif stat.S_ISDIR(mode):
            kind = 'directory'
        elif stat.S_ISREG(mode):
            kind = 'file'
        else:
            kind = 'filesystem entry'
        raise FileExistsError(
            f'cannot create {_ARCHIVE_INFO_FNAME}: the committed repository '
            f'already contains a {kind} at that path'
        )


def _write_manifest(
    manifest: Path,
    repo_root: Path,
    archive_path: Path,
    repo_name: str,
    prefix: str,
    timestamp: str,
    head_sha: str,
    short_sha: str,
    include_git_history: bool,
    clone_depth: int | None,
    branch_refs: BranchRefInventory | None,
    submodule_decisions: Sequence[SubmoduleArchiveDecision],
    exclude_path_selectors: Sequence[str],
    excluded_worktree_paths: Sequence[str],
    unmatched_exclude_path_selectors: Sequence[str],
    excluded_worktree_bytes: int,
    redact_local_paths: bool,
) -> None:
    from git_well import __version__

    source_path_text = (
        '(redacted by --redact-local-paths)'
        if redact_local_paths
        else os.fspath(repo_root)
    )
    archive_path_text = (
        '(redacted by --redact-local-paths)'
        if redact_local_paths
        else os.fspath(archive_path)
    )
    if not include_git_history:
        superproject_history = 'source-only (depth 0)'
    elif clone_depth is None:
        superproject_history = 'full'
    else:
        superproject_history = f'shallow (depth {clone_depth})'

    pruning_details = []
    if not include_git_history:
        pruning_details.append('superproject Git history omitted')
    elif clone_depth is not None:
        pruning_details.append(
            f'superproject Git history limited to depth {clone_depth}'
        )
    for decision in submodule_decisions:
        path = decision.info.path
        if decision.omitted:
            pruning_details.append(
                f'submodule {path!r} omitted: {decision.reason}'
            )
        elif decision.depth == 0:
            pruning_details.append(f'submodule {path!r} Git history omitted')
        elif decision.depth is not None:
            pruning_details.append(
                f'submodule {path!r} Git history limited to depth '
                f'{decision.depth}'
            )

    if exclude_path_selectors:
        pruning_details.append(
            f'{len(excluded_worktree_paths)} tracked worktree path(s) omitted '
            f'({excluded_worktree_bytes} raw bytes); Git history not rewritten'
        )

    lines = [
        'git-well source archive',
        '=======================',
        '',
        f'Generated by: git-well {__version__}',
        f'Generated timestamp: {timestamp}',
        f'Repository: {repo_name}',
        f'Source repository path: {source_path_text}',
        f'Archive output path: {archive_path_text}',
        f'Archive prefix: {prefix}',
        f'Superproject commit: {head_sha}',
        f'Superproject short commit: {short_sha}',
        f'Superproject history: {superproject_history}',
        'Superproject branches: '
        + (
            'all locally cached local and remote-tracking branches'
            if branch_refs is not None
            else 'current HEAD history only'
        ),
        '',
        f'Content pruning: {"yes" if pruning_details else "none"}',
    ]
    if pruning_details:
        lines.append('Pruning details:')
        lines.extend(f'- {detail}' for detail in pruning_details)
    if branch_refs is not None:
        lines += [
            '',
            'Remote network access during archive: none',
            '',
            'Local branches:',
        ]
        if branch_refs.local_branches:
            lines.extend(f'- {name}' for name in branch_refs.local_branches)
        else:
            lines.append('(none)')
        lines += ['', 'Remote-tracking branches:']
        if branch_refs.remote_tracking_branches:
            lines.extend(
                f'- {name}' for name in branch_refs.remote_tracking_branches
            )
        else:
            lines.append('(none)')

    if exclude_path_selectors:
        lines += [
            '',
            'Worktree path exclusions:',
            'Git history rewritten: no',
            f'Matched tracked paths omitted: {len(excluded_worktree_paths)}',
            f'Raw materialized bytes omitted: {excluded_worktree_bytes}',
            'Restore history-bearing repository paths with: '
            'git sparse-checkout disable',
            'Selectors:',
        ]
        lines.extend(f'- {selector}' for selector in exclude_path_selectors)
        if unmatched_exclude_path_selectors:
            lines += ['Unmatched selectors:']
            lines.extend(
                f'- {selector}'
                for selector in unmatched_exclude_path_selectors
            )

    lines += ['', 'Submodules:']
    if submodule_decisions:
        for decision in submodule_decisions:
            if decision.omitted:
                history = 'omitted'
            elif decision.depth == 0:
                history = 'source-only (depth 0)'
            elif decision.depth is None:
                history = 'full'
            else:
                history = f'shallow (depth {decision.depth})'
            lines.extend(
                [
                    f'- path: {decision.info.path}',
                    f'  commit: {decision.info.sha}',
                    f'  status: {"omitted" if decision.omitted else "included"}',
                    f'  history: {history}',
                    f'  reason: {decision.reason}',
                ]
            )
    else:
        lines.append('(none)')
    manifest_text = '\n'.join(lines).rstrip() + '\n'
    manifest.write_bytes(manifest_text.encode('utf8'))


def _write_archive(
    stage: Path,
    prefix: str,
    archive_path: Path,
    archive_format: ResolvedArchiveFormat,
) -> None:
    root = stage / prefix
    if archive_format == 'zip':
        import zipfile

        with zipfile.ZipFile(
            archive_path, mode='w', compression=zipfile.ZIP_DEFLATED
        ) as zfile:
            _add_zip_entry(zfile, root, Path(prefix))
            for path in sorted(root.rglob('*')):
                _add_zip_entry(
                    zfile, path, Path(prefix) / path.relative_to(root)
                )
    else:
        import tarfile

        mode = cast(
            Literal['w', 'w:gz', 'w:bz2', 'w:xz'],
            _FORMAT_TO_TAR_MODE[archive_format],
        )
        with tarfile.open(archive_path, mode) as tar:
            tar.add(str(root), arcname=prefix, recursive=True)


def _add_zip_entry(zfile: 'zipfile.ZipFile', path: Path, arcname: Path) -> None:
    import os
    import stat
    import zipfile

    arcname_text = arcname.as_posix()
    st = path.lstat()
    if stat.S_ISDIR(st.st_mode):
        zinfo = zipfile.ZipInfo(arcname_text.rstrip('/') + '/')
        zinfo.create_system = 3
        zinfo.external_attr = (st.st_mode & 0xFFFF) << 16
        zfile.writestr(zinfo, b'')
    elif stat.S_ISLNK(st.st_mode):
        zinfo = zipfile.ZipInfo(arcname_text)
        zinfo.create_system = 3
        zinfo.external_attr = (st.st_mode & 0xFFFF) << 16
        zfile.writestr(zinfo, os.readlink(path))
    else:
        zfile.write(path, arcname_text)
