#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
from __future__ import annotations

from typing import Any

# import ubelt as ub
import kwconf as kw
import ubelt as ub
from git_well.git_squash_streaks import (
    _squash_between,
    checkout_temporary_branch,
)


class GitSquashCLI(kw.Config):
    """
    Squash all commits between two points (usually main and HEAD).
    This CLI is experimental and designed to eventually supercede
    squash-streaks with a better API.
    """

    __command__ = 'squash'

    oldest = kw.Value('main', help='The oldest commit to sqash onto')
    newest = kw.Value(
        'HEAD', help='The latest commit to be included in the squash'
    )

    inplace = kw.Value(
        False, isflag=True, help='Apply squash directly to current branch'
    )
    dry = kw.Value(True, isflag=True, short_alias=['n'], help='Dry run')
    force = kw.Value(
        None,
        isflag=True,
        short_alias=['f'],
        help='Force squash (opposite of dry)',
    )
    verbose = kw.Value(
        True, isflag=True, short_alias=['v'], help='Print progress'
    )
    dpath = kw.Value('.', help='Path to repo to squash in')

    auto_rollback = kw.Value(
        False,
        isflag=True,
        help=ub.paragraph(
            """
            if True the repo will be reset to a clean state if any
            errors occur. (Default: True)
            """
        ),
    )

    def __post_init__(self) -> None:
        force = self['force']
        dry = self['dry']
        if force is not None:
            self['dry'] = not force
        else:
            self['force'] = not dry

    @classmethod
    def main(
        cls, argv: list[str] | str | bool | None = True, **kwargs: Any
    ) -> None:
        """
        Example:
            >>> # xdoctest: +REQUIRES(LINUX)
            >>> from git_well.git_squash import *  # NOQA
            >>> from git_well.repo import Repo
            >>> repo = Repo.demo()
            >>> repo.git.checkout(b='test-squash-branch')
            >>> new_fpath = (repo.dpath / 'new_file.txt')
            >>> new_fpath.write_text('new data')
            >>> repo.git.add(new_fpath)
            >>> repo.git.commit(m='test-squash commit 1')
            >>> new_fpath.write_text('new data with changes')
            >>> repo.git.add(new_fpath)
            >>> repo.git.commit(m='test-squash commit 2')
            >>> kwargs = {
            >>>     'dpath': repo.dpath,
            >>>     'dry': False,
            >>> }
            >>> argv = False
            >>> cls = GitSquashCLI
            >>> cls.main(argv=argv, **kwargs)

        """
        config = cls.cli(argv=argv, data=kwargs, strict=True, verbose=True)
        squash_commits(config)


def squash_commits(config: Any) -> None:
    from git_well.repo import Repo

    repo = Repo.coerce(config.dpath)
    orig_branch_name = repo.active_branch.name
    oldest = repo.commit(config.oldest)
    newest = repo.commit(config.newest)

    if repo.is_ancestor(ancestor_rev=newest, rev=oldest):
        raise ValueError(f'Commit {oldest} is not an ancestor of {newest}')

    if not config.dry:
        temp_branch = checkout_temporary_branch(repo, '-squash-temp')
    else:
        temp_branch = None

    try:
        _squash_between(
            repo,
            start=oldest,
            stop=newest,
            dry=config.dry,
            verbose=config.verbose,
            start_inclusive=False,
        )
    except Exception:
        print(
            'Squash failed. Consider running with --dry or checking your range.'
        )
        if not config.dry and config.auto_rollback:
            print('ROLLING BACK')
            repo.git.checkout(orig_branch_name)
        raise

    if config['dry']:
        print('Dry run finished. No changes made.')
    elif config['inplace']:
        repo.git.checkout(repo.active_branch.name)
        repo.git.reset(temp_branch, hard=True)
        repo.git.branch('-D', temp_branch)
        print('Squash applied. You should now push with --force if needed.')
    else:
        repo.git.checkout(repo.active_branch.name)
        print(f'Squashed branch is: {temp_branch}')
        print('Review the changes with:')
        print(f'    gitk {repo.active_branch.name} {temp_branch}')
        print('Run again with --inplace to apply or manually reset.')


__cli__ = GitSquashCLI

if __name__ == '__main__':
    __cli__.main()
