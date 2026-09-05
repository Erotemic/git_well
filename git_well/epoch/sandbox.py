from __future__ import annotations

import os
import pathlib
import re
import shutil
import tempfile
from typing import Any, Mapping

from .core import (
    EpochError,
    EpochSafetyError,
    _assert_clean,
    _config_path,
    _current_branch,
    _default_repository_id,
    _git,
    _git_stdout,
    _gitlink_oid,
    _parse_gitmodules,
    _repo_root,
    _resolve_ref,
    _run,
    _tree_oid,
    _write_yaml,
    apply_plan,
    build_plan,
    configure_submodule,
    initialize_config,
    load_config,
    load_plan,
    publish_plan,
    save_plan,
    verify,
)

SANDBOX_FORMAT_VERSION = 1
SANDBOX_MANIFEST = 'sandbox.yaml'


def _is_within(path: pathlib.Path, root: pathlib.Path) -> bool:
    path = path.resolve()
    root = root.resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _safe_repository_id(text: str) -> str:
    text = re.sub(r'[^A-Za-z0-9._-]+', '-', text).strip('-')
    return text or 'repository'


def _source_epoch_config(repo: pathlib.Path) -> dict[str, Any] | None:
    if not _config_path(repo).exists():
        return None
    config = load_config(repo, allow_bootstrap=False)
    if int(config.get('active_epoch', 0)) != 0:
        raise EpochSafetyError(
            'Sandbox creation from an already-published epoch repository is not '
            f'yet supported: {repo}. Rehearsal currently starts from epoch zero.'
        )
    return config


def _effective_identity(repo: pathlib.Path) -> tuple[str, str]:
    name_proc = _git(repo, 'config', '--get', 'user.name', check=False)
    email_proc = _git(repo, 'config', '--get', 'user.email', check=False)
    name = name_proc.stdout.strip() if name_proc.returncode == 0 else ''
    email = email_proc.stdout.strip() if email_proc.returncode == 0 else ''
    if not name:
        name = 'Git Epoch Sandbox'
    if not email:
        email = 'git-epoch-sandbox@example.invalid'
    return name, email


def _discover_source_graph(
    root: pathlib.Path,
    *,
    recursive: bool,
    all_submodules: str | None,
) -> list[dict[str, Any]]:
    if all_submodules not in {None, 'epoch', 'continuous', 'external'}:
        raise EpochError(f'Unknown --all-submodules policy: {all_submodules!r}')

    nodes: list[dict[str, Any]] = []
    root_id = _default_repository_id(root)

    def visit(
        repo: pathlib.Path,
        relpath: pathlib.PurePosixPath,
        parent: dict[str, Any] | None,
        parent_path: str | None,
        relation_policy: str,
    ) -> dict[str, Any]:
        repo = _repo_root(repo)
        _assert_clean(repo)
        source_config = _source_epoch_config(repo)
        if source_config is not None:
            repository_id = source_config['repository']
        elif not relpath.parts:
            repository_id = root_id
        else:
            repository_id = _safe_repository_id(
                root_id + '--' + '--'.join(relpath.parts)
            )

        node: dict[str, Any] = {
            'repository': repository_id,
            'relpath': relpath.as_posix() if relpath.parts else '.',
            'source_repo': str(repo),
            'source_origin': None,
            'source_configured': source_config is not None,
            'policy': relation_policy,
            'parent_repository': None if parent is None else parent['repository'],
            'parent_path': parent_path,
            'children': [],
        }
        origin = _git(repo, 'remote', 'get-url', 'origin', check=False)
        if origin.returncode == 0:
            node['source_origin'] = origin.stdout.strip()
        nodes.append(node)

        if not recursive:
            return node

        configured_submodules = (
            source_config.get('submodules', {}) if source_config is not None else {}
        )
        for occurrence in _parse_gitmodules(repo):
            path = occurrence['path']
            source_child_path = repo / path
            probe = _run(
                ['git', '-C', source_child_path, 'rev-parse', '--show-toplevel'],
                check=False,
            )
            if probe.returncode:
                raise EpochSafetyError(
                    f'Recursive sandbox requires initialized submodules. '
                    f'Missing worktree: {source_child_path}. Run '
                    '`git submodule update --init --recursive` first.'
                )
            child_repo = pathlib.Path(probe.stdout.strip()).resolve()
            parent_commit = _resolve_ref(repo, 'HEAD')
            gitlink = _gitlink_oid(repo, parent_commit, path)
            child_head = _resolve_ref(child_repo, 'HEAD')
            if gitlink != child_head:
                raise EpochSafetyError(
                    f'Submodule worktree does not match its parent gitlink: '
                    f'{repo}:{path} has {gitlink}, child HEAD is {child_head}'
                )

            if all_submodules is not None:
                policy = all_submodules
            else:
                policy = configured_submodules.get(path, {}).get(
                    'policy', 'external'
                )
            child_rel = relpath / pathlib.PurePosixPath(path)
            child = visit(
                child_repo,
                child_rel,
                node,
                path,
                policy,
            )
            node['children'].append(
                {
                    'name': occurrence['name'],
                    'path': path,
                    'repository': child['repository'],
                    'policy': policy,
                    'old_commit': gitlink,
                }
            )
        return node

    visit(root, pathlib.PurePosixPath(), None, None, 'epoch')
    return nodes


