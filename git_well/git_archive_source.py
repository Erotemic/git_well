#!/usr/bin/env python3
"""
Archive committed source with full Git history and initialized submodules.
"""
from __future__ import annotations

import os
import textwrap
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, List, Literal, Optional, Union, cast

import scriptconfig as scfg

if TYPE_CHECKING:  # pragma: no cover
    import git
    import tarfile
    import zipfile

PathLike = Union[str, os.PathLike]
DepthArg = Union[str, int, None]
ArchiveFormatArg = Literal[
    'auto',
    'tar',
    'tar.gz',
    'tgz',
    'zip',
    'tar.bz2',
    'tbz2',
    'tar.xz',
    'txz',
]
ResolvedArchiveFormat = Literal['tar', 'tar.gz', 'zip', 'tar.bz2', 'tar.xz']

_FORMAT_TO_EXTENSION = {
    'tar': '.tar',
    'tar.gz': '.tar.gz',
    'zip': '.zip',
    'tar.bz2': '.tar.bz2',
    'tar.xz': '.tar.xz',
}

_FORMAT_TO_TAR_MODE = {
    'tar': 'w',
    'tar.gz': 'w:gz',
    'tar.bz2': 'w:bz2',
    'tar.xz': 'w:xz',
}

_FORMAT_ALIASES = {
    'tgz': 'tar.gz',
    'tbz2': 'tar.bz2',
    'txz': 'tar.xz',
}


@dataclass(frozen=True)
class SubmoduleStatus:
    """
    Parsed information from ``git submodule status --recursive``.
    """
    status: str
    sha: str
    path: str
    line: str


class ArchiveSourceCLI(scfg.DataConfig):
    """
    Archive committed source with full Git history and initialized submodules.

    This is related to, but intentionally broader than, :command:`git archive`.
    Plain :command:`git archive` is excellent for producing a source-only tar
    or zip snapshot of one repository tree, but it does not include ``.git``
    metadata and it does not recursively materialize submodule contents by
    itself. By default this command stages clean committed checkouts of the
    superproject plus each initialized recursive submodule so the resulting
    archive can include real ``.git`` directories and ``git log`` remains
    available after unpacking. Use ``--depth 0`` to request source-only
    behavior, where this command delegates tree export to :command:`git archive`
    and omits ``.git`` metadata.

    Local edits, untracked files, ignored files, and build outputs are excluded
    in all modes. Submodules must already be initialized locally.
    """
    __command__ = 'archive_source'

    repo_dpath = scfg.Value(
        '.', position=1, nargs='?',
        help='location of the Git repository to archive')
    output = scfg.Value(
        None, short_alias=['o'],
        help=textwrap.dedent('''
            Exact archive path to write. Relative paths are interpreted
            relative to the repository root. If unspecified, the archive is
            written to the repository root as
            <repo>-source-<timestamp>-<short-sha>.<format-extension>.
            ''').strip())
    depth = scfg.Value(
        'full',
        help=textwrap.dedent('''
            Git history depth: "full" for all current-HEAD history, a positive
            integer for shallow history, or 0 for source-only git archive mode.
            ''').strip())
    format = scfg.Value(
        'auto',
        help=textwrap.dedent('''
            Archive format. Defaults to "auto", which infers the format from
            --output when possible, similar to git archive. Supported values are
            auto, tar, tar.gz, tgz, zip, tar.bz2, tbz2, tar.xz, and txz. When
            auto cannot infer from --output, it falls back to tar.gz.
            ''').strip())
    verbose = scfg.Value(1, help='verbosity level')

    @classmethod
    def main(cls, argv: list[str] | str | bool | None = True, **kwargs: Any) -> Path:
        config = cls.cli(argv=argv, data=kwargs, strict=True)
        archive_path = archive_source(
            repo_dpath=config.repo_dpath,
            output=config.output,
            depth=config.depth,
            format=config.format,
            verbose=config.verbose,
        )
        return archive_path


def main(argv: list[str] | str | bool | None = True, **kwargs: Any) -> Path:
    """
    Command line entry point for :class:`ArchiveSourceCLI`.
    """
    return ArchiveSourceCLI.main(argv=argv, **kwargs)


