#!/usr/bin/env python
# PYTHON_ARGCOMPLETE_OK
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
import ubelt as ub
import scriptconfig as scfg


class UpdateDevBranch(scfg.DataConfig):
    """
    Upgrade to the latest "dev" branch. I.e. search for the branch
    ``dev/<version>`` with the greatest semantic version.

    """
    __command__ = 'branch_upgrade'
    repo_dpath = scfg.Value('.', position=1, help='location of the repo')

    @classmethod
    def main(cls, cmdline=1, **kwargs):
        """
        Example:
            >>> from git_well.git_branch_upgrade import UpdateDevBranch
            >>> from git_well.repo import Repo
            >>> cls = UpdateDevBranch
            >>> repo = Repo.demo()
            >>> repo.cmd('git checkout -b dev/1.0.0')
            >>> repo.cmd('git checkout -b dev/2.1.0')
            >>> repo.cmd('git checkout main')
            >>> assert repo.active_branch.name == 'main'
            >>> cmdline = 0
            >>> kwargs = dict()
            >>> kwargs['repo_dpath'] = repo
            >>> cls.main(cmdline=cmdline, **kwargs)
            >>> assert repo.active_branch.name == 'dev/2.1.0'
        """
        config = cls.cli(cmdline=cmdline, data=kwargs)
        from git_well._utils import rich_print
        rich_print('config = {}'.format(ub.urepr(config, nl=1)))
        from git_well.repo import Repo
        repo = Repo.coerce(config['repo_dpath'])

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


# def latest_branch(repo):
#     available = []
#     for remote in repo.remotes:
#         for ref in remote.refs:
#             available.append((ref.commit.committed_datetime, ref, remote))

#     available = []
#     for ref in repo.refs:
#         if ref.tag is None:
#             available.append((ref.commit.committed_datetime, ref))
#     available = sorted(available, key=lambda x: x[0])

#     repo.branches

#     for line in repo.git.branch('-r').split('\n'):
#         line = line.strip().split('->')[-1].strip()
#         print(line)


def dev_branches(repo):
    from packaging.version import parse as Version
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
