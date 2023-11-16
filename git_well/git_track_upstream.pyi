import scriptconfig as scfg
from _typeshed import Incomplete


class TrackUpstreamCLI(scfg.DataConfig):
    __command__: str
    repo_dpath: Incomplete
    force: Incomplete

    @classmethod
    def main(cls, cmdline: int = ..., **kwargs) -> None:
        ...


def unique_remotes_with_branch(repo, branch):
    ...


__cli__ = TrackUpstreamCLI
main: Incomplete
