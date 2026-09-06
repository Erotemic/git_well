from __future__ import annotations

import configparser
import contextlib
import dataclasses
import datetime as datetime_mod
import hashlib
import os
import pathlib
import re
import shlex
import subprocess
import tempfile
import uuid
from collections.abc import Iterable, Mapping
from typing import Any

import yaml


FORMAT_VERSION = 1
CONFIG_RELATIVE_PATH = pathlib.Path('epoch') / 'config.yaml'
TRANSACTION_RELATIVE_PATH = pathlib.Path('epoch') / 'transactions'
_SAFE_REPOSITORY_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')


class EpochError(RuntimeError):
    """Base error raised by git-epoch operations."""


class EpochPlanStaleError(EpochError):
    """Raised when a plan no longer matches repository state."""


class EpochSafetyError(EpochError):
    """Raised when a destructive operation would violate an invariant."""


def _utc_now() -> datetime_mod.datetime:
    return datetime_mod.datetime.now(datetime_mod.timezone.utc)


def _isoformat(value: datetime_mod.datetime | None = None) -> str:
    if value is None:
        value = _utc_now()
    return value.astimezone(datetime_mod.timezone.utc).isoformat().replace(
        '+00:00', 'Z'
    )


def _yaml_dump(data: Any) -> str:
    return yaml.safe_dump(data, sort_keys=False, width=100)


def _yaml_load_text(text: str) -> dict[str, Any]:
    data = yaml.safe_load(text)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise EpochError('Expected a YAML mapping')
    return data


def _read_yaml(path: pathlib.Path) -> dict[str, Any]:
    return _yaml_load_text(path.read_text())


