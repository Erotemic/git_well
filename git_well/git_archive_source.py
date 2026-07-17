#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
"""
Archive committed source with full Git history and initialized submodules.
"""

from __future__ import annotations

import fnmatch
import os
import textwrap
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any, Literal, cast

import kwconf

if TYPE_CHECKING:  # pragma: no cover
    import tarfile
    import zipfile

    import git

PathLike = str | os.PathLike
DepthArg = str | int | None
SubmoduleDepthSpecArg = str | int | None | dict[str, DepthArg]
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

_ARCHIVE_INFO_FNAME = 'GIT_WELL_ARCHIVE_INFO.txt'

# TODO: Re-enable repo-local archive_source defaults after kwconf /
# legacy modal dispatch preserved omitted values distinctly from
# injected defaults. The intended Git config keys were:
# - git-well.archive-source.depth
# - git-well.archive-source.format


@dataclass(frozen=True)
class SubmoduleStatus:
    """
    Committed recursive submodule information resolved from Git trees.
    """

    status: str
    sha: str
    path: str
    line: str


@dataclass(frozen=True)
class SubmoduleArchiveDecision:
    """
    A resolved archive action for one recursive submodule.
    """

    info: SubmoduleStatus
    omitted: bool
    depth: int | None
    mode: str
    reason: str


_UNSET = object()


@dataclass(frozen=True)
class SubmoduleDepthPolicy:
    """
    Parsed ``--submodule-depth`` policy.

    The user-facing spec is intentionally small: a scalar depth applies to all
    submodules; a YAML mapping may contain exact submodule paths, fnmatch-style
    glob patterns, ``"*"`` as a catch-all glob, and ``__default__`` as a
    non-glob fallback.

    Example:
        >>> policy = _parse_submodule_depth_spec('0')
        >>> _depth_label(policy.resolve('any/submodule', inherited_depth=25))
        '0'
        >>> policy = _parse_submodule_depth_spec('{"*": 0, special/submod: 100}')
        >>> _depth_label(policy.resolve('special/submod', inherited_depth=25))
        '100'
        >>> _depth_label(policy.resolve('other/submod', inherited_depth=25))
        '0'
        >>> policy = _parse_submodule_depth_spec('{__default__: 0, special/*: full}')
        >>> _depth_label(policy.resolve('special/lib', inherited_depth=25))
        'full'
        >>> _depth_label(policy.resolve('plain/lib', inherited_depth=25))
        '0'
        >>> policy = _parse_submodule_depth_spec('{special/*: 25, "*/submod": 100}')
        >>> policy.resolve('special/submod', inherited_depth=1)
        Traceback (most recent call last):
        ...
        ValueError: ambiguous submodule depth for special/submod: matched special/* -> 25, */submod -> 100; add an exact path entry to disambiguate
    """

    specified: bool
    raw: Any = None
    scalar_depth: Any = _UNSET
    exact_depths: dict[str, int | None] = field(default_factory=dict)
    glob_depths: dict[str, int | None] = field(default_factory=dict)
    star_depth: Any = _UNSET
    default_depth: Any = _UNSET

    def resolve(self, path: str, inherited_depth: int | None) -> int | None:
        """
        Resolve the depth for one submodule path.
        """
        if not self.specified:
            return inherited_depth
        if self.scalar_depth is not _UNSET:
            return cast(int | None, self.scalar_depth)
        if path in self.exact_depths:
            return self.exact_depths[path]

        matches = [
            (pattern, depth)
            for pattern, depth in self.glob_depths.items()
            if fnmatch.fnmatchcase(path, pattern)
        ]
        if matches:
            depths = {depth for _pattern, depth in matches}
            if len(depths) == 1:
                return matches[0][1]
            rendered = ', '.join(
                f'{pattern} -> {_depth_label(depth)}'
                for pattern, depth in matches
            )
            raise ValueError(
                f'ambiguous submodule depth for {path}: matched {rendered}; '
                'add an exact path entry to disambiguate'
            )

        if self.star_depth is not _UNSET:
            return cast(int | None, self.star_depth)
        if self.default_depth is not _UNSET:
            return cast(int | None, self.default_depth)
        return inherited_depth

    def summary_lines(self) -> list[str]:
        """Return human-readable manifest lines for this policy."""
        if not self.specified:
            return ['Submodule depth spec: (omitted; inherits superproject depth)']
        if self.scalar_depth is not _UNSET:
            return [
                f'Submodule depth spec: scalar {_depth_label(cast(int | None, self.scalar_depth))}'
            ]
        lines = ['Submodule depth spec: YAML mapping']
        if self.default_depth is not _UNSET:
            lines.append(
                f'  __default__: {_depth_label(cast(int | None, self.default_depth))}'
            )
        if self.star_depth is not _UNSET:
            lines.append(
                f'  "*": {_depth_label(cast(int | None, self.star_depth))}'
            )
        if self.exact_depths:
            lines.append('  exact paths:')
            for path, depth in sorted(self.exact_depths.items()):
                lines.append(f'    {path}: {_depth_label(depth)}')
        if self.glob_depths:
            lines.append('  glob patterns:')
            for pattern, depth in sorted(self.glob_depths.items()):
                lines.append(f'    {pattern}: {_depth_label(depth)}')
        return lines


