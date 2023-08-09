import git
import ubelt as ub


class Repo(git.Repo):
    # Extension of git.Repo

    def cmd(self, command, **kwargs):
        """
        Execute a command in the root of the repo.
        """
        defaults = ub.udict({
            'cwd': self.dpath,
            'check': True,
            'verbose': 0,
        })
        final_kwargs = defaults | kwargs
        info = ub.cmd(command, **final_kwargs)
        return info

    @property
    def dpath(self):
        """
        Alias of working_dir wraped in a ubelt Path
        """
        return ub.Path(self.working_dir)

    @property
    def is_submodule(self):
        """
        True if the submodule for another repo.
        """
        git_file = ub.Path(self.working_dir) / '.git'
        # If .git is a file, that means it is a submodule
        return git_file.is_file()

    @property
    def config_fpath(self):
        return ub.Path(self.git_dir) / 'config'

    @classmethod
    def coerce(cls, data):
        """
        Try to construct a Repo object from input dat

        Args:
            data (str | PathLike | Repo):
                If a Repo object, data is returned as-is.
                If a path inside a git repo, return a `Repo` object
                that references the repo root.

        Returns:
            Repo
        """
        from git_well._utils import find_git_root
        import os
        if isinstance(data, cls):
            self = data
        elif isinstance(data, (str, os.PathLike)):
            dpath = data
            repo_root = find_git_root(dpath)
            self = cls(repo_root)
        else:
            raise TypeError(type(data))
        return self

    @classmethod
    def demo(cls):
        """

        Returns:
            Repo

        Example:
            >>> from git_well._repo_ext import *  # NOQA
            >>> self = Repo.demo()
        """
        from git_well.demo import make_dummy_git_repo
        dpath = make_dummy_git_repo()
        return cls.coerce(dpath)
