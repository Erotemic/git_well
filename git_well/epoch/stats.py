from __future__ import annotations

import contextlib
import os
import pathlib
from collections import Counter
from typing import Any, Mapping

from .core import (
    EpochError,
    EpochSafetyError,
    HistoryStore,
    _directory_disk_usage,
    _directory_size,
    _format_bytes,
    _git_dir,
    _git_stdout,
    _manifest_validate,
    _reachable_size,
    _repo_root,
    _resolve_ref,
    _run,
    load_config,
)


def _history_store_object_set(
    store_path: pathlib.Path, refs: list[Mapping[str, Any]]
) -> set[str]:
    if not refs:
        return set()
    refnames = [str(item['archive']) for item in refs]
    proc = _run(
        [
            'git',
            '--git-dir',
            store_path,
            'rev-list',
            '--objects',
            '--no-object-names',
            *refnames,
        ]
    )
    objects = {line.strip() for line in proc.stdout.splitlines() if line.strip()}
    # Keep the direct ref targets as well. This is important for annotated tag
    # objects even if a revision walk peels through them to their commits.
    objects.update(str(item['oid']) for item in refs)
    return objects


def _history_store_object_sizes(
    store_path: pathlib.Path, objects: set[str]
) -> dict[str, tuple[int, int]]:
    if not objects:
        return {}
    ordered = sorted(objects)
    proc = _run(
        [
            'git',
            '--git-dir',
            store_path,
            'cat-file',
            '--batch-check=%(objectname) %(objectsize) %(objectsize:disk)',
        ],
        input='\n'.join(ordered) + '\n',
    )
    result: dict[str, tuple[int, int]] = {}
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) != 3:
            raise EpochSafetyError(
                f'Unable to measure archived object in {store_path}: {line!r}'
            )
        oid, logical_text, disk_text = parts
        try:
            result[oid] = (int(logical_text), int(disk_text))
        except ValueError as ex:
            raise EpochSafetyError(
                f'Unable to measure archived object in {store_path}: {line!r}'
            ) from ex
    missing = objects.difference(result)
    if missing:
        raise EpochSafetyError(
            f'History store is missing {len(missing)} measured objects'
        )
    return result


def _count_objects_stats(store_path: pathlib.Path) -> dict[str, Any]:
    text = _run(
        ['git', '--git-dir', store_path, 'count-objects', '-v']
    ).stdout
    raw: dict[str, int] = {}
    for line in text.splitlines():
        if ': ' not in line:
            continue
        key, value = line.split(': ', 1)
        with contextlib.suppress(ValueError):
            raw[key] = int(value)
    result: dict[str, Any] = dict(raw)
    # Git reports these fields in KiB. Preserve the raw counters and provide
    # byte values beside them so callers do not need to remember that detail.
    for key in ['size', 'size-pack', 'size-garbage']:
        if key in raw:
            byte_key = key.replace('-', '_') + '_bytes'
            result[byte_key] = raw[key] * 1024
            result[byte_key + '_human'] = _format_bytes(raw[key] * 1024)
    return result


def _epoch_bundle_measurement(
    store_path: pathlib.Path,
    repository_id: str,
    epoch: Mapping[str, Any],
) -> dict[str, Any] | None:
    bundle = epoch.get('verification', {}).get('bundle')
    if not isinstance(bundle, Mapping):
        return None
    filename = bundle.get('filename')
    bytes_value = bundle.get('bytes')
    path: pathlib.Path | None = None
    if filename:
        candidates = [
            store_path.parent
            / f'{store_path.name}.bundles'
            / repository_id
            / str(filename),
            store_path.parent / 'bundles' / repository_id / str(filename),
        ]
        for candidate in candidates:
            if candidate.exists():
                path = candidate.resolve()
                bytes_value = candidate.stat().st_size
                break
    if bytes_value is None and path is None:
        return {
            'filename': filename,
            'sha256': bundle.get('sha256'),
            'bytes': None,
            'bytes_human': None,
            'path': None,
        }
    measured = int(bytes_value) if bytes_value is not None else 0
    return {
        'filename': filename,
        'sha256': bundle.get('sha256'),
        'bytes': measured,
        'bytes_human': _format_bytes(measured),
        'path': str(path) if path is not None else None,
    }


