import scriptconfig as scfg
from _typeshed import Incomplete


class GitDiscoverRemoteCLI(scfg.DataConfig):
    __command__: str
    repo_dpath: Incomplete
    host: Incomplete
    remote: Incomplete
    home: Incomplete
    forward_ssh_agent: Incomplete
    test_remote: Incomplete
    remote_cwd: Incomplete

    @classmethod
    def main(cls, cmdline: int = ..., **kwargs) -> None:
        ...


def fsspec_shh_connect(host):
    ...


__cli__ = GitDiscoverRemoteCLI
main: Incomplete