def _normalize_clone_branch(
    source: pathlib.Path,
    clone: pathlib.Path,
    node: Mapping[str, Any],
) -> str:
    target = _resolve_ref(source, 'HEAD')
    source_config = _source_epoch_config(source)
    if source_config is not None:
        branch = str(source_config['primary_branch'])
    else:
        source_branch = _current_branch(source)
        if node['parent_repository'] is None and source_branch:
            branch = source_branch
        else:
            branch = 'epoch-active'

    _git(clone, 'checkout', '--detach', target)
    heads = _git_stdout(
        clone, 'for-each-ref', '--format=%(refname)', 'refs/heads/'
    ).splitlines()
    for ref in heads:
        if ref:
            _git(clone, 'update-ref', '-d', ref)
    branch_ref = f'refs/heads/{branch}'
    _git(clone, 'update-ref', branch_ref, target)
    _git(clone, 'symbolic-ref', 'HEAD', branch_ref)
    _git(clone, 'reset', '--hard', target)
    return branch


def _clone_node(
    node: dict[str, Any],
    *,
    work_root: pathlib.Path,
    active_root: pathlib.Path,
) -> None:
    source = pathlib.Path(node['source_repo'])
    relpath = pathlib.PurePosixPath(node['relpath'])
    if relpath.parts and node['relpath'] != '.':
        clone = work_root.joinpath(*relpath.parts)
    else:
        clone = work_root
    if clone.exists():
        if any(clone.iterdir()):
            raise EpochSafetyError(f'Sandbox worktree path is not empty: {clone}')
        clone.rmdir()
    clone.parent.mkdir(parents=True, exist_ok=True)
    _run(['git', 'clone', '--no-local', '--no-checkout', source, clone])

    branch = _normalize_clone_branch(source, clone, node)
    name, email = _effective_identity(source)
    _git(clone, 'config', '--local', 'user.name', name)
    _git(clone, 'config', '--local', 'user.email', email)

    # A sandbox clone must not retain a network-capable publication remote.
    # The source URL remains in sandbox.yaml for inspection only.
    for remote_name in _git_stdout(clone, 'remote').splitlines():
        if remote_name:
            _git(clone, 'remote', 'remove', remote_name)

    active_remote = active_root / f"{node['repository']}.git"
    if active_remote.exists():
        raise EpochSafetyError(
            f'Duplicate sandbox repository id {node["repository"]!r}: '
            f'{active_remote} already exists'
        )
    _run(['git', 'init', '--bare', active_remote])
    _run(
        [
            'git',
            '--git-dir',
            active_remote,
            'symbolic-ref',
            'HEAD',
            f'refs/heads/{branch}',
        ]
    )
    _git(clone, 'remote', 'add', 'origin', active_remote)
    _git(clone, 'push', '-u', 'origin', f'HEAD:refs/heads/{branch}')
    _git(clone, 'push', 'origin', '--tags')

    node['repo'] = str(clone.resolve())
    node['branch'] = branch
    node['active_remote'] = str(active_remote.resolve())
    node['old_tip'] = _resolve_ref(clone, f'refs/heads/{branch}')
    node['old_tree'] = _tree_oid(clone, node['old_tip'])
    node['old_commit_count'] = int(
        _git_stdout(clone, 'rev-list', '--count', node['old_tip']).strip()
    )


