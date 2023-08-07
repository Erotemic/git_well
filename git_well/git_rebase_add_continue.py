#!/usr/bin/env python
# PYTHON_ARGCOMPLETE_OK
import ubelt as ub
import scriptconfig as scfg


class GitRebaseAddContinue(scfg.DataConfig):
    """
    A single step to make rebasing easier.

    Usually a rebase has the user explicitly add and then continue.
    This script checks all of the paths for conflicts and then if none
    exist adds all files and continues.
    """
    __command__ = 'rebase_add_continue'

    repo_dpath = scfg.Value('.', help='location of the repo')

    skip_editor = scfg.Value(True, help='if True skip the editor to change the commit message on git rebase --continue')

    @classmethod
    def main(cls, cmdline=1, **kwargs):
        """
        import sys, ubelt
        sys.path.append(ubelt.expandpath('~/code/git_well'))
        from git_well.git_rebase_add_continue import *  # NOQA
        cls = GitRebaseAddContinue
        cmdline = 0
        kwargs = {}
        """
        config = cls.cli(cmdline=cmdline, data=kwargs)
        from git_well._utils import rich_print
        rich_print('config = {}'.format(ub.urepr(config, nl=1)))
        resolved_repo = ub.Path(config.repo_dpath).resolve()
        print(f'resolved_repo={resolved_repo}')
        import git
        repo = git.Repo(resolved_repo)

        repo_dpath = resolved_repo

        # status = repo.git.status()
        info = ub.cmd('git status', verbose=3, cwd=resolved_repo)
        status = info['out']
        # print(status)

        # Parse git status to determine paths that have conflicts and need a
        # git add.
        fpaths = {
            'modified': [],
            'both_modified': [],
            'unstaged': [],
        }
        print('parse git status')
        lines = status.split('\n')
        if 'interactive rebase in progress' not in lines[0]:
            raise RuntimeError('Not currently rebasing')

        line_iter = iter(lines)
        state = None
        for line in line_iter:
            if line.strip() == '':
                state = None
            elif line.startswith('Changes not staged for commit:'):
                state = 'UNSTAGED'
                assert 'git add' in next(line_iter)
                assert 'git restore' in next(line_iter)
            elif line.startswith('Changes to be committed:'):
                state = 'MODIFIED'
                assert 'git restore --staged' in next(line_iter)
            elif line.startswith('Unmerged paths:'):
                state = 'UNMERGED'
                assert 'git restore' in next(line_iter)
                assert 'git add <file>' in next(line_iter)
            elif state == 'MODIFIED':
                if line.strip().startswith(('modified:', 'new file:')):
                    rel_fpath = line.split(':', 1)[1].strip()
                    fpath = repo_dpath / rel_fpath
                    fpaths['modified'].append(fpath)
                else:
                    raise AssertionError(f'line={line}')
            elif state == 'UNMERGED':
                if line.strip().startswith(('both modified:',)):
                    rel_fpath = line.split(':', 1)[1].strip()
                    fpath = repo_dpath / rel_fpath
                    fpaths['both_modified'].append(fpath)
                else:
                    raise AssertionError(f'line={line}')
            elif state == 'UNSTAGED':
                if line.strip().startswith(('modified:', 'new file:')):
                    rel_fpath = line.split(':', 1)[1].strip()
                    fpath = repo_dpath / rel_fpath
                    fpaths['unstaged'].append(fpath)
                else:
                    raise AssertionError(f'line={line}')
        print('finished parse git status')
        num_paths = ub.udict(fpaths).map_values(len)
        print('num_paths = {}'.format(ub.urepr(num_paths, nl=1)))

        if 0:
            import xdev
            b = xdev.RegexBuilder.coerce('python')
            b.previous(exact='7')
        import re
        # Check if conflicts are resolved
        conflict_patterns = [
            re.compile('^' + ('<' * 7) + ' HEAD$', flags=re.MULTILINE),
            re.compile('^' + ('>' * 7) + ' HEAD$', flags=re.MULTILINE),
            # re.compile('^' + ('=' * 7) + '$', flags=re.MULTILINE),
            re.compile('^' + ('>' * 7) + r' [0-9a-f]{7} \(.*\)$', flags=re.MULTILINE),
            re.compile('^' + ('<' * 7) + r' [0-9a-f]{7} \(.*\)$', flags=re.MULTILINE),
        ]

        conflicts = []
        for fpath in ub.flatten(fpaths.values()):
            text = fpath.read_text()
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
        repo.git.add(*fpaths['both_modified'], *fpaths['unstaged'])

        print('Running git rebase --continue')
        if config.skip_editor:
            # Use -c core.editor=true to skip the commit message editor
            # References:
            #     ... [SO43489971] https://stackoverflow.com/questions/43489971/how-to-suppress-the-editor-for-git-rebase-continue
            info = ub.cmd('git -c core.editor=true rebase --continue', verbose=3,
                          cwd=resolved_repo, system=True)
        else:
            info = ub.cmd('git rebase --continue', verbose=3,
                          cwd=resolved_repo, system=True)
        if info['ret'] == 0:
            print('rebase is complete')
        else:
            info = ub.cmd('git status', verbose=3, cwd=resolved_repo, system=True)
            print('rebase is still active')


__cli__ = GitRebaseAddContinue
main = __cli__.main


if __name__ == '__main__':
    """
    CommandLine:
        python ~/code/git_well/git_well/git_rebase_add_continue.py
    """
    __cli__.main()
