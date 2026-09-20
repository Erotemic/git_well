"""High-level source archive staging and build operations."""

from __future__ import annotations

import os
from collections.abc import Callable, Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal, cast

from ._common import (
    ArchiveFormatArg,
    BranchRefInventory,
    DepthArg,
    HistoryBlobsArg,
    PathLike,
    PromisorPruneResult,
    ResolvedArchiveFormat,
    SubmoduleArchiveDecision,
    SubmoduleDepthSpecArg,
    _ARCHIVE_INFO_FNAME,
    _FORMAT_TO_EXTENSION,
    _Logger,
    _UNINITIALIZED_SUBMODULE_REASON,
)
from ._io import (
    _append_generated_excludes,
    _assert_archive_info_path_available,
    _extract_git_archive,
    _normalize_archive_root_name,
    _normalize_generated_exclude,
    _resolve_archive_format,
    _resolve_output,
    _write_archive,
    _write_manifest,
)
from ._policy import (
    _clone_depth_from_normalized_depth,
    _depth_label,
    _normalize_archive_path_list,
    _normalize_depth,
    _normalize_history_blobs,
    _normalize_submodule_path_list,
    _parse_submodule_depth_spec,
    _resolve_submodule_archive_decisions,
)
from ._prune import (
    _apply_promisor_history_pruning,
    _apply_worktree_path_exclusions,
)
from ._repo import (
    _assert_has_head,
    _branch_ref_inventory,
    _clone_committed_checkout,
    _coerce_repo,
    _logical_repo_root,
    _open_exact_repo,
    _submodule_status,
)

ArchiveSourceHook = Callable[['ArchiveSourceContext'], None]
ArchiveSourceHookArg = ArchiveSourceHook | Iterable[ArchiveSourceHook] | None


class ArchiveSourceHookError(RuntimeError):
    """
    Wrap an exception raised by a programmatic archive-source hook.
    """

    def __init__(
        self,
        phase: Literal['prepare', 'validate'],
        hook: ArchiveSourceHook,
        cause: Exception,
        context: 'ArchiveSourceContext',
    ) -> None:
        self.phase = phase
        self.hook = hook
        self.hook_name = _hook_name(hook)
        self.cause = cause
        self.stage_dpath = context.stage_dpath
        self.archive_root = context.archive_root
        self.stage_retained = context.keep_stage
        retained_suffix = (
            f'; retained stage: {context.stage_dpath}'
            if context.keep_stage
            else ''
        )
        super().__init__(
            f'archive_source {phase} hook {self.hook_name!r} failed: '
            f'{cause}{retained_suffix}'
        )


