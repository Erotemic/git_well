#!/usr/bin/env python
import git
import ubelt as ub
import scriptconfig as scfg
from .git_branch_upgrade import find_merged_branches, dev_branches
# from packaging.version import LegacyVersion


class CleanDevBranchConfig(scfg.DataConfig):
    __command__ = 'clean'

    repo_dpath = scfg.Value('.', help='location of the repo')
    keep_last = scfg.Value(1, help='previous number of dev branches to keep')
    remove_merged = scfg.Value(False, isflag=True, help='if True, remove other merged branhes as well')

    @classmethod
    def main(cls, cmdline=1, **kwargs):
        """
        import sys, ubelt
        sys.path.append(ubelt.expandpath('~/local/git_tools'))
        from git_devbranch import *  # NOQA
        cls = CleanDevBranchConfig
        cmdline = 0
        kwargs = {}
        """
        config = cls.cli(cmdline=cmdline, data=kwargs)
        try:
            from rich import print as rich_print
        except Exception:
            rich_print = print
        rich_print('config = {}'.format(ub.urepr(config, nl=1)))
        keep_last = config.keep_last
        repo = git.Repo(config.repo_dpath)

        versioned_dev_branches = dev_branches(repo)
        local_dev_branches = [b for b in versioned_dev_branches if b['remote'] is None]
        versioned_branch_names = list(ub.unique([b['branch_name'] for b in local_dev_branches]))
        remove_branches = versioned_branch_names[0:-keep_last]

        merged_branches = find_merged_branches(repo)
        remove_branches = list(ub.oset(remove_branches) | ub.oset(merged_branches) - {'release'})

        print('remove_branches = {}'.format(ub.repr2(remove_branches, nl=1)))
        if not remove_branches:
            print('Local devbranches are already clean')
        else:
            from rich import prompt
            if prompt.Confirm.ask('Remove dev branches?'):
                repo.git.branch(*remove_branches, '-D')

__cli__ = CleanDevBranchConfig
main = __cli__.main


if __name__ == '__main__':
    """
    CommandLine:
        python ~/code/git_well/git_well/git_branch_cleanup.py
    """
    __cli__.main()