class ArchiveSourceCLI(kwconf.Config):
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

    repo_dpath = kwconf.Value(
        '.',
        position=1,
        nargs='?',
        help='location of the Git repository to archive',
    )
    output = kwconf.Value(
        None,
        short_alias=['o'],
        help=textwrap.dedent("""
            Exact archive path to write. Relative paths are interpreted
            relative to the repository root. If unspecified, the archive is
            written to the repository root as
            <repo>-source-<timestamp>-<short-sha>.<format-extension>.
            """).strip(),
    )
    depth = kwconf.Value(
        'full',
        help=textwrap.dedent("""
            Git history depth: "full" for all current-HEAD history, a positive
            integer for shallow history, or 0 for source-only git archive mode.
            """).strip(),
    )
    submodule_depth = kwconf.Value(
        None,
        parser=str,
        alias=['submodule-depth'],
        help=textwrap.dedent("""
            YAML depth spec for recursive submodules. If omitted, submodules
            inherit --depth. A scalar such as 0, 25, or full applies to every
            submodule. A mapping may use exact submodule paths, fnmatch-style
            glob keys, quoted "*" as a catch-all glob, and __default__ as a
            non-glob fallback, e.g. '{"*": 0, special/submod: 100}'.
            """).strip(),
    )
    exclude_submodule = kwconf.Value(
        [],
        nargs='*',
        alias=['exclude-submodule'],
        help=textwrap.dedent("""
            Recursive submodule path selectors to omit from the archive. Each
            selector may be an exact recursive submodule path or a
            fnmatch-style glob pattern. Quote glob patterns such as
            'external/*' to prevent the shell from expanding them before
            git-well sees them. This may be used with --submodule-depth to keep
            most submodules source-only while dropping data-heavy submodules
            entirely.
            """).strip(),
    )
    submodules = kwconf.Value(
        True,
        isflag=True,
        help=textwrap.dedent("""
            Materialize initialized recursive submodule working trees. Pass
            --no-submodules to omit every submodule working tree from the
            archive while keeping superproject gitlinks and .gitmodules.
            """).strip(),
    )
    format = kwconf.Value(
        'auto',
        help=textwrap.dedent("""
            Archive format. Defaults to "auto", which infers the format from
            --output when possible, similar to git archive. Supported values are
            auto, tar, tar.gz, tgz, zip, tar.bz2, tbz2, tar.xz, and txz. When
            auto cannot infer from --output, it falls back to tar.gz.
            """).strip(),
    )
    redact_local_paths = kwconf.Value(
        False,
        isflag=True,
        alias=['redact-local-paths'],
        help=textwrap.dedent("""
            Redact absolute local paths from the archive information file and
            remove the generated clone origins whose URLs point back to local
            working trees. By default these paths and origins are retained for
            agent handoff and overlay workflows.
            """).strip(),
    )
    # TODO: Re-enable when kwconf fixes modal default injection semantics.
    # set_config = kwconf.Value(
    #     None,
    #     nargs='+',
    #     alias=['set-config'],
    #     help=textwrap.dedent("""
    #         Store explicit archive_source defaults in this repository's local
    #         Git config. Values must be key=value assignments. Currently
    #         supports depth and format, e.g. --set_config depth=0 format=zip.
    #         When specified, the config is updated and no archive is created.
    #         """).strip(),
    # )
    verbose = kwconf.Value(1, help='verbosity level')

    @classmethod
    def main(
        cls, argv: list[str] | str | bool | None = True, **kwargs: Any
    ) -> Path:
        if 'no_submodules' in kwargs and 'submodules' not in kwargs:
            kwargs['submodules'] = not bool(kwargs.pop('no_submodules'))
        config = cls.cli(argv=argv, data=kwargs, strict=True)
        archive_path = archive_source(
            repo_dpath=config.repo_dpath,
            output=config.output,
            depth=config.depth,
            submodule_depth=config.submodule_depth,
            exclude_submodule=config.exclude_submodule,
            no_submodules=not bool(config.submodules),
            format=config.format,
            redact_local_paths=bool(config.redact_local_paths),
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
    output: PathLike | None = None,
    depth: DepthArg = 'full',
    submodule_depth: SubmoduleDepthSpecArg = None,
    exclude_submodule: str | list[str] | None = None,
    no_submodules: bool = False,
    format: ArchiveFormatArg = 'auto',
    redact_local_paths: bool = False,
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

        submodule_depth:
            Optional YAML depth spec for recursive submodules. If omitted,
            submodules inherit ``depth``. Scalars such as ``0``, ``25``, or
            ``'full'`` apply to every submodule. Mappings may use exact
            submodule paths, fnmatch-style glob keys, quoted ``"*"`` as a
            catch-all glob, and ``__default__`` as a non-glob fallback.

        exclude_submodule:
            Recursive submodule path selectors to omit from the archive. Each
            selector may be an exact recursive submodule path or a
            fnmatch-style glob pattern. Quote shell glob metacharacters when
            passing patterns through a shell. Omitted submodules are recorded
            in the manifest but not materialized.

        no_submodules:
            If true, omit all recursive submodule working trees from the
            archive.

        format:
            Archive format. ``'auto'`` infers from the output extension when
            possible and otherwise defaults to ``'tar.gz'``.

        redact_local_paths:
            If true, redact absolute source/output paths from the generated
            archive information file and remove generated clone origins that
            point back to local working trees.

        verbose:
            Verbosity level.

    Returns:
        The generated archive path.

    Notes:
        This function only archives committed/tracked source. Local edits,
        untracked files, ignored files, and build outputs are deliberately
        excluded. Every archive contains ``GIT_WELL_ARCHIVE_INFO.txt`` with
        the source/output paths, commits, history depths, and any intentional
        pruning. Use ``redact_local_paths=True`` when those local paths should
        not be included in the artifact.
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
    submodule_depth_policy = _parse_submodule_depth_spec(submodule_depth)
    exclude_submodule_paths = _normalize_submodule_path_list(
        exclude_submodule
    )

    archive_format = _resolve_archive_format(output, format)
    archive_path = _resolve_output(repo_root, output, prefix, archive_format)
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    submodule_status = _submodule_status(repo)
    submodule_decisions = _resolve_submodule_archive_decisions(
        submodule_status,
        policy=submodule_depth_policy,
        inherited_depth=normalized_depth,
        exclude_submodule=exclude_submodule_paths,
        no_submodules=bool(no_submodules),
    )

    log = _Logger(verbose)
    log.path('[source-archive] repo: ', repo_root)
    log.path('[source-archive] output directory: ', archive_path.parent)
    log(f'[source-archive] prefix: {prefix}')
    log(f'[source-archive] archive format: {archive_format}')
    log(
        '[source-archive] git history: {}'.format(
            'included' if include_git_history else 'omitted'
        )
    )
    if include_git_history:
        depth_label = 'full' if clone_depth is None else str(clone_depth)
        log(f'[source-archive] history depth: {depth_label}')
    else:
        log('[source-archive] depth: 0 (source-only git archive mode)')
    for line in submodule_depth_policy.summary_lines():
        log(f'[source-archive] {line}')
    if no_submodules:
        log('[source-archive] submodules: omitted by --no-submodules')
    elif exclude_submodule_paths:
        log(
            '[source-archive] excluded submodule selectors: '
            + ', '.join(exclude_submodule_paths)
        )
    log(f'[source-archive] superproject HEAD: {short_sha}')

    import shutil
    import tempfile

    tmpdir = Path(
        tempfile.mkdtemp(
            prefix=f'{repo_name}-source-archive.',
            dir=os.environ.get('TMPDIR', None),
        )
    )
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
                redact_local_paths=redact_local_paths,
                log=log,
            )
        else:
            log('[source-archive] exporting superproject with git archive')
            _extract_git_archive(repo, 'HEAD', stage, prefix)

        for decision in submodule_decisions:
            info = decision.info
            path = info.path
            submodule_sha = info.sha
            if decision.omitted:
                log(
                    f'[source-archive] omitting submodule {path}: '
                    f'{decision.reason}'
                )
                continue
            if info.status == '-':
                raise RuntimeError(
                    f"submodule '{path}' is not initialized; run: "
                    'git submodule update --init --recursive'
                )
            src_dpath = repo_root / path
            if not src_dpath.exists():
                raise RuntimeError(
                    f"submodule path '{path}' is missing; run: "
                    'git submodule update --init --recursive'
                )
            sub_repo = _open_exact_repo(src_dpath)
            if sub_repo is None:
                raise RuntimeError(
                    f"submodule path '{path}' is not an initialized Git "
                    'working tree; run: git submodule update --init --recursive'
                )
            _assert_has_head(sub_repo)
            sub_short = sub_repo.git.rev_parse('--short=12', 'HEAD').strip()
            log(
                f'[source-archive] exporting submodule {path} HEAD {sub_short} '
                f'depth={_depth_label(decision.depth)} mode={decision.mode}'
            )
            sub_clone_depth = _clone_depth_from_normalized_depth(
                decision.depth
            )
            if decision.depth != 0:
                _clone_committed_checkout(
                    src=sub_repo,
                    dst=archive_root / path,
                    commit=submodule_sha,
                    label=f'submodule {path}',
                    clone_depth=sub_clone_depth,
                    redact_local_paths=redact_local_paths,
                    log=log,
                )
            else:
                (archive_root / path).mkdir(parents=True, exist_ok=True)
                _extract_git_archive(
                    sub_repo, submodule_sha, stage, f'{prefix}/{path}'
                )

        manifest = archive_root / _ARCHIVE_INFO_FNAME
        _assert_archive_info_path_available(manifest)
        if include_git_history:
            _append_manifest_exclude(archive_root)

        _write_manifest(
            manifest=manifest,
            repo_root=repo_root,
            archive_path=archive_path,
            repo_name=repo_name,
            prefix=prefix,
            timestamp=timestamp,
            head_sha=head_sha,
            short_sha=short_sha,
            include_git_history=include_git_history,
            clone_depth=clone_depth,
            submodule_decisions=submodule_decisions,
            redact_local_paths=redact_local_paths,
        )

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


