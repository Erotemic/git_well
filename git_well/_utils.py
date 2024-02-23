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
