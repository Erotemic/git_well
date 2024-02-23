from os import PathLike
import scriptconfig as scfg
from _typeshed import Incomplete


class GitSyncCLI(scfg.DataConfig):
    __command__: str
    host: Incomplete
    remote: Incomplete
    forward_ssh_agent: Incomplete
    dry: Incomplete
    message: Incomplete
    force: Incomplete


def main(cmdline: bool = ..., **kwargs) -> None:
    ...


def getcwd():
    ...


def git_default_push_remote_name():
    ...


def git_sync(host: str,
             remote: str | None = None,
             message: str = 'wip [skip ci]',
             forward_ssh_agent: bool = False,
             dry: bool = False,
             force: bool = False,
             home: str | PathLike | None = None) -> None:
    ...


__cli__ = GitSyncCLI