# TODO: Land this when kwconf modal dispatch can distinguish
# omitted values from defaults injected through modal kwargs.
#
# def _repo_local_archive_source_defaults(repo: 'git.Repo') -> dict[str, str]:
#     """
#     Read repo-local archive_source defaults from Git config.
#     """
#     defaults = {}
#     for field, key in _CONFIGURABLE_DEFAULTS.items():
#         value = _get_local_git_config(repo, key)
#         if value is not None:
#             defaults[field] = value
#     return defaults
#
#
# def _parse_set_config_assignments(
#     assignments: list[str] | str | None,
# ) -> dict[str, str]:
#     """
#     Parse explicit ``--set_config key=value`` assignments.
#     """
#     parsed = {}
#     if not assignments:
#         return parsed
#     if isinstance(assignments, str):
#         assignments = [assignments]
#     key_to_field = {
#         **{field: field for field in _CONFIGURABLE_DEFAULTS},
#         **{key: field for field, key in _CONFIGURABLE_DEFAULTS.items()},
#     }
#     for assignment in assignments:
#         if '=' not in assignment:
#             raise ValueError(
#                 '--set_config values must be key=value assignments; '
#                 f'got {assignment!r}'
#             )
#         key, value = assignment.split('=', 1)
#         field = key_to_field.get(key)
#         if field is None:
#             valid = ', '.join(sorted(key_to_field))
#             raise ValueError(
#                 f'unknown archive_source config key {key!r}; '
#                 f'expected one of: {valid}'
#             )
#         parsed[field] = value
#     _validate_archive_source_config_values(parsed)
#     return parsed
#
#
# def _get_local_git_config(repo: 'git.Repo', key: str) -> str | None:
#     import git
#
#     try:
#         return repo.git.config('--local', '--get', key).strip()
#     except git.GitCommandError:
#         return None
#
#
# def _set_archive_source_config(
#     repo: 'git.Repo',
#     assignments: dict[str, str],
# ) -> None:
#     """
#     Persist explicit archive defaults in the repository-local Git config.
#     """
#     _validate_archive_source_config_values(assignments)
#     for field, value in assignments.items():
#         repo.git.config('--local', _CONFIGURABLE_DEFAULTS[field], value)
#
#
# def _git_config_path(repo: 'git.Repo') -> Path:
#     """
#     Return the path to the local Git config for this repository.
#     """
#     return Path(repo.git_dir) / 'config'
#
#
# def _validate_archive_source_config_values(values: dict[str, str]) -> None:
#     if 'depth' in values:
#         _normalize_depth(values['depth'])
#     if 'format' in values:
#         _resolve_archive_format(None, cast(ArchiveFormatArg, values['format']))


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


