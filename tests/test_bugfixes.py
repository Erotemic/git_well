import os
import subprocess
from pathlib import Path

import pytest


def _git(repo, *args, check=True):
    info = subprocess.run(
        ['git', *args],
        cwd=repo,
        check=False,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and info.returncode:
        raise AssertionError(
            f'git command failed: {args!r}\n{info.stdout}\n{info.stderr}'
        )
    return info


def _init_repo(dpath):
    dpath.mkdir()
    _git(dpath, 'init', '-b', 'main')
    _git(dpath, 'config', 'user.name', 'Test User')
    _git(dpath, 'config', 'user.email', 'test@example.com')
    return dpath


def _commit_file(repo, name, text, message):
    fpath = repo / name
    fpath.parent.mkdir(parents=True, exist_ok=True)
    fpath.write_text(text)
    _git(repo, 'add', name)
    _git(repo, 'commit', '-m', message)
    return fpath


def _chdir_repo(monkeypatch, repo):
    project_root = Path(__file__).resolve().parents[1]
    os.chdir(project_root)
    monkeypatch.chdir(repo)
    monkeypatch.setenv('PWD', os.fspath(repo))


def test_branch_cleanup_respects_remove_merged(tmp_path):
    from git_well.git_branch_cleanup import CleanDevBranchConfig

    repo = _init_repo(tmp_path / 'repo')
    _commit_file(repo, 'base.txt', 'base', 'base')
    _git(repo, 'checkout', '-b', 'feature')
    _commit_file(repo, 'feature.txt', 'feature', 'feature')
    _git(repo, 'checkout', 'main')
    _git(repo, 'merge', '--no-ff', 'feature', '-m', 'merge feature')

    CleanDevBranchConfig.main(
        argv=False,
        repo_dpath=os.fspath(repo),
        yes=True,
        remove_merged=False,
    )
    assert _git(repo, 'branch', '--list', 'feature').stdout.strip()

    CleanDevBranchConfig.main(
        argv=False,
        repo_dpath=os.fspath(repo),
        yes=True,
        remove_merged=True,
    )
    assert not _git(repo, 'branch', '--list', 'feature').stdout.strip()


def test_git_sync_dry_checks_out_before_pull(tmp_path, monkeypatch, capsys):
    from git_well.git_sync import git_sync

    repo = _init_repo(tmp_path / 'repo')
    _commit_file(repo, 'base.txt', 'base', 'base')
    _chdir_repo(monkeypatch, repo)

    git_sync(
        'remote.example',
        remote='origin',
        message='sync message',
        dry=True,
        home=tmp_path,
    )
    output = capsys.readouterr().out
    assert 'git add -A' in output
    assert "git commit -m 'sync message'" in output
    ssh_line = next(line for line in output.splitlines() if line.startswith('ssh '))
    assert ssh_line.index('git checkout main') < ssh_line.index('git pull origin main')


def test_git_sync_commits_untracked_and_propagates_hook_failure(
    tmp_path, monkeypatch
):
    from git_well.git_sync import _commit_local_changes

    repo = _init_repo(tmp_path / 'repo')
    _commit_file(repo, 'base.txt', 'base', 'base')
    _chdir_repo(monkeypatch, repo)

    (repo / 'untracked.txt').write_text('new')
    assert _commit_local_changes('include untracked') is True
    assert _git(repo, 'ls-files', 'untracked.txt').stdout.strip() == 'untracked.txt'

    hook = repo / '.git' / 'hooks' / 'pre-commit'
    hook.write_text('#!/bin/sh\nexit 1\n')
    hook.chmod(0o755)
    (repo / 'another.txt').write_text('another')
    with pytest.raises(RuntimeError, match='git commit'):
        _commit_local_changes('must fail')


def test_git_default_push_remote_name_is_unambiguous(tmp_path, monkeypatch):
    from git_well.git_sync import git_default_push_remote_name

    repo = _init_repo(tmp_path / 'repo')
    _chdir_repo(monkeypatch, repo)
    assert git_default_push_remote_name() is None
    _git(repo, 'remote', 'add', 'one', 'https://example.com/one.git')
    assert git_default_push_remote_name() == 'one'
    _git(repo, 'remote', 'add', 'two', 'https://example.com/two.git')
    assert git_default_push_remote_name() is None


def test_ipfs_sidecar_path_confinement(tmp_path):
    from git_well.ipfs import _tracked_path

    repo = _init_repo(tmp_path / 'repo')
    sidecar = repo / 'data' / 'payload.ipfs'
    sidecar.parent.mkdir()
    sidecar.write_text('type: ipfs-sidecar\ncid: fake\nrel_path: ../../outside\n')
    meta = {'cid': 'fake', 'rel_path': '../../outside'}

    with pytest.raises(ValueError, match='escapes its allowed root'):
        _tracked_path(sidecar, meta)
    assert _tracked_path(sidecar, meta, allow_external=True) == (
        tmp_path / 'outside'
    )


def test_ipfs_pull_exactly_replaces_existing_directory(tmp_path, monkeypatch):
    import git_well.ipfs as ipfs_mod

    out_path = tmp_path / 'payload'
    out_path.mkdir()
    (out_path / 'keep.txt').write_text('old')
    (out_path / 'stale.txt').write_text('stale')

    class FakeInfo:
        returncode = 0
        stdout = ''
        stderr = ''

    def fake_run(argv, **kwargs):
        output_arg = next(arg for arg in argv if arg.startswith('--output='))
        staged = Path(output_arg.split('=', 1)[1])
        staged.mkdir()
        (staged / 'keep.txt').write_text('new')
        return FakeInfo()

    monkeypatch.setattr(ipfs_mod, '_run', fake_run)
    ipfs_mod.sync_ipfs_pull('fake-cid', tmp_path, 'payload')

    assert (out_path / 'keep.txt').read_text() == 'new'
    assert not (out_path / 'stale.txt').exists()


def test_squash_inplace_restores_original_branch(tmp_path):
    from git_well.git_squash import GitSquashCLI

    repo = _init_repo(tmp_path / 'repo')
    _commit_file(repo, 'base.txt', 'base', 'base')
    _git(repo, 'checkout', '-b', 'feature')
    _commit_file(repo, 'one.txt', 'one', 'one')
    _commit_file(repo, 'two.txt', 'two', 'two')

    GitSquashCLI.main(
        argv=False,
        dpath=repo,
        oldest='main',
        newest='HEAD',
        dry=False,
        inplace=True,
    )

    assert _git(repo, 'branch', '--show-current').stdout.strip() == 'feature'
    assert _git(repo, 'rev-list', '--count', 'main..feature').stdout.strip() == '1'
    assert not _git(repo, 'branch', '--list', 'feature-squash-temp').stdout.strip()


def test_remote_protocol_only_updates_remote_keys(tmp_path):
    from git_well.git_remote_protocol import GitRemoteProtocol

    repo = _init_repo(tmp_path / 'repo')
    old_url = 'https://github.com/Org/repo.git'
    _git(repo, 'remote', 'add', 'origin', old_url)
    _git(repo, 'config', 'custom.note', old_url + '/docs')

    GitRemoteProtocol.main(
        argv=False,
        repo_dpath=os.fspath(repo),
        group='Org',
        protocol='git',
    )

    assert _git(repo, 'remote', 'get-url', 'origin').stdout.strip() == (
        'git@github.com:Org/repo.git'
    )
    assert _git(repo, 'config', '--get', 'custom.note').stdout.strip() == (
        old_url + '/docs'
    )


def test_git_url_common_forms():
    from git_well._utils import GitURL

    nested = GitURL('https://gitlab.com/group/subgroup/repo.git')
    assert nested.info['group'] == 'group/subgroup'
    assert nested.info['repo_name'] == 'repo'
    assert nested.to_git() == 'git@gitlab.com:group/subgroup/repo.git'

    scp = GitURL('alice@example.com:group/repo.git')
    assert scp.info['user'] == 'alice'
    assert scp.to_ssh() == 'ssh://alice@example.com/group/repo.git'

    local = GitURL('/home/user/repo.git')
    assert local.info['protocol'] == 'local'
    with pytest.raises(ValueError, match='Cannot convert local'):
        local.to_https()

    ssh_port = GitURL('ssh://git@example.com:2222/group/repo.git')
    assert ssh_port.to_ssh() == ssh_port
    assert ssh_port.to_git() == ssh_port


def test_rebase_status_handles_modify_delete_conflict(tmp_path):
    from git_well.git_rebase_add_continue import parsed_rebase_git_status

    repo = _init_repo(tmp_path / 'repo')
    _commit_file(repo, 'f.txt', 'base', 'base')
    _git(repo, 'checkout', '-b', 'topic')
    _commit_file(repo, 'f.txt', 'topic change', 'topic change')
    _git(repo, 'checkout', 'main')
    _git(repo, 'rm', 'f.txt')
    _git(repo, 'commit', '-m', 'delete on main')
    _git(repo, 'checkout', 'topic')
    result = _git(repo, 'rebase', 'main', check=False)
    assert result.returncode != 0

    paths = parsed_rebase_git_status(repo)
    assert repo / 'f.txt' in paths['both_modified']

    from git_well.git_rebase_add_continue import GitRebaseAddContinue

    GitRebaseAddContinue.main(
        argv=False, repo_dpath=os.fspath(repo), skip_editor=True
    )
    assert not _git(repo, 'status', '--porcelain').stdout.strip()
    assert (repo / 'f.txt').read_text() == 'topic change'


def test_discover_remote_relpath_error_is_not_unbound(
    tmp_path, monkeypatch
):
    from git_well.git_discover_remote import GitDiscoverRemoteCLI

    repo = _init_repo(tmp_path / 'repo')
    _commit_file(repo, 'base.txt', 'base', 'base')

    def fail_relpath(*args, **kwargs):
        raise ValueError('different drives')

    monkeypatch.setattr(os.path, 'relpath', fail_relpath)
    with pytest.raises(ValueError, match='root_dpath='):
        GitDiscoverRemoteCLI.main(
            argv=False,
            repo_dpath=os.fspath(repo),
            host='example.com',
            test_remote=False,
        )
