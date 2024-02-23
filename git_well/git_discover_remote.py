#!/usr/bin/env python3
import scriptconfig as scfg
import ubelt as ub


# NOTE: SeeAlso
# ~/code/simple_dvc/simple_dvc/discover_ssh_remote.py


class GitDiscoverRemoteCLI(scfg.DataConfig):
    """
    Attempt to discover a ssh remote based on an ssh host.

    Like git-sync, the remote machine must have the same directory structure
    relative to the home drive.
    """
    __command__ = 'discover_remote'

    repo_dpath = scfg.Value('.', help=ub.paragraph(
        '''
        The path to the repo to run in.
        NOTE: due to behavior of ``getcwd``, if you are in a logical directory
        that contains a symlink, it can be more stable to set this to the value
        of the ``$PWD`` environment variable.
        '''))

    host = scfg.Value(None, position=1, required=True, help=ub.paragraph(
        '''
        The name or address of the SSH server to attempt to discover remote in.
        '''))

    remote = scfg.Value(None, help=ub.paragraph(
        '''
        If specified use this as the name for the new remote. Otherwise, use
        the host name instead.
        '''))

    home = scfg.Value(None, help='Explicitly specify where your home drive is. Usually this can be inferred')

    forward_ssh_agent = scfg.Value(False, isflag=True, short_alias=['A'], help=ub.paragraph(
            '''
            Enable forwarding of the ssh authentication agent connection
            '''))

    test_remote = scfg.Value(True, isflag=True, help=ub.paragraph(
        '''
        if True, test that the remote exists and there is a git repo in the
        expected location.
        '''))

    remote_cwd = scfg.Value(None, help='path on the remote. inferred if not given')

    @classmethod
    def main(cls, cmdline=1, **kwargs):
        """
        Example:
            >>> from git_well.git_discover_remote import GitDiscoverRemoteCLI
            >>> from git_well.repo import Repo
            >>> cls = GitDiscoverRemoteCLI
            >>> repo = Repo.demo()
            >>> # TODO: make a plausible scenario
            >>> cmdline = 0
            >>> kwargs = dict()
            >>> kwargs['repo_dpath'] = repo
            >>> import pytest
            >>> with pytest.raises(Exception):
            >>>     cls.main(cmdline=cmdline, **kwargs)
        """
        from git_well._utils import rich_print
        config = cls.cli(cmdline=cmdline, data=kwargs, strict=True)
        rich_print('config = ' + ub.urepr(config, nl=1))

        from git_well.repo import Repo
        from os.path import expanduser, relpath, join

        repo = Repo.coerce(config.repo_dpath)
        root_dpath = repo.dpath

        host = config.host
        home = config.home
        if home is None:
            home = expanduser('~')
        try:
            rel_dpath = relpath(root_dpath, home)
        except ValueError:
            raise ValueError((
                'We assume that you are running relative '
                'to your home directory. rel_dpath={}, home={}').format(rel_dpath, home))

        remote_cwd = config.remote_cwd
        if remote_cwd is None:
            remote_cwd = rel_dpath
            print(f'home={home}')
            print(f'root_dpath={root_dpath}')
            print(f'remote_cwd={remote_cwd}')
        remote_gitdir = join(remote_cwd, '.git')

        if config.test_remote:
            # Test that the remote actually has this repo
            remote_parts = [
                f'test -e {remote_gitdir}',
            ]
            remote_part = ' && '.join(remote_parts)
            ssh_flags = []
            if config.forward_ssh_agent:
                ssh_flags += ['-A']
            args = [
                'ssh',
                *ssh_flags,
                host,
            ]
            ssh_command = ' '.join(args) + ' "' + remote_part + '"'
            info = ub.cmd(ssh_command)
            try:
                info.check_returncode()
            except Exception:
                print('Remote does not seem accessable or does not have a git repo')
                raise
            else:
                print('Remote exists and appears to be a git repo')

        remote = config.remote
        if remote is None:
            remote = config.host

        # remote_path = f'ssh://{host}:{remote_gitdir}'
        remote_path = f'{host}:{remote_gitdir}'
        add_command = f'git remote add {remote} {remote_path}'
        ub.cmd(add_command, verbose=3, check=True)


def fsspec_shh_connect(host):
    # This is not as easy as it could be
    # Paramiko does not respect the ssh config by default, but it does
    # give us tools to parse it. However, it is still not straightforward
    # Might look into "fabric"?
    import paramiko
    import os
    ssh_config = paramiko.SSHConfig()
    user_config_file = os.path.expanduser("~/.ssh/config")
    if os.path.exists(user_config_file):
        with open(user_config_file) as f:
            ssh_config.parse(f)
    user_config = ssh_config.lookup(host)
    ssh_kwargs = {
        'username': user_config['user'],
        'key_filename': user_config['identityfile'][0],
    }
    # import fsspec
    from fsspec.implementations.sftp import SFTPFileSystem
    fs = SFTPFileSystem(host=user_config['hostname'], **ssh_kwargs)
    return fs
    # client = paramiko.SSHClient()
    # client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    # client.connect(user_config['hostname'], **ssh_kwargs)


__cli__ = GitDiscoverRemoteCLI
main = __cli__.main

if __name__ == '__main__':
    """

    CommandLine:
        python ~/code/git_well/git_well/git_discover_remote.py
        python -m git_well.git_discover_remote
    """
    main()
