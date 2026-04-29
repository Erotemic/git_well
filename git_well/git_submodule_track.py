#!/usr/bin/env python
"""
Configure an existing submodule to track a named remote branch.
"""
import shlex

import ubelt as ub
import scriptconfig as scfg


class SubmoduleTrackCLI(scfg.DataConfig):
    """
    Configure an existing submodule to track a named remote branch.

    Note:
        Git submodules are always recorded as concrete gitlink commits in the
        superproject. This command sets the branch used by ``git submodule
        update --remote`` and can optionally advance / stage the current
        submodule gitlink.

    Example:
        git well submodule_track external/foo main
        git well submodule_track external/foo release --remote upstream
        git submodule-track external/foo main --hard
    """
    __command__ = 'submodule_track'

    path = scfg.Value(None, position=1, help='Path to an existing submodule')
    branch = scfg.Value(None, position=2, help='Remote branch for the submodule to track')
    repo_dpath = scfg.Value('.', help='Location inside the superproject')
    remote = scfg.Value('origin', help='Remote name inside the submodule')
    hard = scfg.Value(False, isflag=True, help='Reset the submodule worktree hard to remote/branch')
    stage = scfg.Value(True, isflag=True, help='Stage .gitmodules and the submodule gitlink in the superproject')
    fetch = scfg.Value(True, isflag=True, help='Fetch the requested remote branch first')

    @classmethod
    def main(cls, cmdline=1, **kwargs):
        """
        Args:
            cmdline (int | List[str]):
                Command line arguments.
            **kwargs:
                Config overrides.
        """
        config = cls.cli(cmdline=cmdline, data=kwargs)
        from git_well._utils import rich_print
        from git_well.repo import Repo

        rich_print('config = {}'.format(ub.urepr(config, nl=1)))

        path = config['path']
        branch = config['branch']
        remote = config['remote']

        if path is None or branch is None:
            raise ValueError('Both path and branch are required')

        repo = Repo.coerce(config['repo_dpath'])
        super_root = repo.dpath
        submodule_dpath = super_root / path

        if not (super_root / '.gitmodules').exists():
            raise AssertionError(f'No .gitmodules found in {super_root}')

        if not submodule_dpath.exists():
            raise AssertionError(f'Submodule path does not exist: {submodule_dpath}')

        registered_paths = repo.cmd(
            'git config -f .gitmodules --get-regexp "^submodule\\..*\\.path$"',
            check=False,
        )['out'].splitlines()

        known_paths = {line.split(maxsplit=1)[1] for line in registered_paths if line.strip()}
        if path not in known_paths:
            raise AssertionError(
                f'{path!r} is not registered in .gitmodules. '
                f'Known submodules: {sorted(known_paths)!r}'
            )

        qpath = shlex.quote(path)
        qbranch = shlex.quote(branch)
        qremote = shlex.quote(remote)

        repo.cmd(f'git submodule set-branch --branch {qbranch} -- {qpath}')
        repo.cmd(f'git submodule sync -- {qpath}')
        repo.cmd(f'git submodule update --init -- {qpath}')

        if config['fetch']:
            repo.cmd(f'git -C {qpath} fetch {qremote} {qbranch}')

        repo.cmd(f'git -C {qpath} checkout -B {qbranch} {qremote}/{qbranch}')
        repo.cmd(f'git -C {qpath} branch --set-upstream-to={qremote}/{qbranch} {qbranch}')

        if config['hard']:
            repo.cmd(f'git -C {qpath} reset --hard {qremote}/{qbranch}')

        if config['stage']:
            repo.cmd(f'git add .gitmodules {qpath}')

        print('')
        print(f'Submodule {path} is configured to track {remote}/{branch}')
        print('Review with:')
        print(f'    git diff --cached -- .gitmodules {path}')


__cli__ = SubmoduleTrackCLI
main = __cli__.main


if __name__ == '__main__':
    SubmoduleTrackCLI.main()