@dataclass
class ArchiveSourceContext:
    """
    Mutable staging context exposed to programmatic archive extensions.

    Prepare hooks may add files below :attr:`archive_root` and register those
    generated paths with :meth:`add_generated_excludes`. Validation hooks run
    after git-well has written its own metadata and should inspect without
    mutating the staged tree.
    """

    repo_root: Path
    archive_path: Path
    archive_format: ResolvedArchiveFormat
    stage_dpath: Path
    archive_root: Path
    archive_root_name: str
    repo_name: str
    timestamp: str
    head_sha: str
    short_sha: str
    normalized_depth: int | None
    include_git_history: bool
    clone_depth: int | None
    branch_refs: BranchRefInventory | None
    submodule_decisions: tuple[SubmoduleArchiveDecision, ...]
    exclude_path_selectors: tuple[str, ...]
    excluded_worktree_paths: tuple[str, ...]
    unmatched_exclude_path_selectors: tuple[str, ...]
    excluded_worktree_bytes: int
    history_blobs: HistoryBlobsArg
    promisor_prune_results: tuple[PromisorPruneResult, ...]
    redact_local_paths: bool
    all_branches: bool
    keep_stage: bool
    _log: '_Logger' = field(repr=False)
    _generated_excludes: list[str] = field(
        default_factory=lambda: [f'/{_ARCHIVE_INFO_FNAME}'], repr=False
    )
    _metadata_finalized: bool = field(default=False, init=False, repr=False)
    _archive_written: bool = field(default=False, init=False, repr=False)

    @property
    def depth(self) -> int | None:
        """Normalized superproject depth; ``None`` means full history."""
        return self.normalized_depth

    @property
    def manifest_path(self) -> Path:
        """Path where git-well's archive information file is generated."""
        return self.archive_root / _ARCHIVE_INFO_FNAME

    def source_display_path(self) -> str:
        """Return the source path using the configured redaction policy."""
        if self.redact_local_paths:
            return '(redacted by --redact-local-paths)'
        return os.fspath(self.repo_root)

    def output_display_path(self) -> str:
        """Return the output path using the configured redaction policy."""
        if self.redact_local_paths:
            return '(redacted by --redact-local-paths)'
        return os.fspath(self.archive_path)

    def add_generated_excludes(self, paths: str | Iterable[str]) -> None:
        """
        Ignore generated archive-relative paths in history-bearing checkouts.

        These rules only affect ``.git/info/exclude`` in the staged clone.
        They do not hide modifications to tracked files.
        """
        if self._metadata_finalized:
            raise RuntimeError(
                'generated excludes cannot be added after metadata finalization'
            )
        if isinstance(paths, str):
            paths = [paths]
        for path in paths:
            rule = _normalize_generated_exclude(path)
            if rule not in self._generated_excludes:
                self._generated_excludes.append(rule)

    def finalize_metadata(self) -> Path:
        """Write git-well metadata after all prepare hooks have run."""
        if self._metadata_finalized:
            return self.manifest_path
        manifest = self.manifest_path
        _assert_archive_info_path_available(manifest)
        if self.include_git_history:
            _append_generated_excludes(
                self.archive_root, self._generated_excludes
            )
        _write_manifest(
            manifest=manifest,
            repo_root=self.repo_root,
            archive_path=self.archive_path,
            repo_name=self.repo_name,
            prefix=self.archive_root_name,
            timestamp=self.timestamp,
            head_sha=self.head_sha,
            short_sha=self.short_sha,
            include_git_history=self.include_git_history,
            clone_depth=self.clone_depth,
            branch_refs=self.branch_refs,
            submodule_decisions=self.submodule_decisions,
            exclude_path_selectors=self.exclude_path_selectors,
            excluded_worktree_paths=self.excluded_worktree_paths,
            unmatched_exclude_path_selectors=self.unmatched_exclude_path_selectors,
            excluded_worktree_bytes=self.excluded_worktree_bytes,
            history_blobs=self.history_blobs,
            promisor_prune_results=self.promisor_prune_results,
            redact_local_paths=self.redact_local_paths,
        )
        self._metadata_finalized = True
        return manifest

    def write_archive(self) -> Path:
        """Finalize metadata and serialize the staged archive exactly once."""
        if self._archive_written:
            raise RuntimeError('archive has already been written')
        self.finalize_metadata()
        _write_archive(
            self.stage_dpath,
            self.archive_root_name,
            self.archive_path,
            self.archive_format,
        )
        self._archive_written = True
        if self.include_git_history and self.history_blobs == 'full':
            try:
                from .patch import register_full_archive

                register_full_archive(
                    repo_root=self.repo_root,
                    archive_path=self.archive_path,
                    head_sha=self.head_sha,
                    archive_root_name=self.archive_root_name,
                    include_git_history=True,
                    normalized_depth=self.normalized_depth,
                    all_branches=self.all_branches,
                    timestamp=self.timestamp,
                    archive_format=self.archive_format,
                )
            except Exception as ex:
                self._log.warning(
                    '[source-archive] WARNING: could not record archive for future '
                    f'patch=auto selection: {ex}'
                )
        self._log(f'[source-archive] wrote: {self.archive_path}')
        if self.archive_format == 'zip':
            self._log(
                f'[source-archive] list contents: unzip -l {self.archive_path}'
            )
        else:
            self._log(
                f'[source-archive] list contents: tar -tf '
                f'{self.archive_path} | less'
            )
        return self.archive_path

