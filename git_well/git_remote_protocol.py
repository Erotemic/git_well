#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
from __future__ import annotations

from typing import Any
import kwconf as kw
import ubelt as ub
from git_well._utils import GitURL


# TODO: more protocols? ssh?
VALID_PROTOCOLS: list[str] = ['git', 'https', 'ssh']


class GitRemoteProtocol(kw.Config):
    """
    Change the protocol for all remotes that match a specific user / group.

    The new protocol can be git or https.

    An alias for this command is ``git permit`` because it "permits" a specific
    group to use ssh permissions.
    """

    __command__: str = 'remote_protocol'
    __alias__: list[str] = ['permit']

    group: kw.Value = kw.Value(
        'special:auto',
        position=1,
        help=ub.paragraph(
            """
        The group for which all matching remotes will have their protocol
        changed. If "special:auto", then attempts to determine what the group
        should be. (i.e. if there is only one, use that, otherwise ask).
        """
        ),
    )

    protocol: kw.Value = kw.Value(
        'git',
        position=2,
        choices=VALID_PROTOCOLS,
        help=ub.paragraph(
            """
        The protocol to change to.
        """
        ),
    )

    repo_dpath: kw.Value = kw.Value(
        '.',
        position=3,
        help=ub.paragraph(
            """
        A path inside the repo to modify.
        """
        ),
    )


def main(argv: list[str] | str | bool | None = True, **kwargs: Any) -> None:
    """
    Example:
        >>> from git_well.git_remote_protocol import GitRemoteProtocol
        >>> from git_well.repo import Repo
        >>> repo = Repo.demo()
        >>> repo.cmd('git remote add origin https://github.com/Foobar/foobar.git')
        >>> argv = False
        >>> GitRemoteProtocol.main(argv=argv, repo_dpath=repo, protocol='git')
        >>> assert len(repo.remotes) == 1
        >>> assert list(repo.remotes[0].urls)[0] == 'git@github.com:Foobar/foobar.git'
        >>> GitRemoteProtocol.main(argv=argv, repo_dpath=repo, protocol='https')
        >>> assert list(repo.remotes[0].urls)[0] == 'https://github.com/Foobar/foobar.git'
        >>> GitRemoteProtocol.main(argv=argv, repo_dpath=repo, protocol='git')
        >>> assert list(repo.remotes[0].urls)[0] == 'git@github.com:Foobar/foobar.git'

    Ignore:
        >>> # Test the interactive part
        >>> from git_well.git_remote_protocol import GitRemoteProtocol
        >>> from git_well.repo import Repo
        >>> repo = Repo.demo()
        >>> repo.cmd('git remote add remote1 https://github.com/User1/foobar.git')
        >>> repo.cmd('git remote add remote2 https://github.com/User2/foobar.git')
        >>> argv = False
        >>> kwargs = dict()
        >>> kwargs['repo_dpath'] = repo
        >>> GitRemoteProtocol.main(argv=argv, **kwargs)
    """
    config = GitRemoteProtocol.cli(argv=argv, data=kwargs, strict=True)
    from git_well._utils import rich_print

    rich_print('config = ' + ub.urepr(config, nl=1))
    from git_well.repo import Repo

    repo = Repo.coerce(config['repo_dpath'])
    repo.config_fpath

    new_protocol = config.protocol
    if new_protocol not in VALID_PROTOCOLS:
        raise KeyError(new_protocol)

    remote_urls = []
    for remote in repo.remotes:
        for url in remote.urls:
            url = GitURL(url)
            remote_urls.append(url)

    print(
        'remote_urls = {}'.format(ub.urepr([u.info for u in remote_urls], nl=1))
    )

    group = config['group']
    if group == 'special:auto':
        print('Automatically determining group to change protocol for')
        choices = list(ub.unique([r.info['group'] for r in remote_urls]))
        if len(choices) == 1:
            print('Only one choice')
            group = choices[0]
            print(f'Auto group: {group}')
        else:
            print('Multiple choices:')
            # TODO: dont depend on rich?
            # TODO: better interaction?
            from git_well._utils import choice_prompt

            ans = choice_prompt(
                'Which group to change protocol for?', choices=choices
            )
            group = ans

    tasks = []
    for url in remote_urls:
        if url.info['protocol'] != new_protocol:
            if group == url.info['group']:
                if new_protocol == 'git':
                    new_url = url.to_git()
                elif new_protocol == 'ssh':
                    new_url = url.to_ssh()
                elif new_protocol == 'https':
                    new_url = url.to_https()
                else:
                    raise KeyError(new_protocol)
                tasks.append(
                    {
                        'task': 'change',
                        'old': url,
                        'new': new_url,
                    }
                )

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
