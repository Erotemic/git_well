"""Business logic for :mod:`git_well.git_archive_source`."""

from ._common import (
    ArchiveFormatArg,
    BranchRefInventory,
    DepthArg,
    PathLike,
    ResolvedArchiveFormat,
    SubmoduleArchiveDecision,
    SubmoduleDepthSpecArg,
    SubmoduleStatus,
)
from ._policy import SubmoduleDepthPolicy
from .api import (
    ArchiveSourceContext,
    ArchiveSourceHook,
    ArchiveSourceHookArg,
    ArchiveSourceHookError,
    archive_source,
    build_source_archive,
    stage_source_archive,
)

__all__ = [
    'ArchiveFormatArg',
    'ArchiveSourceContext',
    'ArchiveSourceHook',
    'ArchiveSourceHookArg',
    'ArchiveSourceHookError',
    'BranchRefInventory',
    'DepthArg',
    'PathLike',
    'ResolvedArchiveFormat',
    'SubmoduleArchiveDecision',
    'SubmoduleDepthPolicy',
    'SubmoduleDepthSpecArg',
    'SubmoduleStatus',
    'archive_source',
    'build_source_archive',
    'stage_source_archive',
]
