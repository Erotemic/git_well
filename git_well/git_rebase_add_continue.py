#!/usr/bin/env python
# PYTHON_ARGCOMPLETE_OK
from __future__ import annotations

import os
from typing import Any

import kwconf
import ubelt as ub


class GitRebaseAddContinue(kwconf.Config):
    """
    A single step to make rebasing easier.

    Usually a rebase has the user explicitly add and then continue.
    This script checks all of the paths for conflicts and then if none
    exist adds all files and continues.
    """

    __command__: str = 'rebase_add_continue'

    repo_dpath: str = kwconf.Value('.', help='location of the repo')

    skip_editor: bool = kwconf.Value(
        True,
        help='if True skip the editor to change the commit message on git rebase --continue',
    )

    @classmethod
    def main(
        cls, argv: list[str] | str | bool | None = True, **kwargs: Any
    ) -> None:
        """
        Example:
            >>> from git_well.git_rebase_add_continue import GitRebaseAddContinue
            >>> from git_well.repo import Repo
            >>> cls = GitRebaseAddContinue
            >>> repo = Repo.demo()
            >>> # TODO: make a plausible scenario
            >>> argv = False
            >>> kwargs = dict()
            >>> kwargs['repo_dpath'] = repo
            >>> import pytest
            >>> with pytest.raises(RuntimeError):
            >>>     cls.main(argv=argv, **kwargs)
        """
        config = cls.cli(argv=argv, data=kwargs)
        from git_well._utils import rich_print
        from git_well.repo import Repo

        rich_print('config = {}'.format(ub.urepr(config, nl=1)))
        repo = Repo.coerce(config.repo_dpath)
        repo_dpath = repo.dpath

        fpaths = parsed_rebase_git_status(repo_dpath)

        num_paths = ub.udict(fpaths).map_values(len)
        print('num_paths = {}'.format(ub.urepr(num_paths, nl=1)))

        if 0:
            import xdev

            b = xdev.RegexBuilder.coerce('python')
            b.previous(exact='7')
        import re

        # Check if conflicts are resolved
        conflict_patterns = [
            re.compile(r'^<<<<<<< .+$', flags=re.MULTILINE),
            re.compile(r'^>>>>>>> .+$', flags=re.MULTILINE),
        ]


        conflicts = []
        for fpath in ub.flatten(fpaths.values()):
            try:
                text = fpath.read_text()
            except IsADirectoryError:
                continue
            except UnicodeDecodeError:
                conflicts.append(fpath)
                continue
            except FileNotFoundError:
                text = ''
            for pat in conflict_patterns:
                if pat.search(text):
                    conflicts.append(fpath)
                    break

        if conflicts:
            print('conflicts = {}'.format(ub.urepr(conflicts, nl=1)))
            raise Exception('Paths still have unresolved conflicts')

        print('Did not detect any unresolved conflicts')

        # If everything looks ok run git add && git rebase --continue
        print('Running git add')
        add_paths = list(
            ub.unique(fpaths['both_modified'] + fpaths['unstaged'])
        )
        if add_paths:
            repo.git.add(*map(os.fspath, add_paths))

        print('Running git rebase --continue')
        if config.skip_editor:
            # Use -c core.editor=true to skip the commit message editor
            # References:
            #     ... [SO43489971] https://stackoverflow.com/questions/43489971/how-to-suppress-the-editor-for-git-rebase-continue
            info = ub.cmd(
                'git -c core.editor=true rebase --continue',
                verbose=3,
                cwd=repo.dpath,
                system=True,
            )
        else:
            info = ub.cmd(
                'git rebase --continue', verbose=3, cwd=repo.dpath, system=True
            )
        if info['ret'] == 0:
            print('rebase is complete')
        else:
            info = ub.cmd('git status', verbose=3, cwd=repo.dpath, system=True)
            print('rebase is still active')


def _git_output_text(info: Any) -> str:
    stdout = info.stdout
    if isinstance(stdout, bytes):
        return stdout.decode(errors='surrogateescape')
    return stdout or ''


def _git_nul_paths(
    repo_dpath: str | os.PathLike[str], argv: list[str]
) -> list[ub.Path]:
    info = ub.cmd(argv, cwd=repo_dpath, verbose=0)
    if info.returncode:
        raise RuntimeError(
            'Git command failed while inspecting rebase state: '
            + ' '.join(argv)
        )
    return [
        ub.Path(repo_dpath) / rel_path
        for rel_path in _git_output_text(info).split('\0')
        if rel_path
    ]


def _rebase_is_active(repo_dpath: str | os.PathLike[str]) -> bool:
    for name in ['rebase-merge', 'rebase-apply']:
        info = ub.cmd(
            ['git', 'rev-parse', '--git-path', name],
            cwd=repo_dpath,
            verbose=0,
        )
        if info.returncode == 0:
            git_path = ub.Path(_git_output_text(info).strip())
            if not git_path.is_absolute():
                git_path = ub.Path(repo_dpath) / git_path
            if git_path.exists():
                return True
    return False


def parsed_rebase_git_status(
    repo_dpath: str | os.PathLike[str],
) -> dict[str, list[ub.Path]]:
    """Inspect rebase paths using stable, NUL-delimited Git plumbing."""
    if not _rebase_is_active(repo_dpath):
        raise RuntimeError('Not currently rebasing')

    unmerged = _git_nul_paths(
        repo_dpath,
        ['git', 'diff', '--name-only', '--diff-filter=U', '-z'],
    )
    unstaged = _git_nul_paths(
        repo_dpath, ['git', 'diff', '--name-only', '-z']
    )
    staged = _git_nul_paths(
        repo_dpath, ['git', 'diff', '--cached', '--name-only', '-z']
    )

    unmerged_set = set(unmerged)
    return {
        'modified': staged,
        'both_modified': unmerged,
        'unstaged': [p for p in unstaged if p not in unmerged_set],
    }


__cli__ = GitRebaseAddContinue
main = __cli__.main


if __name__ == '__main__':
    """
    CommandLine:
        python ~/code/git_well/git_well/git_rebase_add_continue.py
    """
    __cli__.main()
