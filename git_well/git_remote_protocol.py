#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
from __future__ import annotations

from typing import Any

import kwconf
import ubelt as ub

from git_well._utils import GitURL

# TODO: more protocols? ssh?
VALID_PROTOCOLS: list[str] = ['git', 'https', 'ssh']


class GitRemoteProtocol(kwconf.Config):
    """
    Change the protocol for all remotes that match a specific user / group.

    The new protocol can be git or https.

    An alias for this command is ``git permit`` because it "permits" a specific
    group to use ssh permissions.
    """

    __command__: str = 'remote_protocol'
    __alias__: list[str] = ['permit']

    group: str = kwconf.Value(
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

    protocol: str = kwconf.Value(
        'git',
        position=2,
        choices=VALID_PROTOCOLS,
        help=ub.paragraph(
            """
        The protocol to change to.
        """
        ),
    )

    repo_dpath: str = kwconf.Value(
        '.',
        position=3,
        help=ub.paragraph(
            """
        A path inside the repo to modify.
        """
        ),
    )


def _config_values(repo: Any, key: str) -> list[str]:
    info = ub.cmd(
        ['git', 'config', '--get-all', key], cwd=repo.dpath, verbose=0
    )
    if info.returncode == 1:
        return []
    if info.returncode:
        raise RuntimeError(f'Unable to read git config key: {key}')
    stdout = info.stdout or ''
    if isinstance(stdout, bytes):
        stdout = stdout.decode(errors='replace')
    return stdout.splitlines()


def _replace_config_values(repo: Any, key: str, values: list[str]) -> None:
    info = ub.cmd(
        ['git', 'config', '--unset-all', key], cwd=repo.dpath, verbose=0
    )
    if info.returncode not in {0, 1, 5}:
        raise RuntimeError(f'Unable to clear git config key: {key}')
    for value in values:
        info = ub.cmd(
            ['git', 'config', '--add', key, value],
            cwd=repo.dpath,
            verbose=0,
        )
        if info.returncode:
            raise RuntimeError(f'Unable to update git config key: {key}')


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

    Ignore:
        >>> # Test the interactive part
        >>> from git_well.git_remote_protocol import GitRemoteProtocol
        >>> from git_well.repo import Repo
        >>> repo = Repo.demo()
        >>> repo.cmd('git remote add remote1 https://github.com/User1/foobar.git')
        >>> repo.cmd('git remote add remote2 https://github.com/User2/foobar.git')
        >>> GitRemoteProtocol.main(argv=False, repo_dpath=repo)
    """
    config = GitRemoteProtocol.cli(argv=argv, data=kwargs, strict=True)
    from git_well._utils import rich_print
    from git_well.repo import Repo

    rich_print('config = ' + ub.urepr(config, nl=1))
    repo = Repo.coerce(config['repo_dpath'])

    new_protocol = config.protocol
    if new_protocol not in VALID_PROTOCOLS:
        raise KeyError(new_protocol)

    remote_entries = []
    for remote in repo.remotes:
        for value_name in ['url', 'pushurl']:
            key = f'remote.{remote.name}.{value_name}'
            for index, raw_url in enumerate(_config_values(repo, key)):
                url = GitURL(raw_url)
                try:
                    info = url.info
                except ValueError as ex:
                    print(f'Skipping unsupported remote URL {raw_url!r}: {ex}')
                    continue
                if info['protocol'] in {'local', 'file'}:
                    print(f'Skipping local remote URL: {raw_url}')
                    continue
                remote_entries.append(
                    {
                        'remote': remote.name,
                        'key': key,
                        'index': index,
                        'url': url,
                    }
                )

    print(
        'remote_urls = {}'.format(
            ub.urepr([e['url'].info for e in remote_entries], nl=1)
        )
    )

    group = config['group']
    if group == 'special:auto':
        print('Automatically determining group to change protocol for')
        choices = list(
            ub.unique([e['url'].info['group'] for e in remote_entries])
        )
        if len(choices) == 1:
            group = choices[0]
            print(f'Auto group: {group}')
        elif not choices:
            raise ValueError('No convertible network remotes were found')
        else:
            from git_well._utils import choice_prompt

            group = choice_prompt(
                'Which group to change protocol for?', choices=choices
            )

    changes_by_key: dict[str, list[tuple[int, GitURL]]] = {}
    for entry in remote_entries:
        url = entry['url']
        if group != url.info['group']:
            continue
        converted = url.to_protocol(new_protocol)
        if converted != url:
            changes_by_key.setdefault(entry['key'], []).append(
                (entry['index'], converted)
            )

    num_changes = sum(map(len, changes_by_key.values()))
    print(f'Making {num_changes} changes')
    for key, changes in changes_by_key.items():
        values = _config_values(repo, key)
        for index, new_url in changes:
            values[index] = str(new_url)
        _replace_config_values(repo, key, values)


__cli__ = GitRemoteProtocol
__cli__.main = main

if __name__ == '__main__':
    """
    CommandLine:
        python -m git_well.git_remote_protocol
    """
    main()
