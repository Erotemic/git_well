import git
import ubelt as ub


class Repo(git.Repo):

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
