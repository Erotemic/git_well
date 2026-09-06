from __future__ import annotations

import os
import pathlib
from collections.abc import Mapping
from typing import Any

import yaml


PUBLIC_LOCATOR_FILENAME = '.git-epoch.yaml'
PUBLIC_LOCATOR_FORMAT_VERSION = 1


def _yaml_dump(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, width=100)


def _yaml_load_text(text: str) -> dict[str, Any]:
    from .core import EpochError

    data = yaml.safe_load(text)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise EpochError('Expected .git-epoch.yaml to contain a YAML mapping')
    return data


def public_locator_path(repo: str | os.PathLike[str]) -> pathlib.Path:
    from .core import _repo_root

    return _repo_root(repo) / PUBLIC_LOCATOR_FILENAME


def _validate_locator(locator: Mapping[str, Any]) -> None:
    from .core import EpochError, _validate_repository_id

    if locator.get('format_version') != PUBLIC_LOCATOR_FORMAT_VERSION:
        raise EpochError(
            'Unsupported public Git epoch locator format: '
            f'{locator.get("format_version")!r}'
        )
    repository = locator.get('repository')
    if not isinstance(repository, str):
        raise EpochError('Public Git epoch locator is missing repository')
    _validate_repository_id(repository)
    history = locator.get('history_store')
    if not isinstance(history, Mapping):
        raise EpochError('Public Git epoch locator is missing history_store')
    store_id = history.get('id')
    url = history.get('url')
    if not isinstance(store_id, str) or not store_id:
        raise EpochError('Public Git epoch locator is missing history_store.id')
    _validate_repository_id(store_id)
    if not isinstance(url, str) or not url.strip():
        raise EpochError('Public Git epoch locator is missing history_store.url')
    from .core import _looks_like_local_path

    if _looks_like_local_path(url):
        raise EpochError(
            'history_store.url in .git-epoch.yaml must be clone-portable. Use a '
            'Git URL (including file:// for local rehearsals), not a filesystem '
            f'path: {url!r}'
        )
    browse_url = history.get('browse_url')
    if browse_url is not None and not isinstance(browse_url, str):
        raise EpochError('history_store.browse_url must be a string when present')


def read_public_locator(
    repo: str | os.PathLike[str],
    *,
    commit: str | None = 'HEAD',
    required: bool = True,
) -> dict[str, Any] | None:
    """Read the committed clone-visible Git epoch locator.

    ``commit='HEAD'`` deliberately reads the tracked version rather than an
    uncommitted worktree edit. Pass ``commit=None`` only when inspecting a file
    that is intentionally being prepared before its first commit.
    """
    from .core import EpochError, _git, _repo_root

    repo_path = _repo_root(repo)
    if commit is None:
        path = repo_path / PUBLIC_LOCATOR_FILENAME
        if not path.exists():
            if required:
                raise EpochError(f'No public Git epoch locator at {path}')
            return None
        locator = _yaml_load_text(path.read_text())
    else:
        proc = _git(
            repo_path,
            'show',
            f'{commit}:{PUBLIC_LOCATOR_FILENAME}',
            check=False,
        )
        if proc.returncode:
            worktree_path = repo_path / PUBLIC_LOCATOR_FILENAME
            if required:
                if worktree_path.exists():
                    raise EpochError(
                        f'{PUBLIC_LOCATOR_FILENAME} exists in the worktree but is '
                        f'not committed at {commit}. Commit it before an epoch '
                        'checkpoint so future clones can discover the archive.'
                    )
                raise EpochError(
                    f'No committed {PUBLIC_LOCATOR_FILENAME} at {commit}'
                )
            return None
        locator = _yaml_load_text(proc.stdout)
    _validate_locator(locator)
    return locator


