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


def test_gitignore_pattern_is_anchored_and_escaped():
    from git_well.ipfs import _gitignore_pattern_for
    assert _gitignore_pattern_for('data') == '/data'
    assert _gitignore_pattern_for('big data/#1') == '/big\\ data/\\#1'


def test_gitignore_pattern_rejects_parent_paths():
    import pytest
    from git_well.ipfs import _gitignore_pattern_for
    with pytest.raises(ValueError):
        _gitignore_pattern_for('../data')


def test_tracked_path_rejects_parent_escape(tmp_path):
    import pytest
    from git_well.ipfs import _tracked_path
    sidecar = tmp_path / 'data.ipfs'
    sidecar.write_text('')
    with pytest.raises(ValueError):
        _tracked_path(sidecar, {'rel_path': '../outside', 'cid': 'bafy'})


def test_sidecar_metadata_is_stable(tmp_path):
    from git_well.ipfs import _sidecar_metadata
    fpath = tmp_path / 'data.txt'
    fpath.write_text('data')
    cfg = {
        'recursive': True,
        'cid_version': 1,
        'raw_leaves': False,
        'progress': True,
        'dry_run': False,
        'git_add_sidecar': True,
        'update_gitignore': True,
        'name': 'demo-data',
        'path': fpath,
    }
    meta = _sidecar_metadata(
        cid='bafy-demo', rel_path='data.txt', path=fpath, config=cfg, num_items=1
    )
    assert meta == {
        'schema_version': 1,
        'type': 'ipfs-sidecar',
        'cid': 'bafy-demo',
        'rel_path': 'data.txt',
        'kind': 'file',
        'import': {
            'recursive': True,
            'cid_version': 1,
            'raw_leaves': False,
        },
        'size_bytes': 4,
        'num_items': 1,
        'pin_name': 'demo-data',
    }


def test_sidecar_metadata_records_suggested_peers(tmp_path):
    from git_well.ipfs import _sidecar_metadata
    fpath = tmp_path / 'data.txt'
    fpath.write_text('data')
    meta = _sidecar_metadata(
        cid='bafy-demo',
        rel_path='data.txt',
        path=fpath,
        config={
            'recursive': True,
            'cid_version': 1,
            'raw_leaves': False,
            'suggested_peers': [
                '12D3KooWexample',
                {'multiaddr': '/ip4/127.0.0.1/tcp/4001/p2p/12D3KooWexample'},
                '12D3KooWexample',
            ],
        },
        num_items=1,
    )
    assert meta['suggested_peers'] == [
        '12D3KooWexample',
        '/ip4/127.0.0.1/tcp/4001/p2p/12D3KooWexample',
    ]


def test_sidecar_suggested_peers_accepts_aliases():
    from git_well.ipfs import _sidecar_suggested_peers
    assert _sidecar_suggested_peers({'peers': ['peer-a']}) == ['peer-a']
    assert _sidecar_suggested_peers({'suggested_peers': [{'id': 'peer-b'}]}) == ['peer-b']


def test_parse_kubo_version_text():
    from git_well.ipfs import _parse_kubo_version_text, _version_gte
    assert _parse_kubo_version_text('ipfs version 0.37.0') == (0, 37, 0)
    assert _parse_kubo_version_text('kubo version v0.38.1') == (0, 38, 1)
    assert _version_gte((0, 37, 0), (0, 37, 0))
    assert not _version_gte((0, 36, 0), (0, 37, 0))


def test_connect_to_multiaddr_hint_dry_run(capsys):
    from git_well.ipfs import _connect_to_peer_hint
    ok = _connect_to_peer_hint(
        '/ip4/127.0.0.1/tcp/4001/p2p/12D3KooWexample',
        dry_run=True,
    )
    captured = capsys.readouterr()
    assert ok
    assert 'ipfs swarm connect /ip4/127.0.0.1/tcp/4001/p2p/12D3KooWexample' in captured.out


def test_parse_ipfs_add_root_cid_quiet_mode():
    from git_well.ipfs import _parse_ipfs_add_root_cid
    assert _parse_ipfs_add_root_cid('bafyquiet\n') == 'bafyquiet'
