import scriptconfig as scfg
from _typeshed import Incomplete

VALID_PROTOCOLS: Incomplete


class GitRemoteProtocol(scfg.DataConfig):
    __command__: str
    __alias__: Incomplete
    group: Incomplete
    protocol: Incomplete
    repo_dpath: Incomplete


class GitURL(str):

    def __init__(self, data) -> None:
        ...

    @property
    def info(self):
        ...

    def to_git(self):
        ...

    def to_ssh(self):
        ...

    def to_https(self):
        ...


def main(cmdline: int = ..., **kwargs) -> None:
    ...


__cli__ = GitRemoteProtocol
