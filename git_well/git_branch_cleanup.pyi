import scriptconfig as scfg
from _typeshed import Incomplete


class CleanDevBranchConfig(scfg.DataConfig):
    __command__: str
    repo_dpath: Incomplete
    keep_last: Incomplete
    remove_merged: Incomplete
    yes: Incomplete

    @classmethod
    def main(cls, cmdline: int = ..., **kwargs) -> None:
        ...


__cli__ = CleanDevBranchConfig
main: Incomplete