def _configure_epoch_graph(
    nodes: list[dict[str, Any]], history_store: pathlib.Path
) -> None:
    by_id = {node['repository']: node for node in nodes}
    if len(by_id) != len(nodes):
        raise EpochSafetyError('Sandbox repository ids are not unique')

    # Child configurations must exist before a parent can mark the occurrence
    # as epoch-managed.
    for node in reversed(nodes):
        if node['policy'] != 'epoch':
            continue
        initialize_config(
            node['repo'],
            repository_id=node['repository'],
            history_store=history_store,
            primary_branch=node['branch'],
            active_remote='origin',
            policy='epoch',
        )
        node['managed'] = True
    for node in nodes:
        node.setdefault('managed', False)

    for node in reversed(nodes):
        if not node['managed']:
            continue
        for child in node['children']:
            repository_id = (
                child['repository'] if child['policy'] == 'epoch' else None
            )
            configure_submodule(
                node['repo'],
                child['path'],
                policy=child['policy'],
                repository_id=repository_id,
            )

    # Validate exact parent/child pins after cloning and normalization.
    for node in nodes:
        repo = pathlib.Path(node['repo'])
        for child in node['children']:
            child_node = by_id[child['repository']]
            actual = _gitlink_oid(repo, node['old_tip'], child['path'])
            if actual != child_node['old_tip']:
                raise EpochSafetyError(
                    f'Sandbox gitlink mismatch at {node["repository"]}:'
                    f'{child["path"]}: {actual} != {child_node["old_tip"]}'
                )


def _sandbox_manifest_path(path: str | os.PathLike[str]) -> pathlib.Path:
    root = pathlib.Path(path).expanduser().resolve()
    if root.is_file():
        return root
    return root / SANDBOX_MANIFEST


def load_sandbox(path: str | os.PathLike[str]) -> dict[str, Any]:
    import yaml

    manifest_path = _sandbox_manifest_path(path)
    if not manifest_path.exists():
        raise EpochError(f'No Git epoch sandbox manifest: {manifest_path}')
    data = yaml.safe_load(manifest_path.read_text()) or {}
    if data.get('format_version') != SANDBOX_FORMAT_VERSION:
        raise EpochError(
            f'Unsupported sandbox format: {data.get("format_version")!r}'
        )
    manifest_root = manifest_path.parent.resolve()
    recorded_root = pathlib.Path(str(data.get('sandbox_root', ''))).resolve()
    if recorded_root != manifest_root:
        raise EpochSafetyError(
            'Sandbox manifest root does not match the directory containing the '
            f'manifest: recorded={recorded_root}, actual={manifest_root}'
        )
    if recorded_root == pathlib.Path(recorded_root.anchor):
        raise EpochSafetyError(f'Refusing filesystem-root sandbox: {recorded_root}')
    return data


def _save_sandbox(data: Mapping[str, Any]) -> pathlib.Path:
    manifest = pathlib.Path(data['sandbox_root']) / SANDBOX_MANIFEST
    _write_yaml(manifest, dict(data))
    return manifest


