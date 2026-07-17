from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import ubelt as ub


def rich_print(*args: Any, **kwargs: Any) -> Any:
    try:
        from rich import print as printer
    except Exception:
        printer: Any = print
    return printer(*args, **kwargs)


def rich_link_path(path: str | os.PathLike[str]) -> str:
    """
    Return rich markup that makes a filesystem path clickable.

    This intentionally uses rich's link markup form so terminals that support
    hyperlinks can open local paths directly. Callers should print this with
    :func:`rich_print` or :func:`rich_print_path` when rich is available.
    """
    path_text = os.fspath(path)
    return f'[link={path_text}]{path_text}[/link]'


def rich_print_path(
    prefix: str,
    path: str | os.PathLike[str],
    suffix: str = '',
    **kwargs: Any,
) -> Any:
    """
    Print a filesystem path as a rich hyperlink when rich is available.

    If rich cannot be imported, fall back to builtin :func:`print` with the
    plain path text so users do not see raw rich markup.
    """
    path_text = os.fspath(path)
    try:
        from rich import print as printer
    except Exception:
        printer: Any = print
        msg = f'{prefix}{path_text}{suffix}'
    else:
        msg = f'{prefix}{rich_link_path(path_text)}{suffix}'
    return printer(msg, **kwargs)


def find_merged_branches(repo: Any, main_branch: str = 'main') -> Any:
    # git branch --merged main
    # main_branch = 'main'
    merged_branches = [
        p.replace('*', '').strip()
        for p in repo.git.branch(merged=main_branch).split('\n')
        if p.strip()
    ]
    merged_branches = ub.oset(merged_branches) - {main_branch}
    return merged_branches


def confirm(msg: str) -> bool:
    try:
        from rich import prompt

        ret = prompt.Confirm.ask(msg)
    except ImportError:
        while True:
            ans = input(msg + ' [y/n]')
            if ans in {'y', 'yes'}:
                ret = True
                break
            elif ans in {'n', 'no'}:
                ret = False
                break
            else:
                print('invalid response')
    return ret


def choice_prompt(msg: str, choices: list[str]) -> str:
    """
    Ignore:
        choice_prompt('which one?', choices=['a', 'b', 'c'])
    """
    try:
        from rich.prompt import InvalidResponse, Prompt
    except ImportError:
        print('Rich is required here')
        raise

    class ChoiceWithIntPrompt(Prompt):
        """
        Assigns an integer to each choice.
        """

        def make_prompt(self, default: Any) -> Any:
            prompt = self.prompt.copy()
            prompt.end = ''

            if self.show_choices and self.choices:
                prompt.append('\n')
                for idx, choice in enumerate(self.choices, start=1):
                    try:
                        int(choice)
                    except ValueError:
                        ...
                    else:
                        raise AssertionError('choices cannot be integers')

                    prompt.append(f'{idx}. ', style='json.number')
                    prompt.append(f'{choice}\n', style='prompt')
            if (
                default != ...
                and self.show_default
                and isinstance(default, (str, self.response_type))
            ):
                prompt.append(' ')
                _default = self.render_default(default)
                prompt.append(_default)

            prompt.append(self.prompt_suffix)
            return prompt

        def process_response(self, value: str) -> str:
            value = value.strip()
            assert self.choices is not None
            try:
                return_value = self.response_type(value)
            except ValueError:
                raise InvalidResponse(self.validate_error_message)

            try:
                idx = int(return_value) - 1
                return_value = self.choices[idx]
            except Exception:
                ...

            if return_value not in self.choices:
                raise InvalidResponse(self.illegal_choice_message)

            return return_value

    return ChoiceWithIntPrompt.ask(msg, choices=choices)


def find_git_root(dpath: str | os.PathLike[str]) -> ub.Path:
    if 0:
        # Old implementation
        cwd = ub.Path(dpath).absolute()
        parts = cwd.parts
        found = None
        for i in reversed(range(0, len(parts) + 1)):
            p = ub.Path(*parts[0:i])
            cand = p / '.git'
            if cand.exists():
                found = p
                break
        if found is None:
            raise Exception('cannot find git root')
    else:
        # New implementation (should be more robust)
        # allow running inside a subdir of a repo
        info = ub.cmd('git rev-parse --show-toplevel', cwd=dpath, verbose=0)
        if info['ret'] != 0:
            raise RuntimeError(f'Not a git repo: {dpath}')
        found = ub.Path(info['out'].strip())
    return found


