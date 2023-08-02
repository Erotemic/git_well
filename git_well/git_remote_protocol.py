#!/usr/bin/env python3
import scriptconfig as scfg
import ubelt as ub


class GitRemoteProtocol(scfg.DataConfig):
    """
    Helper to change a remote from https to ssh / git for a specific user /
    group.
    """
    __command__ = 'remote_protocol'
    group = scfg.Value(None, position=1, help='the group to change the protocol for')
    repo_dpath = scfg.Value('.', position=2, help='location of the repo')
    new_protocol = scfg.Value('git', help='protocol to change to')
    # remote = scfg.Value(None, help='the remote to change')


def _parse_remote_url(url):
    info = {}
    if url.startswith('https://'):
        parts = url.split('https://')[1].split('/')
        info['host'] = 'https://' + parts[0]
        info['group'] = parts[1]
        info['repo_name'] = parts[2]
        info['protocol'] = 'https'
    elif url.startswith('git@'):
        url.split('git@')[1]
        parts = url.split('git@')[1].split(':')
        info['host'] = 'https://' + parts[0]
        info['group'] = parts[1].split('/')[0]
        info['repo_name'] = parts[1].split('/')[1]
        info['protocol'] = 'git'
    else:
        raise ValueError(url)
    return info


def main(cmdline=1, **kwargs):
    """
    Example:
        >>> # xdoctest: +SKIP
        >>> cmdline = 0
        >>> kwargs = dict()
        >>> main(cmdline=cmdline, **kwargs)
    """
    config = GitRemoteProtocol.cli(cmdline=cmdline, data=kwargs, strict=True)
    print('config = ' + ub.urepr(dict(config), nl=1))
    from git_well._utils import find_git_root
    from git_well._repo_ext import Repo
    repo_root = find_git_root(config['repo_dpath'])
    repo = Repo(repo_root)
    repo.config_fpath

    tasks = []
    for remote in repo.remotes:
        for url in remote.urls:
            info = _parse_remote_url(url)
            if info['protocol'] != config.new_protocol:
                if config.group == info['group']:
                    new_url = 'git@' + info['host']  + ':' + info['group'] + '/' + info['repo_name']
                    tasks.append({
                        'task': 'change',
                        'old': url,
                        'new': new_url,
                    })

    config_text = repo.config_fpath.read_text()
    for task in tasks:
        config_text = config_text.replace(task['old'], task['new'])
    repo.config_fpath.write_text(config_text)

__config__ = GitRemoteProtocol

if __name__ == '__main__':
    """
    CommandLine:
        python -m git_well.git_remote_protocol
    """
    main()
