"""Compatibility module for :mod:`git_well.archive_source.patch`."""

from git_well.archive_source import patch as _impl
from git_well.archive_source.patch import *  # noqa: F401,F403


def __getattr__(name: str):
    return getattr(_impl, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_impl)))
