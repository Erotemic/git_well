from typing import List
import scriptconfig as scfg
import ubelt as ub
from _typeshed import Incomplete

from git.objects.commit import Commit

EXPERIMENTAL_PSEUDO_CHAIN: int
EXPERIMENTAL_REBASE: int
__docstubs__: str


class SquashStreakCLI(scfg.DataConfig):
    __command__: str
    timedelta: Incomplete
    custom_streak: Incomplete
    pattern: Incomplete
    tags: Incomplete
    preserve_tags: Incomplete
    oldest_commit: Incomplete
    inplace: Incomplete
    auto_rollback: Incomplete
    authors: Incomplete
    dry: Incomplete
    force: Incomplete
    verbose: Incomplete

    def __post_init__(self) -> None:
        ...


def print_exc(exc_info: Incomplete | None = ...):
    ...


class Streak(ub.NiceRepr):
    child: Incomplete

    def __init__(self, child, _streak: Incomplete | None = ...) -> None:
        ...

    def __iter__(self):
        ...

    def __nice__(self):
        ...

    def __len__(self):
        ...

    def append(self, commit) -> None:
        ...

    @property
    def before_start(self):
        ...

    @property
    def after_stop(self):
        ...

    @property
    def start(self):
        ...

    @property
    def stop(self):
        ...


def find_pseudo_chain(head,
                      oldest_commit: Incomplete | None = ...,
                      preserve_tags: bool = ...):
    ...


def git_nx_graph(head,
                 oldest_commit: Incomplete | None = ...,
                 preserve_tags: bool = ...):
    ...


def find_chain(head: Commit,
               authors: set | None = None,
               preserve_tags: bool = True,
               oldest_commit: Incomplete | None = ...):
    ...


def find_streaks(chain: List[Commit],
                 authors: set | None = None,
                 timedelta: float | str = 'sameday',
                 pattern: str | None = None):
    ...


def checkout_temporary_branch(repo, suffix: str = ...):
    ...


def commits_between(repo, start: Commit, stop: Commit) -> List[Commit]:
    ...


class RollbackError(Exception):
    ...


def do_tags(verbose: bool = ...,
            inplace: bool = ...,
            dry: bool = ...,
            auto_rollback: bool = ...) -> None:
    ...


def squash_streaks(authors: set,
                   timedelta: str | int = 'sameday',
                   pattern: str | None = None,
                   inplace: bool = False,
                   auto_rollback: bool = True,
                   dry: bool = False,
                   verbose: bool = True,
                   custom_streak: tuple | None = None,
                   preserve_tags: bool = True,
                   oldest_commit: str | None = None) -> None:
    ...


def git_squash_streaks(cmdline: int = ..., **kwargs) -> None:
    ...


main: Incomplete
__cli__ = SquashStreakCLI
