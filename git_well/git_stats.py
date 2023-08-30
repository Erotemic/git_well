"""
SeeAlso:
    https://github.com/erikbern/git-of-theseus
    https://stackoverflow.com/questions/42715785/how-do-i-show-statistics-for-authors-contributions-in-git
"""
#!/usr/bin/env python3
import scriptconfig as scfg
import ubelt as ub


class GitStatsCLI(scfg.DataConfig):
    repo_dpath = scfg.Value('.', help='param1', position=1)

    @classmethod
    def main(cls, cmdline=1, **kwargs):
        """
        Example:
            >>> # xdoctest: +SKIP
            >>> from git_well.git_stats import *  # NOQA
            >>> cmdline = 0
            >>> kwargs = dict()
            >>> cls = GitStatsCLI
            >>> cls.main(cmdline=cmdline, **kwargs)
        """
        import rich
        config = cls.cli(cmdline=cmdline, data=kwargs, strict=True)
        rich.print('config = ' + ub.urepr(config, nl=1))
        from git_well.repo import Repo
        repo = Repo.coerce(config.repo_dpath)
        author_stats(repo)


def commit_stats(repo):
    commits = list(repo.iter_commits())
    for commit in commits:
        ...


def author_stats(repo):
    log_info = repo.cmd("git log --format='author: %ae' --numstat")
    log_info = repo.cmd("git log --since='1 year ago' --format='author: %ae' --numstat")
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
    import pandas as pd
    import rich
    df = pd.DataFrame(author_stats).T
    rich.print(df)

__cli__ = GitStatsCLI
main = __cli__.main

if __name__ == '__main__':
    """

    CommandLine:
        python ~/code/git_well/git_well/git_stats.py
        python -m git_well.git_stats
    """
    main()