def archive_source(
    repo_dpath: PathLike = '.',
    output: Optional[PathLike] = None,
    depth: DepthArg = 'full',
    format: ArchiveFormatArg = 'auto',
    verbose: int = 1,
) -> Path:
    """
    Create an archive of committed source in a Git repository.

    Args:
        repo_dpath:
            Repository to archive. The repository containing this path is used.

        output:
            Exact archive path to write. Relative paths are interpreted relative
            to the repository root. If unspecified, the archive is written to
            the repository root as
            ``<repo>-source-<timestamp>-<short sha>.<format-extension>``.

        depth:
            ``'full'`` or ``None`` includes full current-HEAD history. A
            positive integer creates shallow staged checkouts. ``0`` omits Git
            metadata and uses source-only :command:`git archive` exports.

        format:
            Archive format. ``'auto'`` infers from the output extension when
            possible and otherwise defaults to ``'tar.gz'``.

        verbose:
            Verbosity level.

    Returns:
        The generated archive path.

    Notes:
        This function only archives committed/tracked source. Local edits,
        untracked files, ignored files, and build outputs are deliberately
        excluded.
    """
    repo = _coerce_repo(repo_dpath)
    _assert_has_head(repo)

    repo_root = Path(cast(str, repo.working_tree_dir)).resolve()
    repo_name = repo_root.name
    head_sha = repo.head.commit.hexsha
    short_sha = repo.git.rev_parse('--short=12', 'HEAD').strip()
    import ubelt as ub

    timestamp = ub.timestamp()
    prefix = f'{repo_name}-source-{timestamp}-{short_sha}'

    normalized_depth = _normalize_depth(depth)
    include_git_history = normalized_depth != 0
    clone_depth = None if normalized_depth in {0, None} else normalized_depth

    archive_format = _resolve_archive_format(output, format)
    archive_path = _resolve_output(repo_root, output, prefix, archive_format)
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    submodule_status = _submodule_status(repo)

    log = _Logger(verbose)
    log(f'[source-archive] repo: {repo_root}')
    log(f'[source-archive] prefix: {prefix}')
    log(f'[source-archive] archive format: {archive_format}')
    log('[source-archive] git history: {}'.format(
        'included' if include_git_history else 'omitted'))
    if include_git_history:
        depth_label = 'full' if clone_depth is None else str(clone_depth)
        log(f'[source-archive] history depth: {depth_label}')
    else:
        log('[source-archive] depth: 0 (source-only git archive mode)')
    log(f'[source-archive] superproject HEAD: {short_sha}')

    import shutil
    import tempfile

    tmpdir = Path(tempfile.mkdtemp(
        prefix=f'{repo_name}-source-archive.',
        dir=os.environ.get('TMPDIR', None),
    ))
    try:
        stage = tmpdir / 'stage'
        stage.mkdir(parents=True, exist_ok=True)
        archive_root = stage / prefix

        if include_git_history:
            log('[source-archive] cloning superproject')
            _clone_committed_checkout(
                src=repo,
                dst=archive_root,
                commit=head_sha,
                label='superproject',
                clone_depth=clone_depth,
                log=log,
            )
        else:
            log('[source-archive] exporting superproject with git archive')
            _extract_git_archive(repo, 'HEAD', stage, prefix)

        for info in submodule_status:
            path = info.path
            submodule_sha = info.sha
            if info.status == '-':
                raise RuntimeError(
                    f"submodule '{path}' is not initialized; run: "
                    'git submodule update --init --recursive')
            src_dpath = repo_root / path
            if not src_dpath.exists():
                raise RuntimeError(
                    f"submodule path '{path}' is missing; run: "
                    'git submodule update --init --recursive')
            sub_repo = _coerce_repo(src_dpath)
            _assert_has_head(sub_repo)
            sub_short = sub_repo.git.rev_parse('--short=12', 'HEAD').strip()
            log(f'[source-archive] exporting submodule {path} HEAD {sub_short}')
            if include_git_history:
                _clone_committed_checkout(
                    src=sub_repo,
                    dst=archive_root / path,
                    commit=submodule_sha,
                    label=f'submodule {path}',
                    clone_depth=clone_depth,
                    log=log,
                )
            else:
                (archive_root / path).mkdir(parents=True, exist_ok=True)
                _extract_git_archive(sub_repo, 'HEAD', stage, f'{prefix}/{path}')

        if include_git_history:
            _append_manifest_exclude(archive_root)

        manifest = archive_root / 'SOURCE_ARCHIVE_MANIFEST.txt'
        _write_manifest(
            manifest=manifest,
            repo=repo,
            repo_root=repo_root,
            repo_name=repo_name,
            prefix=prefix,
            timestamp=timestamp,
            head_sha=head_sha,
            short_sha=short_sha,
            include_git_history=include_git_history,
            clone_depth=clone_depth,
            submodule_status=submodule_status,
        )

        if include_git_history:
            # Re-run exclusion after writing the manifest so the archived
            # checkout opens cleanly with `git status`.
            _append_manifest_exclude(archive_root)

        _write_archive(stage, prefix, archive_path, archive_format)
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    log(f'[source-archive] wrote: {archive_path}')
    if archive_format == 'zip':
        log(f'[source-archive] list contents: unzip -l {archive_path}')
    elif archive_format == 'tar':
        log(f'[source-archive] list contents: tar -tf {archive_path} | less')
    else:
        log(f'[source-archive] list contents: tar -tf {archive_path} | less')
    return archive_path