def assert_sandbox_contained(data: Mapping[str, Any]) -> dict[str, Any]:
    root = pathlib.Path(data['sandbox_root']).resolve()
    violations: list[str] = []
    for key in ['history_store', 'root_repo']:
        path = pathlib.Path(str(data[key])).resolve()
        if not _is_within(path, root):
            violations.append(f'{key}: {path}')
    for node in data.get('nodes', []):
        repo = pathlib.Path(node['repo']).resolve()
        remote = pathlib.Path(node['active_remote']).resolve()
        if not _is_within(repo, root):
            violations.append(f'{node["repository"]} worktree: {repo}')
        if not _is_within(remote, root):
            violations.append(f'{node["repository"]} active remote: {remote}')
        origin_proc = _git(repo, 'remote', 'get-url', 'origin', check=False)
        if origin_proc.returncode:
            violations.append(f'{node["repository"]}: missing origin')
        else:
            actual = pathlib.Path(origin_proc.stdout.strip()).expanduser().resolve()
            if actual != remote or not _is_within(actual, root):
                violations.append(
                    f'{node["repository"]} origin: {actual} (expected {remote})'
                )
        remotes = [r for r in _git_stdout(repo, 'remote').splitlines() if r]
        if remotes != ['origin']:
            violations.append(
                f'{node["repository"]}: sandbox remotes are {remotes}, expected [origin]'
            )
        if node.get('managed'):
            config = load_config(repo, allow_bootstrap=False)
            store = pathlib.Path(config['history_store']).expanduser().resolve()
            if not _is_within(store, root):
                violations.append(
                    f'{node["repository"]} history store escapes sandbox: {store}'
                )
    if violations:
        raise EpochSafetyError(
            'Sandbox containment check failed; refusing publication:\n  - '
            + '\n  - '.join(violations)
        )
    return {
        'sandbox_root': str(root),
        'publication_contained': True,
        'repositories': len(data.get('nodes', [])),
    }


def create_sandbox(
    repo: str | os.PathLike[str] = '.',
    *,
    output: str | os.PathLike[str] | None = None,
    recursive: bool = False,
    all_submodules: str | None = None,
) -> dict[str, Any]:
    source_root = _repo_root(repo)
    if output is None:
        sandbox_root = pathlib.Path(
            tempfile.mkdtemp(prefix='git-well-epoch-sandbox.')
        ).resolve()
    else:
        sandbox_root = pathlib.Path(output).expanduser().resolve()
        if sandbox_root.exists():
            if any(sandbox_root.iterdir()):
                raise EpochSafetyError(
                    f'Sandbox output directory must be absent or empty: {sandbox_root}'
                )
        else:
            sandbox_root.mkdir(parents=True)

    nodes = _discover_source_graph(
        source_root,
        recursive=recursive,
        all_submodules=all_submodules,
    )
    work_parent = sandbox_root / 'work'
    active_root = sandbox_root / 'active-remotes'
    work_parent.mkdir()
    active_root.mkdir()
    root_work = work_parent / source_root.name

    for node in nodes:
        _clone_node(node, work_root=root_work, active_root=active_root)

    # _clone_node interprets '.' as work_root, so relocate the root node from
    # work/ambition rather than work itself.
    root_node = nodes[0]
    if pathlib.Path(root_node['repo']).resolve() != root_work.resolve():
        # This should only happen if the root path calculation above changes.
        raise AssertionError((root_node['repo'], root_work))

    history_store = sandbox_root / 'history.git'
    _configure_epoch_graph(nodes, history_store)
    data: dict[str, Any] = {
        'format_version': SANDBOX_FORMAT_VERSION,
        'sandbox_root': str(sandbox_root),
        'source_root': str(source_root),
        'root_repository': root_node['repository'],
        'root_repo': root_node['repo'],
        'history_store': str(history_store),
        'recursive': bool(recursive),
        'all_submodules': all_submodules,
        'state': 'created',
        'nodes': nodes,
    }
    containment = assert_sandbox_contained(data)
    data['containment'] = containment
    manifest = _save_sandbox(data)
    return {
        'sandbox': str(sandbox_root),
        'manifest': str(manifest),
        'root_repo': data['root_repo'],
        'history_store': data['history_store'],
        'repositories': [
            {
                'repository': node['repository'],
                'relpath': node['relpath'],
                'policy': node['policy'],
                'branch': node['branch'],
                'active_remote': node['active_remote'],
                'source_origin': node['source_origin'],
            }
            for node in nodes
        ],
        'publication_contained': containment['publication_contained'],
    }