def _normalize_depth(depth: DepthArg) -> int | None:
    import re

    if depth is None:
        return None
    if isinstance(depth, bool):
        raise ValueError(
            "depth must be a non-negative integer, None, or 'full'"
        )
    if isinstance(depth, int):
        value = depth
    else:
        text = str(depth)
        if text in {'', 'full'}:
            return None
        if text == '0':
            return 0
        if not re.match(r'^[1-9][0-9]*$', text):
            raise ValueError(
                "depth must be a non-negative integer, None, or 'full'"
            )
        value = int(text)
    if value < 0:
        raise ValueError(
            "depth must be a non-negative integer, None, or 'full'"
        )
    return value


def _depth_label(depth: int | None) -> str:
    """
    Render an internal normalized depth for humans.

    Example:
        >>> _depth_label(None)
        'full'
        >>> _depth_label(0)
        '0'
        >>> _depth_label(25)
        '25'
    """
    return 'full' if depth is None else str(depth)


def _clone_depth_from_normalized_depth(
    depth: int | None,
) -> int | None:
    """Return the Git clone ``--depth`` value for a normalized depth."""
    return None if depth in {0, None} else depth


def _parse_submodule_depth_spec(
    spec: SubmoduleDepthSpecArg,
) -> SubmoduleDepthPolicy:
    """
    Parse a ``--submodule-depth`` YAML spec.

    Example:
        >>> p = _parse_submodule_depth_spec(None)
        >>> p.resolve('lib', inherited_depth=7)
        7
        >>> p = _parse_submodule_depth_spec('full')
        >>> _depth_label(p.resolve('lib', inherited_depth=7))
        'full'
        >>> p = _parse_submodule_depth_spec('{__default__: 0, lib: 3}')
        >>> p.resolve('lib', inherited_depth=7)
        3
        >>> p.resolve('other', inherited_depth=7)
        0
        >>> p = _parse_submodule_depth_spec('{lib: 3}')
        >>> p.resolve('other', inherited_depth=7)
        7
    """
    if spec is None:
        return SubmoduleDepthPolicy(specified=False)

    if isinstance(spec, dict):
        parsed = spec
    elif isinstance(spec, int):
        parsed = spec
    else:
        text = str(spec)
        if text == '':
            return SubmoduleDepthPolicy(specified=False)
        import yaml

        parsed = yaml.safe_load(text)

    if isinstance(parsed, dict):
        exact_depths: dict[str, int | None] = {}
        glob_depths: dict[str, int | None] = {}
        star_depth: Any = _UNSET
        default_depth: Any = _UNSET
        for raw_key, raw_depth in parsed.items():
            if not isinstance(raw_key, str):
                raise ValueError(
                    'submodule depth mapping keys must be strings; got '
                    f'{raw_key!r}'
                )
            key = raw_key.strip()
            if not key:
                raise ValueError('submodule depth mapping keys cannot be empty')
            normalized = _normalize_depth(cast(DepthArg, raw_depth))
            if key == '__default__':
                default_depth = normalized
            elif key == '*':
                star_depth = normalized
            elif _looks_like_fnmatch_pattern(key):
                glob_depths[key] = normalized
            else:
                exact_depths[key] = normalized
        return SubmoduleDepthPolicy(
            specified=True,
            raw=parsed,
            exact_depths=exact_depths,
            glob_depths=glob_depths,
            star_depth=star_depth,
            default_depth=default_depth,
        )
    if isinstance(parsed, (list, tuple)):
        raise ValueError(
            'submodule depth spec must be a scalar depth or a YAML mapping, '
            f'not {type(parsed).__name__}'
        )
    return SubmoduleDepthPolicy(
        specified=True,
        raw=parsed,
        scalar_depth=_normalize_depth(cast(DepthArg, parsed)),
    )


