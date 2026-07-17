import os
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
    assert stat is not None
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

    named_argv = _build_add_argv({
        'path': Path('foo bar'),
        'pin': True,
        'progress': True,
        'recursive': True,
        'only_hash': False,
        'raw_leaves': False,
        'cid_version': 1,
    }, pin_name='pkg:generic/repo#foo%20bar')
    assert named_argv == [
        'ipfs', 'add', '--pin', '--progress', '--recursive',
        '--pin-name=pkg:generic/repo#foo%20bar',
        '--raw-leaves=false', '--cid-version=1', 'foo bar'
    ]


def test_build_pin_add_argv():
    from git_well.ipfs import _build_pin_add_argv

    argv = _build_pin_add_argv(
        'bafyfakecid', pin_name='pkg:generic/repo#foo bar')

    assert argv == [
        'ipfs', 'pin', 'add', '--name=pkg:generic/repo#foo bar',
        '--progress', '--recursive', 'bafyfakecid'
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


def test_sidecar_pin_name_prefers_stored_effective_name(tmp_path):
    from git_well.ipfs import _sidecar_pin_name

    sidecar = tmp_path / 'data.ipfs'
    meta = {
        'cid': 'bafy',
        'rel_path': 'data',
        'pin_name': 'stored-effective-name',
        'pin_name_source': 'generated',
        'add_config': {'name': 'user-requested-name'},
    }
    assert _sidecar_pin_name(sidecar, meta) == 'stored-effective-name'
    assert _sidecar_pin_name(
        sidecar, meta, override_name='override') == 'override'

    old_meta = {
        'cid': 'bafy',
        'rel_path': 'data',
        'add_config': {'name': 'user-requested-name'},
    }
    assert _sidecar_pin_name(sidecar, old_meta) == 'user-requested-name'


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


def test_normalize_ipfs_pin_name_shortens_to_kubo_limit():
    import re

    from git_well.ipfs import KUBO_PIN_NAME_MAX_BYTES, _normalize_ipfs_pin_name

    long_name = 'pkg:generic/repo#' + '/'.join(['longcomponent'] * 40)
    short_name, shortened = _normalize_ipfs_pin_name(long_name)

    assert shortened is True
    assert len(short_name.encode('utf8')) <= KUBO_PIN_NAME_MAX_BYTES
    assert re.search(r'~gw-[0-9a-f]{16}$', short_name)
    assert short_name == _normalize_ipfs_pin_name(long_name)[0]


def test_normalize_ipfs_pin_name_handles_unicode_boundaries():
    from git_well.ipfs import KUBO_PIN_NAME_MAX_BYTES, _normalize_ipfs_pin_name

    long_name = 'pkg:generic/repo#' + ('é' * 200)
    short_name, shortened = _normalize_ipfs_pin_name(long_name)

    assert shortened is True
    assert len(short_name.encode('utf8')) <= KUBO_PIN_NAME_MAX_BYTES
    short_name.encode('utf8').decode('utf8')


def test_normalize_ipfs_pin_name_rejects_control_characters():
    import pytest

    from git_well.ipfs import _normalize_ipfs_pin_name

    with pytest.raises(ValueError, match='newline'):
        _normalize_ipfs_pin_name('bad\nname')


def test_generated_ipfs_pin_name_falls_back_without_origin(tmp_path):
    import ubelt as ub

    from git_well.ipfs import _generated_ipfs_pin_name

    repo = tmp_path / 'repo without origin'
    repo.mkdir()
    ub.cmd(['git', 'init'], cwd=repo, check=True)
    data_fpath = repo / 'data' / 'foo bar.txt'
    data_fpath.parent.mkdir()
    data_fpath.write_text('payload')

    assert _generated_ipfs_pin_name(data_fpath) == (
        'pkg:generic/repo%20without%20origin#data/foo%20bar.txt')


def test_generated_ipfs_pin_name_falls_back_for_local_origin(tmp_path):
    import ubelt as ub

    from git_well.ipfs import _generated_ipfs_pin_name

    repo = tmp_path / 'worktree'
    repo.mkdir()
    ub.cmd(['git', 'init'], cwd=repo, check=True)
    local_origin = tmp_path / 'local project.git'
    ub.cmd(['git', 'remote', 'add', 'origin', os.fspath(local_origin)], cwd=repo, check=True)
    data_fpath = repo / 'data' / 'payload.txt'
    data_fpath.parent.mkdir()
    data_fpath.write_text('payload')

    assert _generated_ipfs_pin_name(data_fpath) == (
        'pkg:generic/local%20project#data/payload.txt')


def test_ipfs_add_dry_run_uses_generated_name_without_origin(tmp_path, capsys):
    import ubelt as ub

    from git_well.ipfs import IPFSCLI

    repo = tmp_path / 'repo'
    repo.mkdir()
    ub.cmd(['git', 'init'], cwd=repo, check=True)
    fpath = repo / 'data.txt'
    fpath.write_text('payload')

    IPFSAdd = next(item['cls'] for item in IPFSCLI.__subconfigs__
                   if item['cls'].__command__ == 'add')
    IPFSAdd.main(cmdline=0, path=fpath, dry_run=True)
    captured = capsys.readouterr().out

    assert "'--pin-name=pkg:generic/repo#data.txt'" in captured
    assert 'ipfs pin add --name' not in captured


def test_ipfs_add_writes_effective_pin_name_to_sidecar(tmp_path, monkeypatch, capsys):
    import ubelt as ub

    import git_well.ipfs as ipfs_mod
    from git_well.ipfs import IPFSCLI

    repo = tmp_path / 'repo'
    repo.mkdir()
    ub.cmd(['git', 'init'], cwd=repo, check=True)
    fpath = repo / 'data.txt'
    fpath.write_text('payload')

    calls = []

    class FakeInfo:
        stdout = 'added bafyfakecid data.txt\n'
        stderr = ' 7 B / 7 B [================] 100.00%\n'

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return FakeInfo()

    monkeypatch.setattr(ipfs_mod, '_run', fake_run)

    IPFSAdd = next(item['cls'] for item in IPFSCLI.__subconfigs__
                   if item['cls'].__command__ == 'add')
    IPFSAdd.main(
        cmdline=0,
        path=fpath,
        update_gitignore=False,
        git_add_sidecar=False,
    )

    assert len(calls) == 1
    assert '--pin-name=pkg:generic/repo#data.txt' in calls[0]
    captured = capsys.readouterr().out
    assert 'Pin on another machine with:' in captured
    assert "ipfs pin add '--name=pkg:generic/repo#data.txt' --progress --recursive bafyfakecid" in captured
    sidecar = ipfs_mod._YamlCodec.load(fpath.with_name('data.txt.ipfs'))
    assert sidecar['pin_name'] == 'pkg:generic/repo#data.txt'
    assert sidecar['pin_name_source'] == 'generated'
    assert sidecar['add_config']['name'] is None


def test_ipfs_export_uses_generated_name_without_origin(tmp_path, capsys):
    import ubelt as ub

    from git_well.ipfs import IPFSCLI

    repo = tmp_path / 'repo'
    repo.mkdir()
    ub.cmd(['git', 'init'], cwd=repo, check=True)
    (repo / 'data.txt').write_text('payload')
    (repo / 'data.txt.ipfs').write_text(
        'type: ipfs-sidecar\n'
        'cid: bafyfakecid\n'
        'rel_path: data.txt\n'
    )

    IPFSExportPins = next(item['cls'] for item in IPFSCLI.__subconfigs__
                          if item['cls'].__command__ == 'export')
    IPFSExportPins.main(cmdline=0, paths=[repo])
    captured = capsys.readouterr().out

    assert '--name=pkg:generic/repo#data.txt' in captured
