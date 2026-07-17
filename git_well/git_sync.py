#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
from __future__ import annotations

import os
import shlex
from os.path import expanduser, normpath, realpath, relpath
from typing import Any

import kwconf
import ubelt as ub

from git_well._utils import cmd_output_text


class GitSyncCLI(kwconf.Config):
    """
    Sync a git repo with a remote server via ssh
    """

    __command__: str = 'sync'
    host: list[str] | None = kwconf.Value(
        None,
        position=1,
        required=True,
        help=ub.paragraph(
            """
            Server to sync to via ssh (e.g. user@servername.edu)
            """
        ),
        nargs=1,
    )
    remote: str | None = kwconf.Value(
        None, position=2, help='The git remote to use (e.g. origin)', nargs='?'
    )
    forward_ssh_agent: bool = kwconf.Value(
        False,
        isflag=True,
        short_alias=['A'],
        help=ub.paragraph(
            """
            Enable forwarding of the ssh authentication agent connection
            """
        ),
    )
    dry: bool = kwconf.Value(
        False, isflag=True, short_alias=['n'], help='Perform a dry run'
    )
    message: str = kwconf.Value(
        'wip [skip ci]',
        parser=str,
        short_alias=['m'],
        help='Specify a custom commit message',
    )
    force: bool = kwconf.Value(
        False, isflag=True, help='Force push and hard reset the remote.'
    )


def main(argv: list[str] | str | bool | None = True, **kwargs: Any) -> None:
    args = GitSyncCLI.cli(argv=argv, data=kwargs)
    try:
        import rich
        from rich.markup import escape

        rich.print('args = ' + escape(ub.urepr(args, nl=1)))
    except Exception:
        print('args = ' + ub.urepr(args, nl=1))

    ns = dict(args).copy()
    ns['host'] = ns['host'][0]
    git_sync(**ns)


def getcwd() -> str:
    """
    Workaround to get the working directory without dereferencing symlinks.
    This may not work on all systems.

    References:
        https://stackoverflow.com/questions/1542803/getcwd-dereference-symlinks
    """
    # TODO: use ubelt version if it lands
    canidate1 = os.getcwd()
    real1 = normpath(realpath(canidate1))

    # test the PWD environment variable
    candidate2 = os.getenv('PWD', None)
    if candidate2 is not None:
        real2 = normpath(realpath(candidate2))
        if real1 == real2:
            # sometimes PWD may not be updated
            return candidate2
    return canidate1


def git_default_push_remote_name() -> str | None:
    local_remotes = ub.cmd(['git', 'remote', '-v'], verbose=0)
    if local_remotes.returncode:
        return None
    stdout = cmd_output_text(local_remotes.stdout)
    candidates = []
    for line in stdout.splitlines():
        if not line:
            continue
        remote_name, remote_url_type = line.split('\t', 1)
        if remote_url_type.endswith('(push)'):
            candidates.append(remote_name)
    unique_candidates = list(ub.unique(candidates))
    if len(unique_candidates) == 1:
        return unique_candidates[0]
    return None


def _devcheck() -> None:
    """
    TODO: need to resolve the  receive.denyCurrentBranch problem less manually

    remote: error: refusing to update checked out branch: refs/heads/updates
    remote: error: By default, updating the current branch in a non-bare repository
    remote: is denied, because it will make the index and work tree inconsistent
    remote: with what you pushed, and will require 'git reset --hard' to match
    remote: the work tree to HEAD.

    On the remote:

        git config --local receive.denyCurrentBranch warn

    """


def _result_stderr_text(result: Any) -> str:
    stderr = result.stderr
    if isinstance(stderr, bytes):
        return stderr.decode(errors='replace')
    return stderr or ''


def _run_checked(argv: list[str], *, verbose: int = 2) -> Any:
    result = ub.cmd(argv, verbose=verbose)
    if result.returncode:
        raise RuntimeError(
            'Command failed with retcode={}:\n{}\n{}'.format(
                result.returncode,
                shlex.join(argv),
                _result_stderr_text(result).strip(),
            )
        )
    return result


def _commit_local_changes(message: str) -> bool:
    """Stage all local changes and commit only when the index is non-empty."""
    _run_checked(['git', 'add', '-A'])
    diff_result = ub.cmd(['git', 'diff', '--cached', '--quiet'], verbose=0)
    if diff_result.returncode == 0:
        print('No local changes to commit')
        return False
    if diff_result.returncode != 1:
        raise RuntimeError(
            'Unable to inspect staged changes:\n{}'.format(
                _result_stderr_text(diff_result).strip()
            )
        )
    _run_checked(['git', 'commit', '-m', message])
    return True


