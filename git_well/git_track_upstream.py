#!/usr/bin/env python
"""
Requirements:
    pip install GitPython
"""
import ubelt as ub
import scriptconfig as scfg


class TrackUpstreamCLI(scfg.DataConfig):
    """
    Set the branch upstream with sensible defaults if possible.

    This script can auto-choose sensible default if there is only one remote
    that also has the working branch. When there is an ambiguity the user will
    be asked to choose from a list of available remotes with this branch.

    Once the remote is found the script executes:

    ..code:: bash

        git branch --set-upstream-to=<remote>/<branch> <branch>
    """
    __command__ = 'track_upstream'
    repo_dpath = scfg.Value('.', position=1, help='location of the repo')
    force = scfg.Value(False, isflag=True, short_alias=['-f'], help='if True, then choose a new tracking branch even if one is set')

    @classmethod
    def main(cls, cmdline=1, **kwargs):
        """
        Example:
            >>> from git_well.git_track_upstream import TrackUpstreamCLI
            >>> from git_well.repo import Repo
            >>> repo = Repo.demo()
            >>> repo.cmd('git remote add origin https://github.com/Erotemic/git_well.git')
            >>> # TODO: make this test work without the network
            >>> repo.cmd('git fetch origin')
            >>> repo.cmd('git reset --hard origin/main')
            >>> cmdline = 0
            >>> cls = TrackUpstreamCLI
            >>> kwargs = cls()
            >>> kwargs['repo_dpath'] = repo
            >>> cls.main(cmdline=cmdline, **kwargs)
        """
        config = cls.cli(cmdline=cmdline, data=kwargs)
        from git_well._utils import rich_print
        rich_print('config = {}'.format(ub.urepr(config, nl=1)))

        from git_well.repo import Repo
        repo = Repo.coerce(config['repo_dpath'])

        assert not repo.active_branch.is_remote()
        assert repo.active_branch.is_valid()
        tracking_branch = repo.head.reference.tracking_branch()
        print('tracking_branch = {}'.format(ub.repr2(tracking_branch, nl=1)))

        if tracking_branch is not None:
            print(f'tracking_branch is already set to {tracking_branch}.')
        else:
            print('tracking branch is not set.')

        find_new = config.force or (tracking_branch is None)
        if find_new:
            print('Finding new tracking branch')
            branch = repo.active_branch
            unique_infos = unique_remotes_with_branch(repo, branch)
            if len(unique_infos) != 1:
                from git_well._utils import choice_prompt
                print('unique_infos = {}'.format(ub.urepr(unique_infos, nl=2)))
                name_to_info = {d['name']: d for d in unique_infos}
                choices = list(name_to_info.keys())
                ans = choice_prompt('Sensible defaults are ambiguous. Choose one.', choices=choices)
                chosen = name_to_info[ans]
            else:
                chosen = unique_infos[0]
                print('Chose sensible default: {!r}'.format(chosen))
            valid_refs = chosen['valid_refs']
            assert len(valid_refs) == 1
            ref = valid_refs[0]
            print('Setting tracking branch to: {!r}'.format(ref))
            repo.active_branch.set_tracking_branch(ref)
        else:
            print('Doing nothing.')


def unique_remotes_with_branch(repo, branch):
    available_remotes = repo.remotes
    remote_infos = {}
    for remote in available_remotes:
        valid_refs = []
        for ref in remote.refs:
            if ref.name[len(ref.remote_name):].lstrip('/') == branch.name:
                valid_refs.append(ref)
        if not valid_refs:
            continue
        info = {'remote': remote, 'name': remote.name, 'valid_refs': valid_refs}
        remote_infos[remote.name] = info
        ref_urls = tuple(sorted(set(ub.flatten(list(remote.urls) for ref in remote.refs))))
        info['ref_urls'] = ref_urls

    groups = ub.group_items(remote_infos.values(), key=lambda x: x['ref_urls'])
    unique_infos = []
    for key, group in groups.items():
        chosen = sorted(group, key=lambda x: ((0 if x['name'] == 'origin' else 1), x['name']))[0]
        unique_infos.append(chosen)

    return unique_infos


__cli__ = TrackUpstreamCLI
main = __cli__.main


if __name__ == '__main__':
    TrackUpstreamCLI.main()