def _looks_like_fnmatch_pattern(text: str) -> bool:
    """
    Return true for keys containing fnmatch glob metacharacters.

    Example:
        >>> _looks_like_fnmatch_pattern('external/lib')
        False
        >>> _looks_like_fnmatch_pattern('external/*')
        True
        >>> _looks_like_fnmatch_pattern('external/lib[12]')
        True
    """
    return any(ch in text for ch in '*?[')


def _normalize_submodule_path_list(
    value: str | list[str] | None
) -> list[str]:
    """
    Normalize CLI/API submodule path lists.

    Example:
        >>> _normalize_submodule_path_list(None)
        []
        >>> _normalize_submodule_path_list('extern/data')
        ['extern/data']
        >>> _normalize_submodule_path_list(['extern/data', ' other '])
        ['extern/data', 'other']
    """
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    else:
        items = list(value)
    paths = []
    for item in items:
        path = str(item).strip()
        if path:
            paths.append(path)
    return paths


def _resolve_exclude_submodule_paths(
    submodule_status: list[SubmoduleStatus],
    exclude_submodule: list[str],
    *,
    no_submodules: bool,
) -> set[str]:
    """
    Resolve ``--exclude-submodule`` selectors to recursive submodule paths.

    Each selector may be either an exact recursive submodule path or an
    fnmatch-style pattern over recursive submodule paths. Shell-expanded globs
    arrive here as ordinary argv entries, so those entries still have to match
    known recursive submodule paths individually.

    Example:
        >>> infos = [
        ...     SubmoduleStatus(' ', 'a' * 40, 'lib/a', ''),
        ...     SubmoduleStatus(' ', 'b' * 40, 'lib/b', ''),
        ...     SubmoduleStatus(' ', 'c' * 40, 'third_party/c', ''),
        ... ]
        >>> sorted(_resolve_exclude_submodule_paths(infos, ['lib/*'], no_submodules=False))
        ['lib/a', 'lib/b']
        >>> sorted(_resolve_exclude_submodule_paths(infos, ['lib/a'], no_submodules=False))
        ['lib/a']
        >>> _resolve_exclude_submodule_paths(infos, ['missing/*'], no_submodules=False)
        Traceback (most recent call last):
        ...
        ValueError: --exclude-submodule selector does not match a recursive submodule: missing/*...
    """
    if not exclude_submodule:
        return set()

    known_paths = sorted({info.path for info in submodule_status})
    exclude_set: set[str] = set()
    unmatched: list[str] = []

    for selector in exclude_submodule:
        if selector in known_paths:
            exclude_set.add(selector)
            continue

        if _looks_like_fnmatch_pattern(selector):
            matches = [
                path
                for path in known_paths
                if fnmatch.fnmatchcase(path, selector)
            ]
            if matches:
                exclude_set.update(matches)
                continue

        unmatched.append(selector)

    if unmatched and not no_submodules:
        rendered = ', '.join(unmatched)
        message = (
            '--exclude-submodule selector does not match a recursive '
            f'submodule: {rendered}'
        )
        if any(_looks_like_fnmatch_pattern(item) for item in unmatched):
            message += (
                "; quote glob patterns such as 'external/*' so your "
                'shell does not expand them before git-well sees them'
            )
        raise ValueError(message)

    inherited_excludes = {
        path
        for path in known_paths
        if any(
            path == excluded or path.startswith(excluded.rstrip('/') + '/')
            for excluded in exclude_set
        )
    }
    exclude_set.update(inherited_excludes)

    return exclude_set


