from os import PathLike
import git


class Repo(git.Repo):

    def cmd(self, command, **kwargs):
        ...

    @property
    def dpath(self):
        ...

    @property
    def is_submodule(self):
        ...

    @property
    def config_fpath(self):
        ...

    @classmethod
    def coerce(cls, data: str | PathLike | Repo) -> Repo:
        ...

    @classmethod
    def demo(cls) -> Repo:
        ...

    def find_merged_branches(repo, main_branch: str = ...):
        ...