def history_store_stats(repo: str | os.PathLike[str] = '.') -> dict[str, Any]:
    """Measure active history and every archived epoch in a local store.

    ``reachable_object_disk_bytes`` measures the compressed object
    representations reachable from one epoch's immutable archive refs. Objects
    can be shared between epochs, so those per-epoch values are not additive.
    ``exclusive_object_disk_bytes`` counts only objects used by one archived
    epoch record. A standalone bundle, when available, is the most direct
    independently-restorable size for that epoch.
    """
    repo_path = _repo_root(repo)
    config = load_config(repo_path)
    store = HistoryStore(config['history_store'], repo_path)
    if not store.is_local:
        raise EpochError('git epoch stats currently requires a local history store')

    store_path = store.local_path
    tip = _resolve_ref(
        repo_path, f"refs/heads/{config['primary_branch']}"
    )
    active_reachable = _reachable_size(repo_path, tip)
    active_git_bytes = _directory_size(_git_dir(repo_path))
    active = {
        'repository': config['repository'],
        'epoch': config['active_epoch'],
        'tip': tip,
        'commits': int(_git_stdout(repo_path, 'rev-list', '--count', tip).strip()),
        'reachable_object_disk_bytes': active_reachable,
        'reachable_object_disk_bytes_human': _format_bytes(active_reachable),
        'git_directory_bytes': active_git_bytes,
        'git_directory_bytes_human': _format_bytes(active_git_bytes),
    }

    if not store_path.exists():
        return {
            'active': active,
            'history_store': {
                'path': str(store_path),
                'state': 'not-initialized',
                'directory_bytes': 0,
                'directory_bytes_human': '0 B',
                'directory_disk_bytes': 0,
                'directory_disk_bytes_human': '0 B',
            },
            'repositories': {},
            'totals': {
                'archived_epochs': 0,
                'unique_archived_objects': 0,
                'unique_archived_object_disk_bytes': 0,
                'unique_archived_object_disk_bytes_human': '0 B',
                'standalone_bundle_bytes': 0,
                'standalone_bundle_bytes_human': '0 B',
            },
        }

    manifest, meta_oid = store.load_manifest()
    _manifest_validate(manifest)
    records: list[dict[str, Any]] = []
    object_sets: dict[tuple[str, int], set[str]] = {}
    for repository_id, epochs in manifest.get('epochs', {}).items():
        for epoch in epochs:
            number = int(epoch['number'])
            key = (repository_id, number)
            objects = _history_store_object_set(store_path, epoch.get('refs', []))
            object_sets[key] = objects
            records.append(
                {
                    'repository': repository_id,
                    'epoch': epoch,
                    'objects': objects,
                }
            )

    all_objects: set[str] = set()
    frequency: Counter[str] = Counter()
    for objects in object_sets.values():
        all_objects.update(objects)
        frequency.update(objects)
    sizes = _history_store_object_sizes(store_path, all_objects)

    repositories: dict[str, list[dict[str, Any]]] = {}
    bundle_total = 0
    for record in records:
        repository_id = record['repository']
        epoch = record['epoch']
        objects = record['objects']
        exclusive = {oid for oid in objects if frequency[oid] == 1}
        logical_bytes = sum(sizes[oid][0] for oid in objects)
        disk_bytes = sum(sizes[oid][1] for oid in objects)
        exclusive_bytes = sum(sizes[oid][1] for oid in exclusive)
        bundle_info = _epoch_bundle_measurement(store_path, repository_id, epoch)
        if bundle_info and bundle_info.get('bytes') is not None:
            bundle_total += int(bundle_info['bytes'])
        main_tip = str(epoch['main_tip'])
        commit_count = int(
            _run(
                [
                    'git',
                    '--git-dir',
                    store_path,
                    'rev-list',
                    '--count',
                    main_tip,
                ]
            ).stdout.strip()
        )
        item = {
            'number': int(epoch['number']),
            'state': epoch.get('state', 'committed'),
            'main_tip': main_tip,
            'commits': commit_count,
            'refs': len(epoch.get('refs', [])),
            'objects': len(objects),
            'reachable_object_logical_bytes': logical_bytes,
            'reachable_object_logical_bytes_human': _format_bytes(logical_bytes),
            'reachable_object_disk_bytes': disk_bytes,
            'reachable_object_disk_bytes_human': _format_bytes(disk_bytes),
            'exclusive_objects': len(exclusive),
            'exclusive_object_disk_bytes': exclusive_bytes,
            'exclusive_object_disk_bytes_human': _format_bytes(exclusive_bytes),
            'shared_object_disk_bytes': disk_bytes - exclusive_bytes,
            'shared_object_disk_bytes_human': _format_bytes(
                disk_bytes - exclusive_bytes
            ),
            'standalone_bundle': bundle_info,
        }
        repositories.setdefault(repository_id, []).append(item)

    unique_disk_bytes = sum(sizes[oid][1] for oid in all_objects)
    directory_bytes = _directory_size(store_path)
    directory_disk_bytes = _directory_disk_usage(store_path)
    overhead = directory_bytes - unique_disk_bytes
    return {
        'active': active,
        'history_store': {
            'path': str(store_path),
            'state': 'available',
            'manifest': meta_oid,
            'directory_bytes': directory_bytes,
            'directory_bytes_human': _format_bytes(directory_bytes),
            'directory_disk_bytes': directory_disk_bytes,
            'directory_disk_bytes_human': _format_bytes(directory_disk_bytes),
            'count_objects': _count_objects_stats(store_path),
        },
        'repositories': repositories,
        'totals': {
            'archived_epochs': len(records),
            'unique_archived_objects': len(all_objects),
            'unique_archived_object_disk_bytes': unique_disk_bytes,
            'unique_archived_object_disk_bytes_human': _format_bytes(
                unique_disk_bytes
            ),
            'history_store_overhead_bytes': overhead,
            'history_store_overhead_bytes_human': _format_bytes(max(overhead, 0)),
            'standalone_bundle_bytes': bundle_total,
            'standalone_bundle_bytes_human': _format_bytes(bundle_total),
        },
        'notes': [
            'Per-epoch reachable object sizes can overlap and are not additive.',
            (
                'Exclusive bytes are objects reachable from only one archived '
                'epoch record.'
            ),
            (
                'Standalone bundle bytes are the best independently-restorable '
                'per-epoch size when a bundle exists.'
            ),
            (
                'History-store directory file bytes measure stored content; '
                'directory disk bytes measure allocated filesystem space.'
            ),
            (
                'The active working checkout can retain unreachable retired '
                'objects; use a fresh clone size for distributed active history.'
            ),
        ],
    }