def _write_yaml(path: pathlib.Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(_yaml_dump(data))


def _run(
    args: Iterable[str | os.PathLike[str]],
    *,
    cwd: pathlib.Path | str | None = None,
    env: Mapping[str, str] | None = None,
    input: str | bytes | None = None,
    check: bool = True,
    text: bool | None = None,
) -> subprocess.CompletedProcess[Any]:
    argv = [os.fspath(arg) for arg in args]
    if text is None:
        text = not isinstance(input, bytes)
    final_env = os.environ.copy()
    if env:
        final_env.update({str(k): str(v) for k, v in env.items()})
    proc = subprocess.run(
        argv,
        cwd=os.fspath(cwd) if cwd is not None else None,
        env=final_env,
        input=input,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
        check=False,
    )
    if check and proc.returncode:
        stdout = proc.stdout.decode() if isinstance(proc.stdout, bytes) else proc.stdout
        stderr = proc.stderr.decode() if isinstance(proc.stderr, bytes) else proc.stderr
        command = ' '.join(shlex.quote(p) for p in argv)
        raise EpochError(
            f'Command failed ({proc.returncode}): {command}\n'
            f'stdout:\n{stdout}\nstderr:\n{stderr}'
        )
    return proc


def _git(
    repo: pathlib.Path,
    *args: str | os.PathLike[str],
    env: Mapping[str, str] | None = None,
    input: str | bytes | None = None,
    check: bool = True,
    text: bool | None = None,
) -> subprocess.CompletedProcess[Any]:
    return _run(
        ['git', *args],
        cwd=repo,
        env=env,
        input=input,
        check=check,
        text=text,
    )


def _git_stdout(
    repo: pathlib.Path,
    *args: str | os.PathLike[str],
    env: Mapping[str, str] | None = None,
    input: str | bytes | None = None,
) -> str:
    proc = _git(repo, *args, env=env, input=input)
    stdout = proc.stdout
    if isinstance(stdout, bytes):
        return stdout.decode()
    return stdout


def _repo_root(path: str | os.PathLike[str]) -> pathlib.Path:
    path = pathlib.Path(path).expanduser().resolve()
    proc = _run(['git', '-C', path, 'rev-parse', '--show-toplevel'])
    return pathlib.Path(proc.stdout.strip()).resolve()


def _git_dir(repo: pathlib.Path) -> pathlib.Path:
    raw = _git_stdout(repo, 'rev-parse', '--absolute-git-dir').strip()
    return pathlib.Path(raw).resolve()


def _config_path(repo: pathlib.Path) -> pathlib.Path:
    return _git_dir(repo) / CONFIG_RELATIVE_PATH


def _transactions_dir(repo: pathlib.Path) -> pathlib.Path:
    return _git_dir(repo) / TRANSACTION_RELATIVE_PATH


def _validate_repository_id(repository_id: str) -> None:
    if not _SAFE_REPOSITORY_ID.fullmatch(repository_id):
        raise EpochError(
            'Repository ids must match [A-Za-z0-9][A-Za-z0-9._-]*; '
            f'got {repository_id!r}'
        )


def _object_format(repo: pathlib.Path) -> str:
    return _git_stdout(repo, 'rev-parse', '--show-object-format').strip()


def _assert_sha1(repo: pathlib.Path) -> None:
    object_format = _object_format(repo)
    if object_format != 'sha1':
        raise EpochSafetyError(
            f'git-epoch v1 supports SHA-1 repositories; got {object_format!r} '
            f'for {repo}'
        )


def _assert_clean(
    repo: pathlib.Path,
    *,
    allowed_paths: set[str] | None = None,
) -> None:
    status = _git_stdout(
        repo,
        'status',
        '--porcelain=v1',
        '--untracked-files=all',
        '--ignore-submodules=none',
    )
    dirty_lines = []
    allowed_paths = allowed_paths or set()
    for line in status.splitlines():
        # Porcelain v1 starts ordinary entries with two status columns and a
        # space. Setup recovery only uses this allowance for the fixed public
        # locator filename, so rename/copy syntax is intentionally not special
        # cased here.
        path = line[3:] if len(line) >= 4 else ''
        if path not in allowed_paths:
            dirty_lines.append(line)
    if dirty_lines:
        dirty = '\n'.join(dirty_lines)
        raise EpochSafetyError(
            f'Repository must be clean before epoch planning/apply: {repo}\n{dirty}'
        )
    for marker in [
        'MERGE_HEAD',
        'CHERRY_PICK_HEAD',
        'REVERT_HEAD',
        'REBASE_HEAD',
    ]:
        proc = _git(repo, 'rev-parse', '-q', '--verify', marker, check=False)
        if proc.returncode == 0:
            raise EpochSafetyError(
                f'Repository has an in-progress operation ({marker}): {repo}'
            )
    git_dir = _git_dir(repo)
    if (git_dir / 'rebase-merge').exists() or (git_dir / 'rebase-apply').exists():
        raise EpochSafetyError(f'Repository has an in-progress rebase: {repo}')


def _assert_one_worktree(repo: pathlib.Path) -> None:
    text = _git_stdout(repo, 'worktree', 'list', '--porcelain')
    count = sum(1 for line in text.splitlines() if line.startswith('worktree '))
    if count != 1:
        raise EpochSafetyError(
            f'git-epoch v1 requires one worktree; found {count} for {repo}'
        )


def _resolve_ref(repo: pathlib.Path, ref: str) -> str:
    return _git_stdout(repo, 'rev-parse', '--verify', ref).strip()


def _tree_oid(repo: pathlib.Path, commit: str) -> str:
    return _git_stdout(repo, 'rev-parse', f'{commit}^{{tree}}').strip()


def _root_commits(repo: pathlib.Path, tip: str) -> list[str]:
    text = _git_stdout(repo, 'rev-list', '--max-parents=0', tip)
    return [line for line in text.splitlines() if line]


def _local_heads(repo: pathlib.Path) -> dict[str, str]:
    text = _git_stdout(
        repo,
        'for-each-ref',
        '--format=%(refname)%00%(objectname)',
        'refs/heads/',
    )
    result = {}
    for line in text.splitlines():
        if not line:
            continue
        ref, oid = line.split('\0', 1)
        result[ref] = oid
    return result


def _local_tags(repo: pathlib.Path) -> dict[str, str]:
    text = _git_stdout(
        repo,
        'for-each-ref',
        '--format=%(refname)%00%(objectname)',
        'refs/tags/',
    )
    result = {}
    for line in text.splitlines():
        if not line:
            continue
        ref, oid = line.split('\0', 1)
        result[ref] = oid
    return result


def _current_branch(repo: pathlib.Path) -> str | None:
    proc = _git(repo, 'symbolic-ref', '--quiet', '--short', 'HEAD', check=False)
    if proc.returncode:
        return None
    return proc.stdout.strip()


def _default_remote_url(repo: pathlib.Path, remote: str) -> str | None:
    proc = _git(repo, 'remote', 'get-url', remote, check=False)
    if proc.returncode:
        return None
    return _normalize_git_location(repo, proc.stdout.strip())


def _parse_ls_remote(text: str) -> dict[str, str]:
    refs: dict[str, str] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        oid, ref = line.split('\t', 1)
        if ref.endswith('^{}'):
            continue
        refs[ref] = oid
    return refs


def _remote_refs(repo: pathlib.Path, remote: str) -> dict[str, str]:
    proc = _git(repo, 'ls-remote', '--heads', '--tags', remote, check=False)
    if proc.returncode:
        raise EpochSafetyError(
            f'Unable to inspect active remote {remote!r} from {repo}: '
            f'{proc.stderr.strip()}'
        )
    return _parse_ls_remote(proc.stdout)


def _directory_size(path: pathlib.Path) -> int:
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            fpath = pathlib.Path(root) / name
            with contextlib.suppress(OSError):
                total += fpath.stat().st_size
    return total


def _directory_disk_usage(path: pathlib.Path) -> int:
    """Return allocated filesystem bytes, with a portable size fallback."""
    total = 0
    for root, _, files in os.walk(path):
        for name in files:
            fpath = pathlib.Path(root) / name
            with contextlib.suppress(OSError):
                stat = fpath.stat()
                blocks = getattr(stat, 'st_blocks', None)
                total += stat.st_size if blocks is None else int(blocks) * 512
    return total


def _format_bytes(num: int) -> str:
    value = float(num)
    for suffix in ['B', 'KiB', 'MiB', 'GiB', 'TiB']:
        if value < 1024 or suffix == 'TiB':
            if suffix == 'B':
                return f'{int(value)} {suffix}'
            return f'{value:.1f} {suffix}'
        value /= 1024
    raise AssertionError


def _reachable_size(repo: pathlib.Path, tip: str) -> int:
    revs = _git_stdout(repo, 'rev-list', '--objects', '--no-object-names', tip)
    if not revs.strip():
        return 0
    proc = _git(
        repo,
        'cat-file',
        '--batch-check=%(objectsize:disk)',
        input=revs,
    )
    total = 0
    for line in proc.stdout.splitlines():
        with contextlib.suppress(ValueError):
            total += int(line)
    return total


def _parse_gitmodules(repo: pathlib.Path, commit: str = 'HEAD') -> list[dict[str, str]]:
    proc = _git(repo, 'show', f'{commit}:.gitmodules', check=False)
    if proc.returncode:
        return []
    parser = configparser.RawConfigParser()
    try:
        parser.read_string(proc.stdout)
    except configparser.Error as ex:
        raise EpochError(f'Unable to parse .gitmodules at {repo}@{commit}: {ex}')
    result = []
    for section in parser.sections():
        if not section.startswith('submodule '):
            continue
        match = re.match(r'^submodule\s+"(.*)"$', section)
        name = match.group(1) if match else section[len('submodule ') :]
        path = parser.get(section, 'path')
        url = parser.get(section, 'url', fallback='')
        result.append({'name': name, 'path': path, 'url': url})
    return result


def _gitlink_oid(repo: pathlib.Path, commit: str, path: str) -> str:
    text = _git_stdout(repo, 'ls-tree', commit, '--', path).strip()
    if not text:
        raise EpochError(f'No tree entry for submodule path {path!r} at {commit}')
    meta, _name = text.split('\t', 1)
    mode, kind, oid = meta.split(' ', 2)
    if mode != '160000' or kind != 'commit':
        raise EpochError(
            f'Expected gitlink at {path!r}; got mode={mode}, type={kind}'
        )
    return oid


def _discover_submodules(repo: pathlib.Path, commit: str = 'HEAD') -> list[dict[str, Any]]:
    occurrences = []
    for item in _parse_gitmodules(repo, commit):
        item = dict(item)
        item['commit'] = _gitlink_oid(repo, commit, item['path'])
        item['worktree'] = str((repo / item['path']).resolve())
        occurrences.append(item)
    return occurrences


def _parse_epoch_trailers(repo: pathlib.Path, root: str) -> dict[str, str]:
    message = _git_stdout(repo, 'show', '-s', '--format=%B', root)
    proc = _git(
        repo,
        'interpret-trailers',
        '--parse',
        input=message,
        check=False,
    )
    if proc.returncode:
        return {}
    result: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        if ':' not in line:
            continue
        key, value = line.split(':', 1)
        result[key.strip()] = value.strip()
    return result


def _normalize_git_location(repo: pathlib.Path, spec: str) -> str:
    if not _looks_like_local_path(spec):
        return spec
    path = pathlib.Path(spec).expanduser()
    if not path.is_absolute():
        path = repo / path
    return str(path.resolve())


def _normalize_history_store(repo: pathlib.Path, spec: str) -> str:
    return _normalize_git_location(repo, spec)


def _looks_like_local_path(spec: str) -> bool:
    if spec.startswith('file://'):
        return False
    if '://' in spec:
        return False
    if re.match(r'^[^/@:]+@[^:]+:', spec):
        return False
    return True


def _default_repository_id(repo: pathlib.Path) -> str:
    name = repo.name
    if name.endswith('.git'):
        name = name[:-4]
    name = re.sub(r'[^A-Za-z0-9._-]+', '-', name).strip('-')
    return name or 'repository'


def _history_store_id(spec: str) -> str:
    if _looks_like_local_path(spec):
        name = pathlib.Path(spec).name
    else:
        name = spec.rstrip('/').rsplit('/', 1)[-1].rsplit(':', 1)[-1]
    if name.endswith('.git'):
        name = name[:-4]
    name = re.sub(r'[^A-Za-z0-9._-]+', '-', name).strip('-')
    return name or 'history-store'


def _reconcile_existing_config(
    repo_path: pathlib.Path,
    *,
    config_path: pathlib.Path,
    repository_id: str | None,
    history_store: str | os.PathLike[str],
    history_store_id: str | None,
    public_history_url: str | None,
    public_history_browse_url: str | None,
    primary_branch: str | None,
    active_remote: str,
    policy: str,
) -> dict[str, Any]:
    """Make repeated ``git epoch init`` setup calls safely idempotent.

    This path is deliberately conservative: it can fill setup metadata that was
    absent in an older/local configuration and can create the public locator,
    but it never changes an already-recorded identity or publication target.
    """
    config = _read_yaml(config_path)
    _validate_config(config, repo_path)
    _assert_no_prepared_transaction(repo_path)

    # A previous successful setup attempt may have created the public locator
    # immediately before the shell session was interrupted. Allow exactly that
    # file to remain modified/untracked so rerunning the same command repairs or
    # confirms the setup. All unrelated worktree changes remain a hard stop.
    _assert_clean(repo_path, allowed_paths={'.git-epoch.yaml'})

    requested_store = _normalize_history_store(
        repo_path, os.fspath(history_store)
    )
    requested_store_id = history_store_id or _history_store_id(requested_store)
    _validate_repository_id(requested_store_id)

    mismatches: dict[str, tuple[Any, Any]] = {}

    def compare_if_requested(
        field: str, requested: Any, *, always: bool = False
    ) -> None:
        if always or requested is not None:
            actual = config.get(field)
            if actual != requested:
                mismatches[field] = (actual, requested)

    compare_if_requested('repository', repository_id)
    compare_if_requested('history_store', requested_store, always=True)
    compare_if_requested('primary_branch', primary_branch)
    compare_if_requested('active_remote', active_remote, always=True)
    compare_if_requested('policy', policy, always=True)

    existing_store_id = config.get('history_store_id')
    repaired = False
    if existing_store_id in {None, ''}:
        # Older setup state may predate the explicit stable store-id field.
        # Filling it from the user's current request is safe because no
        # successor-root lineage has been created by epoch zero yet.
        config['history_store_id'] = requested_store_id
        repaired = True
    elif existing_store_id != requested_store_id:
        mismatches['history_store_id'] = (
            existing_store_id, requested_store_id
        )

    if mismatches:
        rendered = ', '.join(
            f'{key}: existing={actual!r}, requested={requested!r}'
            for key, (actual, requested) in sorted(mismatches.items())
        )
        raise EpochSafetyError(
            'Existing epoch configuration conflicts with the requested '
            f'initialization ({rendered}). Refusing to overwrite local epoch '
            'identity or publication settings. Use the existing values or '
            'remove/reconfigure the local epoch state intentionally.'
        )

    active_url = _default_remote_url(repo_path, active_remote)
    if not config.get('active_url') and active_url:
        config['active_url'] = active_url
        repaired = True

    from .locator import (
        read_public_locator,
        validate_public_locator_against_config,
        write_public_locator,
    )

    if public_history_url is not None:
        write_public_locator(
            repo_path,
            repository_id=config['repository'],
            history_store_id=config['history_store_id'],
            history_url=public_history_url,
            browse_url=public_history_browse_url,
            overwrite=False,
        )
    else:
        committed_locator = read_public_locator(repo_path, required=False)
        if committed_locator is not None:
            validate_public_locator_against_config(
                repo_path, config, required=True
            )

    if repaired:
        _write_yaml(config_path, config)
    return config


def initialize_config(
    repo: str | os.PathLike[str],
    *,
    repository_id: str | None = None,
    history_store: str | os.PathLike[str],
    history_store_id: str | None = None,
    public_history_url: str | None = None,
    public_history_browse_url: str | None = None,
    primary_branch: str | None = None,
    active_remote: str = 'origin',
    policy: str = 'epoch',
    overwrite: bool = False,
) -> dict[str, Any]:
    repo_path = _repo_root(repo)
    _assert_sha1(repo_path)
    _assert_one_worktree(repo_path)
    if policy not in {'epoch', 'continuous', 'external'}:
        raise EpochError(f'Unknown policy: {policy!r}')
    config_path = _config_path(repo_path)
    if config_path.exists() and not overwrite:
        return _reconcile_existing_config(
            repo_path,
            config_path=config_path,
            repository_id=repository_id,
            history_store=history_store,
            history_store_id=history_store_id,
            public_history_url=public_history_url,
            public_history_browse_url=public_history_browse_url,
            primary_branch=primary_branch,
            active_remote=active_remote,
            policy=policy,
        )
    _assert_clean(repo_path)
    if primary_branch is None:
        primary_branch = _current_branch(repo_path)
    if primary_branch is None:
        raise EpochError('Unable to infer a primary branch from detached HEAD')
    branch_ref = f'refs/heads/{primary_branch}'
    tip = _resolve_ref(repo_path, branch_ref)
    roots = _root_commits(repo_path, tip)
    trailers = _parse_epoch_trailers(repo_path, roots[0]) if len(roots) == 1 else {}
    trailer_repository = trailers.get('Git-Epoch-Repository')
    trailer_epoch = trailers.get('Git-Epoch-Number')
    trailer_store_id = trailers.get('Git-Epoch-History-Store')
    managed_root = bool(trailer_repository and trailer_epoch and trailer_store_id)
    repository_record: dict[str, Any] = {}

    if managed_root:
        assert trailer_repository is not None
        assert trailer_epoch is not None
        assert trailer_store_id is not None
        try:
            active_epoch = int(trailer_epoch)
        except ValueError as ex:
            raise EpochError(
                f'Invalid Git-Epoch-Number trailer: {trailer_epoch!r}'
            ) from ex
        if active_epoch <= 0:
            raise EpochError(
                f'Invalid successor epoch number in root trailer: {active_epoch}'
            )
        if repository_id is None:
            repository_id = trailer_repository
        elif repository_id != trailer_repository:
            raise EpochSafetyError(
                f'Repository identity conflicts with successor root: '
                f'{repository_id!r} != {trailer_repository!r}'
            )
    else:
        active_epoch = 0
        if repository_id is None:
            repository_id = _default_repository_id(repo_path)

    assert repository_id is not None
    _validate_repository_id(repository_id)
    store = _normalize_history_store(repo_path, os.fspath(history_store))
    requested_store_id = history_store_id
    if requested_store_id is None:
        history_store_id = _history_store_id(store)
    else:
        _validate_repository_id(requested_store_id)
        history_store_id = requested_store_id
    if managed_root and history_store_id != trailer_store_id:
        raise EpochSafetyError(
            'History-store identity conflicts with successor root: '
            f'{history_store_id!r} != {trailer_store_id!r}'
        )

    if managed_root:
        history = HistoryStore(store, repo_path)
        if history.is_local and not history.local_path.exists():
            raise EpochError(
                'This checkout is already epoch-managed, but the supplied local '
                f'history store does not exist: {history.local_path}'
            )
        history.ensure()
        manifest, meta_oid = history.load_manifest()
        if meta_oid is None:
            raise EpochSafetyError(
                'This checkout is already epoch-managed, but the supplied history '
                'store has no epoch manifest.'
            )
        _manifest_validate(manifest)
        recorded_store_id = manifest.get('history_store', {}).get('id')
        if recorded_store_id != trailer_store_id:
            raise EpochSafetyError(
                'History-store identity conflicts with successor root: '
                f'{recorded_store_id!r} != {trailer_store_id!r}'
            )
        history_store_id = trailer_store_id
        repository_record = dict(
            manifest.get('repositories', {}).get(repository_id, {})
        )
        if not repository_record:
            raise EpochSafetyError(
                f'History store has no repository record for {repository_id!r}'
            )
        recorded_policy = repository_record.get('policy')
        if recorded_policy in {'epoch', 'continuous', 'external'}:
            policy = recorded_policy

    submodules = {
        item['path']: {
            'repository': None,
            'policy': 'external',
            'name': item['name'],
            'url': item['url'],
        }
        for item in _discover_submodules(repo_path, branch_ref)
    }
    for path, item in repository_record.get('submodules', {}).items():
        if path in submodules:
            submodules[path].update(item)

    config = {
        'format_version': FORMAT_VERSION,
        'repository': repository_id,
        'policy': policy,
        'history_store': store,
        'history_store_id': history_store_id,
        'primary_branch': repository_record.get('primary_branch', primary_branch),
        'active_epoch': active_epoch,
        'active_remote': active_remote,
        'active_url': _default_remote_url(repo_path, active_remote),
        'submodules': submodules,
    }
    if managed_root:
        _validate_epoch_lineage_before_plan(repo_path, config, tip)

    from .locator import (
        read_public_locator,
        validate_public_locator_against_config,
        write_public_locator,
    )

    committed_locator = read_public_locator(repo_path, required=False)
    if public_history_url is not None:
        write_public_locator(
            repo_path,
            repository_id=repository_id,
            history_store_id=history_store_id,
            history_url=public_history_url,
            browse_url=public_history_browse_url,
            overwrite=overwrite,
        )
    elif committed_locator is not None:
        validate_public_locator_against_config(repo_path, config, required=True)
    _write_yaml(config_path, config)
    return config


def load_config(
    repo: str | os.PathLike[str],
    *,
    allow_bootstrap: bool = True,
) -> dict[str, Any]:
    repo_path = _repo_root(repo)
    config_path = _config_path(repo_path)
    if config_path.exists():
        config = _read_yaml(config_path)
        _validate_config(config, repo_path)
        return config
    if allow_bootstrap:
        branch = _current_branch(repo_path)
        if branch is not None:
            tip = _resolve_ref(repo_path, f'refs/heads/{branch}')
            roots = _root_commits(repo_path, tip)
            if len(roots) == 1:
                trailers = _parse_epoch_trailers(repo_path, roots[0])
                if (
                    trailers.get('Git-Epoch-Repository')
                    and trailers.get('Git-Epoch-Number')
                    and trailers.get('Git-Epoch-History-Store')
                ):
                    from .locator import read_public_locator

                    locator = read_public_locator(repo_path, required=False)
                    if locator is not None:
                        raise EpochError(
                            'This checkout is already epoch-managed, but local '
                            'epoch configuration is absent. A clone-visible '
                            '.git-epoch.yaml locator is available; run '
                            '`git epoch attach`.'
                        )
                    raise EpochError(
                        'This checkout is already epoch-managed, but local epoch '
                        'configuration is absent. No clone-visible archive locator '
                        'exists; attach explicitly with `git epoch init '
                        '--history-store <store> --config-only`.'
                    )
    raise EpochError(
        f'No epoch configuration at {config_path}. Run `git epoch init` first.'
    )

def save_config(repo: str | os.PathLike[str], config: Mapping[str, Any]) -> None:
    repo_path = _repo_root(repo)
    data = dict(config)
    _validate_config(data, repo_path)
    _write_yaml(_config_path(repo_path), data)


def configure_submodule(
    repo: str | os.PathLike[str],
    path: str,
    *,
    policy: str,
    repository_id: str | None = None,
) -> dict[str, Any]:
    repo_path = _repo_root(repo)
    config = load_config(repo_path)
    if policy not in {'epoch', 'continuous', 'external'}:
        raise EpochError(f'Unknown policy: {policy!r}')
    occurrence_map = {item['path']: item for item in _discover_submodules(repo_path)}
    if path not in occurrence_map:
        raise EpochError(f'No current submodule at path {path!r}')
    item = occurrence_map[path]
    entry = dict(config.get('submodules', {}).get(path, {}))
    entry.update(
        {
            'policy': policy,
            'repository': repository_id,
            'name': item['name'],
            'url': item['url'],
        }
    )
    if policy == 'epoch' and not repository_id:
        child_path = repo_path / path
        try:
            child_config = load_config(child_path)
        except EpochError as ex:
            raise EpochError(
                'Epoch submodules need a logical repository id. Initialize the '
                f'child repo or pass repository_id for {path!r}.'
            ) from ex
        entry['repository'] = child_config['repository']
    config.setdefault('submodules', {})[path] = entry
    save_config(repo_path, config)
    return config


def _validate_config(config: Mapping[str, Any], repo: pathlib.Path) -> None:
    if config.get('format_version') != FORMAT_VERSION:
        raise EpochError(
            f'Unsupported epoch config format: {config.get("format_version")!r}'
        )
    repository_id = config.get('repository')
    if not isinstance(repository_id, str):
        raise EpochError(f'Missing repository id in epoch config for {repo}')
    _validate_repository_id(repository_id)
    if config.get('policy') not in {'epoch', 'continuous', 'external'}:
        raise EpochError(f'Invalid repository policy in epoch config for {repo}')
    if not config.get('history_store'):
        raise EpochError(f'Missing history_store in epoch config for {repo}')
    if not config.get('primary_branch'):
        raise EpochError(f'Missing primary_branch in epoch config for {repo}')
    active_epoch = config.get('active_epoch')
    if not isinstance(active_epoch, int) or active_epoch < 0:
        raise EpochError(f'Invalid active_epoch in epoch config for {repo}')


@dataclasses.dataclass
class HistoryStore:
    spec: str
    source_repo: pathlib.Path

    @property
    def is_local(self) -> bool:
        return _looks_like_local_path(self.spec)

    @property
    def local_path(self) -> pathlib.Path:
        if not self.is_local:
            raise EpochError(f'History store is not local: {self.spec}')
        path = pathlib.Path(self.spec).expanduser()
        if not path.is_absolute():
            path = self.source_repo / path
        return path.resolve()

    def ensure(self) -> None:
        if self.is_local:
            path = self.local_path
            if not path.exists():
                path.parent.mkdir(parents=True, exist_ok=True)
                _run(['git', 'init', '--bare', path])
            proc = _run(
                ['git', '--git-dir', path, 'rev-parse', '--is-bare-repository'],
                check=False,
            )
            if proc.returncode or proc.stdout.strip() != 'true':
                raise EpochError(f'History store is not a bare Git repo: {path}')
        else:
            proc = _run(['git', 'ls-remote', self.spec], check=False)
            if proc.returncode:
                raise EpochError(
                    f'Unable to access history store {self.spec!r}: '
                    f'{proc.stderr.strip()}'
                )

    def ls_refs(self, pattern: str | None = None) -> dict[str, str]:
        args = ['git', 'ls-remote', self.spec]
        if pattern is not None:
            args.append(pattern)
        proc = _run(args, check=False)
        if proc.returncode:
            raise EpochError(
                f'Unable to list history store {self.spec!r}: {proc.stderr.strip()}'
            )
        return _parse_ls_remote(proc.stdout)

    def ref_oid(self, ref: str) -> str | None:
        return self.ls_refs(ref).get(ref)

    def archive_ref(
        self,
        source_ref: str,
        dest_ref: str,
        expected_oid: str,
    ) -> str:
        existing = self.ref_oid(dest_ref)
        if existing is not None:
            if existing != expected_oid:
                raise EpochSafetyError(
                    f'Immutable archive ref conflict: {dest_ref} points to '
                    f'{existing}, expected {expected_oid}'
                )
            return 'already-present'
        _git(
            self.source_repo,
            'push',
            f'--force-with-lease={dest_ref}:',
            self.spec,
            f'{source_ref}:{dest_ref}',
        )
        actual = self.ref_oid(dest_ref)
        if actual != expected_oid:
            raise EpochSafetyError(
                f'Archive verification failed for {dest_ref}: '
                f'expected {expected_oid}, got {actual}'
            )
        return 'created'

    def archive_oid(self, oid: str, dest_ref: str) -> str:
        existing = self.ref_oid(dest_ref)
        if existing is not None:
            if existing != oid:
                raise EpochSafetyError(
                    f'Immutable archive ref conflict: {dest_ref} points to '
                    f'{existing}, expected {oid}'
                )
            return 'already-present'
        _git(
            self.source_repo,
            'push',
            f'--force-with-lease={dest_ref}:',
            self.spec,
            f'{oid}:{dest_ref}',
        )
        actual = self.ref_oid(dest_ref)
        if actual != oid:
            raise EpochSafetyError(
                f'Archive verification failed for {dest_ref}: '
                f'expected {oid}, got {actual}'
            )
        return 'created'

    def delete_ref(self, ref: str, expected_oid: str | None = None) -> None:
        existing = self.ref_oid(ref)
        if existing is None:
            return
        if expected_oid is not None and existing != expected_oid:
            raise EpochSafetyError(
                f'Ref changed before deletion: {ref} is {existing}, '
                f'expected {expected_oid}'
            )
        lease_oid = expected_oid or existing
        _git(
            self.source_repo,
            'push',
            f'--force-with-lease={ref}:{lease_oid}',
            self.spec,
            f':{ref}',
        )

    def verify_refs(self, refs: Mapping[str, str], *, fsck: bool = True) -> None:
        actual = self.ls_refs()
        for ref, expected in refs.items():
            found = actual.get(ref)
            if found != expected:
                raise EpochSafetyError(
                    f'History store ref mismatch: {ref}: expected {expected}, '
                    f'got {found}'
                )
        if fsck and refs:
            with tempfile.TemporaryDirectory(prefix='git-epoch-verify-') as tmp:
                mirror = pathlib.Path(tmp) / 'verify.git'
                _run(['git', 'init', '--bare', mirror])
                for ref in refs:
                    _run(
                        [
                            'git',
                            '--git-dir',
                            mirror,
                            'fetch',
                            '--no-tags',
                            self.spec,
                            f'+{ref}:{ref}',
                        ]
                    )
                _run(['git', '--git-dir', mirror, 'fsck', '--full'])

    def load_manifest(self) -> tuple[dict[str, Any], str | None]:
        if self.is_local:
            path = self.local_path
            old = self.ref_oid('refs/meta/main')
            if old is None:
                return _empty_manifest(_history_store_id(self.spec)), None
            proc = _run(
                [
                    'git',
                    '--git-dir',
                    path,
                    'show',
                    'refs/meta/main:manifest.yaml',
                ]
            )
            return _yaml_load_text(proc.stdout), old
        with tempfile.TemporaryDirectory(prefix='git-epoch-meta-') as tmp:
            bare = pathlib.Path(tmp) / 'meta.git'
            _run(['git', 'init', '--bare', bare])
            refs = self.ls_refs('refs/meta/main')
            old = refs.get('refs/meta/main')
            if old is None:
                return _empty_manifest(_history_store_id(self.spec)), None
            _run(
                [
                    'git',
                    '--git-dir',
                    bare,
                    'fetch',
                    '--no-tags',
                    self.spec,
                    '+refs/meta/main:refs/meta/main',
                ]
            )
            text = _run(
                [
                    'git',
                    '--git-dir',
                    bare,
                    'show',
                    'refs/meta/main:manifest.yaml',
                ]
            ).stdout
            return _yaml_load_text(text), old

    def update_manifest(
        self,
        manifest: Mapping[str, Any],
        *,
        expected_old: str | None,
        message: str,
    ) -> str:
        if self.is_local:
            return self._update_manifest_local(
                self.local_path,
                manifest,
                expected_old=expected_old,
                message=message,
            )
        with tempfile.TemporaryDirectory(prefix='git-epoch-meta-') as tmp:
            bare = pathlib.Path(tmp) / 'meta.git'
            _run(['git', 'init', '--bare', bare])
            if expected_old is not None:
                _run(
                    [
                        'git',
                        '--git-dir',
                        bare,
                        'fetch',
                        '--no-tags',
                        self.spec,
                        '+refs/meta/main:refs/meta/main',
                    ]
                )
            new_oid = self._write_manifest_commit(
                bare,
                manifest,
                parent=expected_old,
                message=message,
            )
            lease = (
                f'--force-with-lease=refs/meta/main:{expected_old}'
                if expected_old
                else '--force-with-lease=refs/meta/main:'
            )
            _run(
                [
                    'git',
                    '--git-dir',
                    bare,
                    'push',
                    lease,
                    self.spec,
                    f'{new_oid}:refs/meta/main',
                ]
            )
            return new_oid

    def _update_manifest_local(
        self,
        bare: pathlib.Path,
        manifest: Mapping[str, Any],
        *,
        expected_old: str | None,
        message: str,
    ) -> str:
        actual_old = self.ref_oid('refs/meta/main')
        if actual_old != expected_old:
            raise EpochPlanStaleError(
                'History manifest changed concurrently: '
                f'expected {expected_old}, got {actual_old}'
            )
        new_oid = self._write_manifest_commit(
            bare,
            manifest,
            parent=expected_old,
            message=message,
        )
        args = ['git', '--git-dir', bare, 'update-ref', 'refs/meta/main', new_oid]
        if expected_old:
            args.append(expected_old)
        else:
            args.append('0' * 40)
        _run(args)
        return new_oid

    @staticmethod
    def _write_manifest_commit(
        bare: pathlib.Path,
        manifest: Mapping[str, Any],
        *,
        parent: str | None,
        message: str,
    ) -> str:
        text = _yaml_dump(manifest)
        blob = _run(
            ['git', '--git-dir', bare, 'hash-object', '-w', '--stdin'],
            input=text,
        ).stdout.strip()
        tree_input = f'100644 blob {blob}\tmanifest.yaml\0'.encode()
        tree = _run(
            ['git', '--git-dir', bare, 'mktree', '-z'],
            input=tree_input,
            text=False,
        ).stdout.decode().strip()
        env = {
            'GIT_AUTHOR_NAME': 'git-epoch',
            'GIT_AUTHOR_EMAIL': 'git-epoch@local',
            'GIT_COMMITTER_NAME': 'git-epoch',
            'GIT_COMMITTER_EMAIL': 'git-epoch@local',
        }
        args = ['git', '--git-dir', bare, 'commit-tree', tree]
        if parent:
            args.extend(['-p', parent])
        args.extend(['-m', message])
        return _run(args, env=env).stdout.strip()


def _empty_manifest(store_id: str) -> dict[str, Any]:
    return {
        'format_version': FORMAT_VERSION,
        'history_store': {'id': store_id, 'object_format': 'sha1'},
        'repositories': {},
        'epochs': {},
        'boundaries': [],
    }


def _manifest_validate(manifest: Mapping[str, Any]) -> None:
    if manifest.get('format_version') != FORMAT_VERSION:
        raise EpochError(
            f'Unsupported manifest format: {manifest.get("format_version")!r}'
        )
    store = manifest.get('history_store')
    if not isinstance(store, dict) or store.get('object_format') != 'sha1':
        raise EpochError('History store manifest must declare SHA-1 object format')


def _active_repo_record(repo: pathlib.Path, config: Mapping[str, Any]) -> dict[str, Any]:
    active_url = config.get('active_url')
    if not active_url:
        active_url = _default_remote_url(repo, config.get('active_remote', 'origin'))
    return {
        'policy': config['policy'],
        'active_url': active_url,
        'history_store': config['history_store'],
    }


def _archive_ref_name(
    repository_id: str,
    epoch: int,
    source_ref: str,
) -> str:
    prefix = f'refs/epochs/{repository_id}/{epoch:03d}'
    if source_ref.startswith('refs/heads/'):
        suffix = source_ref[len('refs/heads/') :]
        return f'{prefix}/heads/{suffix}'
    if source_ref.startswith('refs/tags/'):
        suffix = source_ref[len('refs/tags/') :]
        return f'{prefix}/tags/{suffix}'
    raise EpochError(f'Unsupported active ref for archive: {source_ref}')


def _successor_root_message(entry: Mapping[str, Any]) -> str:
    return (
        f'Start git epoch {entry["new_epoch"]}\n\n'
        f'Git-Epoch-Repository: {entry["repository"]}\n'
        f'Git-Epoch-Number: {entry["new_epoch"]}\n'
        f'Git-Epoch-Predecessor: {entry["old_tip"]}\n'
        f'Git-Epoch-Predecessor-Tree: {entry["old_tree"]}\n'
        f'Git-Epoch-History-Store: {entry["history_store_id"]}\n'
    )


def _commit_env(identity: Mapping[str, Any]) -> dict[str, str]:
    stamp = f'@{identity["timestamp"]} +0000'
    return {
        'GIT_AUTHOR_NAME': str(identity['name']),
        'GIT_AUTHOR_EMAIL': str(identity['email']),
        'GIT_AUTHOR_DATE': stamp,
        'GIT_COMMITTER_NAME': str(identity['name']),
        'GIT_COMMITTER_EMAIL': str(identity['email']),
        'GIT_COMMITTER_DATE': stamp,
    }


def _user_identity(repo: pathlib.Path, timestamp: int) -> dict[str, Any]:
    name = _git_stdout(repo, 'config', '--get', 'user.name').strip()
    email = _git_stdout(repo, 'config', '--get', 'user.email').strip()
    if not name or not email:
        raise EpochSafetyError(
            f'Git user.name and user.email must be configured in {repo}'
        )
    return {'name': name, 'email': email, 'timestamp': timestamp}


def _write_successor_tree(
    repo: pathlib.Path,
    old_tip: str,
    translations: list[Mapping[str, Any]],
    *,
    object_directory: pathlib.Path | None = None,
) -> str:
    if not translations:
        return _tree_oid(repo, old_tip)
    with tempfile.TemporaryDirectory(prefix='git-epoch-index-') as tmp:
        tmp_path = pathlib.Path(tmp)
        index = tmp_path / 'index'
        env: dict[str, str] = {'GIT_INDEX_FILE': str(index)}
        if object_directory is not None:
            objects = object_directory
            objects.mkdir(parents=True, exist_ok=True)
            (objects / 'info').mkdir(exist_ok=True)
            (objects / 'pack').mkdir(exist_ok=True)
            env['GIT_OBJECT_DIRECTORY'] = str(objects)
            env['GIT_ALTERNATE_OBJECT_DIRECTORIES'] = str(_git_dir(repo) / 'objects')
        _git(repo, 'read-tree', old_tip, env=env)
        for item in translations:
            _git(
                repo,
                'update-index',
                '--cacheinfo',
                f'160000,{item["new_commit"]},{item["path"]}',
                env=env,
            )
        return _git_stdout(repo, 'write-tree', env=env).strip()


def _compute_successor_commit(
    repo: pathlib.Path,
    tree: str,
    message: str,
    identity: Mapping[str, Any],
    *,
    object_directory: pathlib.Path | None = None,
) -> str:
    env = _commit_env(identity)
    if object_directory is not None:
        object_directory.mkdir(parents=True, exist_ok=True)
        (object_directory / 'info').mkdir(exist_ok=True)
        (object_directory / 'pack').mkdir(exist_ok=True)
        env['GIT_OBJECT_DIRECTORY'] = str(object_directory)
        env['GIT_ALTERNATE_OBJECT_DIRECTORIES'] = str(_git_dir(repo) / 'objects')
    return _git_stdout(repo, 'commit-tree', tree, '-m', message, env=env).strip()


def _repo_config_for_child(
    parent_repo: pathlib.Path,
    occurrence: Mapping[str, Any],
    parent_config: Mapping[str, Any],
) -> tuple[str, str | None, dict[str, Any] | None]:
    configured = dict(parent_config.get('submodules', {}).get(occurrence['path'], {}))
    policy = configured.get('policy', 'external')
    repository_id = configured.get('repository')
    child_config = None
    child_path = parent_repo / occurrence['path']
    if policy == 'epoch':
        if not child_path.exists():
            raise EpochSafetyError(
                f'Epoch submodule worktree is missing: {child_path}'
            )
        child_config = load_config(child_path)
        child_id = child_config['repository']
        if repository_id is not None and repository_id != child_id:
            raise EpochSafetyError(
                f'Submodule identity mismatch at {occurrence["path"]}: '
                f'parent config says {repository_id}, child says {child_id}'
            )
        repository_id = child_id
    return policy, repository_id, child_config


def _plan_repository_graph(
    root_repo: pathlib.Path,
    *,
    recursive: bool,
) -> list[dict[str, Any]]:
    seen: dict[str, pathlib.Path] = {}
    ordered: list[dict[str, Any]] = []

    def visit(repo: pathlib.Path) -> None:
        config = load_config(repo)
        repository_id = config['repository']
        previous = seen.get(repository_id)
        if previous is not None:
            if previous != repo:
                raise EpochSafetyError(
                    f'Repository identity {repository_id!r} resolves to both '
                    f'{previous} and {repo}'
                )
            return
        seen[repository_id] = repo
        branch = config['primary_branch']
        branch_ref = f'refs/heads/{branch}'
        tip = _resolve_ref(repo, branch_ref)
        occurrences = _discover_submodules(repo, tip)
        resolved_occurrences = []
        for occurrence in occurrences:
            policy, child_id, child_config = _repo_config_for_child(
                repo, occurrence, config
            )
            item = dict(occurrence)
            item['policy'] = policy
            item['repository'] = child_id
            resolved_occurrences.append(item)
            if recursive and policy == 'epoch':
                child_repo = _repo_root(repo / occurrence['path'])
                visit(child_repo)
                child_tip = _resolve_ref(
                    child_repo,
                    f'refs/heads/{child_config["primary_branch"]}',
                )
                if child_tip != occurrence['commit']:
                    raise EpochSafetyError(
                        f'Parent gitlink {repo}:{occurrence["path"]} points to '
                        f'{occurrence["commit"]}, but child primary branch points '
                        f'to {child_tip}. A recursive checkpoint requires them to '
                        'identify the same old state.'
                    )
        ordered.append(
            {
                'repo': repo,
                'config': config,
                'submodules': resolved_occurrences,
            }
        )

    visit(root_repo)
    return ordered


def _validate_epoch_lineage_before_plan(
    repo: pathlib.Path,
    config: Mapping[str, Any],
    old_tip: str,
) -> None:
    repository_id = config['repository']
    active_epoch = int(config['active_epoch'])
    roots = _root_commits(repo, old_tip)
    if active_epoch > 0:
        if len(roots) != 1:
            raise EpochSafetyError(
                f'Active epoch {repository_id}:{active_epoch} must have one root; '
                f'found {roots}'
            )
        trailers = _parse_epoch_trailers(repo, roots[0])
        expected_trailers = {
            'Git-Epoch-Repository': repository_id,
            'Git-Epoch-Number': str(active_epoch),
            'Git-Epoch-History-Store': config.get('history_store_id')
            or _history_store_id(str(config['history_store'])),
        }
        mismatches = {
            key: (trailers.get(key), expected)
            for key, expected in expected_trailers.items()
            if trailers.get(key) != expected
        }
        if mismatches:
            raise EpochSafetyError(
                f'Active root trailers do not match epoch configuration for '
                f'{repository_id}: {mismatches}'
            )

    store = HistoryStore(str(config['history_store']), repo)
    from .locator import validate_public_locator_against_config

    validate_public_locator_against_config(
        repo,
        config,
        required=not store.is_local,
    )
    if store.is_local and not store.local_path.exists():
        if active_epoch == 0:
            return
        raise EpochSafetyError(
            f'History store is missing for active epoch {repository_id}:'
            f'{active_epoch}: {store.local_path}'
        )
    manifest, meta_oid = store.load_manifest()
    _manifest_validate(manifest)
    if meta_oid is None:
        if active_epoch == 0:
            return
        raise EpochSafetyError(
            f'History store has no manifest for active epoch '
            f'{repository_id}:{active_epoch}'
        )
    configured_store_id = config.get('history_store_id') or _history_store_id(
        str(config['history_store'])
    )
    manifest_store_id = manifest.get('history_store', {}).get('id')
    if manifest_store_id != configured_store_id:
        raise EpochSafetyError(
            f'History-store identity mismatch: manifest={manifest_store_id!r}, '
            f'config={configured_store_id!r}'
        )

    epochs = manifest.get('epochs', {}).get(repository_id, [])
    boundaries = [
        boundary
        for boundary in manifest.get('boundaries', [])
        if boundary.get('repository') == repository_id
    ]
    if active_epoch == 0:
        if epochs or boundaries:
            raise EpochSafetyError(
                f'History store already contains epoch lineage for '
                f'{repository_id!r}; refuse to start a new epoch zero with the '
                'same repository identity.'
            )
        return

    matches = [
        boundary
        for boundary in boundaries
        if (
            boundary.get('state', 'committed') == 'committed'
            and int(boundary.get('successor', {}).get('epoch', -1))
            == active_epoch
        )
    ]
    if len(matches) != 1:
        raise EpochSafetyError(
            f'Expected one committed boundary leading to active epoch '
            f'{repository_id}:{active_epoch}; found {len(matches)}'
        )
    boundary = matches[0]
    active_root = roots[0]
    if boundary.get('successor', {}).get('commit') != active_root:
        raise EpochSafetyError(
            f'Active root {active_root} does not match the manifest successor for '
            f'{repository_id}:{active_epoch}: '
            f'{boundary.get("successor", {}).get("commit")}'
        )
    root_tree = _tree_oid(repo, active_root)
    if boundary.get('successor', {}).get('tree') != root_tree:
        raise EpochSafetyError(
            f'Active root tree does not match manifest metadata for '
            f'{repository_id}:{active_epoch}'
        )
    trailers = _parse_epoch_trailers(repo, active_root)
    lineage_pairs = {
        'previous.commit': (
            boundary.get('previous', {}).get('commit'),
            trailers.get('Git-Epoch-Predecessor'),
        ),
        'previous.tree': (
            boundary.get('previous', {}).get('tree'),
            trailers.get('Git-Epoch-Predecessor-Tree'),
        ),
    }
    lineage_mismatches = {
        key: pair for key, pair in lineage_pairs.items() if pair[0] != pair[1]
    }
    if lineage_mismatches:
        raise EpochSafetyError(
            f'Active root predecessor trailers do not match manifest metadata '
            f'for {repository_id}:{active_epoch}: {lineage_mismatches}'
        )


def _validate_primary_ref_policy(
    repo: pathlib.Path,
    branch: str,
    *,
    active_remote: str | None,
    retire_extra_branches: bool = False,
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    heads = _local_heads(repo)
    primary_ref = f'refs/heads/{branch}'
    primary_tip = heads.get(primary_ref)
    if primary_tip is None:
        raise EpochSafetyError(f'Primary branch is missing: {primary_ref}')
    head_oid = _resolve_ref(repo, 'HEAD')
    if head_oid != primary_tip:
        raise EpochSafetyError(
            f'HEAD must identify the primary branch tip before checkpointing: '
            f'HEAD={head_oid}, {primary_ref}={primary_tip}'
        )
    tags = _local_tags(repo)
    remote_refs: dict[str, str] = {}
    remote_heads: dict[str, str] = {}
    if active_remote and _default_remote_url(repo, active_remote):
        remote_refs = _remote_refs(repo, active_remote)
        remote_heads = {
            ref: oid
            for ref, oid in remote_refs.items()
            if ref.startswith('refs/heads/')
        }
        remote_tags = {
            ref: oid
            for ref, oid in remote_refs.items()
            if ref.startswith('refs/tags/')
        }
        if remote_tags != tags:
            missing_local = sorted(set(remote_tags) - set(tags))
            differing = sorted(
                ref
                for ref in set(remote_tags) & set(tags)
                if remote_tags[ref] != tags[ref]
            )
            if missing_local or differing:
                raise EpochSafetyError(
                    'Remote tags are not fully represented by local tag refs; '
                    'refuse to checkpoint until tags are synchronized. '
                    f'missing_local={missing_local}, differing={differing}'
                )

    local_extras = sorted(set(heads) - {primary_ref})
    remote_extras = sorted(set(remote_heads) - {primary_ref})
    if (local_extras or remote_extras) and not retire_extra_branches:
        details = []
        if local_extras:
            details.append('local=' + ', '.join(local_extras))
        if remote_extras:
            details.append('remote=' + ', '.join(remote_extras))
        raise EpochSafetyError(
            'Additional active branches would retain the retired epoch. '
            'Re-run planning with --retire-extra-branches to archive them exactly '
            'and delete them from the active namespace during publication, or '
            'resolve them manually first. ' + '; '.join(details)
        )

    if retire_extra_branches:
        for ref in sorted(set(heads) & set(remote_heads)):
            if heads[ref] != remote_heads[ref]:
                raise EpochSafetyError(
                    f'Local and remote branch disagree before retirement: {ref}: '
                    f'local={heads[ref]}, remote={remote_heads[ref]}'
                )
        local_only = sorted(set(heads) - set(remote_heads) - {primary_ref})
        if local_only:
            # Local-only branches are still preserved in the archive/bundle before
            # deletion. The explicit flag is the user's authorization to retire them.
            pass

    return heads, tags, remote_refs


def build_plan(
    repo: str | os.PathLike[str] = '.',
    *,
    recursive: bool = False,
    bundle: bool = False,
    bundle_dir: str | os.PathLike[str] | None = None,
    retire_extra_branches: bool = False,
) -> dict[str, Any]:
    root_repo = _repo_root(repo)
    graph = _plan_repository_graph(root_repo, recursive=recursive)
    timestamp = int(_utc_now().timestamp())
    transaction_id = uuid.uuid4().hex
    entries: list[dict[str, Any]] = []
    successor_by_id: dict[str, dict[str, Any]] = {}

    for node in graph:
        repo_path: pathlib.Path = node['repo']
        config = node['config']
        if config['policy'] != 'epoch':
            continue
        _assert_sha1(repo_path)
        _assert_clean(repo_path)
        _assert_one_worktree(repo_path)
        _assert_no_prepared_transaction(repo_path)
        branch = config['primary_branch']
        active_remote = config.get('active_remote')
        heads, tags, remote_refs = _validate_primary_ref_policy(
            repo_path,
            branch,
            active_remote=active_remote,
            retire_extra_branches=retire_extra_branches,
        )
        branch_ref = f'refs/heads/{branch}'
        old_tip = heads[branch_ref]
        _validate_epoch_lineage_before_plan(repo_path, config, old_tip)
        old_tree = _tree_oid(repo_path, old_tip)
        active_epoch = int(config['active_epoch'])
        new_epoch = active_epoch + 1
        refs = []
        remote_heads = {
            ref: oid
            for ref, oid in remote_refs.items()
            if ref.startswith('refs/heads/')
        }
        archived_heads = dict(remote_heads)
        archived_heads.update(heads)
        for source_ref, oid in sorted(archived_heads.items()):
            local_present = source_ref in heads
            remote_present = source_ref in remote_heads
            if local_present:
                local_source = source_ref
            else:
                if not active_remote:
                    raise EpochSafetyError(
                        f'No local source ref is available for archived branch {source_ref}'
                    )
                branch_name = source_ref[len('refs/heads/') :]
                local_source = f'refs/remotes/{active_remote}/{branch_name}'
                proc = _git(
                    repo_path,
                    'rev-parse',
                    '--verify',
                    local_source,
                    check=False,
                )
                local_oid = proc.stdout.strip() if proc.returncode == 0 else None
                if local_oid != oid:
                    raise EpochSafetyError(
                        f'Remote branch {source_ref} must be fetched locally before '
                        f'it can be retired safely: expected {oid}, '
                        f'{local_source}={local_oid!r}. Run `git fetch --prune '
                        f'{active_remote} "+refs/heads/*:refs/remotes/'
                        f'{active_remote}/*"` and re-plan.'
                    )
            refs.append(
                {
                    'source': source_ref,
                    'local_source': local_source,
                    'oid': oid,
                    'archive': _archive_ref_name(
                        config['repository'], active_epoch, source_ref
                    ),
                    'local_present': local_present,
                    'remote_present': remote_present,
                }
            )
        for source_ref, oid in sorted(tags.items()):
            refs.append(
                {
                    'source': source_ref,
                    'oid': oid,
                    'archive': _archive_ref_name(
                        config['repository'], active_epoch, source_ref
                    ),
                    'local_source': source_ref,
                    'local_present': True,
                    'remote_present': source_ref in remote_refs,
                }
            )
        translations = []
        for occurrence in node['submodules']:
            if recursive and occurrence['policy'] == 'epoch':
                child = successor_by_id.get(occurrence['repository'])
                if child is None:
                    raise EpochSafetyError(
                        'Recursive planning order error for '
                        f'{occurrence["repository"]}'
                    )
                translations.append(
                    {
                        'path': occurrence['path'],
                        'name': occurrence['name'],
                        'repository': occurrence['repository'],
                        'old_commit': occurrence['commit'],
                        'new_commit': child['successor_root'],
                        'boundary': child['boundary_id'],
                        'gitmodules_url': occurrence['url'],
                    }
                )
        with tempfile.TemporaryDirectory(prefix='git-epoch-plan-objects-') as tmp:
            object_dir = pathlib.Path(tmp) / 'objects'
            new_tree = _write_successor_tree(
                repo_path,
                old_tip,
                translations,
                object_directory=object_dir,
            )
            identity = _user_identity(repo_path, timestamp)
            entry_stub = {
                'repository': config['repository'],
                'new_epoch': new_epoch,
                'old_tip': old_tip,
                'old_tree': old_tree,
                'history_store_id': config.get('history_store_id')
                or _history_store_id(str(config['history_store'])),
            }
            message = _successor_root_message(entry_stub)
            successor_root = _compute_successor_commit(
                repo_path,
                new_tree,
                message,
                identity,
                object_directory=object_dir,
            )
        boundary_id = (
            f'{config["repository"]}-{active_epoch}-to-{new_epoch}'
        )
        remote_branch_oid = remote_refs.get(branch_ref)
        if remote_branch_oid is not None and remote_branch_oid != old_tip:
            raise EpochSafetyError(
                f'Active remote branch {branch_ref} is {remote_branch_oid}, '
                f'but local old tip is {old_tip}; synchronize before planning.'
            )
        history_store = _normalize_history_store(
            repo_path, str(config['history_store'])
        )
        bundle_path = None
        if bundle:
            if bundle_dir is not None:
                base = pathlib.Path(bundle_dir).expanduser().resolve()
            elif _looks_like_local_path(history_store):
                store_path = pathlib.Path(history_store)
                base = store_path.parent / f'{store_path.name}.bundles'
            else:
                raise EpochError(
                    '--bundle-dir is required when the history store is remote'
                )
            bundle_path = str(
                base
                / config['repository']
                / f'epoch-{active_epoch:03d}.bundle'
            )
        entry = {
            'repository': config['repository'],
            'repo_path': str(repo_path),
            'policy': 'epoch',
            'object_format': 'sha1',
            'branch': branch,
            'branch_ref': branch_ref,
            'active_remote': active_remote,
            'active_url': config.get('active_url')
            or _default_remote_url(repo_path, active_remote or 'origin'),
            'history_store': history_store,
            'history_store_id': config.get('history_store_id')
            or _history_store_id(history_store),
            'old_epoch': active_epoch,
            'new_epoch': new_epoch,
            'old_tip': old_tip,
            'old_tree': old_tree,
            'old_roots': _root_commits(repo_path, old_tip),
            'refs': refs,
            'remote_refs': remote_refs,
            'retire_extra_branches': retire_extra_branches,
            'translations': translations,
            'equivalence': 'recursive' if translations else 'exact-tree',
            'new_tree': new_tree,
            'successor_root': successor_root,
            'successor_message': message,
            'identity': identity,
            'boundary_id': boundary_id,
            'submodules': node['submodules'],
            'bundle_path': bundle_path,
        }
        entries.append(entry)
        successor_by_id[config['repository']] = entry

    if not entries:
        raise EpochError('No epoch-policy repositories were found to checkpoint')
    plan = {
        'format_version': FORMAT_VERSION,
        'kind': 'git-epoch-checkpoint-plan',
        'transaction_id': transaction_id,
        'created_at': _isoformat(
            datetime_mod.datetime.fromtimestamp(timestamp, datetime_mod.timezone.utc)
        ),
        'root_repository': str(root_repo),
        'recursive': recursive,
        'retire_extra_branches': retire_extra_branches,
        'repositories': entries,
    }
    plan['digest'] = _plan_digest(plan)
    return plan


def _plan_digest(plan: Mapping[str, Any]) -> str:
    material = dict(plan)
    material.pop('digest', None)
    text = yaml.safe_dump(material, sort_keys=True)
    return hashlib.sha256(text.encode()).hexdigest()


def _validate_plan(plan: Mapping[str, Any]) -> None:
    if plan.get('format_version') != FORMAT_VERSION:
        raise EpochError(f'Unsupported plan format: {plan.get("format_version")!r}')
    if plan.get('kind') != 'git-epoch-checkpoint-plan':
        raise EpochError(f'Unknown plan kind: {plan.get("kind")!r}')
    if plan.get('digest') != _plan_digest(plan):
        raise EpochError('Plan digest does not match plan contents')
    entries = plan.get('repositories')
    if not isinstance(entries, list) or not entries:
        raise EpochError('Plan contains no repository entries')


def _transaction_dir(entry: Mapping[str, Any], transaction_id: str) -> pathlib.Path:
    repo = pathlib.Path(entry['repo_path'])
    return _transactions_dir(repo) / transaction_id


def _transaction_state(entry: Mapping[str, Any], transaction_id: str) -> dict[str, Any]:
    path = _transaction_dir(entry, transaction_id) / 'state.yaml'
    if path.exists():
        return _read_yaml(path)
    return {}


def _write_transaction_files(
    plan: Mapping[str, Any],
    entry: Mapping[str, Any],
    state: Mapping[str, Any],
) -> None:
    tx_dir = _transaction_dir(entry, plan['transaction_id'])
    tx_dir.mkdir(parents=True, exist_ok=True)
    _write_yaml(tx_dir / 'plan.yaml', plan)
    old_refs = {item['source']: item['oid'] for item in entry['refs']}
    new_refs = {entry['branch_ref']: entry['successor_root']}
    _write_yaml(tx_dir / 'old-refs.yaml', old_refs)
    _write_yaml(tx_dir / 'new-refs.yaml', new_refs)
    _write_yaml(tx_dir / 'state.yaml', state)


def _write_transaction_verification(
    plan: Mapping[str, Any],
    entry: Mapping[str, Any],
    verification: Mapping[str, Any],
) -> None:
    tx_dir = _transaction_dir(entry, plan['transaction_id'])
    _write_yaml(tx_dir / 'verification.yaml', verification)


def _assert_no_prepared_transaction(repo: pathlib.Path) -> None:
    root = _transactions_dir(repo)
    if not root.exists():
        return
    pending = []
    for state_path in root.glob('*/state.yaml'):
        with contextlib.suppress(Exception):
            state = _read_yaml(state_path)
            if state.get('status') in {'preparing', 'prepared', 'publishing'}:
                pending.append(state_path.parent.name)
    if pending:
        raise EpochSafetyError(
            f'Incomplete git-epoch transaction(s) in {repo}: {pending}. '
            'Publish or inspect the existing transaction before planning another.'
        )


def find_transactions(repo: str | os.PathLike[str] = '.') -> list[dict[str, Any]]:
    repo_path = _repo_root(repo)
    root = _transactions_dir(repo_path)
    result = []
    if root.exists():
        for state_path in sorted(root.glob('*/state.yaml')):
            state = _read_yaml(state_path)
            result.append({'transaction_id': state_path.parent.name, **state})
    return result


def load_plan(path: str | os.PathLike[str]) -> dict[str, Any]:
    plan = _read_yaml(pathlib.Path(path))
    _validate_plan(plan)
    return plan


def save_plan(plan: Mapping[str, Any], path: str | os.PathLike[str]) -> pathlib.Path:
    _validate_plan(plan)
    path = pathlib.Path(path)
    _write_yaml(path, plan)
    return path


def _assert_plan_inputs_current(entry: Mapping[str, Any]) -> None:
    repo = pathlib.Path(entry['repo_path'])
    _assert_clean(repo)
    _assert_one_worktree(repo)
    current = _resolve_ref(repo, entry['branch_ref'])
    if current != entry['old_tip']:
        raise EpochPlanStaleError(
            f'{entry["repository"]} changed since plan generation: '
            f'{entry["branch_ref"]} is {current}, expected {entry["old_tip"]}'
        )
    head_oid = _resolve_ref(repo, 'HEAD')
    if head_oid != entry['old_tip']:
        raise EpochPlanStaleError(
            f'{entry["repository"]} HEAD changed since plan generation: '
            f'{head_oid}, expected {entry["old_tip"]}'
        )

    expected_local_heads = {
        item['source']: item['oid']
        for item in entry['refs']
        if item['source'].startswith('refs/heads/')
        and item.get('local_present', True)
    }
    current_local_heads = _local_heads(repo)
    if current_local_heads != expected_local_heads:
        raise EpochPlanStaleError(
            f'Local branches changed since planning for {entry["repository"]}: '
            f'expected={expected_local_heads}, current={current_local_heads}'
        )

    remote = entry.get('active_remote')
    if remote and _default_remote_url(repo, remote):
        expected_remote = entry.get('remote_refs', {})
        current_remote = _remote_refs(repo, remote)
        if current_remote != expected_remote:
            raise EpochPlanStaleError(
                f'Active remote refs changed since planning for '
                f'{entry["repository"]}; re-plan before archiving.'
            )

    config = load_config(repo)
    if int(config['active_epoch']) != int(entry['old_epoch']):
        raise EpochPlanStaleError(
            f'{entry["repository"]} active epoch changed since planning: '
            f'{config["active_epoch"]} != {entry["old_epoch"]}'
        )
    for submodule in entry.get('submodules', []):
        current_gitlink = _gitlink_oid(repo, entry['old_tip'], submodule['path'])
        if current_gitlink != submodule['commit']:
            raise EpochPlanStaleError(
                f'Submodule gitlink changed for {entry["repository"]}:'
                f'{submodule["path"]}'
            )


def _bundle_heads(repo: pathlib.Path, bundle_path: pathlib.Path) -> dict[str, str]:
    text = _git_stdout(repo, 'bundle', 'list-heads', bundle_path)
    heads = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        oid, ref = line.split(' ', 1)
        heads[ref] = oid
    return heads


def _create_bundle(entry: Mapping[str, Any]) -> dict[str, Any] | None:
    bundle_path_text = entry.get('bundle_path')
    if not bundle_path_text:
        return None
    repo = pathlib.Path(entry['repo_path'])
    bundle_path = pathlib.Path(bundle_path_text)
    bundle_path.parent.mkdir(parents=True, exist_ok=True)
    expected_heads = {item['source']: item['oid'] for item in entry['refs']}
    if bundle_path.exists():
        verify = _git(repo, 'bundle', 'verify', bundle_path, check=False)
        if verify.returncode != 0:
            raise EpochSafetyError(
                f'Existing bundle failed verification: {bundle_path}'
            )
        actual_heads = _bundle_heads(repo, bundle_path)
        if actual_heads != expected_heads:
            raise EpochSafetyError(
                f'Existing bundle contains different refs: {bundle_path}; '
                f'expected={expected_heads}, actual={actual_heads}'
            )
        return {
            'path': str(bundle_path),
            'sha256': hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
            'bytes': bundle_path.stat().st_size,
            'status': 'already-present',
            'refs': actual_heads,
        }
    refs = [item['source'] for item in entry['refs']]
    with tempfile.TemporaryDirectory(prefix='git-epoch-bundle-stage-') as tmp:
        stage = pathlib.Path(tmp) / 'stage.git'
        _run(['git', 'init', '--bare', stage])
        refspecs = [
            f'+{item.get("local_source", item["source"])}:{item["source"]}'
            for item in entry['refs']
        ]
        _run(
            [
                'git',
                '--git-dir',
                stage,
                'fetch',
                '--no-tags',
                str(repo),
                *refspecs,
            ]
        )
        _run(
            ['git', '--git-dir', stage, 'bundle', 'create', bundle_path, *refs]
        )
        _run(['git', '--git-dir', stage, 'bundle', 'verify', bundle_path])
        actual_heads = _bundle_heads(stage, bundle_path)
    if actual_heads != expected_heads:
        raise EpochSafetyError(
            f'Created bundle contains different refs: {bundle_path}; '
            f'expected={expected_heads}, actual={actual_heads}'
        )
    return {
        'path': str(bundle_path),
        'sha256': hashlib.sha256(bundle_path.read_bytes()).hexdigest(),
        'bytes': bundle_path.stat().st_size,
        'status': 'created',
        'refs': actual_heads,
    }

def _archive_entry(entry: Mapping[str, Any]) -> dict[str, Any]:
    repo = pathlib.Path(entry['repo_path'])
    store = HistoryStore(entry['history_store'], repo)
    store.ensure()
    archived = {}
    expected = {}
    for item in entry['refs']:
        source_ref = item.get('local_source', item['source'])
        status = store.archive_ref(source_ref, item['archive'], item['oid'])
        archived[item['archive']] = status
        expected[item['archive']] = item['oid']
    store.verify_refs(expected, fsck=True)
    bundle = _create_bundle(entry)
    return {
        'archive_refs': expected,
        'archive_status': archived,
        'bundle': bundle,
        'fsck': 'passed',
    }


def _materialize_successor(entry: Mapping[str, Any]) -> None:
    repo = pathlib.Path(entry['repo_path'])
    tree = _write_successor_tree(
        repo,
        entry['old_tip'],
        entry.get('translations', []),
    )
    if tree != entry['new_tree']:
        raise EpochPlanStaleError(
            f'Successor tree changed for {entry["repository"]}: '
            f'{tree} != {entry["new_tree"]}'
        )
    commit = _compute_successor_commit(
        repo,
        tree,
        entry['successor_message'],
        entry['identity'],
    )
    if commit != entry['successor_root']:
        raise EpochSafetyError(
            f'Successor commit mismatch for {entry["repository"]}: '
            f'{commit} != {entry["successor_root"]}'
        )


def _raw_tree_diff(repo: pathlib.Path, old: str, new: str) -> list[dict[str, Any]]:
    proc = _git(
        repo,
        'diff-tree',
        '-r',
        '--no-commit-id',
        '--raw',
        '-z',
        old,
        new,
        text=False,
    )
    raw = proc.stdout
    assert isinstance(raw, bytes)
    fields = raw.split(b'\0')
    if fields and not fields[-1]:
        fields.pop()
    if len(fields) % 2:
        raise EpochError('Unable to parse NUL-delimited git diff-tree output')
    result = []
    for index in range(0, len(fields), 2):
        meta = fields[index].decode('ascii')
        path = os.fsdecode(fields[index + 1])
        parts = meta.split()
        if len(parts) != 5 or not parts[0].startswith(':'):
            raise EpochError(f'Unexpected git diff-tree record: {meta!r}')
        old_mode = parts[0][1:]
        new_mode = parts[1]
        old_oid = parts[2]
        new_oid = parts[3]
        status = parts[4]
        result.append(
            {
                'path': path,
                'old_mode': old_mode,
                'new_mode': new_mode,
                'old_oid': old_oid,
                'new_oid': new_oid,
                'status': status,
            }
        )
    return result

def verify_boundary(entry: Mapping[str, Any]) -> dict[str, Any]:
    repo = pathlib.Path(entry['repo_path'])
    old_tip = entry['old_tip']
    new_root = entry['successor_root']
    parents = _git_stdout(repo, 'rev-list', '--parents', '-n', '1', new_root).split()
    if len(parents) != 1:
        raise EpochSafetyError(
            f'Successor is not a root commit for {entry["repository"]}: {new_root}'
        )
    new_tree = _tree_oid(repo, new_root)
    if new_tree != entry['new_tree']:
        raise EpochSafetyError(
            f'Successor tree mismatch for {entry["repository"]}: '
            f'{new_tree} != {entry["new_tree"]}'
        )
    translations = entry.get('translations', [])
    if not translations:
        old_tree = _tree_oid(repo, old_tip)
        if old_tree != new_tree:
            raise EpochSafetyError(
                f'Exact-tree boundary failed for {entry["repository"]}: '
                f'{old_tree} != {new_tree}'
            )
        return {'kind': 'exact-tree', 'tree': new_tree, 'verified': True}
    diffs = _raw_tree_diff(repo, old_tip, new_root)
    expected = {
        item['path']: (
            item['old_commit'],
            item['new_commit'],
        )
        for item in translations
    }
    observed = {}
    for diff in diffs:
        if diff['old_mode'] != '160000' or diff['new_mode'] != '160000':
            raise EpochSafetyError(
                'Recursive boundary changed ordinary content in '
                f'{entry["repository"]}: {diff}'
            )
        observed[diff['path']] = (diff['old_oid'], diff['new_oid'])
    if observed != expected:
        raise EpochSafetyError(
            f'Recursive boundary translations differ for {entry["repository"]}: '
            f'expected={expected}, observed={observed}'
        )
    return {
        'kind': 'recursive',
        'translations': list(translations),
        'verified': True,
    }


def _manifest_add_entry(
    manifest: dict[str, Any],
    entry: Mapping[str, Any],
    *,
    transaction_id: str,
    verification: Mapping[str, Any] | None = None,
) -> None:
    _manifest_validate(manifest)
    repository_id = entry['repository']
    current_config = load_config(entry['repo_path'])
    manifest.setdefault('repositories', {})[repository_id] = {
        **_active_repo_record(pathlib.Path(entry['repo_path']), current_config),
        'primary_branch': entry['branch'],
        'submodules': current_config.get('submodules', {}),
    }
    epochs = manifest.setdefault('epochs', {}).setdefault(repository_id, [])
    same_number = [e for e in epochs if int(e['number']) == int(entry['old_epoch'])]
    epoch_record = {
        'number': entry['old_epoch'],
        'roots': entry['old_roots'],
        'main_tip': entry['old_tip'],
        'refs_namespace': (
            f'refs/epochs/{repository_id}/{int(entry["old_epoch"]):03d}'
        ),
        'refs': [
            {
                'source': item['source'],
                'archive': item['archive'],
                'oid': item['oid'],
            }
            for item in entry['refs']
        ],
        'closed_at': _isoformat(),
        'transaction_id': transaction_id,
        'state': 'prepared',
    }
    if verification is not None:
        bundle = verification.get('bundle')
        verification_record: dict[str, Any] = {
            'archive_refs': 'verified',
            'fsck': verification.get('fsck'),
        }
        if bundle:
            verification_record['bundle'] = {
                'sha256': bundle.get('sha256'),
                'filename': pathlib.Path(bundle['path']).name,
                'bytes': bundle.get('bytes'),
            }
        epoch_record['verification'] = verification_record
    if same_number:
        existing = same_number[0]
        stable_keys = [
            'number',
            'roots',
            'main_tip',
            'refs_namespace',
            'refs',
            'verification',
        ]
        if any(existing.get(key) != epoch_record.get(key) for key in stable_keys):
            raise EpochSafetyError(
                f'Epoch {repository_id}:{entry["old_epoch"]} is already archived '
                'with different immutable metadata'
            )
    else:
        epochs.append(epoch_record)
        epochs.sort(key=lambda e: int(e['number']))
    boundaries = manifest.setdefault('boundaries', [])
    boundary = {
        'id': entry['boundary_id'],
        'repository': repository_id,
        'previous': {
            'epoch': entry['old_epoch'],
            'commit': entry['old_tip'],
            'tree': entry['old_tree'],
        },
        'successor': {
            'epoch': entry['new_epoch'],
            'commit': entry['successor_root'],
            'tree': entry['new_tree'],
        },
        'equivalence': {
            'kind': entry['equivalence'],
            'translations': entry.get('translations', []),
        },
        'created': {
            'timestamp': _isoformat(),
            'tool_version': _tool_version(),
            'transaction_id': transaction_id,
        },
        'state': 'prepared',
    }
    matches = [b for b in boundaries if b.get('id') == boundary['id']]
    if matches:
        existing = matches[0]
        stable_keys = ['repository', 'previous', 'successor', 'equivalence']
        if any(existing.get(key) != boundary.get(key) for key in stable_keys):
            raise EpochSafetyError(
                f'Boundary {boundary["id"]} already exists with different data'
            )
    else:
        boundaries.append(boundary)


def _tool_version() -> str:
    try:
        from git_well import __version__
    except Exception:
        return 'unknown'
    return __version__


def _mark_manifest_entry_state(
    entry: Mapping[str, Any],
    transaction_id: str,
    state: str,
) -> str | None:
    repo = pathlib.Path(entry['repo_path'])
    store = HistoryStore(entry['history_store'], repo)
    manifest, old_meta = store.load_manifest()
    changed = False
    for epoch in manifest.get('epochs', {}).get(entry['repository'], []):
        if (
            int(epoch.get('number', -1)) == int(entry['old_epoch'])
            and epoch.get('transaction_id') == transaction_id
            and epoch.get('state') != state
        ):
            epoch['state'] = state
            changed = True
    for boundary in manifest.get('boundaries', []):
        created = boundary.get('created', {})
        if (
            boundary.get('id') == entry['boundary_id']
            and created.get('transaction_id') == transaction_id
            and boundary.get('state') != state
        ):
            boundary['state'] = state
            changed = True
    if not changed:
        return old_meta
    return store.update_manifest(
        manifest,
        expected_old=old_meta,
        message=(
            f'Mark git epoch {entry["repository"]}:{entry["old_epoch"]} '
            f'{state}'
        ),
    )


def _update_manifests_for_plan(plan: Mapping[str, Any]) -> dict[str, str]:
    groups: dict[str, list[Mapping[str, Any]]] = {}
    for entry in plan['repositories']:
        groups.setdefault(entry['history_store'], []).append(entry)
    results = {}
    for store_spec, entries in groups.items():
        source_repo = pathlib.Path(entries[0]['repo_path'])
        store = HistoryStore(store_spec, source_repo)
        manifest, old_meta = store.load_manifest()
        _manifest_validate(manifest)
        expected_store_ids = {entry['history_store_id'] for entry in entries}
        if len(expected_store_ids) != 1:
            raise EpochSafetyError(
                f'One physical history store cannot use multiple logical ids in '
                f'the same transaction: {sorted(expected_store_ids)}'
            )
        expected_store_id = next(iter(expected_store_ids))
        manifest_store_id = manifest.get('history_store', {}).get('id')
        if old_meta is None:
            manifest.setdefault('history_store', {})['id'] = expected_store_id
        elif manifest_store_id != expected_store_id:
            raise EpochSafetyError(
                'History-store identity mismatch before manifest update: '
                f'{manifest_store_id!r} != {expected_store_id!r}'
            )
        for entry in entries:
            verification_path = (
                _transaction_dir(entry, plan['transaction_id'])
                / 'verification.yaml'
            )
            verification = (
                _read_yaml(verification_path)
                if verification_path.exists()
                else None
            )
            _manifest_add_entry(
                manifest,
                entry,
                transaction_id=plan['transaction_id'],
                verification=verification,
            )
        new_meta = store.update_manifest(
            manifest,
            expected_old=old_meta,
            message=f'Archive git epoch transaction {plan["transaction_id"]}',
        )
        results[store_spec] = new_meta
    return results


def apply_plan(
    plan_or_path: Mapping[str, Any] | str | os.PathLike[str],
    *,
    publish: bool = False,
    fresh_clone: bool = True,
) -> dict[str, Any]:
    if isinstance(plan_or_path, Mapping):
        plan = dict(plan_or_path)
        _validate_plan(plan)
    else:
        plan = load_plan(plan_or_path)
    for entry in plan['repositories']:
        state = _transaction_state(entry, plan['transaction_id'])
        if state.get('status') in {'prepared', 'published'}:
            continue
        _assert_plan_inputs_current(entry)
        state = {
            'status': 'preparing',
            'transaction_id': plan['transaction_id'],
            'repository': entry['repository'],
            'started_at': _isoformat(),
            'phases': {},
        }
        _write_transaction_files(plan, entry, state)
        verification = _archive_entry(entry)
        state['phases']['archive'] = 'verified'
        _write_transaction_files(plan, entry, state)
        _materialize_successor(entry)
        boundary = verify_boundary(entry)
        verification['boundary'] = boundary
        state['phases']['successor'] = 'verified'
        _write_transaction_verification(plan, entry, verification)
        state['status'] = 'prepared'
        state['prepared_at'] = _isoformat()
        _write_transaction_files(plan, entry, state)
    manifest_commits = _update_manifests_for_plan(plan)
    from .locator import verify_public_archive_visibility

    public_visibility: dict[str, Any] = {}
    for entry in plan['repositories']:
        state = _transaction_state(entry, plan['transaction_id'])
        state['manifest_commits'] = manifest_commits
        visibility = verify_public_archive_visibility(
            entry['repo_path'],
            history_store_id=entry['history_store_id'],
            manifest_oid=manifest_commits[entry['history_store']],
            archive_refs={item['archive']: item['oid'] for item in entry['refs']},
        )
        if visibility is not None:
            public_visibility[entry['repository']] = visibility
            state.setdefault('phases', {})['public_archive'] = 'verified'
        _write_transaction_files(plan, entry, state)
    result = {
        'transaction_id': plan['transaction_id'],
        'status': 'prepared',
        'manifest_commits': manifest_commits,
    }
    if public_visibility:
        result['public_archive_visibility'] = public_visibility
    if publish:
        publish_result = publish_plan(plan, fresh_clone=fresh_clone)
        result.update(publish_result)
    return result


def _assert_publish_worktree_safe(entry: Mapping[str, Any]) -> None:
    repo = pathlib.Path(entry['repo_path'])
    branch_ref = entry['branch_ref']
    current = _resolve_ref(repo, branch_ref)
    head_oid = _resolve_ref(repo, 'HEAD')
    if head_oid not in {entry['old_tip'], entry['successor_root']}:
        raise EpochPlanStaleError(
            f'HEAD moved before publication in {repo}: {head_oid}'
        )
    if current == entry['old_tip'] and head_oid != entry['old_tip']:
        raise EpochPlanStaleError(
            f'HEAD no longer identifies the retiring primary tip in {repo}'
        )

    index_bases = [entry['old_tip']]
    if current == entry['successor_root']:
        index_bases.append(entry['successor_root'])
    index_matches = False
    for base in index_bases:
        proc = _git(
            repo,
            'diff-index',
            '--cached',
            '--quiet',
            base,
            '--',
            check=False,
        )
        if proc.returncode == 0:
            index_matches = True
            break
        if proc.returncode not in {0, 1}:
            raise EpochError(
                f'Unable to verify index before publication in {repo}: '
                f'{proc.stderr.strip()}'
            )
    if not index_matches:
        raise EpochSafetyError(
            f'Index changed after checkpoint preparation in {repo}; '
            'refuse to publish.'
        )

    worktree = _git(
        repo,
        'diff-files',
        '--quiet',
        '--ignore-submodules=all',
        '--',
        check=False,
    )
    if worktree.returncode == 1:
        raise EpochSafetyError(
            f'Working tree changed after checkpoint preparation in {repo}; '
            'refuse to publish.'
        )
    if worktree.returncode != 0:
        raise EpochError(
            f'Unable to verify working tree before publication in {repo}: '
            f'{worktree.stderr.strip()}'
        )
    untracked = _git_stdout(
        repo, 'ls-files', '--others', '--exclude-standard', '-z'
    )
    if untracked:
        paths = [p for p in untracked.split('\0') if p]
        raise EpochSafetyError(
            f'Untracked files appeared after checkpoint preparation in {repo}: '
            + ', '.join(paths[:20])
        )


def _archive_refs_verified(entry: Mapping[str, Any]) -> None:
    repo = pathlib.Path(entry['repo_path'])
    store = HistoryStore(entry['history_store'], repo)
    expected = {item['archive']: item['oid'] for item in entry['refs']}
    store.verify_refs(expected, fsck=False)


def _delete_local_tag(repo: pathlib.Path, ref: str, expected: str) -> None:
    current_proc = _git(repo, 'rev-parse', '--verify', ref, check=False)
    if current_proc.returncode:
        return
    current = current_proc.stdout.strip()
    if current != expected:
        raise EpochPlanStaleError(
            f'Local tag changed before publication: {ref} is {current}, '
            f'expected {expected}'
        )
    _git(repo, 'update-ref', '-d', ref, expected)


def _publish_remote(entry: Mapping[str, Any]) -> None:
    repo = pathlib.Path(entry['repo_path'])
    remote = entry.get('active_remote')
    if not remote or not _default_remote_url(repo, remote):
        return
    expected_remote = entry.get('remote_refs', {})
    current_remote = _remote_refs(repo, remote)
    branch_ref = entry['branch_ref']

    expected_heads = {
        ref: oid
        for ref, oid in expected_remote.items()
        if ref.startswith('refs/heads/')
    }
    current_heads = {
        ref: oid
        for ref, oid in current_remote.items()
        if ref.startswith('refs/heads/')
    }
    expected_tags = {
        ref: oid
        for ref, oid in expected_remote.items()
        if ref.startswith('refs/tags/')
    }
    current_tags = {
        ref: oid
        for ref, oid in current_remote.items()
        if ref.startswith('refs/tags/')
    }

    old_remote_tip = expected_heads.get(branch_ref)
    current_tip = current_heads.get(branch_ref)
    expected_extra_heads = {
        ref: oid for ref, oid in expected_heads.items() if ref != branch_ref
    }
    current_extra_heads = {
        ref: oid for ref, oid in current_heads.items() if ref != branch_ref
    }

    # A completed atomic remote publication is a valid resume point after a
    # process failure before local refs/config were updated.
    if current_tip == entry['successor_root']:
        if current_extra_heads or current_tags:
            raise EpochPlanStaleError(
                f'Remote primary branch is already published but retired refs '
                f'remain for {entry["repository"]}: '
                f'heads={current_extra_heads}, tags={current_tags}'
            )
        return

    if old_remote_tip is None:
        if current_tip is not None:
            raise EpochPlanStaleError(
                f'Remote branch appeared since planning: {branch_ref}={current_tip}'
            )
    elif current_tip != old_remote_tip:
        raise EpochPlanStaleError(
            f'Remote branch changed since planning: {branch_ref} is {current_tip}, '
            f'expected {old_remote_tip}'
        )

    if current_extra_heads != expected_extra_heads:
        raise EpochPlanStaleError(
            f'Remote auxiliary branches changed since planning for '
            f'{entry["repository"]}: expected={expected_extra_heads}, '
            f'current={current_extra_heads}'
        )
    if current_tags != expected_tags:
        raise EpochPlanStaleError(
            f'Remote tags changed since planning for {entry["repository"]}: '
            f'expected={expected_tags}, current={current_tags}'
        )

    args = ['git', 'push', '--atomic']
    refspecs = []
    if old_remote_tip is None:
        args.append(f'--force-with-lease={branch_ref}:')
    else:
        args.append(f'--force-with-lease={branch_ref}:{old_remote_tip}')
    refspecs.append(f'{entry["successor_root"]}:{branch_ref}')

    for ref, oid in sorted(expected_extra_heads.items()):
        args.append(f'--force-with-lease={ref}:{oid}')
        refspecs.append(f':{ref}')
    for ref, oid in sorted(expected_tags.items()):
        args.append(f'--force-with-lease={ref}:{oid}')
        refspecs.append(f':{ref}')
    args.append(remote)
    args.extend(refspecs)
    _run(args, cwd=repo)


def _publish_local(entry: Mapping[str, Any]) -> None:
    repo = pathlib.Path(entry['repo_path'])
    branch_ref = entry['branch_ref']
    heads = _local_heads(repo)
    current = heads.get(branch_ref)
    if current not in {entry['old_tip'], entry['successor_root']}:
        raise EpochPlanStaleError(
            f'Local branch changed before publication: {branch_ref} is {current}, '
            f'expected {entry["old_tip"]}'
        )

    expected_local_heads = {
        item['source']: item['oid']
        for item in entry['refs']
        if item['source'].startswith('refs/heads/')
        and item.get('local_present', True)
    }
    expected_local_extras = {
        ref: oid for ref, oid in expected_local_heads.items() if ref != branch_ref
    }
    current_local_extras = {
        ref: oid for ref, oid in heads.items() if ref != branch_ref
    }
    if current_local_extras != expected_local_extras:
        raise EpochPlanStaleError(
            f'Local auxiliary branches changed before publication for '
            f'{entry["repository"]}: expected={expected_local_extras}, '
            f'current={current_local_extras}'
        )

    expected_tags = {
        item['source']: item['oid']
        for item in entry['refs']
        if item['source'].startswith('refs/tags/')
    }
    current_tags = _local_tags(repo)
    if current_tags != expected_tags:
        raise EpochPlanStaleError(
            f'Local tags changed before publication for {entry["repository"]}: '
            f'expected={expected_tags}, current={current_tags}'
        )

    commands = ['start']
    if current == entry['old_tip']:
        commands.append(
            f'update {branch_ref} {entry["successor_root"]} {entry["old_tip"]}'
        )
    for ref, oid in sorted(expected_local_extras.items()):
        commands.append(f'delete {ref} {oid}')
    for ref, oid in sorted(current_tags.items()):
        commands.append(f'delete {ref} {oid}')
    commands.extend(['prepare', 'commit', ''])
    if len(commands) > 4:
        _git(repo, 'update-ref', '--stdin', input='\n'.join(commands))

    current_branch = _current_branch(repo)
    head_oid = _resolve_ref(repo, 'HEAD')
    if current_branch == entry['branch'] or (
        current_branch is None
        and head_oid in {entry['old_tip'], entry['successor_root']}
    ):
        _git(repo, 'reset', '--hard', entry['successor_root'])
    else:
        raise EpochPlanStaleError(
            f'HEAD changed during local publication in {repo}'
        )


def _update_remote_tracking(entry: Mapping[str, Any]) -> None:
    repo = pathlib.Path(entry['repo_path'])
    remote = entry.get('active_remote')
    if not remote:
        return
    tracking = f'refs/remotes/{remote}/{entry["branch"]}'
    proc = _git(repo, 'rev-parse', '--verify', tracking, check=False)
    if proc.returncode == 0:
        current = proc.stdout.strip()
        if current in {entry['old_tip'], entry['successor_root']}:
            _git(repo, 'update-ref', tracking, entry['successor_root'])

    for item in entry['refs']:
        source = item['source']
        if (
            not source.startswith('refs/heads/')
            or source == entry['branch_ref']
            or not item.get('remote_present', False)
        ):
            continue
        branch_name = source[len('refs/heads/') :]
        tracking_ref = f'refs/remotes/{remote}/{branch_name}'
        proc = _git(repo, 'rev-parse', '--verify', tracking_ref, check=False)
        if proc.returncode == 0 and proc.stdout.strip() == item['oid']:
            _git(repo, 'update-ref', '-d', tracking_ref, item['oid'])


def _update_active_epoch_config(entry: Mapping[str, Any]) -> None:
    repo = pathlib.Path(entry['repo_path'])
    config = load_config(repo)
    old = int(config['active_epoch'])
    if old == int(entry['new_epoch']):
        return
    if old != int(entry['old_epoch']):
        raise EpochPlanStaleError(
            f'Config active_epoch changed before publication in {repo}: {old}'
        )
    config['active_epoch'] = int(entry['new_epoch'])
    config['active_url'] = _default_remote_url(
        repo, config.get('active_remote', 'origin')
    )
    save_config(repo, config)


def _fresh_clone_validate(
    entry: Mapping[str, Any],
    *,
    recursive: bool = False,
) -> dict[str, Any]:
    repo = pathlib.Path(entry['repo_path'])
    remote = entry.get('active_remote')
    if remote and _default_remote_url(repo, remote):
        source = _default_remote_url(repo, remote)
    else:
        source = f'file://{repo}'
    assert source is not None
    with tempfile.TemporaryDirectory(prefix='git-epoch-clone-') as tmp:
        clone = pathlib.Path(tmp) / 'clone'
        clone_args = [
            'git',
            '-c',
            'protocol.file.allow=always',
            'clone',
            '--no-local',
            '--branch',
            entry['branch'],
        ]
        clone_args.extend([source, clone])
        _run(clone_args)
        head = _resolve_ref(clone, 'HEAD')
        if head != entry['successor_root']:
            raise EpochSafetyError(
                f'Fresh clone HEAD mismatch for {entry["repository"]}: '
                f'{head} != {entry["successor_root"]}'
            )
        old = _git(clone, 'cat-file', '-e', f'{entry["old_tip"]}^{{commit}}', check=False)
        if old.returncode == 0:
            raise EpochSafetyError(
                f'Retired epoch commit is still present in ordinary fresh clone: '
                f'{entry["old_tip"]}'
            )
        recursive_gitlinks = None
        if recursive:
            recursive_gitlinks = {}
            for translation in entry.get('translations', []):
                path = translation['path']
                actual = _gitlink_oid(clone, 'HEAD', path)
                expected = translation['new_commit']
                if actual != expected:
                    raise EpochSafetyError(
                        f'Fresh clone gitlink mismatch at {path}: '
                        f'{actual} != {expected}'
                    )
                recursive_gitlinks[path] = actual
        git_size = _directory_size(_git_dir(clone))
        return {
            'head': head,
            'git_size': git_size,
            'git_size_human': _format_bytes(git_size),
            'retired_tip_present': False,
            'recursive_gitlinks': recursive_gitlinks,
        }


def publish_plan(
    plan_or_path: Mapping[str, Any] | str | os.PathLike[str],
    *,
    fresh_clone: bool = True,
) -> dict[str, Any]:
    if isinstance(plan_or_path, Mapping):
        plan = dict(plan_or_path)
        _validate_plan(plan)
    else:
        plan = load_plan(plan_or_path)
    clone_results = {}
    for entry in plan['repositories']:
        state = _transaction_state(entry, plan['transaction_id'])
        if not state:
            raise EpochSafetyError(
                f'Transaction has not been prepared for {entry["repository"]}'
            )
        if state.get('status') == 'published':
            continue
        if state.get('status') not in {'prepared', 'publishing'}:
            raise EpochSafetyError(
                f'Transaction is not ready to publish for {entry["repository"]}: '
                f'{state.get("status")}'
            )
        current = _resolve_ref(pathlib.Path(entry['repo_path']), entry['branch_ref'])
        if current not in {entry['old_tip'], entry['successor_root']}:
            raise EpochPlanStaleError(
                f'{entry["repository"]} changed before publication: {current}'
            )
        _assert_publish_worktree_safe(entry)
        _archive_refs_verified(entry)
        verify_boundary(entry)
        state['status'] = 'publishing'
        _write_transaction_files(plan, entry, state)
        _publish_remote(entry)
        _publish_local(entry)
        _update_remote_tracking(entry)
        _update_active_epoch_config(entry)
        committed_meta = _mark_manifest_entry_state(
            entry, plan['transaction_id'], 'committed'
        )
        state['committed_manifest'] = committed_meta
        state['status'] = 'published'
        state['published_at'] = _isoformat()
        _write_transaction_files(plan, entry, state)
    if fresh_clone:
        for entry in plan['repositories']:
            clone_results[entry['repository']] = _fresh_clone_validate(
                entry,
                recursive=bool(entry.get('translations')),
            )
            verification_path = (
                _transaction_dir(entry, plan['transaction_id'])
                / 'verification.yaml'
            )
            verification = _read_yaml(verification_path)
            verification['fresh_clone'] = clone_results[entry['repository']]
            _write_yaml(verification_path, verification)
    return {
        'transaction_id': plan['transaction_id'],
        'status': 'published',
        'fresh_clones': clone_results,
    }


def _remove_prepared_manifest_entry(
    entry: Mapping[str, Any],
    transaction_id: str,
) -> str | None:
    repo = pathlib.Path(entry['repo_path'])
    store = HistoryStore(entry['history_store'], repo)
    manifest, old_meta = store.load_manifest()
    epochs = manifest.get('epochs', {}).get(entry['repository'], [])
    kept_epochs = [
        epoch
        for epoch in epochs
        if not (
            int(epoch.get('number', -1)) == int(entry['old_epoch'])
            and epoch.get('transaction_id') == transaction_id
            and epoch.get('state') == 'prepared'
        )
    ]
    boundaries = manifest.get('boundaries', [])
    kept_boundaries = [
        boundary
        for boundary in boundaries
        if not (
            boundary.get('id') == entry['boundary_id']
            and boundary.get('created', {}).get('transaction_id') == transaction_id
            and boundary.get('state') == 'prepared'
        )
    ]
    changed = len(kept_epochs) != len(epochs) or len(kept_boundaries) != len(boundaries)
    if not changed:
        return old_meta
    manifest.setdefault('epochs', {})[entry['repository']] = kept_epochs
    manifest['boundaries'] = kept_boundaries
    return store.update_manifest(
        manifest,
        expected_old=old_meta,
        message=(
            f'Abort prepared git epoch '
            f'{entry["repository"]}:{entry["old_epoch"]}'
        ),
    )


def abort_plan(
    plan_or_path: Mapping[str, Any] | str | os.PathLike[str],
) -> dict[str, Any]:
    if isinstance(plan_or_path, Mapping):
        plan = dict(plan_or_path)
        _validate_plan(plan)
    else:
        plan = load_plan(plan_or_path)
    # Refuse cleanup after any repository has adopted its successor root.
    for entry in plan['repositories']:
        repo = pathlib.Path(entry['repo_path'])
        current = _resolve_ref(repo, entry['branch_ref'])
        if current == entry['successor_root']:
            raise EpochSafetyError(
                f'Cannot abort transaction {plan["transaction_id"]}: '
                f'{entry["repository"]} has already published its successor. '
                'Resume publication instead.'
            )
        if current != entry['old_tip']:
            raise EpochPlanStaleError(
                f'Cannot abort: {entry["repository"]} branch changed to {current}'
            )
    results = {}
    for entry in reversed(plan['repositories']):
        state = _transaction_state(entry, plan['transaction_id'])
        if state.get('status') == 'published':
            raise EpochSafetyError(
                f'Cannot abort published repository {entry["repository"]}'
            )
        meta = _remove_prepared_manifest_entry(entry, plan['transaction_id'])
        repo = pathlib.Path(entry['repo_path'])
        store = HistoryStore(entry['history_store'], repo)
        for item in entry['refs']:
            store.delete_ref(item['archive'], expected_oid=item['oid'])
        bundle_text = entry.get('bundle_path')
        if bundle_text:
            bundle_path = pathlib.Path(bundle_text)
            if bundle_path.exists():
                abandoned = bundle_path.with_name(
                    bundle_path.name + f'.aborted-{plan["transaction_id"]}'
                )
                bundle_path.rename(abandoned)
        state = {
            **state,
            'status': 'aborted',
            'aborted_at': _isoformat(),
            'manifest_commit': meta,
        }
        _write_transaction_files(plan, entry, state)
        results[entry['repository']] = state
    return {
        'transaction_id': plan['transaction_id'],
        'status': 'aborted',
        'repositories': results,
    }


def abort_latest(repo: str | os.PathLike[str] = '.') -> dict[str, Any]:
    repo_path = _repo_root(repo)
    plan = _find_latest_prepared_plan(repo_path)
    return abort_plan(plan)


def checkpoint(
    repo: str | os.PathLike[str] = '.',
    *,
    recursive: bool = False,
    publish: bool = False,
    bundle: bool = False,
    bundle_dir: str | os.PathLike[str] | None = None,
    fresh_clone: bool = True,
    retire_extra_branches: bool = False,
) -> dict[str, Any]:
    plan = build_plan(
        repo,
        recursive=recursive,
        bundle=bundle,
        bundle_dir=bundle_dir,
        retire_extra_branches=retire_extra_branches,
    )
    result = apply_plan(plan, publish=publish, fresh_clone=fresh_clone)
    result['plan'] = plan
    return result


def _find_latest_prepared_plan(repo: pathlib.Path) -> dict[str, Any]:
    txs = find_transactions(repo)
    pending = [tx for tx in txs if tx.get('status') in {'prepared', 'publishing'}]
    if not pending:
        raise EpochError(f'No prepared git-epoch transaction found in {repo}')
    tx = pending[-1]
    plan_path = _transactions_dir(repo) / tx['transaction_id'] / 'plan.yaml'
    return load_plan(plan_path)


def publish_latest(
    repo: str | os.PathLike[str] = '.',
    *,
    fresh_clone: bool = True,
) -> dict[str, Any]:
    repo_path = _repo_root(repo)
    plan = _find_latest_prepared_plan(repo_path)
    return publish_plan(plan, fresh_clone=fresh_clone)


def status(repo: str | os.PathLike[str] = '.') -> dict[str, Any]:
    repo_path = _repo_root(repo)
    attached = True
    public_context: dict[str, Any] | None = None
    try:
        config = load_config(repo_path, allow_bootstrap=False)
    except EpochError:
        from .locator import resolve_public_epoch_context

        public_context = resolve_public_epoch_context(
            repo_path,
            verify_remote=False,
        )
        config = public_context['config']
        attached = False
    if attached:
        branch = config['primary_branch']
        tip = _resolve_ref(repo_path, f'refs/heads/{branch}')
    else:
        assert public_context is not None
        branch = public_context['observed_branch'] or '(detached)'
        tip = public_context['observed_tip']
    roots = _root_commits(repo_path, tip)
    manifest_info: dict[str, Any]
    if not attached:
        manifest_info = {
            'meta_oid': None,
            'archived_epochs': [],
            'archive_verification': 'not-attached',
        }
    else:
        store = HistoryStore(config['history_store'], repo_path)
        if store.is_local and not store.local_path.exists():
            manifest_info = {
                'meta_oid': None,
                'archived_epochs': [],
                'archive_verification': 'not-initialized',
            }
        else:
            try:
                manifest, meta_oid = store.load_manifest()
                archived_epochs = manifest.get('epochs', {}).get(
                    config['repository'], []
                )
                manifest_info = {
                    'meta_oid': meta_oid,
                    'archived_epochs': [int(e['number']) for e in archived_epochs],
                    'archive_verification': 'available',
                }
            except EpochError as ex:
                manifest_info = {
                    'meta_oid': None,
                    'archived_epochs': [],
                    'archive_verification': f'unavailable: {ex}',
                }
    reachable_size = _reachable_size(repo_path, tip)
    from .locator import read_public_locator

    locator = (
        public_context['locator']
        if public_context is not None
        else read_public_locator(repo_path, required=False)
    )
    public_metadata = None
    if locator is not None:
        public_metadata = {
            'path': str(repo_path / '.git-epoch.yaml'),
            'history_store_id': locator['history_store']['id'],
            'url': locator['history_store']['url'],
            'browse_url': locator['history_store'].get('browse_url'),
        }
    result = {
        'repository': config['repository'],
        'policy': config['policy'],
        'repo_path': str(repo_path),
        'active_epoch': config['active_epoch'],
        'branch': branch,
        'root': roots[0] if len(roots) == 1 else roots,
        'tip': tip,
        'commits': int(_git_stdout(repo_path, 'rev-list', '--count', tip).strip()),
        'reachable_size': reachable_size,
        'reachable_size_human': _format_bytes(reachable_size),
        'history_store': config['history_store'],
        'history_store_id': config.get('history_store_id'),
        'attached': attached,
        'public_locator': public_metadata,
        'submodules': config.get('submodules', {}),
        'transactions': find_transactions(repo_path) if attached else [],
        **manifest_info,
    }
    if not attached:
        result['attach_command'] = 'git epoch attach'
    return result


def _deep_verify_manifest(
    repo_path: pathlib.Path,
    config: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    repository_id = config['repository']
    store = HistoryStore(config['history_store'], repo_path)
    committed_boundaries = [
        boundary
        for boundary in manifest.get('boundaries', [])
        if (
            boundary.get('repository') == repository_id
            and boundary.get('state', 'committed') == 'committed'
        )
    ]
    committed_epochs = [
        epoch
        for epoch in manifest.get('epochs', {}).get(repository_id, [])
        if epoch.get('state', 'committed') == 'committed'
    ]
    with tempfile.TemporaryDirectory(prefix='git-epoch-deep-verify-') as tmp:
        bare = pathlib.Path(tmp) / 'verify.git'
        _run(['git', 'init', '--bare', bare])
        branch = config['primary_branch']
        active_ref = f'refs/active/{branch}'
        _run(
            [
                'git',
                '--git-dir',
                bare,
                'fetch',
                '--no-tags',
                str(repo_path),
                f'+refs/heads/{branch}:{active_ref}',
            ]
        )
        archive_prefix = f'refs/epochs/{repository_id}/'
        archive_refs = {
            ref: oid
            for ref, oid in store.ls_refs().items()
            if ref.startswith(archive_prefix)
        }
        for ref in archive_refs:
            _run(
                [
                    'git',
                    '--git-dir',
                    bare,
                    'fetch',
                    '--no-tags',
                    store.spec,
                    f'+{ref}:{ref}',
                ]
            )
        boundary_results = []
        for boundary in committed_boundaries:
            previous = boundary['previous']['commit']
            successor = boundary['successor']['commit']
            for oid, label in [(previous, 'predecessor'), (successor, 'successor')]:
                proc = _git(bare, 'cat-file', '-e', f'{oid}^{{commit}}', check=False)
                if proc.returncode:
                    raise EpochSafetyError(
                        f'Committed boundary {boundary["id"]} {label} is unavailable: {oid}'
                    )
            actual_previous_tree = _tree_oid(bare, previous)
            actual_successor_tree = _tree_oid(bare, successor)
            if actual_previous_tree != boundary['previous'].get('tree'):
                raise EpochSafetyError(
                    f'Boundary {boundary["id"]} predecessor tree metadata is wrong'
                )
            if actual_successor_tree != boundary['successor'].get('tree'):
                raise EpochSafetyError(
                    f'Boundary {boundary["id"]} successor tree metadata is wrong'
                )
            parent_words = _git_stdout(
                bare, 'rev-list', '--parents', '-n', '1', successor
            ).split()
            if parent_words != [successor]:
                raise EpochSafetyError(
                    f'Boundary {boundary["id"]} successor is not a root commit'
                )
            equivalence = boundary['equivalence']
            kind = equivalence['kind']
            if kind == 'exact-tree':
                if actual_previous_tree != actual_successor_tree:
                    raise EpochSafetyError(
                        f'Boundary {boundary["id"]} exact-tree equivalence failed'
                    )
            elif kind == 'recursive':
                expected = {
                    item['path']: (item['old_commit'], item['new_commit'])
                    for item in equivalence.get('translations', [])
                }
                observed = {}
                for diff in _raw_tree_diff(bare, previous, successor):
                    if diff['old_mode'] != '160000' or diff['new_mode'] != '160000':
                        raise EpochSafetyError(
                            f'Boundary {boundary["id"]} changed ordinary content: {diff}'
                        )
                    observed[diff['path']] = (diff['old_oid'], diff['new_oid'])
                if observed != expected:
                    raise EpochSafetyError(
                        f'Boundary {boundary["id"]} recursive translation mismatch: '
                        f'expected={expected}, observed={observed}'
                    )
            else:
                raise EpochError(
                    f'Unknown equivalence kind in boundary {boundary["id"]}: {kind}'
                )
            boundary_results.append({'id': boundary['id'], 'kind': kind, 'verified': True})
        active_tip = _resolve_ref(bare, active_ref)
        retired_reachable = []
        for epoch in committed_epochs:
            old_tip = epoch['main_tip']
            proc = _git(
                bare,
                'merge-base',
                '--is-ancestor',
                old_tip,
                active_tip,
                check=False,
            )
            if proc.returncode == 0:
                retired_reachable.append(old_tip)
            elif proc.returncode != 1:
                raise EpochSafetyError(
                    f'Unable to evaluate retired reachability for {old_tip}: '
                    f'{proc.stderr.strip()}'
                )
        if retired_reachable:
            raise EpochSafetyError(
                'Committed retired epoch tips remain ancestors of the active branch: '
                + ', '.join(retired_reachable)
            )
        _run(['git', '--git-dir', bare, 'fsck', '--full'])
        return {
            'boundaries': boundary_results,
            'retired_tip_reachable_from_active': False,
            'assembled_archive_refs': len(archive_refs),
        }


def verify(
    repo: str | os.PathLike[str] = '.',
    *,
    deep: bool = False,
) -> dict[str, Any]:
    repo_path = _repo_root(repo)
    config = load_config(repo_path)
    store = HistoryStore(config['history_store'], repo_path)
    manifest, meta_oid = store.load_manifest()
    _manifest_validate(manifest)
    repository_id = config['repository']
    epoch_records = manifest.get('epochs', {}).get(repository_id, [])
    refs = {}
    for epoch in epoch_records:
        for item in epoch.get('refs', []):
            refs[item['archive']] = item['oid']
    store.verify_refs(refs, fsck=deep)
    boundaries = [
        boundary
        for boundary in manifest.get('boundaries', [])
        if (
            boundary.get('repository') == repository_id
            and boundary.get('state', 'committed') == 'committed'
        )
    ]
    boundary_results = []
    for boundary in boundaries:
        previous = boundary['previous']['commit']
        successor = boundary['successor']['commit']
        # The active checkout may not have every archived object, so verify
        # archived boundaries in a temporary reconstruction when needed.
        have_previous = _git(repo_path, 'cat-file', '-e', previous, check=False).returncode == 0
        have_successor = _git(repo_path, 'cat-file', '-e', successor, check=False).returncode == 0
        boundary_results.append(
            {
                'id': boundary['id'],
                'previous_available_locally': have_previous,
                'successor_available_locally': have_successor,
                'kind': boundary['equivalence']['kind'],
            }
        )
    deep_result = None
    if deep:
        deep_result = _deep_verify_manifest(repo_path, config, manifest)
    return {
        'repository': repository_id,
        'manifest': meta_oid,
        'archived_refs': len(refs),
        'boundaries': boundary_results,
        'fsck': 'passed' if deep else 'not-requested',
        'deep': deep_result,
    }


def _insert_parent_into_commit(raw: bytes, parent_oid: str) -> bytes:
    marker = b'\n'
    first_end = raw.find(marker)
    if first_end < 0 or not raw.startswith(b'tree '):
        raise EpochError('Malformed root commit object')
    return raw[: first_end + 1] + f'parent {parent_oid}\n'.encode() + raw[first_end + 1 :]


def _create_replace_graft(repo: pathlib.Path, root: str, predecessor: str) -> str:
    raw = _git(repo, 'cat-file', 'commit', root, text=False).stdout
    replacement_raw = _insert_parent_into_commit(raw, predecessor)
    replacement = _git(
        repo,
        'hash-object',
        '-t',
        'commit',
        '-w',
        '--stdin',
        input=replacement_raw,
        text=False,
    ).stdout.decode().strip()
    ref = f'refs/replace/{root}'
    _git(repo, 'update-ref', ref, replacement)
    return replacement


def _fetch_archived_repo(
    dest: pathlib.Path,
    store_spec: str,
    repository_id: str,
) -> None:
    refspec = (
        f'+refs/epochs/{repository_id}/*:'
        f'refs/epochs/{repository_id}/*'
    )
    _git(dest, 'fetch', '--no-tags', store_spec, refspec)


def _load_read_context(repo_path: pathlib.Path) -> tuple[dict[str, Any], bool]:
    try:
        return load_config(repo_path, allow_bootstrap=False), True
    except EpochError:
        from .locator import resolve_public_epoch_context

        context = resolve_public_epoch_context(repo_path, verify_remote=True)
        return context['config'], False


def reconstruct(
    repo: str | os.PathLike[str] = '.',
    *,
    output: str | os.PathLike[str] | None = None,
    recursive: bool = False,
) -> dict[str, Any]:
    source_repo = _repo_root(repo)
    config, attached = _load_read_context(source_repo)
    repository_id = config['repository']
    if output is None:
        output_path = source_repo.parent / f'{source_repo.name}-reconstructed'
    else:
        output_path = pathlib.Path(output).expanduser().resolve()
    if output_path.exists():
        raise EpochError(f'Reconstruction destination already exists: {output_path}')
    active_source = config.get('active_url') or f'file://{source_repo}'
    _run(
        [
            'git',
            '-c',
            'protocol.file.allow=always',
            'clone',
            '--no-local',
            '--branch',
            config['primary_branch'],
            active_source,
            output_path,
        ]
    )
    store = HistoryStore(config['history_store'], source_repo)
    manifest, meta_oid = store.load_manifest()
    _manifest_validate(manifest)
    _fetch_archived_repo(output_path, config['history_store'], repository_id)
    boundaries = [
        boundary
        for boundary in manifest.get('boundaries', [])
        if (
            boundary.get('repository') == repository_id
            and boundary.get('state', 'committed') == 'committed'
        )
    ]
    replacements = []
    for boundary in sorted(
        boundaries, key=lambda b: int(b['successor']['epoch'])
    ):
        root = boundary['successor']['commit']
        predecessor = boundary['previous']['commit']
        if _git(output_path, 'cat-file', '-e', root, check=False).returncode:
            raise EpochError(
                f'Successor root is unavailable during reconstruction: {root}'
            )
        if _git(output_path, 'cat-file', '-e', predecessor, check=False).returncode:
            raise EpochError(
                f'Predecessor is unavailable during reconstruction: {predecessor}'
            )
        replacement = _create_replace_graft(output_path, root, predecessor)
        replacements.append(
            {
                'root': root,
                'predecessor': predecessor,
                'replacement': replacement,
            }
        )
    reconstruction = {
        'format_version': FORMAT_VERSION,
        'repository': repository_id,
        'source_repository': str(source_repo),
        'history_store': config['history_store'],
        'attached': attached,
        'manifest': meta_oid,
        'replacements': replacements,
        'recursive': recursive,
        'children': {},
    }
    if recursive:
        child_root = output_path / '.git-epoch-repositories'
        for path, item in config.get('submodules', {}).items():
            if item.get('policy') != 'epoch':
                continue
            child_source = source_repo / path
            child_id = item.get('repository')
            if not child_id:
                continue
            child_output = child_root / child_id
            child_result = reconstruct(
                child_source,
                output=child_output,
                recursive=True,
            )
            reconstruction['children'][child_id] = child_result
            name = item.get('name') or path
            _git(
                output_path,
                'config',
                f'submodule.{name}.url',
                str(child_output),
            )
    _write_yaml(output_path / 'reconstruction.yaml', reconstruction)
    return reconstruction


def inspect_manifest(repo: str | os.PathLike[str] = '.') -> dict[str, Any]:
    repo_path = _repo_root(repo)
    config, attached = _load_read_context(repo_path)
    store = HistoryStore(config['history_store'], repo_path)
    manifest, meta_oid = store.load_manifest()
    _manifest_validate(manifest)
    return {
        'attached': attached,
        'history_store': config['history_store'],
        'meta_oid': meta_oid,
        'manifest': manifest,
    }


def gc_history_store(repo: str | os.PathLike[str] = '.') -> dict[str, Any]:
    from .stats import history_store_stats

    repo_path = _repo_root(repo)
    config = load_config(repo_path)
    store = HistoryStore(config['history_store'], repo_path)
    if not store.is_local:
        raise EpochError('git epoch gc currently requires a local history store')
    before_stats = history_store_stats(repo_path)
    before = int(before_stats['history_store']['directory_bytes'])
    before_disk = int(before_stats['history_store']['directory_disk_bytes'])
    _run(['git', '--git-dir', store.local_path, 'gc', '--prune=now'])
    verify_result = verify(repo_path, deep=True)
    after_stats = history_store_stats(repo_path)
    after = int(after_stats['history_store']['directory_bytes'])
    after_disk = int(after_stats['history_store']['directory_disk_bytes'])
    file_delta = after - before
    disk_delta = after_disk - before_disk
    reclaimed_disk = max(before_disk - after_disk, 0)
    reduction_fraction = (before_disk - after_disk) / before_disk if before_disk else 0.0
    return {
        'before': before,
        'after': after,
        'before_human': _format_bytes(before),
        'after_human': _format_bytes(after),
        'file_bytes_delta': file_delta,
        'before_disk_bytes': before_disk,
        'after_disk_bytes': after_disk,
        'before_disk_bytes_human': _format_bytes(before_disk),
        'after_disk_bytes_human': _format_bytes(after_disk),
        'disk_bytes_delta': disk_delta,
        'disk_bytes_reclaimed': reclaimed_disk,
        'disk_bytes_reclaimed_human': _format_bytes(reclaimed_disk),
        'disk_reduction_percent': round(reduction_fraction * 100, 1),
        'verification': verify_result,
    }


def plan_summary(plan: Mapping[str, Any]) -> str:
    _validate_plan(plan)
    lines = ['CHECKPOINT PLAN', '']
    for index, entry in enumerate(plan['repositories'], 1):
        lines.extend(
            [
                f'{index}. {entry["repository"]}',
                f'   epoch {entry["old_epoch"]} -> {entry["new_epoch"]}',
                f'   old tip: {entry["old_tip"]}',
                f'   successor root: {entry["successor_root"]}',
                f'   successor tree: {entry["new_tree"]}',
                f'   history store: {entry["history_store"]}',
            ]
        )
        retired_branches = [
            item for item in entry.get('refs', [])
            if item['source'].startswith('refs/heads/')
            and item['source'] != entry['branch_ref']
        ]
        if retired_branches:
            lines.append('   retire branches after archival verification:')
            for item in retired_branches:
                scopes = []
                if item.get('local_present'):
                    scopes.append('local')
                if item.get('remote_present'):
                    scopes.append('remote')
                scope_text = '+'.join(scopes) or 'archive-only'
                name = item['source'][len('refs/heads/') :]
                lines.append(f'     {name}: {item["oid"]} ({scope_text})')
        if entry.get('translations'):
            lines.append('   translate:')
            for item in entry['translations']:
                lines.append(
                    f'     {item["path"]}: {item["old_commit"]} -> '
                    f'{item["new_commit"]}'
                )
        lines.append('')
    lines.append('No refs have been changed.')
    return '\n'.join(lines)