def build_source_archive(*args: Any, **kwargs: Any) -> Path:
    """
    Backwards-compatible Python alias for :func:`archive_source`.
    """
    if 'history_depth' in kwargs:
        kwargs['depth'] = kwargs.pop('history_depth')
    if 'include_git_history' in kwargs:
        include_git_history = kwargs.pop('include_git_history')
        if not include_git_history:
            kwargs['depth'] = 0
    kwargs.pop('output_dir', None)
    kwargs.pop('prefix', None)
    return archive_source(*args, **kwargs)


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


def _assert_has_head(repo: 'git.Repo') -> None:
    try:
        _ = repo.head.commit.hexsha
    except ValueError as ex:
        raise RuntimeError('repository has no HEAD commit to archive') from ex


def _normalize_depth(depth: DepthArg) -> Optional[int]:
    import re
    if depth in {None, '', 'full'}:
        return None
    if isinstance(depth, bool):
        raise ValueError("depth must be a non-negative integer, None, or 'full'")
    if isinstance(depth, int):
        value = depth
    else:
        text = str(depth)
        if text == '0':
            return 0
        if not re.match(r'^[1-9][0-9]*$', text):
            raise ValueError("depth must be a non-negative integer, None, or 'full'")
        value = int(text)
    if value < 0:
        raise ValueError("depth must be a non-negative integer, None, or 'full'")
    return value


def _normalize_format(format: str) -> ResolvedArchiveFormat:
    raw_format = str(format).lower()
    normalized = _FORMAT_ALIASES.get(raw_format, raw_format)
    if normalized not in _FORMAT_TO_EXTENSION:
        valid = ', '.join(['auto', *_FORMAT_TO_EXTENSION, *_FORMAT_ALIASES])
        raise ValueError(f'unknown archive format {format!r}; expected one of: {valid}')
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
        'specify --format explicitly')


def _resolve_archive_format(
    output: Optional[PathLike],
    format: ArchiveFormatArg,
) -> ResolvedArchiveFormat:
    if str(format).lower() == 'auto':
        if output is None:
            return 'tar.gz'
        return _infer_format_from_output(output)
    return _normalize_format(str(format))


def _resolve_output(
    repo_root: Path,
    output: Optional[PathLike],
    prefix: str,
    archive_format: ResolvedArchiveFormat,
) -> Path:
    if output is None:
        archive_path = repo_root / f'{prefix}{_FORMAT_TO_EXTENSION[archive_format]}'
    else:
        archive_path = Path(output).expanduser()
        if not archive_path.is_absolute():
            archive_path = repo_root / archive_path
    return archive_path.resolve()


def _submodule_status(repo: 'git.Repo') -> List[SubmoduleStatus]:
    import git
    try:
        stdout = repo.git.submodule('status', '--recursive')
    except git.GitCommandError:
        return []
    infos: List[SubmoduleStatus] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        parts = line.split()
        if len(parts) < 2:
            raise RuntimeError(f'could not parse submodule status line: {line}')
        status = line[0]
        first = parts[0]
        if status == ' ':
            sha = first
        else:
            sha = first[1:]
        path = parts[1]
        if not path or not sha:
            raise RuntimeError(f'could not parse submodule status line: {line}')
        infos.append(SubmoduleStatus(status=status, sha=sha, path=path, line=line))
    return infos


def _clone_options_for_depth(clone_depth: Optional[int]) -> List[str]:
    options = ['--quiet', '--no-local', '--single-branch', '--no-checkout']
    if clone_depth is not None:
        options += ['--depth', str(clone_depth)]
    return options


def _clone_committed_checkout(
    src: 'git.Repo',
    dst: PathLike,
    commit: str,
    label: str,
    clone_depth: Optional[int],
    log: '_Logger',
) -> None:
    import git
    import shutil

    dst = Path(dst)
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst)

    src_root = Path(cast(str, src.working_tree_dir)).resolve()
    try:
        old_cwd = os.getcwd()
    except FileNotFoundError:
        old_cwd = None
    os.chdir(src_root.parent)
    try:
        cloned = git.Repo.clone_from(
            str(src_root),
            str(dst),
            multi_options=_clone_options_for_depth(clone_depth),
        )
    finally:
        if old_cwd is not None:
            os.chdir(old_cwd)
    _checkout_commit(cloned, commit, label, clone_depth, log)

    # The archive is for inspection, not local recovery. Expire the clone's
    # fresh reflogs so they do not keep extra objects alive, then repack to make
    # the archived .git directory reasonably small.
    try:
        cloned.git.reflog(
            'expire', '--expire=now', '--expire-unreachable=now', '--all')
    except git.GitCommandError:
        pass
    try:
        cloned.git.gc('--prune=now', '--quiet')
    except git.GitCommandError:
        pass


