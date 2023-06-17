import scriptconfig as scfg
from git_well import git_squash_streaks
from git_well import git_branch_upgrade
from git_well import git_branch_cleanup
from git_well import git_sync


class GitWellModalCLI(scfg.ModalCLI):
    branch_upgrade = git_branch_upgrade.__cli__
    squash_streaks = git_squash_streaks.__cli__
    sync = git_sync.__cli__
    branch_cleanup = git_branch_cleanup.__cli__
    ...


def main():
    modal = GitWellModalCLI()
    modal.main()


if __name__ == '__main__':
    """
    CommandLine:
        python ~/code/git_well/git_well/main.py
    """
    main()
