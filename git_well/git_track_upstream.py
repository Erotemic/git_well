#!/usr/bin/env python
"""
Requirements:
    pip install GitPython

A quick script that executes
``git branch --set-upstream-to=<remote>/<branch> <branch>``
with sensible defaults
"""
import ubelt as ub
import scriptconfig as scfg


class TrackUpstreamCLI(scfg.DataConfig):
    """
    Set the branch upstream with sensible defaults if possible.

    A quick script that executes
    ``git branch --set-upstream-to=<remote>/<branch> <branch>``
    with sensible defaults
    """
    __command__ = 'track_upstream'
    repo_dpath = scfg.Value('.', position=1, help='location of the repo')

    @classmethod
    def main(cls, cmdline=1, **kwargs):
        config = cls.cli(cmdline=cmdline, data=kwargs)
        from git_well._utils import rich_print
        rich_print('config = {}'.format(ub.urepr(config, nl=1)))

        repo_root = find_git_root(config['repo_dpath'])

        if repo_root is None:
            raise Exception('Could not find git repo')

        # Find the repo root.
        import os
        import git as pygit
        repo = pygit.Repo(os.fspath(repo_root))

        assert not repo.active_branch.is_remote()
        assert repo.active_branch.is_valid()
        tracking_branch = repo.head.reference.tracking_branch()
        print('tracking_branch = {}'.format(ub.repr2(tracking_branch, nl=1)))

        if tracking_branch is not None:
            print('tracking_branch is already set. Doing nothing.')
        else:
            print('tracking branch is not set. Attempt to find sensible defaults')
            branch = repo.active_branch
            unique_infos = unique_remotes_with_branch(repo, branch)
            if len(unique_infos) != 1:
                raise Exception('Sensible defaults are ambiguous. Giving up')
            # remote = unique_infos[0]['remote']
            valid_refs = unique_infos[0]['valid_refs']
            assert len(valid_refs) == 1
            ref = valid_refs[0]
            print('Chose sensible default tracking ref = {!r}'.format(ref))
            repo.active_branch.set_tracking_branch(ref)


def find_git_root(dpath):
    cwd = ub.Path(dpath).resolve()
    parts = cwd.parts
    for i in reversed(range(0, len(parts))):
        p = ub.Path(*parts[0:i])
        cand = p / '.git'
        if cand.exists():
            return p
    return None


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