def git_sync(
    host: str,
    remote: str | None = None,
    message: str = 'wip [skip ci]',
    forward_ssh_agent: bool = False,
    dry: bool = False,
    force: bool = False,
    home: str | os.PathLike[str] | None = None,
) -> None:
    """
    Commit any changes in the current working directory, ssh into a remote
    machine, and then update the matching branch there.

    Args:
        host (str):
            The name of the host to sync to: e.g. user@remote.com

        remote (str):
            The git remote used to push and pull from

        message (str, default='wip [skip ci]'):
            Default git commit message.

        forward_ssh_agent (bool):
            Enable forwarding of the ssh authentication agent connection

        force (bool, default=False):
            if True does a forced push and additionally forces the remote to do
            a hard reset to the remote state.

        dry (bool, default=False):
            Executes dry run mode.

        home (str | PathLike | None):
            if specified, overwrite where git-sync thinks the home location is

    Example:
        >>> # xdoctest: +IGNORE_WANT
        >>> from git_well.repo import Repo
        >>> repo = Repo.demo()
        >>> host = 'user@remote.com'
        >>> remote = 'origin'
        >>> message = 'this is the commit message'
        >>> with ub.ChDir(repo.dpath):
        >>>     git_sync(
        >>>         host, remote, message, dry=True, home=repo.dpath.parent
        >>>     )
        git add -A
        git diff --cached --quiet || git commit -m 'this is the commit message'
        git push origin
        ssh user@remote.com 'cd ... && ...'
    """
    cwd = getcwd()
    if home is None:
        home = expanduser('~')
    try:
        relcwd = relpath(cwd, home)
    except ValueError:
        raise ValueError(
            (
                'git-sync assumes that you are running relative '
                'to your home directory. cwd={}, home={}'
            ).format(cwd, home)
        )

    remote_cwd = relcwd
    branch_info = ub.cmd(
        ['git', 'rev-parse', '--abbrev-ref', 'HEAD'], check=True
    )
    local_branch_name = cmd_output_text(branch_info.stdout).strip()
    remote_branch_name = local_branch_name

    if force and remote is None:
        remote = git_default_push_remote_name()
        if remote is None:
            raise ValueError(
                'Force sync requires an explicit or unambiguous push remote'
            )

    quoted_branch = shlex.quote(remote_branch_name)
    checkout_command = (
        'if [ "$(git rev-parse --abbrev-ref HEAD)" != '
        f'{quoted_branch} ]; then git checkout {quoted_branch}; fi'
    )

    remote_parts = [f'cd {shlex.quote(remote_cwd)}']
    if force:
        if remote is None:
            raise AssertionError('force sync remote should already be resolved')
        quoted_remote = shlex.quote(remote)
        remote_parts.extend(
            [
                f'git fetch {quoted_remote}',
                checkout_command,
                f'git reset --hard {quoted_remote}/{quoted_branch}',
            ]
        )
    elif host == remote:
        remote_parts.extend([checkout_command, 'git reset --hard'])
    else:
        remote_parts.append(checkout_command)
        if remote:
            remote_parts.append(
                'git pull {} {}'.format(
                    shlex.quote(remote), quoted_branch
                )
            )
        else:
            remote_parts.append('git pull')

    remote_command = ' && '.join(remote_parts)
    push_argv = ['git', 'push']
    if remote:
        push_argv.append(remote)
    if force:
        push_argv.append('--force')

    ssh_argv = ['ssh']
    if forward_ssh_agent:
        ssh_argv.append('-A')
    ssh_argv.extend([host, remote_command])

    if dry:
        print('git add -A')
        print(
            'git diff --cached --quiet || git commit -m {}'.format(
                shlex.quote(message)
            )
        )
        print(shlex.join(push_argv))
        print(shlex.join(ssh_argv))
        return

    _commit_local_changes(message)

    push_result = ub.cmd(push_argv, verbose=2)
    if push_result.returncode:
        stderr = _result_stderr_text(push_result)
        if 'refusing to update checked out branch:' in stderr:
            from rich import prompt

            ans = prompt.Confirm.ask(
                ub.paragraph(
                    """
                    The remote needs to be configured to allow pushes
                    to a checked out branch. Do you want to do this?
                    """
                )
            )
            if ans:
                reconfig_remote_part = ' && '.join(
                    [
                        f'cd {shlex.quote(remote_cwd)}',
                        'git config --local receive.denyCurrentBranch warn',
                    ]
                )
                reconfig_argv = ['ssh']
                if forward_ssh_agent:
                    reconfig_argv.append('-A')
                reconfig_argv.extend([host, reconfig_remote_part])
                _run_checked(reconfig_argv)
                print('Now rerun the command')
        raise RuntimeError(
            'git-sync cannot continue after failed push:\n{}'.format(
                stderr.strip()
            )
        )

    _run_checked(ssh_argv)


__cli__ = GitSyncCLI
setattr(__cli__, 'main', main)


if __name__ == '__main__':
    r"""
    CommandLine:
        python -m git_sync remote_host_name --dry
    """
    main()
