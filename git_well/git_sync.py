#!/usr/bin/env python
from os.path import normpath
from os.path import realpath
from os.path import expanduser
from os.path import relpath
import os
import ubelt as ub
import scriptconfig as scfg


class GitSyncCLI(scfg.DataConfig):
    """
    Sync a git repo with a remote server via ssh
    """
    __command__ = 'sync'
    host = scfg.Value(None, position=1, required=True, help=ub.paragraph(
            '''
            Server to sync to via ssh (e.g. user@servername.edu)
            '''), nargs=1)
    remote = scfg.Value(None, position=2, help='The git remote to use (e.g. origin)', nargs='?')
    forward_ssh_agent = scfg.Value(False, isflag=True, short_alias=['A'], help=ub.paragraph(
            '''
            Enable forwarding of the ssh authentication agent connection
            '''))
    dry = scfg.Value(False, isflag=True, short_alias=['n'], help='Perform a dry run')
    message = scfg.Value('wip [skip ci]', type=str, short_alias=['m'], help='Specify a custom commit message')
    force = scfg.Value(False, isflag=True, help='Force push and hard reset the remote.')


def main(cmdline=True, **kwargs):
    args = GitSyncCLI.cli(cmdline=cmdline, data=kwargs)
    try:
        import rich
        from rich.markup import escape
        rich.print('args = ' + escape(ub.urepr(args, nl=1)))
    except Exception:
        print('args = ' + ub.urepr(args, nl=1))

    ns = dict(args).copy()
    ns['host'] = ns['host'][0]
    git_sync(**ns)


def getcwd():
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


def git_default_push_remote_name():
    local_remotes = ub.cmd('git remote -v')['out'].strip()
    lines = [line for line in local_remotes.split('\n') if line]
    candidates = []
    for line in lines:
        parts = line.split('\t')
        remote_name, remote_url_type = parts
        if remote_url_type.endswith('(push)'):
            candidates.append(remote_name)
    if len(candidates) == 1:
        remote_name = candidates[0]
    return remote_name


def _devcheck():
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


def git_sync(host, remote=None, message='wip [skip ci]',
             forward_ssh_agent=False, dry=False, force=False, home=None):
    """
    Commit any changes in the current working directory, ssh into a remote
    machine, and then pull those changes.

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
        >>> host = 'user@remote.com'
        >>> remote = 'origin'
        >>> message = 'this is the commit message'
        >>> home = getcwd()  # pretend the home is here for the test
        >>> git_sync(host, remote, message, dry=True, home=home)
        git commit -am "this is the commit message"
        git push origin
        ssh user@remote.com "cd ... && git pull origin ..."
    """
    cwd = getcwd()
    if home is None:
        home = expanduser('~')
    try:
        relcwd = relpath(cwd, home)
    except ValueError:
        raise ValueError((
            'git-sync assumes that you are running relative '
            'to your home directory. cwd={}, home={}').format(cwd, home))

    """
    # How to check if a branch exists
    git branch --list ${branch}

    # Get current branch name

    if [[ "$(git rev-parse --abbrev-ref HEAD)" != "{branch}" ]];
        then git checkout {branch} ;
    fi

    # git rev-parse --abbrev-ref HEAD
    if [[ -z $(git branch --list ${branch}) ]]; then
    else
    fi
    """

    # $(git branch --list ${branch})

    # Assume the remote directory is the same as the local one (relative to home)
    remote_cwd = relcwd

    # Build one command to execute on the remote
    remote_parts = [
        'cd {remote_cwd}',
    ]

    # Get branch name from the local
    local_branch_name = ub.cmd('git rev-parse --abbrev-ref HEAD')['out'].strip()
    # Assume the branches are the same between local / remote
    remote_branch_name = local_branch_name

    if force:
        if remote is None:
            # FIXME: might not work in all cases
            remote = git_default_push_remote_name()

        # Force the remote to the state of the remote
        remote_checkout_branch_force = ub.paragraph(
            '''
            git fetch {remote};
            if [[ "$(git rev-parse --abbrev-ref HEAD)" != "{branch}" ]]; then
                git checkout {branch};
            fi;
            git reset {remote}/{branch} --hard
            ''').format(
                remote=remote,
                branch=remote_branch_name
            )

        remote_parts += [
            'git fetch {remote}',
            remote_checkout_branch_force.replace('"', r'\"'),
        ]
    else:
        # ensure the remote is on the right branch
        # (this assumes no conflicts and will fail if anything bad
        #  might happen)
        remote_checkout_branch_simple = ub.paragraph(
            r'''
            if [[ "$(git rev-parse --abbrev-ref HEAD)" != "{branch}" ]]; then
                git checkout {branch};
            fi
            ''').format(branch=local_branch_name)

        if host == remote:
            remote_parts += [
                'git reset --hard',
                remote_checkout_branch_simple.replace('"', r'\"'),
            ]
        else:
            remote_parts += [
                'git pull {remote}' if remote else 'git pull',
                remote_checkout_branch_simple.replace('"', r'\"'),
            ]

    remote_part = ' && '.join(remote_parts)

    # Build one comand to execute locally
    commit_command = 'git commit -am "{}"'.format(message)

    push_args = ['git push']
    if remote:
        push_args.append('{remote}')
    if force:
        push_args.append('--force')
    push_command = ' '.join(push_args)

    sync_command = 'ssh {ssh_flags} {host} "' + remote_part + '"'

    local_parts = [
        commit_command,
        push_command,
        sync_command,
    ]

    ssh_flags = []
    if forward_ssh_agent:
        ssh_flags += ['-A']
    ssh_flags = ' '.join(ssh_flags)

    kw = dict(
        host=host,
        remote_cwd=remote_cwd,
        remote=remote,
        ssh_flags=ssh_flags
    )

    for part in local_parts:
        command = part.format(**kw)
        if not dry:
            result = ub.cmd(command, verbose=2)
            retcode = result.returncode
            if command.startswith('git commit') and retcode == 1:
                pass
            elif retcode != 0:
                print(f'command={command}')
                if command.startswith('git push'):
                    if 'refusing to update checked out branch:' in result.stderr:
                        from rich import prompt
                        ans = prompt.Confirm.ask(ub.paragraph(
                            '''
                            The remote needs to be configured to allow pushes
                            to a checked out branch. Do you want to do this?
                            '''))
                        if ans:
                            reconfig_remote_part = ' && '.join([
                                f'cd {remote_cwd}',
                                'git config --local receive.denyCurrentBranch warn',
                            ])
                            reconfig_command = f'ssh {ssh_flags} {host} "' + reconfig_remote_part + '"'
                            ub.cmd(reconfig_command, verbose=2)
                            print('Now rerun the command')

                print('git-sync cannot continue. retcode={}'.format(retcode))
                break
        else:
            print(command)


__cli__ = GitSyncCLI
__cli__.main = main


if __name__ == '__main__':
    r"""
    CommandLine:
        python -m git_sync remote_host_name --dry
    """
    main()