def _resolve_submodule_archive_decisions(
    submodule_status: list[SubmoduleStatus],
    *,
    policy: SubmoduleDepthPolicy,
    inherited_depth: int | None,
    exclude_submodule: list[str],
    no_submodules: bool,
) -> list[SubmoduleArchiveDecision]:
    """
    Resolve submodule archive decisions.

    Example:
        >>> infos = [SubmoduleStatus(' ', 'a' * 40, 'lib/a', ''), SubmoduleStatus(' ', 'b' * 40, 'lib/b', '')]
        >>> policy = _parse_submodule_depth_spec('{"*": 0, lib/a: 5}')
        >>> decisions = _resolve_submodule_archive_decisions(infos, policy=policy, inherited_depth=10, exclude_submodule=[], no_submodules=False)
        >>> [(d.info.path, d.mode, _depth_label(d.depth)) for d in decisions]
        [('lib/a', 'shallow-git-checkout', '5'), ('lib/b', 'source-only-git-archive', '0')]
        >>> decisions = _resolve_submodule_archive_decisions(infos, policy=policy, inherited_depth=10, exclude_submodule=['lib/b'], no_submodules=False)
        >>> [(d.info.path, d.omitted, d.reason) for d in decisions]
        [('lib/a', False, 'included'), ('lib/b', True, 'excluded by --exclude-submodule')]
    """
    exclude_set = _resolve_exclude_submodule_paths(
        submodule_status,
        exclude_submodule,
        no_submodules=no_submodules,
    )
    decisions: list[SubmoduleArchiveDecision] = []
    for info in submodule_status:
        if no_submodules:
            decisions.append(
                SubmoduleArchiveDecision(
                    info=info,
                    omitted=True,
                    depth=inherited_depth,
                    mode='omitted',
                    reason='omitted by --no-submodules',
                )
            )
            continue
        if info.path in exclude_set:
            decisions.append(
                SubmoduleArchiveDecision(
                    info=info,
                    omitted=True,
                    depth=inherited_depth,
                    mode='omitted',
                    reason='excluded by --exclude-submodule',
                )
            )
            continue

        depth = policy.resolve(info.path, inherited_depth)
        if depth == 0:
            mode = 'source-only-git-archive'
        elif depth is None:
            mode = 'full-git-checkout'
        else:
            mode = 'shallow-git-checkout'
        decisions.append(
            SubmoduleArchiveDecision(
                info=info,
                omitted=False,
                depth=depth,
                mode=mode,
                reason='included',
            )
        )
    return decisions


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


