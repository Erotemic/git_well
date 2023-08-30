import scriptconfig as scfg
from _typeshed import Incomplete


class GitStatsCLI(scfg.DataConfig):
    repo_dpath: Incomplete

    @classmethod
    def main(cls, cmdline: int = ..., **kwargs) -> None:
        ...


def commit_stats(repo) -> None:
    ...


def author_stats(repo):
    ...


__cli__ = GitStatsCLI
main: Incomplete
