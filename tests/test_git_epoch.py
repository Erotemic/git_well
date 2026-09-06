from __future__ import annotations

import pathlib
import subprocess

import pytest
import yaml

from git_well.epoch import (
    EpochError,
    EpochPlanStaleError,
    EpochSafetyError,
    abort_plan,
    apply_sandbox,
    apply_plan,
    build_plan,
    compact_active,
    configure_submodule,
    create_sandbox,
    gc_history_store,
    history_store_stats,
    initialize_config,
    inspect_manifest,
    load_config,
    plan_sandbox,
    publish_plan,
    publish_sandbox,
    reconstruct,
    run_sandbox,
    sandbox_stats,
    status,
    sync_history_views,
    verify_sandbox,
)


def _run(args, cwd=None, check=True):
    proc = subprocess.run(
        [str(a) for a in args],
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    if check and proc.returncode:
        raise AssertionError(
            f'command failed: {args}\nstdout={proc.stdout}\nstderr={proc.stderr}'
        )
    return proc


def _git(repo, *args, check=True):
    return _run(['git', *args], cwd=repo, check=check)


def _init_repo(path: pathlib.Path, remote: pathlib.Path | None = None):
    path.mkdir(parents=True)
    _git(path, 'init', '-b', 'main')
    _git(path, 'config', 'user.name', 'Epoch Tester')
    _git(path, 'config', 'user.email', 'epoch@example.com')
    if remote is not None:
        _run(['git', 'init', '--bare', remote])
        _run(['git', '--git-dir', remote, 'symbolic-ref', 'HEAD', 'refs/heads/main'])
        _git(path, 'remote', 'add', 'origin', remote)
    return path


def _commit(repo: pathlib.Path, name: str, text: str | None = None):
    if text is None:
        text = name + '\n'
    fpath = repo / 'tracked.txt'
    fpath.write_text(text)
    _git(repo, 'add', 'tracked.txt')
    _git(repo, 'commit', '-m', name)
    return _git(repo, 'rev-parse', 'HEAD').stdout.strip()


def _push(repo: pathlib.Path):
    _git(repo, 'push', '-u', 'origin', 'main')


def _tree(repo: pathlib.Path, commit: str):
    return _git(repo, 'rev-parse', f'{commit}^{{tree}}').stdout.strip()


def _manifest(repo: pathlib.Path):
    return inspect_manifest(repo)['manifest']


def test_sandbox_single_repo_rehearsal_is_contained(tmp_path):
    source_remote = tmp_path / 'source-remote.git'
    source = _init_repo(tmp_path / 'source', source_remote)
    _commit(source, 'A')
    source_head_before = _commit(source, 'B')
    _push(source)
    source_remote_before = _run(
        [
            'git', '--git-dir', source_remote,
            'rev-parse', 'refs/heads/main',
        ]
    ).stdout.strip()

    sandbox_dpath = tmp_path / 'sandbox'
    created = create_sandbox(source, output=sandbox_dpath)
    sandbox_repo = pathlib.Path(created['root_repo'])
    sandbox_origin = pathlib.Path(
        _git(sandbox_repo, 'remote', 'get-url', 'origin').stdout.strip()
    ).resolve()
    sandbox_origin.relative_to(sandbox_dpath.resolve())
    assert _git(source, 'remote', 'get-url', 'origin').stdout.strip() == str(
        source_remote
    )

    planned = plan_sandbox(sandbox_dpath, bundle=False)
    assert planned['status'] == 'planned'
    prepared = apply_sandbox(sandbox_dpath)
    assert prepared['status'] == 'prepared'
    published = publish_sandbox(sandbox_dpath)
    assert published['status'] == 'published'
    verified = verify_sandbox(sandbox_dpath)
    assert verified['status'] == 'verified'
    assert all(
        item['retired_tip_present'] is False
        for item in verified['repositories'].values()
    )
    assert _run(
        [
            'git', '--git-dir', source_remote,
            'rev-parse', 'refs/heads/main',
        ]
    ).stdout.strip() == source_remote_before
    assert _git(source, 'rev-parse', 'HEAD').stdout.strip() == source_head_before


def test_sandbox_recursive_rehearsal_translates_nested_gitlinks(tmp_path):
    leaf_remote = tmp_path / 'leaf-source.git'
    leaf_seed = _init_repo(tmp_path / 'leaf-seed', leaf_remote)
    _commit(leaf_seed, 'leaf-A')
    _push(leaf_seed)

    middle_remote = tmp_path / 'middle-source.git'
    middle_seed = _init_repo(tmp_path / 'middle-seed', middle_remote)
    _run(
        [
            'git', '-c', 'protocol.file.allow=always',
            'submodule', 'add', leaf_remote, 'leaf',
        ],
        cwd=middle_seed,
    )
    _git(middle_seed, 'commit', '-am', 'middle-A')
    _push(middle_seed)

    root_remote = tmp_path / 'root-source.git'
    root = _init_repo(tmp_path / 'root-source', root_remote)
    _run(
        [
            'git', '-c', 'protocol.file.allow=always',
            'submodule', 'add', middle_remote, 'middle',
        ],
        cwd=root,
    )
    _git(root, 'commit', '-am', 'root-A')
    _push(root)
    _git(
        root,
        '-c', 'protocol.file.allow=always',
        'submodule', 'update', '--init', '--recursive',
    )

    sandbox_dpath = tmp_path / 'recursive-sandbox'
    created = create_sandbox(
        root,
        output=sandbox_dpath,
        recursive=True,
        all_submodules='epoch',
    )
    assert len(created['repositories']) == 3
    result = run_sandbox(sandbox_dpath, bundle=False)
    assert result['published']['status'] == 'published'
    verification = result['verification']
    assert len(verification['repositories']) == 3
    recursive = verification['recursive_fresh_clone']
    assert recursive['repositories_initialized'] == 3
    assert recursive['clean'] is True
    recursive_root = pathlib.Path(recursive['path'])
    assert _git(recursive_root / 'middle', 'rev-parse', 'HEAD').returncode == 0
    assert _git(
        recursive_root / 'middle' / 'leaf', 'rev-parse', 'HEAD'
    ).returncode == 0
    first_verification_run = pathlib.Path(
        result['verification']['verification_run']
    )

    rerun = run_sandbox(sandbox_dpath, bundle=False)
    assert rerun['planned']['status'] == 'skipped'
    assert rerun['prepared']['status'] == 'skipped'
    assert rerun['published']['status'] == 'skipped'
    assert rerun['verification']['status'] == 'verified'
    second_verification_run = pathlib.Path(
        rerun['verification']['verification_run']
    )
    assert second_verification_run != first_verification_run
    assert first_verification_run.exists()
    assert second_verification_run.exists()


def test_sandbox_reports_gitlink_mismatch_before_generic_dirty_error(tmp_path):
    child_remote = tmp_path / 'child-source.git'
    child_seed = _init_repo(tmp_path / 'child-seed', child_remote)
    child_tip = _commit(child_seed, 'child-A')
    _push(child_seed)

    root = _init_repo(tmp_path / 'root-source')
    _run(
        [
            'git', '-c', 'protocol.file.allow=always',
            'submodule', 'add', child_remote, 'child',
        ],
        cwd=root,
    )
    _git(root, 'commit', '-am', 'root-A')

    unavailable = '1' * 40
    assert unavailable != child_tip
    _git(root, 'update-index', '--cacheinfo', f'160000,{unavailable},child')
    _git(root, 'commit', '-m', 'point at unavailable child')

    with pytest.raises(EpochSafetyError) as exc_info:
        create_sandbox(
            root,
            output=tmp_path / 'sandbox',
            recursive=True,
            all_submodules='epoch',
        )

    message = str(exc_info.value)
    assert 'Cannot create recursive epoch sandbox.' in message
    assert 'Submodule checkout does not match parent gitlink:' in message
    assert f'expected gitlink:           {unavailable}' in message
    assert f'checked-out HEAD:           {child_tip}' in message
    assert 'expected commit available:  no' in message
    assert 'Repository must be clean before epoch planning/apply' not in message


def test_sandbox_reports_dirty_child_at_child_repository(tmp_path):
    child_remote = tmp_path / 'child-source.git'
    child_seed = _init_repo(tmp_path / 'child-seed', child_remote)
    _commit(child_seed, 'child-A')
    _push(child_seed)

    root = _init_repo(tmp_path / 'root-source')
    _run(
        [
            'git', '-c', 'protocol.file.allow=always',
            'submodule', 'add', child_remote, 'child',
        ],
        cwd=root,
    )
    _git(root, 'commit', '-am', 'root-A')

    child_repo = root / 'child'
    (child_repo / 'tracked.txt').write_text('dirty child\n')

    with pytest.raises(EpochSafetyError) as exc_info:
        create_sandbox(
            root,
            output=tmp_path / 'sandbox',
            recursive=True,
            all_submodules='epoch',
        )

    message = str(exc_info.value)
    assert (
        f'Repository must be clean before epoch planning/apply: {child_repo}'
        in message
    )
    assert ' M tracked.txt' in message
    assert ' m child' not in message


def test_sandbox_reports_uninitialized_submodule_worktree(tmp_path):
    child_remote = tmp_path / 'child-source.git'
    child_seed = _init_repo(tmp_path / 'child-seed', child_remote)
    _commit(child_seed, 'child-A')
    _push(child_seed)

    root = _init_repo(tmp_path / 'root-source')
    _run(
        [
            'git', '-c', 'protocol.file.allow=always',
            'submodule', 'add', child_remote, 'child',
        ],
        cwd=root,
    )
    _git(root, 'commit', '-am', 'root-A')
    _git(root, 'submodule', 'deinit', '-f', 'child')

    with pytest.raises(EpochSafetyError) as exc_info:
        create_sandbox(
            root,
            output=tmp_path / 'sandbox',
            recursive=True,
            all_submodules='epoch',
        )

    message = str(exc_info.value)
    assert 'Cannot create recursive epoch sandbox.' in message
    assert 'Submodule worktree is not initialized:' in message
    assert 'path:     child' in message


def test_sandbox_defaults_unconfigured_submodules_to_external(tmp_path):
    child_remote = tmp_path / 'child-source.git'
    child_seed = _init_repo(tmp_path / 'child-seed', child_remote)
    child_tip = _commit(child_seed, 'child-A')
    _push(child_seed)

    root_remote = tmp_path / 'root-source.git'
    root = _init_repo(tmp_path / 'root-source', root_remote)
    _run(
        [
            'git', '-c', 'protocol.file.allow=always',
            'submodule', 'add', child_remote, 'child',
        ],
        cwd=root,
    )
    _git(root, 'commit', '-am', 'root-A')
    _push(root)

    sandbox_dpath = tmp_path / 'sandbox'
    created = create_sandbox(root, output=sandbox_dpath, recursive=True)
    assert [item['policy'] for item in created['repositories']] == [
        'epoch',
        'external',
    ]
    result = run_sandbox(sandbox_dpath, bundle=False)
    assert list(result['verification']['repositories']) == ['root-source']
    recursive = result['verification']['recursive_fresh_clone']
    assert recursive['repositories_initialized'] == 2
    recursive_child = pathlib.Path(recursive['path']) / 'child'
    assert _git(recursive_child, 'rev-parse', 'HEAD').stdout.strip() == child_tip
    sandbox_root = pathlib.Path(created['root_repo'])
    successor = _git(sandbox_root, 'rev-parse', 'HEAD').stdout.strip()
    successor_gitlink = _git(
        sandbox_root, 'ls-tree', successor, 'child'
    ).stdout.split()[2]
    assert successor_gitlink == child_tip


def test_sandbox_refuses_publication_if_origin_escapes(tmp_path):
    source_remote = tmp_path / 'source-remote.git'
    source = _init_repo(tmp_path / 'source', source_remote)
    _commit(source, 'A')
    _push(source)
    source_remote_before = _run(
        [
            'git', '--git-dir', source_remote,
            'rev-parse', 'refs/heads/main',
        ]
    ).stdout.strip()

    sandbox_dpath = tmp_path / 'sandbox'
    created = create_sandbox(source, output=sandbox_dpath)
    sandbox_repo = pathlib.Path(created['root_repo'])
    _git(sandbox_repo, 'remote', 'set-url', 'origin', source_remote)

    with pytest.raises(EpochSafetyError, match='containment check failed'):
        run_sandbox(sandbox_dpath, bundle=False)
    assert _run(
        [
            'git', '--git-dir', source_remote,
            'rev-parse', 'refs/heads/main',
        ]
    ).stdout.strip() == source_remote_before


def test_sandbox_rejects_tampered_manifest_root(tmp_path):
    source = _init_repo(tmp_path / 'source')
    _commit(source, 'A')
    sandbox_dpath = tmp_path / 'sandbox'
    create_sandbox(source, output=sandbox_dpath)
    manifest = sandbox_dpath / 'sandbox.yaml'
    text = manifest.read_text()
    text = text.replace(
        f'sandbox_root: {sandbox_dpath}',
        f'sandbox_root: {tmp_path}',
        1,
    )
    manifest.write_text(text)
    with pytest.raises(EpochSafetyError, match='manifest root does not match'):
        run_sandbox(sandbox_dpath, bundle=False)


def test_epoch_stats_report_store_epoch_bundle_and_sandbox_sizes(
    tmp_path, monkeypatch
):
    source_remote = tmp_path / 'source-remote.git'
    source = _init_repo(tmp_path / 'source', source_remote)
    for index in range(12):
        _commit(source, f'commit-{index}', ('payload-' + str(index)) * 100 + '\n')
    _push(source)

    sandbox_dpath = tmp_path / 'sandbox'
    created = create_sandbox(source, output=sandbox_dpath)
    plan_sandbox(sandbox_dpath, bundle=True)
    apply_sandbox(sandbox_dpath)
    publish_sandbox(sandbox_dpath)
    verified = verify_sandbox(sandbox_dpath)
    assert verified['recursive_fresh_clone']['repositories_initialized'] == 1

    sandbox_repo = pathlib.Path(created['root_repo'])
    stats = history_store_stats(sandbox_repo)
    assert stats['history_store']['directory_bytes'] > 0
    assert stats['totals']['archived_epochs'] == 1
    epoch = stats['repositories']['source'][0]
    assert epoch['number'] == 0
    assert epoch['objects'] > 0
    assert epoch['reachable_object_disk_bytes'] > 0
    assert epoch['exclusive_object_disk_bytes'] > 0
    assert epoch['standalone_bundle']['bytes'] > 0

    sandbox_report = sandbox_stats(sandbox_dpath)
    assert sandbox_report['history']['totals']['archived_epochs'] == 1
    assert sandbox_report['bundles']['bytes'] > 0
    assert sandbox_report['recursive_fresh_clone']['clean'] is True

    from git_well import git_archive_source

    def fake_archive_source(*, output, **kwargs):
        output = pathlib.Path(output)
        output.write_bytes(b'x' * 4096)
        return output

    monkeypatch.setattr(git_archive_source, 'archive_source', fake_archive_source)
    package_report = sandbox_stats(sandbox_dpath, source_archive=True)
    assert package_report['source_archive']['bytes'] == 4096
    assert pathlib.Path(package_report['source_archive']['path']).exists()

    gc_result = gc_history_store(sandbox_repo)
    assert gc_result['before'] > 0
    assert gc_result['after'] > 0
    assert 'disk_reduction_percent' in gc_result
    after_gc = history_store_stats(sandbox_repo)
    assert after_gc['totals']['archived_epochs'] == 1


def test_status_before_first_archive_and_direct_cli_exit_code(
    tmp_path, monkeypatch, capsys
):
    repo = _init_repo(tmp_path / 'work')
    _commit(repo, 'A')
    history = tmp_path / 'history.git'
    initialize_config(repo, repository_id='status-demo', history_store=history)

    info = status(repo)
    assert info['archive_verification'] == 'not-initialized'
    assert not history.exists()

    from git_well import git_epoch

    monkeypatch.chdir(repo)
    assert git_epoch.main(['status']) == 0
    captured = capsys.readouterr()
    assert 'archive_verification: not-initialized' in captured.out
    assert "{'repository':" not in captured.out


def test_basic_checkpoint_publish_reconstruct_and_repeat(tmp_path):
    remote = tmp_path / 'active.git'
    repo = _init_repo(tmp_path / 'work', remote)
    first = _commit(repo, 'A', 'alpha\n')
    _commit(repo, 'B', 'beta\n')
    old_tip = _commit(repo, 'C', 'gamma\n')
    _git(repo, 'tag', '-a', 'v0', '-m', 'v0', first)
    _push(repo)
    _git(repo, 'push', 'origin', 'v0')

    history = tmp_path / 'history.git'
    initialize_config(repo, repository_id='demo', history_store=history)
    plan = build_plan(repo, bundle=True)
    entry = plan['repositories'][0]
    assert entry['old_tip'] == old_tip
    assert entry['old_tree'] == entry['new_tree']

    prepared = apply_plan(plan, publish=False)
    assert prepared['status'] == 'prepared'
    prepared_manifest = _manifest(repo)
    prepared_epoch = prepared_manifest['epochs']['demo'][0]
    assert prepared_epoch['state'] == 'prepared'
    assert prepared_epoch['verification']['fsck'] == 'passed'
    assert len(prepared_epoch['verification']['bundle']['sha256']) == 64
    assert prepared_manifest['boundaries'][0]['state'] == 'prepared'
    assert _git(repo, 'rev-parse', 'main').stdout.strip() == old_tip
    assert (tmp_path / 'history.git.bundles' / 'demo' / 'epoch-000.bundle').exists()

    published = publish_plan(plan)
    assert published['status'] == 'published'
    new_root = entry['successor_root']
    assert _git(repo, 'rev-parse', 'main').stdout.strip() == new_root
    assert _git(repo, 'rev-list', '--parents', '-n', '1', new_root).stdout.split() == [
        new_root
    ]
    assert _tree(repo, old_tip) == _tree(repo, new_root)
    assert _git(repo, 'show-ref', '--tags', check=False).stdout.strip() == ''

    manifest = _manifest(repo)
    assert manifest['epochs']['demo'][0]['main_tip'] == old_tip
    assert manifest['epochs']['demo'][0]['state'] == 'committed'
    assert manifest['boundaries'][0]['successor']['commit'] == new_root
    assert manifest['boundaries'][0]['state'] == 'committed'

    fresh = tmp_path / 'fresh'
    _run(['git', 'clone', '--no-local', '--branch', 'main', remote, fresh])
    assert _git(fresh, 'cat-file', '-e', old_tip, check=False).returncode != 0
    with pytest.raises(EpochError, match='already epoch-managed'):
        load_config(fresh)
    bootstrapped = initialize_config(fresh, history_store=history)
    assert bootstrapped['repository'] == 'demo'
    assert bootstrapped['active_epoch'] == 1
    root_message = _git(repo, 'show', '-s', '--format=%B', new_root).stdout
    assert str(history.resolve()) not in root_message
    assert 'Git-Epoch-History-Store: history' in root_message

    reconstructed = tmp_path / 'reconstructed'
    result = reconstruct(repo, output=reconstructed)
    assert result['replacements'][0]['root'] == new_root
    subjects = _git(reconstructed, 'log', '--format=%s', 'main').stdout.splitlines()
    assert subjects[:4] == ['Start git epoch 1', 'C', 'B', 'A']

    # Repeat from epoch one.
    _commit(repo, 'D', 'delta\n')
    _push(repo)
    plan2 = build_plan(repo)
    apply_plan(plan2, publish=True)
    assert status(repo)['active_epoch'] == 2
    manifest2 = _manifest(repo)
    assert [e['number'] for e in manifest2['epochs']['demo']] == [0, 1]
    assert len(manifest2['boundaries']) == 2

    reconstructed2 = tmp_path / 'reconstructed2'
    reconstruct(repo, output=reconstructed2)
    subjects2 = _git(reconstructed2, 'log', '--format=%s', 'main').stdout.splitlines()
    assert subjects2[:6] == [
        'Start git epoch 2',
        'D',
        'Start git epoch 1',
        'C',
        'B',
        'A',
    ]

    gc_result = gc_history_store(repo)
    assert gc_result['verification']['fsck'] == 'passed'


def test_stale_plan_rejected_before_archive(tmp_path):
    repo = _init_repo(tmp_path / 'work')
    _commit(repo, 'A')
    initialize_config(
        repo,
        repository_id='stale-demo',
        history_store=tmp_path / 'history.git',
    )
    plan = build_plan(repo)
    _commit(repo, 'B')
    with pytest.raises(EpochPlanStaleError):
        apply_plan(plan)
    assert not (tmp_path / 'history.git').exists()


def test_recursive_epoch_submodule_checkpoint(tmp_path):
    child_remote = tmp_path / 'child-remote.git'
    child_seed = _init_repo(tmp_path / 'child-seed', child_remote)
    child_old = _commit(child_seed, 'child-A', 'child\n')
    _push(child_seed)

    parent_remote = tmp_path / 'parent-remote.git'
    parent = _init_repo(tmp_path / 'parent', parent_remote)
    _run(
        [
            'git',
            '-c',
            'protocol.file.allow=always',
            'submodule',
            'add',
            child_remote,
            'child',
        ],
        cwd=parent,
    )
    _git(parent, 'add', '.gitmodules', 'child')
    _git(parent, 'commit', '-m', 'parent-A')
    parent_old = _git(parent, 'rev-parse', 'HEAD').stdout.strip()
    _push(parent)

    child = parent / 'child'
    _git(child, 'config', 'user.name', 'Epoch Tester')
    _git(child, 'config', 'user.email', 'epoch@example.com')
    initialize_config(
        child,
        repository_id='child',
        history_store=tmp_path / 'child-history.git',
    )
    initialize_config(
        parent,
        repository_id='parent',
        history_store=tmp_path / 'parent-history.git',
    )
    configure_submodule(parent, 'child', policy='epoch', repository_id='child')

    plan = build_plan(parent, recursive=True)
    assert [e['repository'] for e in plan['repositories']] == ['child', 'parent']
    child_entry, parent_entry = plan['repositories']
    assert child_entry['old_tip'] == child_old
    assert parent_entry['old_tip'] == parent_old
    assert parent_entry['equivalence'] == 'recursive'
    assert parent_entry['translations'][0]['old_commit'] == child_old
    assert (
        parent_entry['translations'][0]['new_commit']
        == child_entry['successor_root']
    )
    assert parent_entry['new_tree'] != parent_entry['old_tree']

    apply_plan(plan, publish=True)
    parent_new = parent_entry['successor_root']
    child_new = child_entry['successor_root']
    assert _git(parent, 'rev-parse', 'main').stdout.strip() == parent_new
    assert _git(child, 'rev-parse', 'main').stdout.strip() == child_new
    gitlink = _git(parent, 'ls-tree', parent_new, 'child').stdout.split()[2]
    assert gitlink == child_new

    parent_manifest = _manifest(parent)
    boundary = parent_manifest['boundaries'][0]
    assert boundary['equivalence']['kind'] == 'recursive'
    assert boundary['equivalence']['translations'][0]['boundary'] == 'child-0-to-1'

    # A transfer-style fresh clone can recover recursive policy from the
    # successor-root trailers plus the versioned manifest.
    fresh_parent = tmp_path / 'parent-fresh'
    _run(
        [
            'git',
            '-c',
            'protocol.file.allow=always',
            'clone',
            '--no-local',
            '--recurse-submodules',
            '--branch',
            'main',
            parent_remote,
            fresh_parent,
        ]
    )
    with pytest.raises(EpochError, match='already epoch-managed'):
        load_config(fresh_parent)
    fresh_config = initialize_config(
        fresh_parent, history_store=tmp_path / 'parent-history.git'
    )
    assert fresh_config['repository'] == 'parent'
    assert fresh_config['active_epoch'] == 1
    assert fresh_config['submodules']['child']['policy'] == 'epoch'
    assert fresh_config['submodules']['child']['repository'] == 'child'

    reconstructed = tmp_path / 'parent-reconstructed'
    rec = reconstruct(parent, output=reconstructed, recursive=True)
    assert 'child' in rec['children']
    subjects = _git(reconstructed, 'log', '--format=%s', 'main').stdout.splitlines()
    assert subjects[:2] == ['Start git epoch 1', 'parent-A']

    # An archived parent commit can materialize the exact archived child commit
    # through the reconstructed child repository.
    _git(reconstructed, 'checkout', parent_old)
    _git(
        reconstructed,
        '-c',
        'protocol.file.allow=always',
        'submodule',
        'update',
        '--init',
        'child',
    )
    historical_child = reconstructed / 'child'
    assert _git(historical_child, 'rev-parse', 'HEAD').stdout.strip() == child_old


def test_nonrecursive_parent_checkpoint_keeps_epoch_child_gitlink(tmp_path):
    child_remote = tmp_path / 'child-remote.git'
    child_seed = _init_repo(tmp_path / 'child-seed', child_remote)
    child_old = _commit(child_seed, 'child-A')
    _push(child_seed)

    parent = _init_repo(tmp_path / 'parent')
    _run(
        [
            'git',
            '-c',
            'protocol.file.allow=always',
            'submodule',
            'add',
            child_remote,
            'child',
        ],
        cwd=parent,
    )
    _git(parent, 'add', '.gitmodules', 'child')
    _git(parent, 'commit', '-m', 'parent-A')

    child = parent / 'child'
    _git(child, 'config', 'user.name', 'Epoch Tester')
    _git(child, 'config', 'user.email', 'epoch@example.com')
    initialize_config(
        child,
        repository_id='child',
        history_store=tmp_path / 'child-history.git',
    )
    initialize_config(
        parent,
        repository_id='parent',
        history_store=tmp_path / 'parent-history.git',
    )
    configure_submodule(parent, 'child', policy='epoch', repository_id='child')

    plan = build_plan(parent, recursive=False)
    assert [e['repository'] for e in plan['repositories']] == ['parent']
    entry = plan['repositories'][0]
    assert entry['translations'] == []
    assert entry['old_tree'] == entry['new_tree']
    assert _git(parent, 'ls-tree', entry['old_tip'], 'child').stdout.split()[2] == child_old


def test_file_url_history_store_uses_remote_manifest_path(tmp_path):
    repo = _init_repo(tmp_path / 'work')
    old_tip = _commit(repo, 'A')
    history = tmp_path / 'history.git'
    _run(['git', 'init', '--bare', history])
    history_url = history.as_uri()
    initialize_config(
        repo,
        repository_id='remote-store-demo',
        history_store=history_url,
        public_history_url=history_url,
    )
    _git(repo, 'add', '.git-epoch.yaml')
    _git(repo, 'commit', '-m', 'Record public history locator')
    old_tip = _git(repo, 'rev-parse', 'HEAD').stdout.strip()
    plan = build_plan(repo)
    apply_plan(plan, publish=False)
    meta = _run(
        ['git', '--git-dir', history, 'rev-parse', 'refs/meta/main']
    ).stdout.strip()
    assert len(meta) == 40
    archived = _run(
        [
            'git',
            '--git-dir',
            history,
            'rev-parse',
            'refs/epochs/remote-store-demo/000/heads/main',
        ]
    ).stdout.strip()
    assert archived == old_tip
    publish_plan(plan)
    manifest = _manifest(repo)
    assert manifest['boundaries'][0]['state'] == 'committed'


def test_abort_prepared_checkpoint_allows_replan(tmp_path):
    repo = _init_repo(tmp_path / 'work')
    old_tip = _commit(repo, 'A')
    history = tmp_path / 'history.git'
    initialize_config(repo, repository_id='abort-demo', history_store=history)
    plan = build_plan(repo, bundle=True)
    apply_plan(plan, publish=False)
    assert _manifest(repo)['boundaries'][0]['state'] == 'prepared'
    result = abort_plan(plan)
    assert result['status'] == 'aborted'
    assert _git(repo, 'rev-parse', 'main').stdout.strip() == old_tip
    manifest = _manifest(repo)
    assert manifest['epochs']['abort-demo'] == []
    assert manifest['boundaries'] == []
    archive_ref = 'refs/epochs/abort-demo/000/heads/main'
    assert _run(
        ['git', '--git-dir', history, 'rev-parse', '--verify', archive_ref],
        check=False,
    ).returncode != 0
    # The old immutable backup is retained under an explicit aborted name,
    # while the canonical path becomes available to a later checkpoint.
    canonical_bundle = tmp_path / 'history.git.bundles' / 'abort-demo' / 'epoch-000.bundle'
    assert not canonical_bundle.exists()
    assert list(canonical_bundle.parent.glob('epoch-000.bundle.aborted-*'))
    plan2 = build_plan(repo)
    assert plan2['repositories'][0]['old_tip'] == old_tip


def test_relative_local_active_remote_is_normalized(tmp_path):
    remote = tmp_path / 'active.git'
    repo = _init_repo(tmp_path / 'work', remote)
    _commit(repo, 'A')
    _push(repo)
    _git(repo, 'remote', 'set-url', 'origin', '../active.git')
    config = initialize_config(
        repo,
        repository_id='relative-remote-demo',
        history_store=tmp_path / 'history.git',
    )
    assert config['active_url'] == str(remote.resolve())
    plan = build_plan(repo)
    apply_plan(plan, publish=True)
    reconstructed = tmp_path / 'reconstructed'
    reconstruct(repo, output=reconstructed)
    assert _git(reconstructed, 'rev-parse', 'main').stdout.strip() == (
        plan['repositories'][0]['successor_root']
    )


def test_merge_topology_and_annotated_tag_object_are_archived_exactly(tmp_path):
    repo = _init_repo(tmp_path / 'work')
    first = _commit(repo, 'A', 'alpha\n')
    _git(repo, 'checkout', '-b', 'feature')
    (repo / 'feature.txt').write_text('feature\n')
    _git(repo, 'add', 'feature.txt')
    _git(repo, 'commit', '-m', 'feature')
    feature = _git(repo, 'rev-parse', 'HEAD').stdout.strip()
    _git(repo, 'checkout', 'main')
    (repo / 'main.txt').write_text('main\n')
    _git(repo, 'add', 'main.txt')
    _git(repo, 'commit', '-m', 'main-B')
    _git(repo, 'merge', '--no-ff', 'feature', '-m', 'merge-feature')
    merge_tip = _git(repo, 'rev-parse', 'HEAD').stdout.strip()
    parents = _git(repo, 'rev-list', '--parents', '-n', '1', merge_tip).stdout.split()
    assert len(parents) == 3
    assert feature in parents
    _git(repo, 'branch', '-D', 'feature')
    _git(repo, 'tag', '-a', 'v0', '-m', 'archive-tag', first)
    tag_object = _git(repo, 'rev-parse', 'refs/tags/v0').stdout.strip()

    history = tmp_path / 'history.git'
    initialize_config(repo, repository_id='topology-demo', history_store=history)
    plan = build_plan(repo)
    apply_plan(plan, publish=False)

    archived_tip = _run(
        [
            'git',
            '--git-dir',
            history,
            'rev-parse',
            'refs/epochs/topology-demo/000/heads/main',
        ]
    ).stdout.strip()
    archived_tag = _run(
        [
            'git',
            '--git-dir',
            history,
            'rev-parse',
            'refs/epochs/topology-demo/000/tags/v0',
        ]
    ).stdout.strip()
    archived_parents = _run(
        [
            'git',
            '--git-dir',
            history,
            'rev-list',
            '--parents',
            '-n',
            '1',
            archived_tip,
        ]
    ).stdout.split()
    assert archived_tip == merge_tip
    assert archived_tag == tag_object
    assert archived_parents == parents


def test_checkpoint_refuses_additional_active_branch(tmp_path):
    repo = _init_repo(tmp_path / 'work')
    _commit(repo, 'A')
    _git(repo, 'branch', 'keep-me')
    initialize_config(
        repo,
        repository_id='branch-policy-demo',
        history_store=tmp_path / 'history.git',
    )
    with pytest.raises(EpochSafetyError, match='Additional active branches'):
        build_plan(repo)


def test_checkpoint_retires_remote_only_branch_exactly(tmp_path):
    remote = tmp_path / 'active.git'
    repo = _init_repo(tmp_path / 'work', remote)
    main_tip = _commit(repo, 'main-A', 'main\n')
    _push(repo)

    _git(repo, 'checkout', '-b', 'feature/old-work')
    (repo / 'feature-only.txt').write_text('retired branch payload\n')
    _git(repo, 'add', 'feature-only.txt')
    _git(repo, 'commit', '-m', 'feature-only')
    feature_tip = _git(repo, 'rev-parse', 'HEAD').stdout.strip()
    _git(repo, 'push', '-u', 'origin', 'feature/old-work')
    _git(repo, 'checkout', 'main')
    _git(repo, 'branch', '-D', 'feature/old-work')
    assert _git(
        repo, 'rev-parse', 'refs/remotes/origin/feature/old-work'
    ).stdout.strip() == feature_tip

    _git(repo, 'checkout', '-b', 'feature/local-copy')
    (repo / 'local-copy.txt').write_text('local and remote branch payload\n')
    _git(repo, 'add', 'local-copy.txt')
    _git(repo, 'commit', '-m', 'local-copy')
    local_copy_tip = _git(repo, 'rev-parse', 'HEAD').stdout.strip()
    _git(repo, 'push', '-u', 'origin', 'feature/local-copy')
    _git(repo, 'checkout', 'main')

    history = tmp_path / 'history.git'
    bundle_dir = tmp_path / 'bundles'
    initialize_config(
        repo,
        repository_id='branch-retire-demo',
        history_store=history,
    )
    with pytest.raises(EpochSafetyError, match='Additional active branches'):
        build_plan(repo)
    plan = build_plan(
        repo,
        retire_extra_branches=True,
        bundle=True,
        bundle_dir=bundle_dir,
    )
    entry = plan['repositories'][0]
    retired = [
        item for item in entry['refs']
        if item['source'] == 'refs/heads/feature/old-work'
    ]
    assert len(retired) == 1
    assert retired[0]['oid'] == feature_tip
    assert retired[0]['local_present'] is False
    assert retired[0]['remote_present'] is True
    assert retired[0]['local_source'] == 'refs/remotes/origin/feature/old-work'
    local_copy = [
        item for item in entry['refs']
        if item['source'] == 'refs/heads/feature/local-copy'
    ]
    assert len(local_copy) == 1
    assert local_copy[0]['oid'] == local_copy_tip
    assert local_copy[0]['local_present'] is True
    assert local_copy[0]['remote_present'] is True
    assert local_copy[0]['local_source'] == 'refs/heads/feature/local-copy'

    apply_plan(plan, publish=False)
    archived = _run(
        [
            'git', '--git-dir', history, 'rev-parse',
            'refs/epochs/branch-retire-demo/000/heads/feature/old-work',
        ]
    ).stdout.strip()
    assert archived == feature_tip
    archived_local_copy = _run(
        [
            'git', '--git-dir', history, 'rev-parse',
            'refs/epochs/branch-retire-demo/000/heads/feature/local-copy',
        ]
    ).stdout.strip()
    assert archived_local_copy == local_copy_tip
    bundle = pathlib.Path(entry['bundle_path'])
    bundle_heads = {}
    for line in _git(repo, 'bundle', 'list-heads', bundle).stdout.splitlines():
        oid, ref = line.split(' ', 1)
        bundle_heads[ref] = oid
    assert bundle_heads['refs/heads/feature/old-work'] == feature_tip
    assert bundle_heads['refs/heads/feature/local-copy'] == local_copy_tip
    assert bundle_heads['refs/heads/main'] == main_tip

    publish_plan(plan, fresh_clone=True)
    remote_heads = _run(
        ['git', '--git-dir', remote, 'for-each-ref', '--format=%(refname)', 'refs/heads/']
    ).stdout.splitlines()
    assert remote_heads == ['refs/heads/main']
    assert _git(
        repo, 'rev-parse', '--verify', 'refs/remotes/origin/feature/old-work', check=False
    ).returncode != 0
    assert _git(
        repo, 'rev-parse', '--verify', 'refs/heads/feature/local-copy', check=False
    ).returncode != 0
    assert _git(
        repo, 'rev-parse', '--verify', 'refs/remotes/origin/feature/local-copy', check=False
    ).returncode != 0

    fresh = tmp_path / 'fresh'
    _run(['git', 'clone', '--no-local', '--branch', 'main', remote, fresh])
    assert _git(fresh, 'cat-file', '-e', feature_tip, check=False).returncode != 0


def test_branch_retirement_requires_remote_branch_to_be_fetched(tmp_path):
    remote = tmp_path / 'active.git'
    repo = _init_repo(tmp_path / 'work', remote)
    _commit(repo, 'A')
    _push(repo)

    other = tmp_path / 'other'
    _run(['git', 'clone', '--no-local', remote, other])
    _git(other, 'config', 'user.name', 'Test User')
    _git(other, 'config', 'user.email', 'test@example.com')
    _git(other, 'checkout', '-b', 'remote-only')
    (other / 'remote-only.txt').write_text('remote only\n')
    _git(other, 'add', 'remote-only.txt')
    _git(other, 'commit', '-m', 'remote-only')
    _git(other, 'push', 'origin', 'remote-only')

    initialize_config(
        repo,
        repository_id='unfetched-branch-demo',
        history_store=tmp_path / 'history.git',
    )
    with pytest.raises(EpochSafetyError, match='must be fetched locally'):
        build_plan(repo, retire_extra_branches=True)

    _git(
        repo,
        'fetch',
        '--prune',
        'origin',
        '+refs/heads/*:refs/remotes/origin/*',
    )
    plan = build_plan(repo, retire_extra_branches=True)
    assert any(
        item['source'] == 'refs/heads/remote-only'
        for item in plan['repositories'][0]['refs']
    )


def test_publish_refuses_worktree_change_after_prepare(tmp_path):
    remote = tmp_path / 'active.git'
    repo = _init_repo(tmp_path / 'work', remote)
    old_tip = _commit(repo, 'A', 'alpha\n')
    _push(repo)
    initialize_config(
        repo,
        repository_id='publish-clean-demo',
        history_store=tmp_path / 'history.git',
    )
    plan = build_plan(repo)
    apply_plan(plan, publish=False)

    (repo / 'tracked.txt').write_text('edited after prepare\n')
    with pytest.raises(EpochSafetyError, match='Working tree changed'):
        publish_plan(plan, fresh_clone=False)
    assert _git(repo, 'rev-parse', 'main').stdout.strip() == old_tip
    assert _run(
        ['git', '--git-dir', remote, 'rev-parse', 'refs/heads/main']
    ).stdout.strip() == old_tip

    _git(repo, 'restore', 'tracked.txt')
    publish_plan(plan, fresh_clone=False)
    assert _git(repo, 'rev-parse', 'main').stdout.strip() == (
        plan['repositories'][0]['successor_root']
    )


def test_publish_resumes_after_remote_update_before_local_update(tmp_path, monkeypatch):
    import git_well.epoch.core as epoch_core

    remote = tmp_path / 'active.git'
    repo = _init_repo(tmp_path / 'work', remote)
    old_tip = _commit(repo, 'A')
    _push(repo)
    initialize_config(
        repo,
        repository_id='resume-demo',
        history_store=tmp_path / 'history.git',
    )
    plan = build_plan(repo)
    apply_plan(plan, publish=False)
    entry = plan['repositories'][0]
    real_publish_local = epoch_core._publish_local

    def fail_local(_entry):
        raise RuntimeError('injected local publication failure')

    monkeypatch.setattr(epoch_core, '_publish_local', fail_local)
    with pytest.raises(RuntimeError, match='injected'):
        publish_plan(plan, fresh_clone=False)
    assert _git(repo, 'rev-parse', 'main').stdout.strip() == old_tip
    assert _run(
        ['git', '--git-dir', remote, 'rev-parse', 'refs/heads/main']
    ).stdout.strip() == entry['successor_root']

    monkeypatch.setattr(epoch_core, '_publish_local', real_publish_local)
    result = publish_plan(plan, fresh_clone=False)
    assert result['status'] == 'published'
    assert _git(repo, 'rev-parse', 'main').stdout.strip() == entry['successor_root']


def test_publish_refuses_remote_branch_created_after_plan(tmp_path):
    remote = tmp_path / 'active.git'
    repo = _init_repo(tmp_path / 'work', remote)
    old_tip = _commit(repo, 'A')
    _push(repo)
    initialize_config(
        repo,
        repository_id='remote-race-demo',
        history_store=tmp_path / 'history.git',
    )
    plan = build_plan(repo)
    apply_plan(plan, publish=False)

    _git(repo, 'push', 'origin', f'{old_tip}:refs/heads/keep-old')
    with pytest.raises(EpochPlanStaleError, match='auxiliary branches changed'):
        publish_plan(plan, fresh_clone=False)
    assert _git(repo, 'rev-parse', 'main').stdout.strip() == old_tip
    assert _run(
        ['git', '--git-dir', remote, 'rev-parse', 'refs/heads/main']
    ).stdout.strip() == old_tip

    _git(repo, 'push', 'origin', '--delete', 'keep-old')
    publish_plan(plan, fresh_clone=False)


def test_recursive_mixed_submodule_policies_translate_only_epoch(tmp_path):
    child_specs = {}
    for name in ['epoch-child', 'continuous-child', 'external-child']:
        remote = tmp_path / f'{name}.git'
        seed = _init_repo(tmp_path / f'{name}-seed', remote)
        tip = _commit(seed, name)
        _push(seed)
        child_specs[name] = (remote, tip)

    parent_remote = tmp_path / 'parent-remote.git'
    parent = _init_repo(tmp_path / 'parent', parent_remote)
    for name, (remote, _tip) in child_specs.items():
        _run(
            [
                'git',
                '-c',
                'protocol.file.allow=always',
                'submodule',
                'add',
                remote,
                name,
            ],
            cwd=parent,
        )
    _git(parent, 'commit', '-am', 'parent-A')
    _push(parent)

    epoch_child = parent / 'epoch-child'
    _git(epoch_child, 'config', 'user.name', 'Epoch Tester')
    _git(epoch_child, 'config', 'user.email', 'epoch@example.com')
    initialize_config(
        epoch_child,
        repository_id='epoch-child',
        history_store=tmp_path / 'epoch-child-history.git',
    )
    initialize_config(
        parent,
        repository_id='mixed-parent',
        history_store=tmp_path / 'mixed-parent-history.git',
    )
    configure_submodule(
        parent, 'epoch-child', policy='epoch', repository_id='epoch-child'
    )
    configure_submodule(parent, 'continuous-child', policy='continuous')
    configure_submodule(parent, 'external-child', policy='external')

    plan = build_plan(parent, recursive=True)
    assert [entry['repository'] for entry in plan['repositories']] == [
        'epoch-child',
        'mixed-parent',
    ]
    child_entry, parent_entry = plan['repositories']
    assert [item['path'] for item in parent_entry['translations']] == ['epoch-child']
    # Planning predicts successor object IDs without importing the objects into
    # the source repositories.  Preparation materializes the exact predicted
    # commits so they can be inspected with ordinary Git plumbing.
    apply_plan(plan, publish=False)
    assert (
        _git(parent, 'ls-tree', parent_entry['successor_root'], 'epoch-child')
        .stdout.split()[2]
        == child_entry['successor_root']
    )
    for name in ['continuous-child', 'external-child']:
        old_oid = child_specs[name][1]
        new_oid = _git(
            parent, 'ls-tree', parent_entry['successor_root'], name
        ).stdout.split()[2]
        assert new_oid == old_oid


def test_nested_recursive_checkpoint_supports_detached_submodule_heads(tmp_path):
    leaf_remote = tmp_path / 'leaf.git'
    leaf_seed = _init_repo(tmp_path / 'leaf-seed', leaf_remote)
    leaf_old = _commit(leaf_seed, 'leaf-A')
    _push(leaf_seed)

    middle_remote = tmp_path / 'middle.git'
    middle_seed = _init_repo(tmp_path / 'middle-seed', middle_remote)
    _run(
        [
            'git',
            '-c',
            'protocol.file.allow=always',
            'submodule',
            'add',
            leaf_remote,
            'leaf',
        ],
        cwd=middle_seed,
    )
    _git(middle_seed, 'commit', '-am', 'middle-A')
    middle_old = _git(middle_seed, 'rev-parse', 'HEAD').stdout.strip()
    _push(middle_seed)

    root_remote = tmp_path / 'root.git'
    root = _init_repo(tmp_path / 'root', root_remote)
    _run(
        [
            'git',
            '-c',
            'protocol.file.allow=always',
            'submodule',
            'add',
            middle_remote,
            'middle',
        ],
        cwd=root,
    )
    _git(root, 'commit', '-am', 'root-A')
    root_old = _git(root, 'rev-parse', 'HEAD').stdout.strip()
    _push(root)
    _git(
        root,
        '-c',
        'protocol.file.allow=always',
        'submodule',
        'update',
        '--init',
        '--recursive',
    )

    middle = root / 'middle'
    leaf = middle / 'leaf'
    for child in [middle, leaf]:
        _git(child, 'config', 'user.name', 'Epoch Tester')
        _git(child, 'config', 'user.email', 'epoch@example.com')
        _git(child, 'checkout', '--detach')

    initialize_config(
        leaf,
        repository_id='leaf',
        history_store=tmp_path / 'leaf-history.git',
        primary_branch='main',
    )
    initialize_config(
        middle,
        repository_id='middle',
        history_store=tmp_path / 'middle-history.git',
        primary_branch='main',
    )
    configure_submodule(middle, 'leaf', policy='epoch', repository_id='leaf')
    initialize_config(
        root,
        repository_id='root',
        history_store=tmp_path / 'root-history.git',
    )
    configure_submodule(root, 'middle', policy='epoch', repository_id='middle')

    plan = build_plan(root, recursive=True)
    assert [entry['repository'] for entry in plan['repositories']] == [
        'leaf',
        'middle',
        'root',
    ]
    leaf_entry, middle_entry, root_entry = plan['repositories']
    assert leaf_entry['old_tip'] == leaf_old
    assert middle_entry['old_tip'] == middle_old
    assert root_entry['old_tip'] == root_old

    apply_plan(plan, publish=True, fresh_clone=False)
    assert _git(leaf, 'rev-parse', 'HEAD').stdout.strip() == leaf_entry['successor_root']
    assert _git(middle, 'rev-parse', 'HEAD').stdout.strip() == middle_entry['successor_root']
    assert _git(root, 'rev-parse', 'HEAD').stdout.strip() == root_entry['successor_root']
    assert _git(middle, 'ls-tree', 'HEAD', 'leaf').stdout.split()[2] == (
        leaf_entry['successor_root']
    )
    assert _git(root, 'ls-tree', 'HEAD', 'middle').stdout.split()[2] == (
        middle_entry['successor_root']
    )


def test_remote_history_store_requires_committed_public_locator(tmp_path):
    repo = _init_repo(tmp_path / 'work')
    _commit(repo, 'A')
    history = tmp_path / 'history.git'
    _run(['git', 'init', '--bare', history])
    history_url = history.as_uri()
    initialize_config(
        repo,
        repository_id='remote-locator-demo',
        history_store=history_url,
    )
    with pytest.raises(EpochError, match='No committed .git-epoch.yaml'):
        build_plan(repo)

    from git_well.epoch import write_public_locator

    write_public_locator(
        repo,
        repository_id='remote-locator-demo',
        history_store_id='history',
        history_url=history_url,
    )
    _git(repo, 'add', '.git-epoch.yaml')
    _git(repo, 'commit', '-m', 'Record epoch history locator')
    plan = build_plan(repo)
    assert plan['repositories'][0]['repository'] == 'remote-locator-demo'


def test_public_locator_status_attach_inspect_and_reconstruct(tmp_path):
    active_remote = tmp_path / 'active.git'
    repo = _init_repo(tmp_path / 'work', active_remote)
    _commit(repo, 'A')
    history = tmp_path / 'history.git'
    _run(['git', 'init', '--bare', history])
    history_url = history.as_uri()

    initialize_config(
        repo,
        repository_id='public-demo',
        history_store=history_url,
        history_store_id='public-history',
        public_history_url=history_url,
        public_history_browse_url='https://example.test/public-history',
    )
    locator_path = repo / '.git-epoch.yaml'
    assert locator_path.exists()
    locator = yaml.safe_load(locator_path.read_text())
    assert locator == {
        'format_version': 1,
        'repository': 'public-demo',
        'history_store': {
            'id': 'public-history',
            'url': history_url,
            'browse_url': 'https://example.test/public-history',
        },
    }
    _git(repo, 'add', '.git-epoch.yaml')
    _git(repo, 'commit', '-m', 'Record public history location')
    _push(repo)

    plan = build_plan(repo)
    old_tip = plan['repositories'][0]['old_tip']
    new_root = plan['repositories'][0]['successor_root']
    apply_plan(plan, publish=True)

    fresh = tmp_path / 'fresh'
    _run(['git', 'clone', '--no-local', '--branch', 'main', active_remote, fresh])
    assert _git(fresh, 'cat-file', '-e', old_tip, check=False).returncode != 0

    public_status = status(fresh)
    assert public_status['repository'] == 'public-demo'
    assert public_status['active_epoch'] == 1
    assert public_status['attached'] is False
    assert public_status['archive_verification'] == 'not-attached'
    assert public_status['attach_command'] == 'git epoch attach'
    assert public_status['public_locator']['history_store_id'] == 'public-history'
    assert public_status['public_locator']['url'] == history_url

    public_inspect = inspect_manifest(fresh)
    assert public_inspect['attached'] is False
    assert public_inspect['manifest']['history_store']['id'] == 'public-history'

    public_reconstructed = tmp_path / 'public-reconstructed'
    rec = reconstruct(fresh, output=public_reconstructed)
    assert rec['attached'] is False
    assert rec['replacements'][0]['root'] == new_root
    assert rec['replacements'][0]['predecessor'] == old_tip

    from git_well.epoch import attach_history_store

    attached = attach_history_store(fresh)
    assert attached['status'] == 'attached'
    assert attached['repository'] == 'public-demo'
    assert attached['history_store_id'] == 'public-history'
    attached_status = status(fresh)
    assert attached_status['attached'] is True
    assert attached_status['archive_verification'] == 'available'


def test_attach_rejects_public_locator_that_conflicts_with_root(tmp_path):
    active_remote = tmp_path / 'active.git'
    repo = _init_repo(tmp_path / 'work', active_remote)
    _commit(repo, 'A')
    history = tmp_path / 'history.git'
    _run(['git', 'init', '--bare', history])
    history_url = history.as_uri()
    initialize_config(
        repo,
        repository_id='locator-tamper-demo',
        history_store=history_url,
        history_store_id='history',
        public_history_url=history_url,
    )
    _git(repo, 'add', '.git-epoch.yaml')
    _git(repo, 'commit', '-m', 'Record locator')
    _push(repo)
    plan = build_plan(repo)
    apply_plan(plan, publish=True)

    fresh = tmp_path / 'fresh'
    _run(['git', 'clone', '--no-local', '--branch', 'main', active_remote, fresh])
    locator_path = fresh / '.git-epoch.yaml'
    locator = yaml.safe_load(locator_path.read_text())
    locator['history_store']['id'] = 'other-history'
    locator_path.write_text(yaml.safe_dump(locator, sort_keys=False))
    _git(fresh, 'add', '.git-epoch.yaml')
    _git(fresh, 'config', 'user.name', 'Epoch Tester')
    _git(fresh, 'config', 'user.email', 'epoch@example.com')
    _git(fresh, 'commit', '-m', 'Tamper locator')

    from git_well.epoch import attach_history_store

    with pytest.raises(EpochSafetyError, match='conflicts with successor-root trailers'):
        attach_history_store(fresh)


def test_apply_refuses_public_locator_that_cannot_see_prepared_archive(tmp_path):
    repo = _init_repo(tmp_path / 'work')
    _commit(repo, 'A')
    writable_history = tmp_path / 'writable-history.git'
    wrong_public_history = tmp_path / 'wrong-public-history.git'
    _run(['git', 'init', '--bare', writable_history])
    _run(['git', 'init', '--bare', wrong_public_history])

    initialize_config(
        repo,
        repository_id='public-visibility-demo',
        history_store=writable_history.as_uri(),
        history_store_id='shared-history',
        public_history_url=wrong_public_history.as_uri(),
    )
    _git(repo, 'add', '.git-epoch.yaml')
    _git(repo, 'commit', '-m', 'Record intentionally wrong public locator')
    plan = build_plan(repo)

    with pytest.raises(
        EpochSafetyError,
        match='public history URL does not expose the prepared archive refs',
    ):
        apply_plan(plan, publish=False)
    retiring_tip = plan['repositories'][0]['old_tip']
    assert _git(repo, 'rev-parse', 'HEAD').stdout.strip() == retiring_tip
    assert retiring_tip != plan['repositories'][0]['successor_root']


def test_public_locator_reconstructs_detached_recursive_submodule(tmp_path):
    child_remote = tmp_path / 'child-active.git'
    child_seed = _init_repo(tmp_path / 'child-seed', child_remote)
    _commit(child_seed, 'child-A')
    _push(child_seed)

    parent_remote = tmp_path / 'parent-active.git'
    parent = _init_repo(tmp_path / 'parent', parent_remote)
    _run(
        [
            'git', '-c', 'protocol.file.allow=always',
            'submodule', 'add', child_remote, 'child',
        ],
        cwd=parent,
    )
    _git(parent, 'commit', '-am', 'parent-A')
    _push(parent)

    child = parent / 'child'
    _git(child, 'switch', 'main')
    _git(child, 'config', 'user.name', 'Epoch Tester')
    _git(child, 'config', 'user.email', 'epoch@example.com')
    shared_history = tmp_path / 'shared-history.git'
    _run(['git', 'init', '--bare', shared_history])
    history_url = shared_history.as_uri()

    initialize_config(
        child,
        repository_id='child-public',
        history_store=history_url,
        history_store_id='shared-history',
        public_history_url=history_url,
        primary_branch='main',
    )
    _git(child, 'add', '.git-epoch.yaml')
    _git(child, 'commit', '-m', 'Record child history locator')
    _git(child, 'push', '-u', 'origin', 'main')
    _git(parent, 'add', 'child')
    _git(parent, 'commit', '-m', 'Advance child to epoch-ready tip')
    _push(parent)

    initialize_config(
        parent,
        repository_id='parent-public',
        history_store=history_url,
        history_store_id='shared-history',
        public_history_url=history_url,
    )
    configure_submodule(
        parent,
        'child',
        policy='epoch',
        repository_id='child-public',
    )
    _git(parent, 'add', '.git-epoch.yaml')
    _git(parent, 'commit', '-m', 'Record parent history locator')
    _push(parent)

    plan = build_plan(parent, recursive=True)
    apply_plan(plan, publish=True)

    fresh = tmp_path / 'fresh-parent'
    _run(
        [
            'git', '-c', 'protocol.file.allow=always',
            'clone', '--no-local', '--recurse-submodules',
            '--branch', 'main', parent_remote, fresh,
        ]
    )
    fresh_child = fresh / 'child'
    assert _git(fresh_child, 'symbolic-ref', '-q', 'HEAD', check=False).returncode != 0
    child_status = status(fresh_child)
    assert child_status['attached'] is False
    assert child_status['branch'] == '(detached)'
    assert child_status['active_epoch'] == 1

    reconstructed = tmp_path / 'reconstructed-public-parent'
    result = reconstruct(fresh, output=reconstructed, recursive=True)
    assert result['attached'] is False
    assert 'child-public' in result['children']
    assert result['children']['child-public']['attached'] is False


def test_initialize_config_repairs_equivalent_existing_setup(tmp_path):
    """Repeated setup repairs a missing public locator instead of failing."""
    repo = _init_repo(tmp_path / 'work')
    _commit(repo, 'A')
    history = tmp_path / 'history.git'
    history_url = history.as_uri()

    first = initialize_config(
        repo,
        repository_id='repair-demo',
        history_store=history_url,
        history_store_id='shared-history',
        primary_branch='main',
    )
    assert first['repository'] == 'repair-demo'
    assert not (repo / '.git-epoch.yaml').exists()

    repaired = initialize_config(
        repo,
        repository_id='repair-demo',
        history_store=history_url,
        history_store_id='shared-history',
        public_history_url=history_url,
        public_history_browse_url='https://example.test/history',
        primary_branch='main',
    )
    assert repaired == first
    locator_path = repo / '.git-epoch.yaml'
    assert locator_path.exists()
    locator = yaml.safe_load(locator_path.read_text())
    assert locator['repository'] == 'repair-demo'
    assert locator['history_store']['id'] == 'shared-history'
    assert locator['history_store']['url'] == history_url

    # The locator is intentionally still untracked here. A user can rerun the
    # same setup command after an interrupted shell block and get a no-op rather
    # than a dirty-worktree failure.
    repeated_uncommitted = initialize_config(
        repo,
        repository_id='repair-demo',
        history_store=history_url,
        history_store_id='shared-history',
        public_history_url=history_url,
        public_history_browse_url='https://example.test/history',
        primary_branch='main',
    )
    assert repeated_uncommitted == repaired

    _git(repo, 'add', '.git-epoch.yaml')
    _git(repo, 'commit', '-m', 'Record locator')
    repeated_committed = initialize_config(
        repo,
        repository_id='repair-demo',
        history_store=history_url,
        history_store_id='shared-history',
        public_history_url=history_url,
        public_history_browse_url='https://example.test/history',
        primary_branch='main',
    )
    assert repeated_committed == repaired


def test_initialize_config_reconcile_refuses_real_conflict(tmp_path):
    repo = _init_repo(tmp_path / 'work')
    _commit(repo, 'A')
    first_history = (tmp_path / 'history-a.git').as_uri()
    other_history = (tmp_path / 'history-b.git').as_uri()
    initialize_config(
        repo,
        repository_id='conflict-demo',
        history_store=first_history,
        history_store_id='shared-history',
        primary_branch='main',
    )

    with pytest.raises(
        EpochSafetyError,
        match='Existing epoch configuration conflicts with the requested initialization',
    ):
        initialize_config(
            repo,
            repository_id='conflict-demo',
            history_store=other_history,
            history_store_id='shared-history',
            primary_branch='main',
        )


def test_initialize_config_reconcile_allows_only_locator_dirt(tmp_path):
    repo = _init_repo(tmp_path / 'work')
    _commit(repo, 'A')
    history_url = (tmp_path / 'history.git').as_uri()
    initialize_config(
        repo,
        repository_id='dirty-demo',
        history_store=history_url,
        history_store_id='shared-history',
        primary_branch='main',
    )
    (repo / 'unrelated.txt').write_text('unfinished work\n')

    with pytest.raises(EpochSafetyError, match='Repository must be clean'):
        initialize_config(
            repo,
            repository_id='dirty-demo',
            history_store=history_url,
            history_store_id='shared-history',
            public_history_url=history_url,
            primary_branch='main',
        )
    assert not (repo / '.git-epoch.yaml').exists()


def test_history_store_exposes_browsable_views_and_can_backfill(tmp_path):
    remote = tmp_path / 'active.git'
    repo = _init_repo(tmp_path / 'work', remote)
    old_main = _commit(repo, 'A', 'main payload\n')
    _git(repo, 'tag', '-a', 'v0', '-m', 'v0', old_main)
    _push(repo)
    _git(repo, 'push', 'origin', 'v0')

    _git(repo, 'switch', '-c', 'feature/old')
    feature_tip = _commit(repo, 'feature', 'feature payload\n')
    _git(repo, 'push', '-u', 'origin', 'feature/old')
    _git(repo, 'switch', 'main')

    history = tmp_path / 'history.git'
    initialize_config(repo, repository_id='view-demo', history_store=history)
    plan = build_plan(repo, retire_extra_branches=True)
    apply_plan(plan)
    publish_plan(plan, fresh_clone=False)

    def history_ref(ref):
        return _run(
            ['git', '--git-dir', history, 'rev-parse', '--verify', ref]
        ).stdout.strip()

    assert history_ref('refs/heads/archive/view-demo/epoch-000/main') == old_main
    assert (
        history_ref('refs/heads/archive/view-demo/epoch-000/feature/old')
        == feature_tip
    )
    # The source tag was retired locally at publication, so compare the view to
    # the machine archive ref instead of the now-absent active tag.
    archived_tag = history_ref('refs/epochs/view-demo/000/tags/v0')
    assert history_ref('refs/tags/archive/view-demo/epoch-000/v0') == archived_tag

    landing = _run(
        ['git', '--git-dir', history, 'show', 'refs/heads/main:README.md']
    ).stdout
    assert 'Git Epoch History Store' in landing
    assert '`view-demo`' in landing
    assert 'refs/epochs/view-demo/000/heads/main' not in landing  # generic example only
    index_text = _run(
        ['git', '--git-dir', history, 'show', 'refs/heads/main:archive-index.yaml']
    ).stdout
    assert 'archive/view-demo/epoch-000/main' in index_text

    clone = tmp_path / 'history-clone'
    _run(['git', 'clone', '--no-local', history, clone])
    branches = _git(clone, 'branch', '-r').stdout
    assert 'origin/archive/view-demo/epoch-000/main' in branches
    assert _git(
        clone,
        'log',
        '-1',
        '--format=%s',
        'origin/archive/view-demo/epoch-000/main',
    ).stdout.strip() == 'A'

    # Simulate a store created by an older git-epoch version, then backfill.
    for ref in [
        'refs/heads/archive/view-demo/epoch-000/main',
        'refs/heads/archive/view-demo/epoch-000/feature/old',
        'refs/tags/archive/view-demo/epoch-000/v0',
        'refs/heads/main',
    ]:
        _run(['git', '--git-dir', history, 'update-ref', '-d', ref])
    repaired = sync_history_views(repo)
    assert repaired['archive_views']['created'] == 3
    assert repaired['landing']['status'] == 'updated'
    assert history_ref('refs/heads/archive/view-demo/epoch-000/main') == old_main


def test_apply_batches_archive_push_and_deep_verify_fetch(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path / 'work')
    old_main = _commit(repo, 'A')
    _git(repo, 'tag', 'v0')
    _git(repo, 'switch', '-c', 'feature/a')
    _commit(repo, 'feature')
    _git(repo, 'switch', 'main')
    history = tmp_path / 'history.git'
    initialize_config(repo, repository_id='batch-demo', history_store=history)
    plan = build_plan(repo, retire_extra_branches=True)

    import git_well.epoch.core as core

    calls = []
    original_run = core._run

    def recording_run(args, **kwargs):
        args_list = [str(a) for a in args]
        calls.append((args_list, kwargs.get('input')))
        return original_run(args, **kwargs)

    monkeypatch.setattr(core, '_run', recording_run)
    progress = []
    apply_plan(plan, progress=progress.append)
    archive_pushes = [
        args
        for args, _input in calls
        if args[:3] == ['git', 'push', '--atomic'] and str(history) in args
    ]
    assert len(archive_pushes) == 1
    assert sum(1 for token in archive_pushes[0] if token.endswith('/heads/main')) >= 1
    assert any('one atomic push' in message for message in progress)

    apply_stdin_calls = [
        (args, input_payload)
        for args, input_payload in calls
        if '--stdin' in args
    ]
    assert apply_stdin_calls
    for _args, input_payload in apply_stdin_calls:
        assert isinstance(input_payload, bytes)
        assert b'\r\n' not in input_payload

    calls.clear()
    publish_plan(plan, fresh_clone=False)
    publish_stdin_calls = [
        (args, input_payload)
        for args, input_payload in calls
        if '--stdin' in args
    ]
    assert publish_stdin_calls
    update_ref_batches = [
        (args, input_payload)
        for args, input_payload in publish_stdin_calls
        if args[:3] == ['git', 'update-ref', '--stdin']
    ]
    assert len(update_ref_batches) == 1
    for _args, input_payload in publish_stdin_calls:
        assert isinstance(input_payload, bytes)
        assert b'\r\n' not in input_payload

    calls.clear()
    progress.clear()
    from git_well.epoch import verify
    result = verify(repo, deep=True, progress=progress.append)
    assert result['fsck'] == 'passed'
    stdin_fetches = [
        (args, input_text)
        for args, input_text in calls
        if 'fetch' in args and '--stdin' in args and str(history) in args
    ]
    assert len(stdin_fetches) == 1
    stdin_payload = stdin_fetches[0][1]
    assert isinstance(stdin_payload, bytes)
    assert stdin_payload.count(b'\n') >= 3
    assert b'\r\n' not in stdin_payload
    assert '--no-auto-maintenance' in stdin_fetches[0][0]
    assert any('archive refs in one batch' in message for message in progress)


def test_compact_active_prunes_unreachable_retired_tip(tmp_path):
    remote = tmp_path / 'active.git'
    repo = _init_repo(tmp_path / 'work', remote)
    _commit(repo, 'A', 'alpha\n')
    old_tip = _commit(repo, 'B', 'beta\n')
    _push(repo)
    history = tmp_path / 'history.git'
    initialize_config(repo, repository_id='compact-demo', history_store=history)
    plan = build_plan(repo)
    apply_plan(plan)
    publish_plan(plan, fresh_clone=False)
    assert _git(repo, 'cat-file', '-e', f'{old_tip}^{{commit}}', check=False).returncode == 0

    progress = []
    result = compact_active(repo, progress=progress.append)
    row = result['repositories']['compact-demo']
    assert row['before_bytes'] > 0
    assert row['after_bytes'] > 0
    assert row['retired_predecessor_present'] is False
    assert _git(repo, 'cat-file', '-e', f'{old_tip}^{{commit}}', check=False).returncode != 0
    assert any('git gc --prune=now' in message for message in progress)


def test_save_plan_refuses_unignored_worktree_output(tmp_path, monkeypatch):
    repo = _init_repo(tmp_path / 'work')
    _commit(repo, 'A')
    initialize_config(repo, repository_id='plan-output-demo', history_store=tmp_path / 'history.git')
    plan = build_plan(repo)
    monkeypatch.chdir(repo)
    from git_well.epoch import save_plan
    with pytest.raises(EpochSafetyError, match='would make the repository dirty'):
        save_plan(plan, 'checkpoint.yaml')
    outside = tmp_path / 'cutover' / 'checkpoint.yaml'
    saved = save_plan(plan, outside)
    assert saved == outside.resolve()
    assert outside.exists()


def test_history_view_failure_does_not_fail_published_epoch(tmp_path, monkeypatch):
    remote = tmp_path / 'active.git'
    repo = _init_repo(tmp_path / 'work', remote)
    _commit(repo, 'A', 'payload\n')
    _push(repo)
    history = tmp_path / 'history.git'
    initialize_config(repo, repository_id='view-warning-demo', history_store=history)
    plan = build_plan(repo)
    apply_plan(plan)

    import git_well.epoch.core as core

    def fail_views(*args, **kwargs):
        raise core.EpochError('simulated browsing-view failure')

    monkeypatch.setattr(core, '_sync_published_history_views', fail_views)
    progress = []
    result = publish_plan(plan, fresh_clone=False, progress=progress.append)
    assert result['status'] == 'published'
    warning = result['history_view_warnings']['view-warning-demo']
    assert warning['status'] == 'failed'
    assert warning['repair_command'] == 'git epoch history-sync'
    assert _git(repo, 'rev-parse', 'HEAD').stdout.strip() == plan['repositories'][0]['successor_root']
    assert any('WARNING: history browsing views were not updated' in msg for msg in progress)
