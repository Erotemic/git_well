"""Shared types and lightweight support objects for source archives."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

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
HistoryBlobsArg = Literal['full', 'sparse']

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
_UNINITIALIZED_SUBMODULE_REASON = 'not initialized locally'

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


@dataclass(frozen=True)
class BranchRefInventory:
    """
    Local and remote-tracking branch refs cached in one repository.
    """

    local_branches: tuple[str, ...]
    remote_tracking_branches: tuple[str, ...]

@dataclass(frozen=True)
class PromisorPruneResult:
    """Summary of history blobs intentionally omitted from one repository."""

    archive_prefix: str
    matched_history_paths: tuple[str, ...]
    omitted_blob_oids: tuple[str, ...]
    omitted_blob_bytes: int
    promisor_remote_name: str
    promisor_remote_url: str


class _Logger:
    def __init__(self, verbose: int) -> None:
        self.verbose = verbose

    def __call__(self, msg: str) -> None:
        if self.verbose:
            print(msg)

    def warning(self, msg: str) -> None:
        import sys

        print(msg, file=sys.stderr)

    def path(self, prefix: str, path: PathLike, suffix: str = '') -> None:
        if self.verbose:
            from git_well._utils import rich_print_path

            rich_print_path(prefix, path, suffix=suffix)
