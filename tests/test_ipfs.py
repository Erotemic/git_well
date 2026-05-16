from pathlib import Path


def test_modal_registration_preserves_command_classes():
    from git_well.ipfs import IPFSAdd, IPFSDoctor, IPFSPull

    assert IPFSAdd is not None
    assert IPFSPull is not None
    assert IPFSDoctor is not None
    assert IPFSAdd.__command__ == 'add'
    assert IPFSPull.__command__ == 'pull'


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


def _install_fake_ipfs(tmp_path, monkeypatch):
    """Install a tiny fake Kubo executable for end-to-end CLI tests."""
    import os
    import textwrap

    bin_dpath = tmp_path / 'bin'
    bin_dpath.mkdir()
    log_fpath = tmp_path / 'fake-ipfs-calls.jsonl'
    fake_ipfs = bin_dpath / 'ipfs'
    fake_ipfs.write_text(textwrap.dedent(r'''
        #!/usr/bin/env python3
        import json
        import os
        import sys
        from pathlib import Path

        argv = sys.argv[1:]
        log_fpath = Path(os.environ['GIT_WELL_FAKE_IPFS_LOG'])
        with log_fpath.open('a') as file:
            file.write(json.dumps(argv) + '\n')

        def main():
            if not argv:
                return 0
            if argv[0] == 'version':
                print('ipfs version 0.37.0')
                return 0
            if argv[:2] == ['repo', 'stat']:
                print('NumObjects: 1')
                return 0
            if argv[:2] == ['swarm', 'peers']:
                print('/ip4/127.0.0.1/tcp/4001/p2p/12D3KooWfake')
                return 0
            if argv[:4] == ['pin', 'remote', 'service', 'ls']:
                print('test-service http://example.invalid')
                return 0
            if argv[0] == 'add':
                path = argv[-1]
                if '--only-hash' in argv or '-n' in argv:
                    print(f'added bafyfake {path}')
                else:
                    print(f'added bafyfake {path}/file.txt')
                    print(f'added bafyfake {path}')
                return 0
            if argv[:2] == ['dht', 'findpeer']:
                print('/ip4/127.0.0.1/tcp/4001')
                return 0
            if argv[:2] == ['swarm', 'connect']:
                print('connect ' + argv[2] + ' success')
                return 0
            if argv[0] == 'get':
                output = None
                cid = argv[-1]
                for item in argv[1:]:
                    if item.startswith('--output='):
                        output = Path(item.split('=', 1)[1])
                if output is None:
                    print('missing --output', file=sys.stderr)
                    return 2
                output.mkdir(parents=True, exist_ok=True)
                (output / 'file.txt').write_text('restored from ' + cid + '\n')
                print('saved ' + str(output))
                return 0
            print('unexpected fake ipfs invocation: ' + repr(argv), file=sys.stderr)
            return 2

        raise SystemExit(main())
    ''').lstrip())
    fake_ipfs.chmod(0o755)
    monkeypatch.setenv('GIT_WELL_FAKE_IPFS_LOG', os.fspath(log_fpath))
    monkeypatch.setenv('PATH', os.fspath(bin_dpath) + os.pathsep + os.environ.get('PATH', ''))
    return bin_dpath, log_fpath


def test_fake_ipfs_add_pull_integration(tmp_path, monkeypatch):
    import json
    import shutil
    import subprocess

    from git_well.ipfs import IPFSAdd, IPFSPull

    _bin_dpath, log_fpath = _install_fake_ipfs(tmp_path, monkeypatch)
    repo = tmp_path / 'repo'
    repo.mkdir()
    subprocess.run(['git', 'init'], cwd=repo, check=True, capture_output=True, text=True)

    data_dpath = repo / 'data'
    data_dpath.mkdir()
    (data_dpath / 'file.txt').write_text('original\n')

    IPFSAdd.main(
        cmdline=False,
        path=data_dpath,
        name='demo-data',
        suggested_peers=['/ip4/127.0.0.1/tcp/4001/p2p/12D3KooWfake'],
        git_add_sidecar=True,
    )

    sidecar_fpath = repo / 'data.ipfs'
    assert sidecar_fpath.exists()
    sidecar_text = sidecar_fpath.read_text()
    assert 'pin_name: demo-data' in sidecar_text
    assert 'suggested_peers:' in sidecar_text
    assert '/data' in (repo / '.gitignore').read_text().splitlines()

    status = subprocess.run(
        ['git', 'status', '--short'], cwd=repo, check=True,
        capture_output=True, text=True).stdout
    assert 'A  .gitignore' in status
    assert 'A  data.ipfs' in status
    assert 'data/' not in status

    shutil.rmtree(data_dpath)
    IPFSPull.main(cmdline=False, path=repo)
    assert (data_dpath / 'file.txt').read_text() == 'restored from bafyfake\n'

    calls = [json.loads(line) for line in log_fpath.read_text().splitlines()]
    assert any(call and call[0] == 'add' for call in calls)
    assert any(call[:2] == ['swarm', 'connect'] for call in calls)
    assert any(call and call[0] == 'get' for call in calls)


def test_command_failure_message_for_missing_retrieval():
    from types import SimpleNamespace

    from git_well.ipfs import _command_failure_message

    info = SimpleNamespace(returncode=1, stdout='', stderr='routing: not found')
    msg = _command_failure_message(['ipfs', 'get', '--output=/tmp/out', 'bafyfake'], info)
    assert 'Could not retrieve the requested CID' in msg
    assert 'git ipfs peers --connect' in msg


def test_run_missing_ipfs_has_actionable_error(monkeypatch):
    import pytest

    from git_well.ipfs import GitIPFSError, _run

    monkeypatch.setenv('PATH', '')
    with pytest.raises(GitIPFSError, match='Could not find the `ipfs` executable'):
        _run(['ipfs', 'version'], verbose=0)