class GitURL(str):
    """Parse and convert common network Git remote URL forms.

    Local paths are parsed so callers can inspect or skip them, but they cannot
    be converted into a network URL without an explicit host.

    Example:
        >>> from git_well._utils import GitURL
        >>> urls = [
        >>>     GitURL('https://foo.bar/group/subgroup/repo.git'),
        >>>     GitURL('ssh://git@foo.bar:2222/group/repo.git'),
        >>>     GitURL('alice@foo.bar:group/repo.git'),
        >>>     GitURL('/home/me/repo.git'),
        >>> ]
        >>> assert urls[0].info['group'] == 'group/subgroup'
        >>> assert urls[0].info['repo_name'] == 'repo'
        >>> assert urls[1].to_ssh() == urls[1]
        >>> assert urls[2].info['user'] == 'alice'
        >>> assert urls[3].info['protocol'] == 'local'
    """

    def __init__(self, data: str) -> None:
        self._info: dict[str, Any] | None = None

    @staticmethod
    def _fixup_endpoint(repo_endpoint: str) -> tuple[str, str]:
        if repo_endpoint.endswith('/.git'):
            repo_name = repo_endpoint[: -len('/.git')].rsplit('/', 1)[-1]
        elif repo_endpoint.endswith('.git'):
            repo_name = repo_endpoint[:-4]
        else:
            repo_name = repo_endpoint
            repo_endpoint = repo_name + '.git'
        return repo_name, repo_endpoint

    @classmethod
    def _split_repo_path(cls, path: str) -> tuple[str, str, str]:
        path = path.strip('/')
        if not path:
            raise ValueError('Git remote URL does not contain a repository path')
        parts = path.split('/')
        if parts[-1] == '.git':
            if len(parts) < 2:
                raise ValueError(f'Invalid explicit-directory Git URL: {path!r}')
            repo_endpoint = parts[-2] + '/.git'
            repo_name = parts[-2]
            group = '/'.join(parts[:-2])
        else:
            repo_name, repo_endpoint = cls._fixup_endpoint(parts[-1])
            group = '/'.join(parts[:-1])
        return group, repo_name, repo_endpoint

    @property
    def info(self) -> dict[str, Any]:
        if self._info is not None:
            return self._info

        import re
        from urllib.parse import unquote, urlsplit

        url = str(self)
        info: dict[str, Any]
        parsed = urlsplit(url) if '://' in url else None
        if parsed is not None:
            protocol = parsed.scheme.lower()
            if protocol == 'file':
                info = {
                    'host': parsed.hostname,
                    'port': parsed.port,
                    'group': '',
                    'repo_name': Path(unquote(parsed.path)).stem,
                    'repo_endpoint': Path(unquote(parsed.path)).name,
                    'user': parsed.username,
                    'protocol': 'file',
                    'path': unquote(parsed.path),
                }
            elif protocol in {'http', 'https', 'ssh', 'git'}:
                group, repo_name, repo_endpoint = self._split_repo_path(
                    unquote(parsed.path)
                )
                info = {
                    'host': parsed.hostname,
                    'port': parsed.port,
                    'group': group,
                    'repo_name': repo_name,
                    'repo_endpoint': repo_endpoint,
                    'user': parsed.username,
                    'protocol': protocol,
                }
            else:
                raise ValueError(f'Unsupported Git URL protocol: {protocol!r}')
        else:
            scp_match = re.match(
                r'^(?:(?P<user>[^/@:]+)@)?'
                r'(?P<host>[^/:\\]+):(?P<path>.+)$',
                url,
            )
            is_windows_drive = bool(re.match(r'^[A-Za-z]:[\\/]', url))
            if scp_match and not is_windows_drive:
                group, repo_name, repo_endpoint = self._split_repo_path(
                    scp_match.group('path')
                )
                user = scp_match.group('user')
                info = {
                    'host': scp_match.group('host'),
                    'port': None,
                    'group': group,
                    'repo_name': repo_name,
                    'repo_endpoint': repo_endpoint,
                    'user': user,
                    'protocol': 'git' if user == 'git' else 'scp',
                }
            else:
                local_path = Path(url)
                endpoint = local_path.name
                if endpoint == '.git':
                    repo_name = local_path.parent.name
                    endpoint = repo_name + '/.git'
                else:
                    repo_name, endpoint = self._fixup_endpoint(endpoint)
                info = {
                    'host': None,
                    'port': None,
                    'group': '',
                    'repo_name': repo_name,
                    'repo_endpoint': endpoint,
                    'user': None,
                    'protocol': 'local',
                    'path': url,
                }
        info['url'] = url
        self._info = info
        return info

    def _network_path(self) -> str:
        info = self.info
        if not info.get('host'):
            raise ValueError(f'Cannot convert local Git URL: {self!s}')
        parts = [info['group'], info['repo_endpoint']]
        return '/'.join(part for part in parts if part)

    def to_protocol(self, protocol: str) -> GitURL:
        if protocol == 'git':
            return self.to_git()
        if protocol in {'ssh', 'scp'}:
            return self.to_ssh()
        if protocol == 'https':
            return self.to_https()
        raise KeyError(protocol)

    def to_git(self) -> GitURL:
        info = self.info
        path = self._network_path()
        host = info['host']
        if info.get('port') is not None:
            return self.__class__(
                f"ssh://git@{host}:{info['port']}/{path}"
            )
        return self.__class__(f'git@{host}:{path}')

    def to_ssh(self) -> GitURL:
        info = self.info
        path = self._network_path()
        user = info.get('user')
        user_part = '' if user is None else user + '@'
        port_part = '' if info.get('port') is None else f":{info['port']}"
        return self.__class__(
            f"ssh://{user_part}{info['host']}{port_part}/{path}"
        )

    def to_https(self) -> GitURL:
        info = self.info
        path = self._network_path()
        return self.__class__(f"https://{info['host']}/{path}")
