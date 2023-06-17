
def make_dummy_git_repo():
    import ubelt as ub
    repo_dpath = ub.Path.appdir('git_well', 'tests', 'dummy-git-repo')
    repo_dpath.delete().ensuredir()

    import git
    repo = git.Repo.init(repo_dpath, initial_branch='main')

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
