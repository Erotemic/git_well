#!/usr/bin/env python3
import scriptconfig as scfg
import ubelt as ub


class GitRemoteProtocol(scfg.DataConfig):
    """
    Helper to change a remote from https to ssh / git for a specific user /
    group.
    """
    __command__ = 'remote_protocol'
    group = scfg.Value('auto', position=1, help='the group to change the protocol for. If "auto", then attempts to determine.')
    new_protocol = scfg.Value('git', help='protocol to change to', position=2)
    repo_dpath = scfg.Value('.', help='location of the repo', position=3)
    # remote = scfg.Value(None, help='the remote to change')


def _parse_remote_url(url):
    info = {}
    if url.startswith('https://'):
        parts = url.split('https://')[1].split('/')
        info['host'] = parts[0]
        info['group'] = parts[1]
        info['repo_name'] = parts[2]
        info['protocol'] = 'https'
    elif url.startswith('git@'):
        url.split('git@')[1]
        parts = url.split('git@')[1].split(':')
        info['host'] = parts[0]
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
    import rich
    rich.print('config = ' + ub.urepr(config, nl=1))
    from git_well._utils import find_git_root
    from git_well._repo_ext import Repo
    repo_root = find_git_root(config['repo_dpath'])
    repo = Repo(repo_root)
    repo.config_fpath

    if config.new_protocol not in {'git', 'https'}:
        raise KeyError(config.new_protocol)

    remote_infos = []
    for remote in repo.remotes:
        for url in remote.urls:
            info = _parse_remote_url(url)
            info['url'] = url
            remote_infos.append(info)

    print('remote_infos = {}'.format(ub.urepr(remote_infos, nl=1)))

    if config.group == 'auto':
        print('Automatically determining group to change protocol for')
        choices = list(ub.unique([r['group'] for r in remote_infos]))
        if len(choices) == 1:
            print('Only one choice')
            config.group = choices[0]
            print(f'Auto group: {config.group}')
        else:
            print('Multiple choices:')
            import xdev
            xdev.embed()
            from rich.prompt import Prompt
            ans = Prompt.ask('Which group to change protocol for?', choices=choices)
            if not ans:
                raise Exception
            config.group = ans

    tasks = []
    for info in remote_infos:
        if info['protocol'] != config.new_protocol:
            if config.group == info['group']:
                if config.new_protocol == 'git':
                    new_url = 'git@' + info['host']  + ':' + info['group'] + '/' + info['repo_name']
                elif config.new_protocol == 'https':
                    new_url = 'https://' + info['host']  + '/' + info['group'] + '/' + info['repo_name']
                else:
                    raise KeyError(config.new_protocol)
                tasks.append({
                    'task': 'change',
                    'old': info['url'],
                    'new': new_url,
                })

    config_text = repo.config_fpath.read_text()
    # print('tasks = {}'.format(ub.urepr(tasks, nl=1)))
    print(f'Making {len(tasks)} changes')
    for task in tasks:
        config_text = config_text.replace(task['old'], task['new'])
    repo.config_fpath.write_text(config_text)

__cli__ = GitRemoteProtocol
__cli__.main = main

if __name__ == '__main__':
    """
    CommandLine:
        python -m git_well.git_remote_protocol
    """
    main()