def verify_sandbox(path: str | os.PathLike[str]) -> dict[str, Any]:
    data = load_sandbox(path)
    assert_sandbox_contained(data)
    nodes = data['nodes']
    by_id = {node['repository']: node for node in nodes}
    fresh_root = pathlib.Path(data['sandbox_root']) / 'verification' / 'fresh'
    if fresh_root.exists():
        shutil.rmtree(fresh_root)
    fresh_root.mkdir(parents=True)

    results: dict[str, Any] = {}
    for node in nodes:
        if not node.get('managed'):
            continue
        repo = pathlib.Path(node['repo'])
        branch = node['branch']
        old = node['old_tip']
        new = _resolve_ref(repo, f'refs/heads/{branch}')
        if new == old:
            raise EpochSafetyError(
                f'Sandbox repository has not been published: {node["repository"]}'
            )
        parents = _git_stdout(repo, 'rev-list', '--parents', '-n', '1', new).split()
        if parents != [new]:
            raise EpochSafetyError(
                f'Successor is not a root commit for {node["repository"]}: {parents}'
            )
        epoch_children = [
            child for child in node['children'] if child['policy'] == 'epoch'
        ]
        if not epoch_children and _tree_oid(repo, new) != node['old_tree']:
            raise EpochSafetyError(
                f'Leaf successor tree changed for {node["repository"]}'
            )
        for child in node['children']:
            actual = _gitlink_oid(repo, new, child['path'])
            child_node = by_id[child['repository']]
            if child['policy'] == 'epoch':
                expected = _resolve_ref(
                    pathlib.Path(child_node['repo']),
                    f"refs/heads/{child_node['branch']}",
                )
            else:
                expected = child_node['old_tip']
            if actual != expected:
                raise EpochSafetyError(
                    f'Gitlink mismatch after sandbox publication at '
                    f'{node["repository"]}:{child["path"]}: {actual} != {expected}'
                )

        remote_tip = _run(
            [
                'git',
                '--git-dir',
                node['active_remote'],
                'rev-parse',
                f'refs/heads/{branch}',
            ]
        ).stdout.strip()
        if remote_tip != new:
            raise EpochSafetyError(
                f'Sandbox active remote mismatch for {node["repository"]}: '
                f'{remote_tip} != {new}'
            )

        fresh = fresh_root / node['repository']
        _run(
            [
                'git',
                'clone',
                '--no-local',
                '--branch',
                branch,
                node['active_remote'],
                fresh,
            ]
        )
        fresh_head = _resolve_ref(fresh, 'HEAD')
        if fresh_head != new:
            raise EpochSafetyError(
                f'Fresh sandbox clone mismatch for {node["repository"]}'
            )
        retired = _git(fresh, 'cat-file', '-e', f'{old}^{{commit}}', check=False)
        if retired.returncode == 0:
            raise EpochSafetyError(
                f'Retired tip leaked into fresh clone for {node["repository"]}: {old}'
            )
        deep = verify(repo, deep=True)
        results[node['repository']] = {
            'old_tip': old,
            'new_tip': new,
            'fresh_clone': str(fresh),
            'retired_tip_present': False,
            'deep_verified': all(
                item.get('verified', False)
                for item in deep.get('deep', {}).get('boundaries', [])
            ),
        }
        node['new_tip'] = new
        node['new_tree'] = _tree_oid(repo, new)

    data['state'] = 'verified'
    data['verification'] = results
    _save_sandbox(data)
    return {
        'sandbox': data['sandbox_root'],
        'status': 'verified',
        'repositories': results,
        'publication_contained': True,
    }


def plan_sandbox(
    path: str | os.PathLike[str],
    *,
    bundle: bool = False,
) -> dict[str, Any]:
    data = load_sandbox(path)
    assert_sandbox_contained(data)
    root_repo = pathlib.Path(data['root_repo'])
    sandbox_root = pathlib.Path(data['sandbox_root'])
    plan_path = sandbox_root / 'checkpoint.yaml'
    if data.get('state') not in {'created', 'planned'}:
        raise EpochSafetyError(
            f'Sandbox is not ready to plan: state={data.get("state")!r}'
        )
    plan = build_plan(
        root_repo,
        recursive=bool(data.get('recursive')),
        bundle=bundle,
        bundle_dir=sandbox_root / 'bundles' if bundle else None,
    )
    save_plan(plan, plan_path)
    data['state'] = 'planned'
    data['plan'] = str(plan_path)
    _save_sandbox(data)
    return {
        'sandbox': str(sandbox_root),
        'status': 'planned',
        'plan_path': str(plan_path),
        'plan': plan,
    }


