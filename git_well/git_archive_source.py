#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
"""Command-line interface for creating committed source archives."""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any

import kwconf

from git_well.archive_source import archive_source


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
    in all modes. Initialized submodules are included; uninitialized submodules
    are omitted with a warning and recorded as pruned in the archive manifest.
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
            <repo>-source-<timestamp>-<short-sha>.<format-extension>. Patch
            mode appends ``-patch`` to that generated archive root name.
            """).strip(),
    )
    depth = kwconf.Value(
        'full',
        short_alias=['d'],
        help=textwrap.dedent("""
            Git history depth: "full" for all current-HEAD history, a positive
            integer for shallow history, or 0 for source-only git archive mode.
            """).strip(),
    )
    all_branches = kwconf.Value(
        False,
        isflag=True,
        alias=['all-branches'],
        help=textwrap.dedent("""
            Include every local branch and every remote-tracking branch already
            cached in the superproject repository. This never contacts configured
            remotes; fetch contributor forks before archiving. Requires Git
            history, so it cannot be combined with --depth 0.
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
    exclude_submodule: list[str] = kwconf.Value(
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
            Materialize initialized recursive submodule working trees.
            Uninitialized submodules are omitted with a warning. Pass
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
    patch = kwconf.Value(
        None,
        parser=str,
        help=textwrap.dedent("""
            Create an incremental source patch against a prior full Git-bearing
            source archive. Use "auto" to select the closest compatible full
            archive previously written by git-well, or pass an explicit base
            archive path. Patch mode requires superproject Git history and v1
            only supports descendant updates with the same superproject history
            and --all-branches policy.
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
    verbose: int = kwconf.Value(
        1,
        isflag='counter',
        short_alias=['v'],
        help='Increase verbosity; repeat for more detail',
    )

    @classmethod
    def main(
        cls, argv: list[str] | str | bool | None = True, **kwargs: Any
    ) -> Path:
        if 'no_submodules' in kwargs and 'submodules' not in kwargs:
            kwargs['submodules'] = not bool(kwargs.pop('no_submodules'))
        config = cls.cli(argv=argv, data=kwargs, strict=True)
        return archive_source(
            repo_dpath=config.repo_dpath,
            output=config.output,
            depth=config.depth,
            all_branches=bool(config.all_branches),
            submodule_depth=config.submodule_depth,
            exclude_submodule=config.exclude_submodule,
            no_submodules=not bool(config.submodules),
            format=config.format,
            patch=config.patch,
            redact_local_paths=bool(config.redact_local_paths),
            verbose=config.verbose,
        )


def main(argv: list[str] | str | bool | None = True, **kwargs: Any) -> Path:
    """Run the archive-source command-line interface."""
    return ArchiveSourceCLI.main(argv=argv, **kwargs)


# Legacy import compatibility -------------------------------------------------
#
# ``git_archive_source`` used to own all archive implementation details. Keep
# those names importable here so existing Python callers do not have to move in
# lockstep with the internal package split. New code should use
# ``git_well.archive_source``.
from git_well.archive_source import (  # noqa: E402,F401
    ArchiveFormatArg,
    ArchiveSourceContext,
    ArchiveSourceHook,
    ArchiveSourceHookArg,
    ArchiveSourceHookError,
    BranchRefInventory,
    DepthArg,
    PathLike,
    ResolvedArchiveFormat,
    SubmoduleArchiveDecision,
    SubmoduleDepthPolicy,
    SubmoduleDepthSpecArg,
    SubmoduleStatus,
    build_source_archive,
    stage_source_archive,
)
from git_well.archive_source._common import (  # noqa: E402,F401
    _ARCHIVE_INFO_FNAME,
    _FORMAT_ALIASES,
    _FORMAT_TO_EXTENSION,
    _FORMAT_TO_TAR_MODE,
    _Logger,
    _UNINITIALIZED_SUBMODULE_REASON,
)
from git_well.archive_source._io import (  # noqa: E402,F401
    _add_zip_entry,
    _append_generated_excludes,
    _assert_archive_info_path_available,
    _extract_git_archive,
    _infer_format_from_output,
    _normalize_archive_root_name,
    _normalize_format,
    _normalize_generated_exclude,
    _resolve_archive_format,
    _resolve_output,
    _safe_extractall,
    _write_archive,
    _write_manifest,
)
from git_well.archive_source._policy import (  # noqa: E402,F401
    _UNSET,
    _clone_depth_from_normalized_depth,
    _depth_label,
    _looks_like_fnmatch_pattern,
    _normalize_depth,
    _normalize_submodule_path_list,
    _parse_submodule_depth_spec,
    _resolve_exclude_submodule_paths,
    _resolve_submodule_archive_decisions,
)
from git_well.archive_source._repo import (  # noqa: E402,F401
    _assert_has_head,
    _branch_ref_inventory,
    _checkout_commit,
    _clone_committed_checkout,
    _clone_options_for_depth,
    _collect_committed_submodules,
    _committed_gitlinks,
    _committed_gitmodule_paths,
    _configure_archive_git_portability,
    _copy_cached_branch_refs,
    _coerce_repo,
    _fetch_exact_commit,
    _import_local_history_from_object_database,
    _local_history_slice,
    _logical_cwd,
    _logical_repo_root,
    _open_exact_repo,
    _record_shallow_boundaries,
    _remove_remote_configs_preserving_refs,
    _repo_has_commit,
    _source_remote_urls,
    _submodule_status,
    _verify_cached_branch_refs,
)
from git_well.archive_source.api import (  # noqa: E402,F401
    _coerce_archive_hooks,
    _hook_name,
    _run_archive_hooks,
)


__cli__ = ArchiveSourceCLI


if __name__ == '__main__':
    main()