def _clone_options_for_depth(clone_depth: int | None) -> list[str]:
    options = ['--quiet', '--no-local', '--single-branch', '--no-checkout']
    if clone_depth is not None:
        options += ['--depth', str(clone_depth)]
    return options


def _clone_committed_checkout(
    src: 'git.Repo',
    dst: PathLike,
    commit: str,
    label: str,
    clone_depth: int | None,
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
    _checkout_commit(cloned, commit, label, clone_depth, log)

    if redact_local_paths:
        for remote in list(cloned.remotes):
            cloned.git.remote('remove', remote.name)

    # The archive is for inspection, not local recovery. Expire the clone's
    # fresh reflogs so they do not keep extra objects alive, then repack to make
    # the archived .git directory reasonably small.
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


def _checkout_commit(
    repo: 'git.Repo',
    commit: str,
    label: str,
    clone_depth: int | None,
    log: '_Logger',
) -> None:
    import git

    try:
        repo.git.checkout('-q', '--detach', commit)
        return
    except git.GitCommandError:
        log(
            f'[source-archive] checkout of {label} failed after clone; fetching exact commit'
        )

    if clone_depth is not None:
        try:
            repo.git.fetch(
                '--quiet', '--depth', str(clone_depth), 'origin', commit
            )
        except git.GitCommandError:
            repo.git.fetch('--quiet', 'origin', commit)
    else:
        repo.git.fetch('--quiet', 'origin', commit)
    repo.git.checkout('-q', '--detach', commit)


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


def _append_manifest_exclude(repo_dpath: PathLike) -> None:
    info = Path(repo_dpath) / '.git' / 'info'
    if info.exists():
        exclude = info / 'exclude'
        rule = f'/{_ARCHIVE_INFO_FNAME}'
        existing = exclude.read_text() if exclude.exists() else ''
        if rule not in existing.splitlines():
            block = (
                '\n# Added by git-well archive_source for generated metadata.\n'
                f'{rule}\n'
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
    submodule_decisions: list[SubmoduleArchiveDecision],
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
        '',
        f'Content pruning: {"yes" if pruning_details else "none"}',
    ]
    if pruning_details:
        lines.append('Pruning details:')
        lines.extend(f'- {detail}' for detail in pruning_details)

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


class _Logger:
    def __init__(self, verbose: int) -> None:
        self.verbose = verbose

    def __call__(self, msg: str) -> None:
        if self.verbose:
            print(msg)

    def path(self, prefix: str, path: PathLike, suffix: str = '') -> None:
        if self.verbose:
            from git_well._utils import rich_print_path

            rich_print_path(prefix, path, suffix=suffix)


__cli__ = ArchiveSourceCLI


if __name__ == '__main__':
    """
    CommandLine:
        python -m git_well.git_archive_source --help
        python -m git_well archive_source --help
    """
    main()
