
def make_dummy_git_repo():
    import ubelt as ub
    repo_dpath = ub.Path.appdir('git_well', 'tests', 'dummy-git-repo')
    repo_dpath.delete().ensuredir()

    import git
    repo = git.Repo.init(repo_dpath, initial_branch='main')

    repo.git.config('user.email', 'demo.user@zombo.com', local=True)
    repo.git.config('user.name', 'Demo User', local=True)

    fpath = (repo_dpath / 'data1.txt')
    fpath.write_text('data')

    repo.git.add(fpath)
    repo.git.commit(a=True, m="Add Data1")

    for idx in range(5):
        fpath.write_text(f'data{idx}')
        repo.git.commit(a=True, m="wip")

    repo.git.checkout(b='branch1')
    fpath = (repo_dpath / 'data2.txt')
    for idx in range(5):
        fpath.write_text(f'data{idx}')
        repo.git.add(fpath)
        repo.git.commit(a=True, m="wip")

    repo.git.checkout('main')

    repo.git.checkout(b='branch2')
    fpath = (repo_dpath / 'data3.txt')
    for idx in range(5):
        fpath.write_text(f'data{idx}')
        repo.git.add(fpath)
        repo.git.commit(a=True, m="wip")

    repo.git.checkout('main')
    repo.git.merge('branch1')
    repo.git.merge('branch2')

    fpath = (repo_dpath / 'data3.txt')
    for idx in range(10):
        fpath.write_text(f'data{idx}')
        repo.git.add(fpath)
        repo.git.commit(a=True, m="wip")

    return repo_dpath


def make_dummy_git_repo_with_orphans():
    import ubelt as ub
    import git
    repo_dpath = ub.Path.appdir('git_well', 'tests', 'dummy-git-orphan-repo')
    repo_dpath.delete().ensuredir()

    # Setup a demo git repo
    repo = git.Repo.init(repo_dpath, initial_branch='main')
    repo.git.config('user.email', 'demo.user@zombo.com', local=True)
    repo.git.config('user.name', 'Demo User', local=True)

    # Write a starting commit
    fpath = (repo_dpath / 'data1.txt')
    fpath.write_text('data-base')
    repo.git.add(fpath)
    repo.git.commit(a=True, m='base commit')
    base_commit = repo.head.commit.hexsha

    # Make 3 variants, orphaning 2 of them.
    num_paths = 3
    leaf_commits = []
    for idx in range(num_paths):
        repo.git.checkout(base_commit)
        fpath.write_text(f'data-path{idx}-step1')
        repo.git.add(fpath)
        repo.git.commit(a=True, m=f'path{idx}-step1 commit')
        fpath.write_text(f'data-path{idx}-step2')
        repo.git.add(fpath)
        repo.git.commit(a=True, m=f'path{idx}-step2 commit')
        leaf_commit = repo.head.commit.hexsha
        leaf_commits.append(leaf_commit)

    ###
    # Requires a GUI
    # ub.cmd('gitk --all', cwd=repo_dpath)
    command = f'gitk {" ".join(leaf_commits)}'
    print(command)

    # ub.cmd(, cwd=repo_dpath)
    return repo_dpath
