import ubelt as ub


def rich_print(*args, **kwargs):
    try:
        from rich import print as print_
    except Exception:
        print_ = print
    return print_(*args, **kwargs)


def find_merged_branches(repo, main_branch='main'):
    # git branch --merged main
    # main_branch = 'main'
    merged_branches = [p.replace('*', '').strip() for p in repo.git.branch(merged=main_branch).split('\n') if p.strip()]
    merged_branches = ub.oset(merged_branches) - {main_branch}
    return merged_branches


def confirm(msg):
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


def choice_prompt(msg, choices):
    """
    Ignore:
        choice_prompt('which one?', choices=['a', 'b', 'c'])
    """
    try:
        from rich.prompt import Prompt, InvalidResponse
    except ImportError:
        print('Rich is required here')
        raise

    class ChoiceWithIntPrompt(Prompt):
        """
        Assigns an integer to each choice.
        """
        def make_prompt(self, default):
            prompt = self.prompt.copy()
            prompt.end = ""

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
                    prompt.append(f'{choice}\n', style="prompt")
            if (
                default != ...
                and self.show_default
                and isinstance(default, (str, self.response_type))
            ):
                prompt.append(" ")
                _default = self.render_default(default)
                prompt.append(_default)

            prompt.append(self.prompt_suffix)
            return prompt

        def process_response(self, value: str):
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


def find_git_root(dpath):
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
    return found


class GitURL(str):
    """
    Represents a url to a git repo and can parse info about / modify the
    protocol

    References:
        https://git-scm.com/docs/git-clone#_git_urls

    CommandLine:
        xdoctest -m git_well.git_remote_protocol GitURL

    Example:
        >>> from git_well.git_remote_protocol import *  # NOQA
        >>> from git_well._utils import *  # NOQA
        >>> urls = [
        >>>     GitURL('https://foo.bar/user/repo.git'),
        >>>     GitURL('ssh://foo.bar/user/repo.git'),
        >>>     GitURL('ssh://git@foo.bar/user/repo.git'),
        >>>     GitURL('git@foo.bar:group/repo.git'),
        >>>     GitURL('host:path/to/my/repo/.git'),
        >>> ]
        >>> for url in urls:
        >>>     info = url.info
        >>>     print('---')
        >>>     print(f'url = {url}')
        >>>     print(ub.urepr(info))
        >>>     print('As git   : ' + url.to_git())
        >>>     print('As ssh   : ' + url.to_ssh())
        >>>     print('As https : ' + url.to_https())
        >>>     if info['protocol'] not in {'scp'}:
        >>>         # SCP recon is broken
        >>>         recon = url.to_protocol(info['protocol'])
        >>>         assert recon == url
    """

    def __init__(self, data):
        # note: inheriting from str so data is handled in __new__
        self._info = None

    def _parse(self):
        import parse
        parse.Parser('ssh://{user}')

    def _fixup_endpoint(self, repo_endpoint):
        if repo_endpoint.endswith('.git'):
            repo_name = repo_endpoint[:-4]
        else:
            repo_name = repo_endpoint
            repo_endpoint = repo_name + '.git'
        return repo_name, repo_endpoint

    @property
    def info(self):
        if self._info is None:
            url = self
            info = {}
            if url.startswith('https://'):
                parts = url.split('https://')[1].split('/', 3)
                repo_endpoint = parts[2]
                repo_name, repo_endpoint = self._fixup_endpoint(repo_endpoint)
                info['host'] = parts[0]
                info['group'] = parts[1]
                info['repo_name'] = repo_name
                info['repo_endpoint'] = repo_endpoint
                info['user'] = None
                info['protocol'] = 'https'
            elif url.startswith('git@'):
                parts = url.split('git@')[1].split(':')
                repo_endpoint = parts[1].split('/')[1]
                repo_name, repo_endpoint = self._fixup_endpoint(repo_endpoint)
                info['host'] = parts[0]
                info['group'] = parts[1].split('/')[0]
                info['repo_name'] = repo_name
                info['repo_endpoint'] = repo_endpoint
                info['user'] = 'git'
                info['protocol'] = 'git'
            elif url.startswith('ssh://'):
                parts = url.split('ssh://')[1].split('/', 3)
                user = None
                if '@' in parts[0]:
                    user, host = parts[0].split('@')
                else:
                    host = parts[0]
                repo_name, repo_endpoint = self._fixup_endpoint(parts[2])
                info['host'] = host
                info['user'] = user
                info['group'] = parts[1]
                info['repo_name'] = repo_name
                info['repo_endpoint'] = repo_endpoint
                info['protocol'] = 'ssh'
            elif url.endswith('/.git'):
                # An ssh protocol to an explicit directory
                host, rest = url.split(':', 1)
                parts = rest.rsplit('/',  2)
                info['host'] = host
                info['group'] = parts[0]
                info['repo_name'] = parts[1]
                info['repo_endpoint'] = parts[1] + '/.git'
                info['protocol'] = 'scp'
            elif '//' not in url and '@' not in url:
                parts = url.split(':')
                repo_name, repo_endpoint = self._fixup_endpoint(parts[1].split('/')[1])
                info['host'] = parts[0]
                info['group'] = parts[1].split('/')[0]
                info['repo_name'] = repo_name
                info['repo_endpoint'] = repo_endpoint
                info['protocol'] = 'ssh'
            else:
                raise ValueError(url)
            info['url'] = url
            self._info = info
        return self._info

    def to_protocol(self, protocol):
        """
        Convert the URL to a different protocol
        """
        if protocol == 'git':
            return self.to_git()
        elif protocol in {'ssh', 'scp'}:
            return self.to_ssh()
        elif protocol == 'https':
            return self.to_https()
        else:
            raise KeyError(protocol)

    def to_git(self):
        info = self.info
        new_url = 'git@' + info['host']  + ':' + info['group'] + '/' + info['repo_endpoint']
        return self.__class__(new_url)

    def to_ssh(self):
        info = self.info
        user = info.get('user', None)
        if user is None:
            user_part = ''
        else:
            user_part = user + '@'
        new_url = 'ssh://' + user_part + info['host']  + '/' + info['group'] + '/' + info['repo_endpoint']
        return self.__class__(new_url)

    def to_https(self):
        info = self.info
        new_url = 'https://' + info['host']  + '/' + info['group'] + '/' + info['repo_endpoint']
        return self.__class__(new_url)