def apply_sandbox(path: str | os.PathLike[str]) -> dict[str, Any]:
    data = load_sandbox(path)
    assert_sandbox_contained(data)
    if data.get('state') not in {'planned', 'prepared'}:
        raise EpochSafetyError(
            f'Sandbox is not ready to apply: state={data.get("state")!r}'
        )
    plan_path = pathlib.Path(data.get('plan') or '')
    if not plan_path.exists():
        raise EpochError('Sandbox has no saved checkpoint plan')
    plan = load_plan(plan_path)
    if data.get('state') == 'prepared':
        return {
            'sandbox': data['sandbox_root'],
            'status': 'prepared',
            'plan_path': str(plan_path),
        }
    result = apply_plan(plan, publish=False)
    data['state'] = 'prepared'
    _save_sandbox(data)
    return {
        'sandbox': data['sandbox_root'],
        'status': 'prepared',
        'plan_path': str(plan_path),
        'result': result,
    }


def publish_sandbox(
    path: str | os.PathLike[str],
    *,
    fresh_clone: bool = True,
) -> dict[str, Any]:
    data = load_sandbox(path)
    if data.get('state') not in {'prepared', 'published'}:
        raise EpochSafetyError(
            f'Sandbox is not ready to publish: state={data.get("state")!r}'
        )
    plan_path = pathlib.Path(data.get('plan') or '')
    if not plan_path.exists():
        raise EpochError('Sandbox has no saved checkpoint plan')
    plan = load_plan(plan_path)

    # Re-read actual remotes/config immediately before crossing the destructive
    # boundary. This is the hard guard that makes sandbox publication local.
    assert_sandbox_contained(data)
    if data.get('state') == 'published':
        return {
            'sandbox': data['sandbox_root'],
            'status': 'published',
            'plan_path': str(plan_path),
        }
    published = publish_plan(plan, fresh_clone=fresh_clone)
    data['state'] = 'published'
    _save_sandbox(data)
    return {
        'sandbox': data['sandbox_root'],
        'status': 'published',
        'plan_path': str(plan_path),
        'result': published,
    }


def run_sandbox(
    path: str | os.PathLike[str],
    *,
    bundle: bool = False,
) -> dict[str, Any]:
    data = load_sandbox(path)
    assert_sandbox_contained(data)
    sandbox_root = pathlib.Path(data['sandbox_root'])

    state = data.get('state')
    planned = None
    prepared = None
    if state == 'created':
        planned = plan_sandbox(sandbox_root, bundle=bundle)
        state = 'planned'
    if state == 'planned':
        prepared = apply_sandbox(sandbox_root)
        state = 'prepared'
    if state == 'prepared':
        published = publish_sandbox(sandbox_root, fresh_clone=True)
    elif state in {'published', 'verified'}:
        published = {'status': state}
    else:
        raise EpochSafetyError(f'Cannot run sandbox from state {state!r}')
    verification = verify_sandbox(sandbox_root)
    return {
        'sandbox': str(sandbox_root),
        'planned': planned,
        'prepared': prepared,
        'published': published,
        'verification': verification,
    }


def inspect_sandbox(path: str | os.PathLike[str]) -> dict[str, Any]:
    data = load_sandbox(path)
    containment = assert_sandbox_contained(data)
    return {
        'sandbox': data['sandbox_root'],
        'state': data.get('state'),
        'source_root': data['source_root'],
        'root_repo': data['root_repo'],
        'history_store': data['history_store'],
        'recursive': data.get('recursive'),
        'all_submodules': data.get('all_submodules'),
        'containment': containment,
        'repositories': [
            {
                'repository': node['repository'],
                'relpath': node['relpath'],
                'policy': node['policy'],
                'managed': node.get('managed', False),
                'branch': node['branch'],
                'old_tip': node['old_tip'],
                'new_tip': node.get('new_tip'),
                'active_remote': node['active_remote'],
                'source_origin': node.get('source_origin'),
            }
            for node in data['nodes']
        ],
    }
