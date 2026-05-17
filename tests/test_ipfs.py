from pathlib import Path


def test_argv_to_str_quotes_paths():
    from git_well.ipfs import argv_to_str
    assert argv_to_str(['ipfs', 'pin', 'add', 'bafy foo']) == "ipfs pin add 'bafy foo'"


def test_find_sidecars(tmp_path):
    from git_well.ipfs import _find_sidecars
    (tmp_path / 'a.ipfs').write_text('type: ipfs-sidecar\ncid: cid\nrel_path: a\n')
    sub = tmp_path / 'sub'
    sub.mkdir()
    (sub / 'b.ipfs').write_text('type: ipfs-sidecar\ncid: cid\nrel_path: b\n')
    got = _find_sidecars(tmp_path)
    assert [p.name for p in got] == ['a.ipfs', 'b.ipfs']


def test_quickstat_file(tmp_path):
    from git_well.ipfs import _compute_quickstat
    fpath = tmp_path / 'data.txt'
    fpath.write_text('data')
    stat = _compute_quickstat(fpath)
    assert stat['kind'] == 'file'
    assert stat['bytes'] == 4


def test_build_add_argv():
    from git_well.ipfs import _build_add_argv
    argv = _build_add_argv({
        'path': Path('foo bar'),
        'pin': True,
        'progress': True,
        'recursive': True,
        'only_hash': False,
        'raw_leaves': False,
        'cid_version': 1,
    })
    assert argv == [
        'ipfs', 'add', '--pin', '--progress', '--recursive',
        '--raw-leaves=false', '--cid-version=1', 'foo bar'
    ]


def test_git_origin_url_to_purl_base_common_remotes():
    from git_well.ipfs import _git_origin_url_to_purl_base
    assert _git_origin_url_to_purl_base(
        'git@github.com:Erotemic/git_well.git'
    ) == 'pkg:github/Erotemic/git_well'
    assert _git_origin_url_to_purl_base(
        'https://github.com/Erotemic/git_well.git'
    ) == 'pkg:github/Erotemic/git_well'
    assert _git_origin_url_to_purl_base(
        'ssh://git@gitlab.com/group/subgroup/repo.git'
    ) == 'pkg:gitlab/group/subgroup/repo'
    assert _git_origin_url_to_purl_base(
        'https://token@example.com/org/repo.git'
    ) == 'pkg:generic/example.com/org/repo'
    assert _git_origin_url_to_purl_base('/home/user/repo.git') is None


def test_generated_ipfs_pin_name_uses_current_repo_relative_path(tmp_path):
    import ubelt as ub
    from git_well.ipfs import _generated_ipfs_pin_name

    repo = tmp_path / 'repo'
    repo.mkdir()
    ub.cmd(['git', 'init'], cwd=repo, check=True)
    ub.cmd(
        ['git', 'remote', 'add', 'origin',
         'git@github.com:Erotemic/git_well.git'],
        cwd=repo, check=True)
    data_fpath = repo / 'data' / 'foo bar.txt'
    data_fpath.parent.mkdir()
    data_fpath.write_text('payload')

    assert _generated_ipfs_pin_name(data_fpath) == (
        'pkg:github/Erotemic/git_well#data/foo%20bar.txt')

    moved_fpath = repo / 'moved' / 'foo bar.txt'
    moved_fpath.parent.mkdir()
    data_fpath.rename(moved_fpath)
    assert _generated_ipfs_pin_name(moved_fpath) == (
        'pkg:github/Erotemic/git_well#moved/foo%20bar.txt')


def test_sidecar_pin_name_prefers_explicit_name(tmp_path):
    from git_well.ipfs import _sidecar_pin_name

    sidecar = tmp_path / 'data.ipfs'
    meta = {
        'cid': 'bafy',
        'rel_path': 'data',
        'add_config': {'name': 'user-requested-name'},
    }
    assert _sidecar_pin_name(sidecar, meta) == 'user-requested-name'
    assert _sidecar_pin_name(
        sidecar, meta, override_name='override') == 'override'


def test_ipfs_add_name_requires_pin(tmp_path):
    import pytest
    from git_well.ipfs import IPFSCLI

    IPFSAdd = next(item['cls'] for item in IPFSCLI.__subconfigs__
                   if item['cls'].__command__ == 'add')
    fpath = tmp_path / 'data.txt'
    fpath.write_text('payload')
    with pytest.raises(ValueError, match='--name requires --pin'):
        IPFSAdd.main(
            cmdline=0, path=fpath, name='explicit-name',
            pin=False, dry_run=True)