def write_public_locator(
    repo: str | os.PathLike[str],
    *,
    repository_id: str,
    history_store_id: str,
    history_url: str,
    browse_url: str | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Write the tracked locator that survives ordinary clones."""
    from .core import EpochError, _repo_root, _validate_repository_id

    repo_path = _repo_root(repo)
    _validate_repository_id(repository_id)
    _validate_repository_id(history_store_id)
    locator: dict[str, Any] = {
        'format_version': PUBLIC_LOCATOR_FORMAT_VERSION,
        'repository': repository_id,
        'history_store': {
            'id': history_store_id,
            'url': history_url,
        },
    }
    if browse_url:
        locator['history_store']['browse_url'] = browse_url
    _validate_locator(locator)

    path = repo_path / PUBLIC_LOCATOR_FILENAME
    if path.exists():
        existing = _yaml_load_text(path.read_text())
        _validate_locator(existing)
        if existing == locator:
            return {
                'path': str(path),
                'changed': False,
                'locator': locator,
            }
        if not overwrite:
            raise EpochError(
                f'Public Git epoch locator already exists with different content: '
                f'{path}. Pass overwrite=True only when intentionally moving or '
                'changing the archive locator.'
            )
    path.write_text(_yaml_dump(locator))
    return {'path': str(path), 'changed': True, 'locator': locator}


def validate_public_locator_against_config(
    repo: str | os.PathLike[str],
    config: Mapping[str, Any],
    *,
    required: bool,
) -> dict[str, Any] | None:
    """Validate committed public metadata against local management config."""
    from .core import EpochSafetyError

    locator = read_public_locator(repo, required=required)
    if locator is None:
        return None
    mismatches: dict[str, tuple[Any, Any]] = {}
    if locator['repository'] != config.get('repository'):
        mismatches['repository'] = (
            locator['repository'],
            config.get('repository'),
        )
    locator_store_id = locator['history_store']['id']
    config_store_id = config.get('history_store_id')
    if locator_store_id != config_store_id:
        mismatches['history_store.id'] = (locator_store_id, config_store_id)
    if mismatches:
        raise EpochSafetyError(
            f'{PUBLIC_LOCATOR_FILENAME} conflicts with local epoch configuration: '
            f'{mismatches}'
        )
    return locator


def _root_public_context(
    repo: str | os.PathLike[str],
) -> tuple[pathlib.Path, str | None, str, dict[str, str], dict[str, Any]]:
    from .core import (
        EpochSafetyError,
        _current_branch,
        _parse_epoch_trailers,
        _repo_root,
        _resolve_ref,
        _root_commits,
    )

    repo_path = _repo_root(repo)
    branch = _current_branch(repo_path)
    tip = (
        _resolve_ref(repo_path, f'refs/heads/{branch}')
        if branch is not None
        else _resolve_ref(repo_path, 'HEAD')
    )
    roots = _root_commits(repo_path, tip)
    if len(roots) != 1:
        raise EpochSafetyError(
            'An epoch-managed active branch must have exactly one root; '
            f'found {roots}'
        )
    root = roots[0]
    trailers = _parse_epoch_trailers(repo_path, root)
    required_trailers = [
        'Git-Epoch-Repository',
        'Git-Epoch-Number',
        'Git-Epoch-Predecessor',
        'Git-Epoch-Predecessor-Tree',
        'Git-Epoch-History-Store',
    ]
    missing = [key for key in required_trailers if not trailers.get(key)]
    if missing:
        raise EpochSafetyError(
            'Public epoch metadata is present, but the active root lacks required '
            f'Git epoch trailers: {missing}'
        )
    locator = read_public_locator(repo_path, required=True)
    assert locator is not None
    mismatches = {}
    if locator['repository'] != trailers['Git-Epoch-Repository']:
        mismatches['repository'] = (
            locator['repository'],
            trailers['Git-Epoch-Repository'],
        )
    if locator['history_store']['id'] != trailers['Git-Epoch-History-Store']:
        mismatches['history_store.id'] = (
            locator['history_store']['id'],
            trailers['Git-Epoch-History-Store'],
        )
    if mismatches:
        raise EpochSafetyError(
            f'{PUBLIC_LOCATOR_FILENAME} conflicts with successor-root trailers: '
            f'{mismatches}'
        )
    return repo_path, branch, tip, trailers, locator


def resolve_public_epoch_context(
    repo: str | os.PathLike[str],
    *,
    history_store: str | os.PathLike[str] | None = None,
    active_remote: str = 'origin',
    verify_remote: bool = True,
) -> dict[str, Any]:
    """Resolve a clone-visible epoch context without writing local config."""
    from .core import (
        EpochError,
        EpochSafetyError,
        FORMAT_VERSION,
        HistoryStore,
        _default_remote_url,
        _discover_submodules,
        _manifest_validate,
        _normalize_history_store,
        _tree_oid,
    )

    repo_path, branch, tip, trailers, locator = _root_public_context(repo)
    try:
        active_epoch = int(trailers['Git-Epoch-Number'])
    except ValueError as ex:
        raise EpochSafetyError(
            f'Invalid Git-Epoch-Number trailer: {trailers["Git-Epoch-Number"]!r}'
        ) from ex
    if active_epoch <= 0:
        raise EpochSafetyError(
            f'Public epoch metadata requires a successor epoch; got {active_epoch}'
        )

    public_url = locator['history_store']['url']
    store_spec = _normalize_history_store(
        repo_path,
        os.fspath(history_store) if history_store is not None else public_url,
    )
    repository_id = trailers['Git-Epoch-Repository']
    repository_record: dict[str, Any] = {}
    meta_oid: str | None = None
    if verify_remote:
        store = HistoryStore(store_spec, repo_path)
        store.ensure()
        manifest, meta_oid = store.load_manifest()
        _manifest_validate(manifest)
        manifest_store_id = manifest.get('history_store', {}).get('id')
        expected_store_id = locator['history_store']['id']
        if manifest_store_id != expected_store_id:
            raise EpochSafetyError(
                'History-store identity conflicts with the public locator: '
                f'{manifest_store_id!r} != {expected_store_id!r}'
            )
        repository_record = dict(
            manifest.get('repositories', {}).get(repository_id, {})
        )
        if not repository_record:
            raise EpochSafetyError(
                f'History store has no repository record for {repository_id!r}'
            )
        boundaries = [
            boundary
            for boundary in manifest.get('boundaries', [])
            if (
                boundary.get('repository') == repository_id
                and boundary.get('state', 'committed') == 'committed'
                and int(boundary.get('successor', {}).get('epoch', -1))
                == active_epoch
            )
        ]
        if len(boundaries) != 1:
            raise EpochSafetyError(
                f'Expected exactly one committed manifest boundary leading to '
                f'{repository_id}:{active_epoch}; found {len(boundaries)}'
            )
        boundary = boundaries[0]
        from .core import _root_commits

        active_root = _root_commits(repo_path, tip)[0]
        expected_pairs = {
            'successor.commit': (
                boundary.get('successor', {}).get('commit'),
                active_root,
            ),
            'successor.tree': (
                boundary.get('successor', {}).get('tree'),
                _tree_oid(repo_path, active_root),
            ),
            'previous.commit': (
                boundary.get('previous', {}).get('commit'),
                trailers['Git-Epoch-Predecessor'],
            ),
            'previous.tree': (
                boundary.get('previous', {}).get('tree'),
                trailers['Git-Epoch-Predecessor-Tree'],
            ),
        }
        lineage_mismatches = {
            key: pair for key, pair in expected_pairs.items() if pair[0] != pair[1]
        }
        if lineage_mismatches:
            raise EpochSafetyError(
                'Public history-store manifest does not match the active successor '
                f'root lineage: {lineage_mismatches}'
            )

    submodules = {
        item['path']: {
            'repository': None,
            'policy': 'external',
            'name': item['name'],
            'url': item['url'],
        }
        for item in _discover_submodules(repo_path, tip)
    }
    for path, item in repository_record.get('submodules', {}).items():
        if path in submodules:
            submodules[path].update(item)

    config = {
        'format_version': FORMAT_VERSION,
        'repository': repository_id,
        'policy': repository_record.get('policy', 'epoch'),
        'history_store': store_spec,
        'history_store_id': locator['history_store']['id'],
        'primary_branch': repository_record.get('primary_branch') or branch or '(detached)',
        'active_epoch': active_epoch,
        'active_remote': active_remote,
        'active_url': _default_remote_url(repo_path, active_remote),
        'submodules': submodules,
    }
    return {
        'config': config,
        'locator': locator,
        'locator_path': str(repo_path / PUBLIC_LOCATOR_FILENAME),
        'meta_oid': meta_oid,
        'public_history_url': public_url,
        'observed_branch': branch,
        'observed_tip': tip,
    }


def verify_public_archive_visibility(
    repo: str | os.PathLike[str],
    *,
    history_store_id: str,
    manifest_oid: str,
    archive_refs: Mapping[str, str],
) -> dict[str, Any] | None:
    """Prove the committed public URL can see a prepared remote archive."""
    from .core import (
        EpochSafetyError,
        HistoryStore,
        _manifest_validate,
        _repo_root,
    )

    repo_path = _repo_root(repo)
    locator = read_public_locator(repo_path, required=False)
    if locator is None:
        return None
    locator_store_id = locator['history_store']['id']
    if locator_store_id != history_store_id:
        raise EpochSafetyError(
            f'{PUBLIC_LOCATOR_FILENAME} history-store identity changed after '
            f'planning: {locator_store_id!r} != {history_store_id!r}'
        )
    public_store = HistoryStore(locator['history_store']['url'], repo_path)
    actual = public_store.ls_refs()
    expected = {'refs/meta/main': manifest_oid, **dict(archive_refs)}
    mismatches = {
        ref: (actual.get(ref), oid)
        for ref, oid in expected.items()
        if actual.get(ref) != oid
    }
    if mismatches:
        raise EpochSafetyError(
            'The committed public history URL does not expose the prepared '
            f'archive refs: {mismatches}'
        )
    manifest, public_meta = public_store.load_manifest()
    _manifest_validate(manifest)
    if public_meta != manifest_oid:
        raise EpochSafetyError(
            'The committed public history URL resolved a different manifest: '
            f'{public_meta} != {manifest_oid}'
        )
    manifest_store_id = manifest.get('history_store', {}).get('id')
    if manifest_store_id != history_store_id:
        raise EpochSafetyError(
            'The committed public history URL resolved the wrong logical store: '
            f'{manifest_store_id!r} != {history_store_id!r}'
        )
    return {
        'url': locator['history_store']['url'],
        'manifest': manifest_oid,
        'refs_verified': len(expected),
    }


def attach_history_store(
    repo: str | os.PathLike[str] = '.',
    *,
    history_store: str | os.PathLike[str] | None = None,
    active_remote: str = 'origin',
    overwrite: bool = False,
) -> dict[str, Any]:
    """Attach clone-local management state to the public history locator."""
    from .core import EpochError, _config_path, _repo_root, _write_yaml

    repo_path = _repo_root(repo)
    config_path = _config_path(repo_path)
    if config_path.exists() and not overwrite:
        raise EpochError(
            f'Epoch configuration already exists: {config_path}. Use '
            '--overwrite only when deliberately replacing local attachment state.'
        )
    context = resolve_public_epoch_context(
        repo_path,
        history_store=history_store,
        active_remote=active_remote,
        verify_remote=True,
    )
    _write_yaml(config_path, context['config'])
    return {
        'status': 'attached',
        'config_path': str(config_path),
        'repository': context['config']['repository'],
        'active_epoch': context['config']['active_epoch'],
        'history_store': context['config']['history_store'],
        'history_store_id': context['config']['history_store_id'],
        'public_locator': context['locator_path'],
        'public_history_url': context['public_history_url'],
        'manifest': context['meta_oid'],
    }
