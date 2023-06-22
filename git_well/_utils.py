import ubelt as ub
from packaging.version import parse as Version


try:
    from rich import print as rich_print
except Exception:
    rich_print = print


def find_merged_branches(repo, main_branch='main'):
    # git branch --merged main
    # main_branch = 'main'
    merged_branches = [p.strip() for p in repo.git.branch(merged=main_branch).split('\n') if p.strip()]
    merged_branches = ub.oset(merged_branches) - {main_branch}
    return merged_branches


def dev_branches(repo):
    branch_infos = []
    for line in repo.git.branch('-r').split('\n'):
        line = line.strip().split('->')[-1].strip()
        for remote in repo.remotes:
            if line.startswith(remote.name):
                info = {
                    'remote': remote,
                    'branch_name': line.lstrip(remote.name + '/'),
                    'full_name': line,
                }
                branch_infos.append(info)

    for branch in repo.branches:
        info = {
            'remote': None,
            'branch': branch,
            'branch_name': branch.name,
            'datetime': branch.commit.committed_datetime,
        }
        branch_infos.append(info)

    dev_infos = []
    for info in branch_infos:
        if info['branch_name'].startswith('dev/'):
            vstr = info['branch_name'].split('/')[-1]
            try:
                info['version'] = Version(vstr)
            except Exception:
                ...
            else:
                # if not isinstance(info['version'], LegacyVersion):
                dev_infos.append(info)

    versioned_dev_branches = sorted(dev_infos, key=lambda x: x['version'])
    return versioned_dev_branches


def confirm(msg):
    try:
        from rich import prompt
        ret = prompt.Confirm.ask('Remove dev branches?')
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
