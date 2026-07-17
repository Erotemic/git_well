#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
from __future__ import annotations

"""
SeeAlso:
    https://github.com/erikbern/git-of-theseus
    https://stackoverflow.com/questions/42715785/how-do-i-show-statistics-for-authors-contributions-in-git
"""
#!/usr/bin/env python3
from typing import Any

import kwconf
import ubelt as ub


class GitStatsCLI(kwconf.Config):
    repo_dpath = kwconf.Value('.', help='param1', position=1)

    @classmethod
    def main(
        cls, argv: list[str] | str | bool | None = True, **kwargs: Any
    ) -> None:
        """
        Example:
            >>> # xdoctest: +SKIP
            >>> from git_well.git_stats import *  # NOQA
            >>> argv = 0
            >>> kwargs = dict()
            >>> cls = GitStatsCLI
            >>> cls.main(argv=argv, **kwargs)
        """
        import rich

        config = cls.cli(argv=argv, data=kwargs, strict=True)
        rich.print('config = ' + ub.urepr(config, nl=1))
        from git_well.repo import Repo

        repo = Repo.coerce(config.repo_dpath)
        author_stats(repo)


def _rich_print_author_stats(author_stats, author_files):
    """
    Print author contribution stats as a Rich table.
    """
    from rich.console import Console
    from rich.table import Table

    table = Table(title='Git author stats')
    table.add_column('author')
    table.add_column('commits', justify='right')
    table.add_column('inserts', justify='right')
    table.add_column('deletes', justify='right')
    table.add_column('total', justify='right')
    table.add_column('files', justify='right')

    for author, stats in author_stats.items():
        table.add_row(
            str(author),
            str(stats.get('commits', 0)),
            str(stats.get('inserts', 0)),
            str(stats.get('deletes', 0)),
            str(stats.get('total', 0)),
            str(len(author_files.get(author, set()))),
        )

    Console().print(table)


def commit_stats(repo):
    commits = list(repo.iter_commits())
    for commit in commits:
        ...


def author_stats(repo):
    log_info = repo.cmd("git log --format='author: %ae' --numstat")
    log_info = repo.cmd(
        "git log --since='1 year ago' --format='author: %ae' --numstat"
    )
    print(log_info.stdout)
    author_stats = ub.ddict(lambda: ub.ddict(int))
    author_files = ub.ddict(set)
    author = None
    for line in log_info.stdout.split('\n'):
        line_ = line.strip()
        if line_:
            if line.startswith('author: '):
                author = line.split(' ')[1]
                author_stats[author]['commits'] += 1
            else:
                inserts, deletes, fpath = line.split('\t')
                inserts = int(0 if inserts == '-' else inserts)
                deletes = int(0 if deletes == '-' else deletes)
                total = inserts + deletes
                author_stats[author]['inserts'] += inserts
                author_stats[author]['deletes'] += deletes
                author_stats[author]['total'] += total
                author_files[author].add(fpath)

    author_stats = ub.udict(author_stats).sorted_values(lambda v: v['commits'])
    _rich_print_author_stats(author_stats, author_files)


__cli__ = GitStatsCLI
main = __cli__.main

if __name__ == '__main__':
    """

    CommandLine:
        python ~/code/git_well/git_well/git_stats.py
        python -m git_well.git_stats
    """
    main()