def _checkout_commit(
    repo: 'git.Repo',
    commit: str,
    label: str,
    clone_depth: Optional[int],
    log: '_Logger',
) -> None:
    import git

    try:
        repo.git.checkout('-q', '--detach', commit)
        return
    except git.GitCommandError:
        log(f'[source-archive] checkout of {label} failed after clone; fetching exact commit')

    if clone_depth is not None:
        try:
            repo.git.fetch('--quiet', '--depth', str(clone_depth), 'origin', commit)
        except git.GitCommandError:
            repo.git.fetch('--quiet', 'origin', commit)
    else:
        repo.git.fetch('--quiet', 'origin', commit)
    repo.git.checkout('-q', '--detach', commit)


def _extract_git_archive(repo: 'git.Repo', treeish: str, dst: Path, prefix: str) -> None:
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


def _append_manifest_exclude(repo_dpath: PathLike) -> None:
    info = Path(repo_dpath) / '.git' / 'info'
    if info.exists():
        exclude = info / 'exclude'
        with exclude.open('a') as file:
            file.write(textwrap.dedent(
                '''

                # Added by git-well archive_source so the archive manifest does not
                # dirty the checkout.
                /SOURCE_ARCHIVE_MANIFEST.txt
                ''').lstrip('\n'))


def _write_manifest(
    manifest: Path,
    repo: 'git.Repo',
    repo_root: Path,
    repo_name: str,
    prefix: str,
    timestamp: str,
    head_sha: str,
    short_sha: str,
    include_git_history: bool,
    clone_depth: Optional[int],
    submodule_status: List[SubmoduleStatus],
) -> None:
    import git

    depth_label = 'full' if clone_depth is None else str(clone_depth)
    try:
        super_status = repo.git.status('--short', '--branch').rstrip()
    except git.GitCommandError:
        super_status = ''

    lines = textwrap.dedent(
        f'''
        Source archive manifest
        =======================

        Generated timestamp: {timestamp}
        Repository: {repo_name}
        Repository root: {repo_root}
        Archive prefix: {prefix}
        Superproject HEAD: {head_sha}
        Superproject short HEAD: {short_sha}
        Git history included: {'yes' if include_git_history else 'no'}
        '''
    ).splitlines()

    if include_git_history:
        lines.extend([
            f'Git history depth: {depth_label}',
            f'Shallow clone: {"yes" if clone_depth is not None else "no"}',
        ])
    else:
        lines.extend([
            'Git history depth: 0',
            'Shallow clone: no',
        ])

    lines.extend(textwrap.dedent(
        '''

        Archive policy:
        - Includes committed/tracked files from the superproject HEAD.
        - Includes committed/tracked files from each initialized recursive submodule HEAD.
        '''
    ).splitlines())
    if include_git_history:
        lines.extend([
            '- Includes .git metadata for the superproject and initialized recursive submodules.',
            '- Excludes local edits, untracked files, ignored files, and build outputs.',
        ])
    else:
        lines.extend([
            '- Uses git archive source-only exports.',
            '- Excludes local edits, untracked files, ignored files, build outputs, and .git directories.',
        ])
    lines.extend([
        '',
        'Superproject status at archive time:',
        super_status,
        '',
        'Recursive submodule status at archive time:',
    ])
    if submodule_status:
        lines.extend(info.line for info in submodule_status)
    else:
        lines.append('(none)')

    lines += ['', 'Submodule HEADs included:']
    if submodule_status:
        for info in submodule_status:
            path = info.path
            src = repo_root / path
            if src.exists():
                try:
                    sub_repo = _coerce_repo(src)
                    lines.append(f'{sub_repo.head.commit.hexsha} {path}')
                except RuntimeError:
                    pass
    else:
        lines.append('(none)')
    manifest.write_text('\n'.join(lines).rstrip() + '\n')


def _write_archive(
    stage: Path,
    prefix: str,
    archive_path: Path,
    archive_format: ResolvedArchiveFormat,
) -> None:
    root = stage / prefix
    if archive_format == 'zip':
        import zipfile
        with zipfile.ZipFile(archive_path, mode='w', compression=zipfile.ZIP_DEFLATED) as zfile:
            _add_zip_entry(zfile, root, Path(prefix))
            for path in sorted(root.rglob('*')):
                _add_zip_entry(zfile, path, Path(prefix) / path.relative_to(root))
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


class _Logger:
    def __init__(self, verbose: int) -> None:
        self.verbose = verbose

    def __call__(self, msg: str) -> None:
        if self.verbose:
            print(msg)


__cli__ = ArchiveSourceCLI


if __name__ == '__main__':
    """
    CommandLine:
        python -m git_well.git_archive_source --help
        python -m git_well archive_source --help
    """
    main()
