#!/usr/bin/env python
"""
A git tool for handling the dev/<version> branch patterns

See the GitDevbranchConfig for functionality

Notes:
    to remove branches from remotes

        f"git push origin --delete {branch_name}"

    to see results:

        git fetch --prune

Requires:
    scriptconfig
    git-python
    packaging
    ubelt
"""
import git
import ubelt as ub
import scriptconfig as scfg
# from packaging.version import LegacyVersion
from packaging.version import parse as Version
try:
    from rich import print as rich_print
except Exception:
    rich_print = print


class UpdateDevBranch(scfg.DataConfig):
    __command__ = 'upgrade'
    repo_dpath = scfg.Value('.', position=1, help='location of the repo')

    @classmethod
    def main(cls, cmdline=1, **kwargs):
        config = cls.cli(cmdline=cmdline, data=kwargs)
        rich_print('config = {}'.format(ub.urepr(config, nl=1)))
        repo = git.Repo(config['repo_dpath'])

        versioned_dev_branches = dev_branches(repo)
        if len(versioned_dev_branches) == 0:
            raise Exception('There are no versioned branches')

        version = max(versioned_dev_branches, key=lambda x: x['version'])['version']
        final_cand = [d for d in versioned_dev_branches if d['version'] == version]

        latest = None
        for c in final_cand:
            if c.get('branch', None) is not None:
                latest = c['branch']

        print('Remember to pull and fetch before running this command')
        # Need to fetch from remote
        if latest is None:
            info = final_cand[-1]
            print('info = {}'.format(ub.repr2(info, nl=1)))
            print('Latest seems to be on a remote')
            info['branch_name']
            repo.git.checkout(info['branch_name'])
            # raise NotImplementedError
        else:
            # dev_branches = [b for b in repo.branches if b.name.startswith('dev/')]
            # branch_versions = sorted(dev_branches, key=lambda x: Version(x.name.split('/')[-1]))
            # latest = branch_versions[-1]
            try:
                active_branch_name = repo.active_branch.name
            except TypeError:
                active_branch_name = None
            if active_branch_name == latest.name:
                print('Already on the latest dev branch')
            else:
                print('active_branch_name = {!r}'.format(active_branch_name))
                print('latest = {!r}'.format(latest))
                repo.git.checkout(latest.name)


def find_merged_branches(repo, main_branch='main'):
    # git branch --merged main
    # main_branch = 'main'
    merged_branches = [p.strip() for p in repo.git.branch(merged=main_branch).split('\n') if p.strip()]
    merged_branches = ub.oset(merged_branches) - {main_branch}
    return merged_branches


def dev_branches(repo):
    branch_infos = []
    for line in repo.git.branch('-r').split('\n'):
        line = line.strip().split('->')[-1].strip()
        for remote in repo.remotes:
            if line.startswith(remote.name):
                info = {
                    'remote': remote,
                    'branch_name': line.lstrip(remote.name + '/'),
                    'full_name': line,
                }
                branch_infos.append(info)

    for branch in repo.branches:
        info = {
            'remote': None,
            'branch': branch,
            'branch_name': branch.name,
            'datetime': branch.commit.committed_datetime,
        }
        branch_infos.append(info)

    dev_infos = []
    for info in branch_infos:
        if info['branch_name'].startswith('dev/'):
            vstr = info['branch_name'].split('/')[-1]
            try:
                info['version'] = Version(vstr)
            except Exception:
                ...
            else:
                # if not isinstance(info['version'], LegacyVersion):
                dev_infos.append(info)

    versioned_dev_branches = sorted(dev_infos, key=lambda x: x['version'])
    return versioned_dev_branches


main = UpdateDevBranch.main
__cli__ = UpdateDevBranch


if __name__ == '__main__':
    """
    CommandLine:
        python ~/code/git_well/git_well/git_devbranch.py clean --remove_merged
        python ~/local/git_tools/git_devbranch.py clean --remove_merged
    """
    main()
