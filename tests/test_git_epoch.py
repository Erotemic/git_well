from __future__ import annotations

import pathlib
import subprocess

import pytest

from git_well.epoch import (
    EpochError,
    EpochPlanStaleError,
    EpochSafetyError,
    abort_plan,
    apply_plan,
    build_plan,
    configure_submodule,
    gc_history_store,
    initialize_config,
    inspect_manifest,
    load_config,
    publish_plan,
    reconstruct,
    status,
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
    )
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
    with pytest.raises(EpochSafetyError, match='one active local branch'):
        build_plan(repo)


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
    with pytest.raises(EpochPlanStaleError, match='additional branches'):
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
