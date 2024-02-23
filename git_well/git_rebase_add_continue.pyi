import scriptconfig as scfg
from _typeshed import Incomplete


class GitRebaseAddContinue(scfg.DataConfig):
    __command__: str
    repo_dpath: Incomplete
    skip_editor: Incomplete

    @classmethod
    def main(cls, cmdline: int = ..., **kwargs) -> None:
        ...


def parsed_rebase_git_status(repo_dpath):
    ...


__cli__ = GitRebaseAddContinue
main: Incomplete
