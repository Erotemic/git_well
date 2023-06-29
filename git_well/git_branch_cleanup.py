#!/usr/bin/env python
# PYTHON_ARGCOMPLETE_OK
import ubelt as ub
import scriptconfig as scfg


class CleanDevBranchConfig(scfg.DataConfig):
    """
    Cleanup branches that have been merged into main.
    """
    __command__ = 'branch_cleanup'

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
        from git_well._utils import find_merged_branches, dev_branches, rich_print
        rich_print('config = {}'.format(ub.urepr(config, nl=1)))
        keep_last = config.keep_last
        resolved_repo = ub.Path(config.repo_dpath).resolve()
        print(f'resolved_repo={resolved_repo}')
        import git
        repo = git.Repo(resolved_repo)

        # TODO: fix * prefixed in front of branch
        versioned_dev_branches = dev_branches(repo)
        local_dev_branches = [b for b in versioned_dev_branches if b['remote'] is None]
        versioned_branch_names = list(ub.unique([b['branch_name'] for b in local_dev_branches]))
        remove_branches = versioned_branch_names[0:-keep_last]

        try:
            merged_branches = find_merged_branches(repo, 'main')
        except Exception:
            merged_branches = find_merged_branches(repo, 'origin/main')
        remove_branches = list(ub.oset(remove_branches) | ub.oset(merged_branches) - {'release'})

        print('remove_branches = {}'.format(ub.repr2(remove_branches, nl=1)))
        if not remove_branches:
            print('Local devbranches are already clean')
        else:
            from ._utils import confirm
            if confirm('Remove dev branches?'):
                repo.git.branch(*remove_branches, '-D')

__cli__ = CleanDevBranchConfig
main = __cli__.main


if __name__ == '__main__':
    """
    CommandLine:
        python ~/code/git_well/git_well/git_branch_cleanup.py
    """
    __cli__.main()