def archive_source(
    repo_dpath: PathLike = '.',
    output: PathLike | None = None,
    depth: DepthArg = 'full',
    submodule_depth: SubmoduleDepthSpecArg = None,
    exclude_submodule: str | list[str] | None = None,
    exclude_path: str | list[str] | None = None,
    history_blobs: HistoryBlobsArg | str = 'full',
    no_submodules: bool = False,
    format: ArchiveFormatArg = 'auto',
    redact_local_paths: bool = False,
    verbose: int = 1,
    all_branches: bool = False,
    prepare: ArchiveSourceHookArg = None,
    validate: ArchiveSourceHookArg = None,
    archive_root_name: str | None = None,
    keep_stage: bool = False,
    patch: PathLike | None = None,
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
            ``<repo>-source-<timestamp>-<short sha>.<format-extension>``. Patch
            mode appends ``-patch`` to the generated archive root name.

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

        exclude_path:
            Archive-root-relative tracked path selectors to omit from staged
            working trees. Exact file or directory paths and fnmatch-style
            patterns are accepted. This never rewrites Git history. In
            history-bearing repositories omitted paths remain available as Git
            objects and the staged repository uses sparse checkout so status
            remains clean and the files can be restored.

        history_blobs:
            ``'full'`` keeps every reachable Git blob, including blobs for
            paths omitted by ``exclude_path``. ``'sparse'`` turns those path
            exclusions into a partial/promisor repository: matching blobs are
            removed from local Git object storage without changing commit or
            tree hashes and may be lazily recovered from the recorded promisor
            remote. The default is ``'full'``.

        no_submodules:
            If true, omit all recursive submodule working trees from the
            archive. When false, initialized submodules are included and
            uninitialized submodules are omitted with a warning.

        format:
            Archive format. ``'auto'`` infers from the output extension when
            possible and otherwise defaults to ``'tar.gz'``.

        patch:
            If omitted, write a normal full source archive. ``'auto'`` creates
            an incremental source patch against the closest compatible full
            archive recorded for this repository. Any other value is treated as
            an explicit base archive path. Patch mode requires superproject Git
            history and currently supports descendant updates only. Prepare and
            validate hooks still operate on the complete target staging tree.

        redact_local_paths:
            If true, redact absolute source/output paths from the generated
            archive information file and remove generated clone origins that
            point back to local working trees.

        verbose:
            Verbosity level.

        all_branches:
            If true, include every local branch and every remote-tracking
            branch already cached in the superproject repository. No configured
            remote is contacted. This cannot be combined with ``depth=0``.

        prepare:
            One callable, or an iterable of callables, invoked after committed
            source and submodules are staged but before git-well metadata is
            written. Prepare hooks may modify ``context.archive_root``.

        validate:
            One callable, or an iterable of callables, invoked after git-well
            metadata is written and immediately before serialization.
            Validation hooks should not mutate the staged tree.

        archive_root_name:
            Optional programmatic override for the top-level directory inside
            the archive. This is intentionally not exposed by the CLI.

        keep_stage:
            If true, retain the temporary staging directory after success or
            failure. Hook errors expose the retained path as ``stage_dpath``.

    Returns:
        The generated archive path.

    Notes:
        This function only archives committed/tracked source. Local edits,
        untracked files, ignored files, and build outputs are deliberately
        excluded. Hook failures abort serialization and are wrapped in
        :class:`ArchiveSourceHookError` with the hook phase and name.
    """
    normalized_history_blobs = _normalize_history_blobs(history_blobs)
    if patch is not None and _normalize_depth(depth) == 0:
        from .patch import SourcePatchError

        raise SourcePatchError(
            'archive_source patch mode requires Git history; depth=0 is not supported'
        )
    if patch is not None and normalized_history_blobs != 'full':
        from .patch import SourcePatchError

        raise SourcePatchError(
            'archive_source patch mode currently requires history_blobs="full"'
        )
    prepare_hooks = _coerce_archive_hooks(prepare, phase='prepare')
    validate_hooks = _coerce_archive_hooks(validate, phase='validate')
    with stage_source_archive(
        repo_dpath=repo_dpath,
        output=output,
        depth=depth,
        submodule_depth=submodule_depth,
        exclude_submodule=exclude_submodule,
        exclude_path=exclude_path,
        history_blobs=normalized_history_blobs,
        no_submodules=no_submodules,
        format=format,
        redact_local_paths=redact_local_paths,
        verbose=verbose,
        all_branches=all_branches,
        archive_root_name=archive_root_name,
        keep_stage=keep_stage,
    ) as context:
        if patch is not None and output is None:
            extension = _FORMAT_TO_EXTENSION[context.archive_format]
            context.archive_path = (
                context.repo_root
                / f'{context.archive_root_name}-patch{extension}'
            ).resolve()
        _run_archive_hooks('prepare', prepare_hooks, context)
        context.finalize_metadata()
        _run_archive_hooks('validate', validate_hooks, context)
        if patch is None:
            return context.write_archive()
        from .patch import build_source_patch

        return build_source_patch(
            context=context, patch=patch, write_archive=_write_archive
        )


@contextmanager
def stage_source_archive(
    repo_dpath: PathLike = '.',
    output: PathLike | None = None,
    depth: DepthArg = 'full',
    submodule_depth: SubmoduleDepthSpecArg = None,
    exclude_submodule: str | list[str] | None = None,
    exclude_path: str | list[str] | None = None,
    history_blobs: HistoryBlobsArg | str = 'full',
    no_submodules: bool = False,
    format: ArchiveFormatArg = 'auto',
    redact_local_paths: bool = False,
    verbose: int = 1,
    all_branches: bool = False,
    archive_root_name: str | None = None,
    keep_stage: bool = False,
) -> Iterator[ArchiveSourceContext]:
    """
    Stage committed source and yield a context before metadata/serialization.

    The temporary staging tree is removed when the context exits unless
    ``keep_stage=True``. Call :meth:`ArchiveSourceContext.write_archive` to
    serialize when using this lower-level API directly.
    """
    repo = _coerce_repo(repo_dpath)
    _assert_has_head(repo)

    repo_root = Path(cast(str, repo.working_tree_dir)).resolve()
    logical_repo_root = _logical_repo_root(repo_dpath, repo_root)
    repo_name = logical_repo_root.name
    head_sha = repo.head.commit.hexsha
    short_sha = repo.git.rev_parse('--short=12', 'HEAD').strip()
    import ubelt as ub

    timestamp = ub.timestamp()
    default_root_name = f'{repo_name}-source-{timestamp}-{short_sha}'
    if archive_root_name is None:
        resolved_root_name = default_root_name
    else:
        resolved_root_name = _normalize_archive_root_name(archive_root_name)

    normalized_depth = _normalize_depth(depth)
    include_git_history = normalized_depth != 0
    if all_branches and not include_git_history:
        raise ValueError(
            '--all-branches requires Git history; use --depth 1 or greater'
        )
    clone_depth = None if normalized_depth in {0, None} else normalized_depth
    branch_refs = _branch_ref_inventory(repo) if all_branches else None
    submodule_depth_policy = _parse_submodule_depth_spec(submodule_depth)
    exclude_submodule_paths = _normalize_submodule_path_list(
        exclude_submodule
    )

    exclude_path_selectors = _normalize_archive_path_list(exclude_path)
    normalized_history_blobs = _normalize_history_blobs(history_blobs)

    archive_format = _resolve_archive_format(output, format)
    archive_path = _resolve_output(
        repo_root, output, resolved_root_name, archive_format
    )
    archive_path.parent.mkdir(parents=True, exist_ok=True)

    submodule_status = _submodule_status(repo)
    submodule_decisions = tuple(
        _resolve_submodule_archive_decisions(
            submodule_status,
            policy=submodule_depth_policy,
            inherited_depth=normalized_depth,
            exclude_submodule=exclude_submodule_paths,
            no_submodules=bool(no_submodules),
        )
    )

    log = _Logger(verbose)
    log.path('[source-archive] repo: ', repo_root)
    log.path('[source-archive] output directory: ', archive_path.parent)
    log(f'[source-archive] prefix: {resolved_root_name}')
    log(f'[source-archive] archive format: {archive_format}')
    log(
        '[source-archive] git history: {}'.format(
            'included' if include_git_history else 'omitted'
        )
    )
    if include_git_history:
        depth_label = 'full' if clone_depth is None else str(clone_depth)
        log(f'[source-archive] history depth: {depth_label}')
        if all_branches:
            assert branch_refs is not None
            log(
                '[source-archive] branches: all locally cached '
                f'({len(branch_refs.local_branches)} local, '
                f'{len(branch_refs.remote_tracking_branches)} remote-tracking)'
            )
        else:
            log('[source-archive] branches: current HEAD history only')
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
    if exclude_path_selectors:
        log(
            '[source-archive] worktree exclusion selectors: '
            + ', '.join(exclude_path_selectors)
        )
    log(f'[source-archive] history blobs: {normalized_history_blobs}')
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
        archive_root = stage / resolved_root_name

        if include_git_history:
            log('[source-archive] cloning superproject')
            _clone_committed_checkout(
                src=repo,
                dst=archive_root,
                commit=head_sha,
                label='superproject',
                clone_depth=clone_depth,
                branch_refs=branch_refs,
                redact_local_paths=redact_local_paths,
                log=log,
            )
        else:
            log('[source-archive] exporting superproject with git archive')
            _extract_git_archive(
                repo, 'HEAD', stage, resolved_root_name
            )

        staged_repo_units = [
            ('', repo, head_sha, archive_root, include_git_history)
        ]

        for decision in submodule_decisions:
            info = decision.info
            path = info.path
            submodule_sha = info.sha
            if decision.omitted:
                if decision.reason == _UNINITIALIZED_SUBMODULE_REASON:
                    log.warning(
                        f'[source-archive] WARNING: omitting submodule '
                        f'{path}: {decision.reason}'
                        + '; run: git submodule update --init --recursive '
                        'to include it'
                    )
                else:
                    log(
                        f'[source-archive] omitting submodule {path}: '
                        f'{decision.reason}'
                    )
                continue
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
                    branch_refs=None,
                    redact_local_paths=redact_local_paths,
                    log=log,
                )
            else:
                (archive_root / path).mkdir(parents=True, exist_ok=True)
                _extract_git_archive(
                    sub_repo,
                    submodule_sha,
                    stage,
                    f'{resolved_root_name}/{path}',
                )
            staged_repo_units.append(
                (
                    path,
                    sub_repo,
                    submodule_sha,
                    archive_root / path,
                    decision.depth != 0,
                )
            )

        (
            excluded_worktree_paths,
            unmatched_exclude_path_selectors,
            excluded_worktree_bytes,
        ) = _apply_worktree_path_exclusions(
            staged_repo_units, exclude_path_selectors, log
        )

        if normalized_history_blobs == 'sparse':
            promisor_prune_results = _apply_promisor_history_pruning(
                staged_repo_units,
                exclude_path_selectors,
                redact_local_paths=redact_local_paths,
                all_branches=all_branches,
                log=log,
            )
        else:
            promisor_prune_results = ()

        context = ArchiveSourceContext(
            repo_root=repo_root,
            archive_path=archive_path,
            archive_format=archive_format,
            stage_dpath=stage,
            archive_root=archive_root,
            archive_root_name=resolved_root_name,
            repo_name=repo_name,
            timestamp=timestamp,
            head_sha=head_sha,
            short_sha=short_sha,
            normalized_depth=normalized_depth,
            include_git_history=include_git_history,
            clone_depth=clone_depth,
            branch_refs=branch_refs,
            submodule_decisions=submodule_decisions,
            exclude_path_selectors=tuple(exclude_path_selectors),
            excluded_worktree_paths=excluded_worktree_paths,
            unmatched_exclude_path_selectors=unmatched_exclude_path_selectors,
            excluded_worktree_bytes=excluded_worktree_bytes,
            history_blobs=normalized_history_blobs,
            promisor_prune_results=promisor_prune_results,
            redact_local_paths=redact_local_paths,
            all_branches=all_branches,
            keep_stage=keep_stage,
            _log=log,
        )
        yield context
    finally:
        if keep_stage:
            log.path('[source-archive] retained stage: ', tmpdir)
        else:
            shutil.rmtree(tmpdir, ignore_errors=True)


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

def _hook_name(hook: ArchiveSourceHook) -> str:
    name = getattr(hook, '__qualname__', None)
    if name is None:
        name = getattr(hook, '__name__', None)
    if name is None:
        name = hook.__class__.__qualname__
    return str(name)


def _coerce_archive_hooks(
    hooks: ArchiveSourceHookArg,
    phase: Literal['prepare', 'validate'],
) -> tuple[ArchiveSourceHook, ...]:
    if hooks is None:
        return ()
    if callable(hooks):
        # Callable objects may also satisfy Iterable, so make this branch's
        # intended interpretation explicit to static type checkers.
        return (cast(ArchiveSourceHook, hooks),)
    iterable_hooks = cast(Iterable[ArchiveSourceHook], hooks)
    try:
        coerced = tuple(iterable_hooks)
    except TypeError as ex:
        raise TypeError(
            f'{phase} hooks must be a callable or iterable of callables'
        ) from ex
    for hook in coerced:
        if not callable(hook):
            raise TypeError(
                f'{phase} hooks must contain only callables; got {hook!r}'
            )
    return coerced


def _run_archive_hooks(
    phase: Literal['prepare', 'validate'],
    hooks: Sequence[ArchiveSourceHook],
    context: ArchiveSourceContext,
) -> None:
    for hook in hooks:
        try:
            hook(context)
        except Exception as ex:
            raise ArchiveSourceHookError(phase, hook, ex, context) from ex
