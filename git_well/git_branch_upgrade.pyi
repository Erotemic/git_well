import scriptconfig as scfg
from _typeshed import Incomplete


class UpdateDevBranch(scfg.DataConfig):
    __command__: str
    repo_dpath: Incomplete

    @classmethod
    def main(cls, cmdline: int = ..., **kwargs):
        ...


def dev_branches(repo):
    ...


main: Incomplete
__cli__ = UpdateDevBranch
