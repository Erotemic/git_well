#!/usr/bin/env python3
"""
Reusable IPFS sidecar tooling.

This module is derived from the Shitspotter IPFS helper CLI, but generalized
for use in git-backed repositories.  The central convention is a DVC-like
``*.ipfs`` YAML sidecar containing the CID, relative tracked path, and the
``ipfs add`` options that produced it.

Examples
--------
Add a directory and write ``data.ipfs`` next to it:

    python -m git_well ipfs add data --name my-data

Pull the content described by a sidecar:

    python -m git_well ipfs pull data.ipfs

Inspect local drift quickly, or by recomputing the CID:

    python -m git_well ipfs status .
    python -m git_well ipfs status . --full

Run a first-use health check, add data, and pull it later:

    git ipfs doctor
    git ipfs add data --name my-data
    git ipfs pull

Export local pin commands for all sidecars under the current repo:

    python -m git_well ipfs export . --emit_bash
"""
from __future__ import annotations

import glob
import json
import os
import shlex
import shutil
import tempfile
from pathlib import Path
from typing import Any, Iterable

import scriptconfig as scfg
import ubelt as ub


class IPFSCLI(scfg.ModalCLI):
    """Utilities for git-tracked IPFS sidecar files."""
    __command__ = 'ipfs'


def _register_modal(parent: Any) -> Any:
    """Register a scriptconfig command while preserving the class name.

    Some scriptconfig versions return ``None`` from ``ModalCLI.register``.
    Using that method directly as a decorator can therefore overwrite names
    such as ``IPFSAdd`` with ``None``, which is surprising for tests and for
    downstream callers that import command classes directly.
    """
    def _decorator(cls):
        parent.register(cls)
        return cls
    return _decorator


SIDECAR_SCHEMA_VERSION = 1
MIN_KUBO_VERSION = (0, 37, 0)
MIN_KUBO_VERSION_TEXT = '.'.join(map(str, MIN_KUBO_VERSION))

# Options that affect the CID produced by ``ipfs add`` and therefore must be
# kept in the tracked sidecar.  Runtime/UI flags such as dry_run, progress,
# update_gitignore, and git_add_sidecar intentionally stay out of committed
# sidecars so repeated runs do not create noisy diffs.
CID_IMPORT_KEYS = (
    'recursive',
    'cid_version',
    'raw_leaves',
    'only_hash',
)


KUBO_INSTALL_HINT = (
    f'Kubo >= {MIN_KUBO_VERSION_TEXT} is required. '
    'Install Kubo, make sure `ipfs` is on PATH, then rerun `git ipfs doctor`.'
)
KUBO_DAEMON_HINT = (
    'The Kubo API/daemon does not appear to be reachable. '
    'Start it with `ipfs daemon`, or verify that IPFS_PATH points at the intended Kubo repo.'
)
KUBO_PIN_NAME_HINT = (
    f'Named pins during add require Kubo >= {MIN_KUBO_VERSION_TEXT} '
    'because git-ipfs uses `ipfs add --pin-name`. Upgrade Kubo or omit `--name`.'
)


class GitIPFSError(RuntimeError):
    """User-facing error raised for actionable git-ipfs failures."""


class _YamlCodec:
    """Small PyYAML wrapper with a JSON fallback for machines without PyYAML."""

    @staticmethod
    def _yaml():
        try:
            import yaml  # type: ignore
        except Exception as ex:  # pragma: no cover - environment dependent
            raise RuntimeError(
                'Reading/writing .ipfs sidecars requires PyYAML. '
                'Install it with: python -m pip install pyyaml'
            ) from ex
        return yaml

    @classmethod
    def load(cls, fpath: os.PathLike | str) -> dict[str, Any]:
        fpath = Path(fpath)
        text = fpath.read_text()
        try:
            yaml = cls._yaml()
        except RuntimeError:
            # Sidecars are normally YAML.  This fallback only helps if a caller
            # intentionally wrote JSON-compatible sidecars.
            data = json.loads(text)
        else:
            data = yaml.safe_load(text)
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise TypeError(f'Expected mapping in {fpath!s}, got {type(data)!r}')
        return data

    @classmethod
    def dumps(cls, data: dict[str, Any]) -> str:
        data = _json_coerce(data)
        try:
            yaml = cls._yaml()
        except RuntimeError:
            return json.dumps(data, indent=2, sort_keys=False) + '\n'
        else:
            return yaml.safe_dump(data, sort_keys=False)


def _json_coerce(data: Any) -> Any:
    """Coerce common Python objects into JSON/YAML serializable values."""
    if isinstance(data, os.PathLike):
        return os.fspath(data)
    if isinstance(data, dict):
        return {str(k): _json_coerce(v) for k, v in data.items()}
    if isinstance(data, (list, tuple)):
        return [_json_coerce(v) for v in data]
    try:
        json.dumps(data)
    except TypeError:
        return repr(data)
    else:
        return data


def argv_to_str(argv: Iterable[os.PathLike | str]) -> str:
    """Shell-quote an argv sequence for copy/paste or dry-run output."""
    return ' '.join(shlex.quote(os.fspath(p)) for p in argv)


def _cmd_text(value: str | bytes | None) -> str:
    """Normalize ubelt command output to text for static checkers."""
    if value is None:
        return ''
    if isinstance(value, bytes):
        return value.decode(errors='replace')
    return value


def _command_failure_message(argv: list[str], info: Any) -> str:
    """Build an actionable command-failure message for CLI users."""
    cmdline = argv_to_str(argv)
    stdout = _cmd_text(getattr(info, 'stdout', None)).strip()
    stderr = _cmd_text(getattr(info, 'stderr', None)).strip()
    returncode = getattr(info, 'returncode', None)
    parts = [f'Command failed with exit code {returncode}: {cmdline}']
    if stderr:
        parts.append(f'stderr:\n{stderr}')
    if stdout:
        parts.append(f'stdout:\n{stdout}')

    text = '\n'.join([stderr, stdout]).lower()
    if argv[:2] == ['ipfs', 'get']:
        cid = argv[-1] if argv else '<cid>'
        parts.append(
            'Could not retrieve the requested CID. Verify that the content is '
            f'pinned somewhere reachable, then try `git ipfs peers --connect` or `ipfs swarm peers`. CID: {cid}'
        )
    elif argv[:3] == ['ipfs', 'swarm', 'peers']:
        parts.append(KUBO_DAEMON_HINT)
    elif argv[:2] == ['ipfs', 'repo']:
        parts.append(
            'The user-level Kubo repository may not be initialized. Run `ipfs init` '
            'for Kubo itself; git-ipfs does not create an IPFS store inside this Git repo.'
        )
    elif argv[:2] == ['ipfs', 'add'] and ('pin-name' in text or any(a.startswith('--pin-name') for a in argv)):
        parts.append(KUBO_PIN_NAME_HINT)
    elif argv and argv[0] == 'ipfs' and (
        'api' in text or 'daemon' in text or 'connection refused' in text or 'no such file' in text
    ):
        parts.append(KUBO_DAEMON_HINT)
    elif argv and argv[0] == 'ipfs':
        parts.append('Run `git ipfs doctor` for a local Kubo/Git environment report.')
    return '\n\n'.join(parts)


def _run(argv: list[str], *, cwd: os.PathLike | str | None = None,
         dry_run: bool = False, verbose: int = 3, check: bool = True) -> Any:
    """Run or print a command using ubelt's command wrapper."""
    if dry_run:
        print(argv_to_str(argv))
        return None  # type: ignore[return-value]
    if argv and argv[0] == 'ipfs' and shutil.which('ipfs') is None:
        raise GitIPFSError('Could not find the `ipfs` executable on PATH. ' + KUBO_INSTALL_HINT)
    try:
        info = ub.cmd(argv, cwd=cwd, verbose=verbose)
    except FileNotFoundError as ex:
        if argv and argv[0] == 'ipfs':
            raise GitIPFSError('Could not find the `ipfs` executable on PATH. ' + KUBO_INSTALL_HINT) from ex
        raise
    if check and getattr(info, 'returncode', 0):
        raise GitIPFSError(_command_failure_message(argv, info))
    return info


def _find_sidecars(path: os.PathLike | str, recursive: bool = True) -> list[Path]:
    """
    Resolve a file, directory, or glob into sorted ``*.ipfs`` sidecars.
    """
    path = Path(path)
    path_str = os.fspath(path)
    sidecars: list[Path] = []

    if any(ch in path_str for ch in '*?['):
        for match in glob.glob(path_str, recursive=True):
            mpath = Path(match)
            if mpath.is_dir():
                sidecars.extend(mpath.rglob('*.ipfs') if recursive else mpath.glob('*.ipfs'))
            elif mpath.is_file() and mpath.suffix == '.ipfs':
                sidecars.append(mpath)
    elif path.is_file() and path.suffix == '.ipfs':
        sidecars.append(path)
    elif path.is_dir():
        sidecars.extend(path.rglob('*.ipfs') if recursive else path.glob('*.ipfs'))

    seen = set()
    unique = []
    for fpath in sorted(sidecars, key=lambda p: os.fspath(p)):
        key = fpath.resolve() if fpath.exists() else fpath.absolute()
        if key not in seen:
            seen.add(key)
            unique.append(fpath)
    return unique


def _read_sidecar(fpath: os.PathLike | str) -> dict[str, Any]:
    data = _YamlCodec.load(fpath)
    if data.get('type') not in {None, 'ipfs-sidecar'}:
        raise ValueError(f'Unsupported sidecar type in {fpath!s}: {data.get("type")!r}')
    if 'cid' not in data:
        raise KeyError(f'Missing required "cid" field in {fpath!s}')
    return data


def _path_is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    else:
        return True


def _safe_resolve(path: Path) -> Path:
    """Resolve a path even when the final component does not exist."""
    try:
        return path.resolve(strict=False)
    except RuntimeError:
        # Defensive guard against pathological symlink loops.
        return path.absolute()


def _tracked_path(sidecar_fpath: os.PathLike | str, meta: dict[str, Any]) -> Path:
    """Return the materialization target for a sidecar, with traversal guards."""
    sidecar_fpath = Path(sidecar_fpath)
    rel_path = meta.get('rel_path')
    if rel_path is None:
        raise KeyError(f'Missing required "rel_path" field in {sidecar_fpath!s}')

    rel_path = Path(os.fspath(rel_path))
    if rel_path.is_absolute():
        raise ValueError(
            f'Refusing absolute rel_path in {sidecar_fpath!s}: {rel_path!s}'
        )

    target = sidecar_fpath.parent / rel_path
    boundary = _git_toplevel(sidecar_fpath.parent) or sidecar_fpath.parent
    target_resolved = _safe_resolve(target)
    boundary_resolved = _safe_resolve(boundary)
    if not _path_is_relative_to(target_resolved, boundary_resolved):
        raise ValueError(
            'Refusing sidecar target outside safe boundary: '
            f'sidecar={sidecar_fpath!s}, rel_path={rel_path!s}, '
            f'boundary={boundary_resolved!s}, target={target_resolved!s}'
        )
    return target


def _sidecar_import_config(meta: dict[str, Any]) -> dict[str, Any]:
    """Return CID-affecting import settings from new or legacy sidecars."""
    import_config = meta.get('import')
    if isinstance(import_config, dict):
        return dict(import_config)
    legacy = meta.get('add_config')
    if isinstance(legacy, dict):
        return {
            key: legacy[key]
            for key in CID_IMPORT_KEYS
            if key in legacy and legacy[key] is not None
        }
    return {}


def _sidecar_pin_name(meta: dict[str, Any]) -> str | None:
    """Return a human-readable pin name from new or legacy sidecars."""
    pin_name = meta.get('pin_name')
    if pin_name:
        return str(pin_name)
    import_config = meta.get('import')
    if isinstance(import_config, dict) and import_config.get('pin_name'):
        return str(import_config['pin_name'])
    add_config = meta.get('add_config')
    if isinstance(add_config, dict) and add_config.get('name'):
        return str(add_config['name'])
    return None


def _coerce_suggested_peers(peers: Any) -> list[str]:
    """Normalize sidecar peer hints into a stable list of strings."""
    if peers is None or peers is False:
        return []
    if isinstance(peers, str):
        raw_items = [peers]
    else:
        raw_items = list(peers)
    normalized: list[str] = []
    seen = set()
    for item in raw_items:
        if isinstance(item, dict):
            candidate = item.get('addr') or item.get('multiaddr') or item.get('peer_id') or item.get('id')
        else:
            candidate = item
        if candidate is None:
            continue
        text = str(candidate).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        normalized.append(text)
    return normalized


def _sidecar_suggested_peers(meta: dict[str, Any]) -> list[str]:
    """Return peer hints from new or legacy-compatible sidecar fields."""
    peers = _coerce_suggested_peers(meta.get('suggested_peers'))
    if peers:
        return peers
    # Accept a shorter alias for hand-written sidecars without making it the
    # preferred writer field.
    return _coerce_suggested_peers(meta.get('peers'))


def _connect_to_peer_hint(peer_hint: str, *, dry_run: bool = False, verbose: int = 1) -> bool:
    """Best-effort connection to a peer hint.

    Hints may be full multiaddrs or bare peer IDs.  A bare peer ID requires the
    local node to discover usable addresses through the DHT / routing system, so
    failures are reported but are not fatal to pulls.
    """
    peer_hint = str(peer_hint).strip()
    if not peer_hint:
        return False

    candidate_addrs: list[str]
    if peer_hint.startswith('/'):
        candidate_addrs = [peer_hint]
    else:
        find_argv = ['ipfs', 'dht', 'findpeer', peer_hint]
        if dry_run:
            print(argv_to_str(find_argv))
            candidate_addrs = []
        else:
            info = _run(find_argv, verbose=0, check=False)
            if info.returncode:
                if verbose:
                    detail = (_cmd_text(info.stderr) or _cmd_text(info.stdout)).strip()
                    print(f'warning: could not resolve peer {peer_hint!r}: {detail}')
                return False
            stdout = _cmd_text(info.stdout)
            candidate_addrs = [ln.strip() for ln in stdout.splitlines() if ln.strip()]
            candidate_addrs = [
                addr if addr.endswith('/p2p/' + peer_hint) else addr.rstrip('/') + '/p2p/' + peer_hint
                for addr in candidate_addrs
            ]

    connected = False
    for addr in candidate_addrs:
        connect_argv = ['ipfs', 'swarm', 'connect', addr]
        if dry_run:
            print(argv_to_str(connect_argv))
            connected = True
        else:
            info = _run(connect_argv, verbose=0, check=False)
            if info.returncode == 0:
                connected = True
                if verbose:
                    print((_cmd_text(info.stdout) or f'connected: {addr}').strip())
            elif verbose:
                detail = (_cmd_text(info.stderr) or _cmd_text(info.stdout)).strip()
                print(f'warning: could not connect to {addr!r}: {detail}')
    return connected


def _connect_suggested_peers(meta: dict[str, Any], *, dry_run: bool = False, verbose: int = 1) -> int:
    """Connect to all sidecar peer hints and return the success count."""
    count = 0
    for peer_hint in _sidecar_suggested_peers(meta):
        if _connect_to_peer_hint(peer_hint, dry_run=dry_run, verbose=verbose):
            count += 1
    return count


def _parse_kubo_version_text(text: str) -> tuple[int, ...] | None:
    """Parse tuples from outputs like ``ipfs version 0.37.0``."""
    import re
    match = re.search(r'(?:kubo|ipfs) version\s+v?([0-9]+(?:\.[0-9]+){1,3})', text, re.I)
    if match is None:
        match = re.search(r'\bv?([0-9]+(?:\.[0-9]+){1,3})\b', text)
    if match is None:
        return None
    return tuple(int(part) for part in match.group(1).split('.'))


def _version_gte(found: tuple[int, ...] | None, minimum: tuple[int, ...]) -> bool:
    """Compare version tuples with zero padding."""
    if found is None:
        return False
    width = max(len(found), len(minimum))
    return tuple(found + (0,) * (width - len(found))) >= tuple(minimum + (0,) * (width - len(minimum)))


def _clean_import_config(config: scfg.DataConfig | dict[str, Any]) -> dict[str, Any]:
    cfg = dict(config)
    return {
        key: cfg[key]
        for key in CID_IMPORT_KEYS
        if key in cfg and cfg[key] is not None
    }


def _sidecar_metadata(
    *,
    cid: str,
    rel_path: os.PathLike | str,
    path: os.PathLike | str,
    config: scfg.DataConfig | dict[str, Any],
    num_items: int | None = None,
) -> dict[str, Any]:
    """Build the deterministic tracked sidecar payload."""
    path = Path(path)
    quickstat = _compute_quickstat(path)
    import_config = _clean_import_config(config)
    cfg = dict(config)
    meta: dict[str, Any] = {
        'schema_version': SIDECAR_SCHEMA_VERSION,
        'type': 'ipfs-sidecar',
        'cid': cid,
        'rel_path': os.fspath(rel_path),
        'kind': 'directory' if path.is_dir() else 'file',
        'import': import_config,
    }
    if quickstat is not None:
        meta['size_bytes'] = quickstat.get('bytes')
        if quickstat.get('nfiles') is not None:
            meta['num_files'] = quickstat.get('nfiles')
    if num_items is not None:
        meta['num_items'] = num_items
    if cfg.get('name'):
        meta['pin_name'] = cfg['name']
    suggested_peers = _coerce_suggested_peers(cfg.get('suggested_peers'))
    if suggested_peers:
        meta['suggested_peers'] = suggested_peers
    return meta


def _gitignore_pattern_for(rel_path: os.PathLike | str) -> str:
    """Return a conservative anchored .gitignore pattern for a sidecar target."""
    rel = Path(os.fspath(rel_path))
    if rel.is_absolute():
        raise ValueError(f'Cannot make .gitignore pattern for absolute path: {rel!s}')
    parts = []
    for part in rel.parts:
        if part in {'', '.'}:
            continue
        if part == '..':
            raise ValueError(f'Cannot make .gitignore pattern for parent path: {rel!s}')
        part = part.replace('\\', '\\\\')
        part = part.replace(' ', '\\ ')
        part = part.replace('#', '\\#')
        part = part.replace('!', '\\!')
        parts.append(part)
    if not parts:
        raise ValueError(f'Cannot make .gitignore pattern for empty path: {rel!s}')
    return '/' + '/'.join(parts)


def _git_toplevel(start: os.PathLike | str) -> Path | None:
    """Return the enclosing git worktree root, or None outside git."""
    info = ub.cmd(['git', 'rev-parse', '--show-toplevel'], cwd=start, verbose=0)
    if info.returncode:
        return None
    stdout = _cmd_text(info.stdout).strip()
    if not stdout:
        return None
    return Path(stdout)


def _append_unique_line(fpath: Path, line: str) -> bool:
    """Append a line to a text file if it is not already present."""
    existing: list[str]
    if fpath.exists():
        existing = [p.strip() for p in fpath.read_text().splitlines()]
    else:
        existing = []
    if line in existing:
        return False
    with fpath.open('a') as file:
        if fpath.exists() and fpath.stat().st_size and not fpath.read_text().endswith('\n'):
            file.write('\n')
        file.write(line + '\n')
    return True


def _parse_ipfs_add_root_cid(stdout: str) -> str:
    """Parse the root CID from kubo ``ipfs add`` output."""
    lines = [ln for ln in stdout.splitlines() if ln.strip()]
    if not lines:
        raise RuntimeError('ipfs add produced no stdout; cannot find CID')
    last = lines[-1]
    parts = last.split()
    # Typical format: ``added <cid> <path>``.
    if parts[0] == 'added':
        if len(parts) < 2:
            raise RuntimeError(f'Unexpected ipfs add output line: {last!r}')
        return parts[1]
    # ``ipfs add -q`` can emit just the CID.
    return parts[0]


def _parse_ipfs_progress_size(stderr: str) -> str | None:
    """
    Best-effort parser for kubo progress output.

    The progress format is not a stable API, so failures return None rather than
    aborting a successful add.
    """
    for line in reversed([ln for ln in stderr.splitlines() if ln.strip()]):
        if '[' in line and '/' in line.split('[', 1)[0]:
            try:
                return line.split('[', 1)[0].split('/', 1)[1].strip()
            except Exception:
                return None
    return None


def _build_add_argv(config: scfg.DataConfig | dict[str, Any]) -> list[str]:
    cfg = dict(config)
    argv = ['ipfs', 'add']
    bool_flags = ['pin', 'progress', 'recursive', 'only_hash']
    keyval_flags = ['raw_leaves', 'cid_version']
    for key in bool_flags:
        if cfg.get(key):
            argv.append('--' + key.replace('_', '-'))
    for key in keyval_flags:
        if key in cfg and cfg[key] is not None:
            argv.append('--{}={}'.format(key.replace('_', '-'), json.dumps(cfg[key])))
    if cfg.get('pin') and cfg.get('name'):
        argv.append('--pin-name={}'.format(str(cfg['name'])))
    argv.append(os.fspath(cfg['path']))
    return argv


def _build_rehash_argv(tracked_path: Path, add_config: dict[str, Any]) -> list[str]:
    """Build a conservative ``ipfs add --only-hash`` command for verification."""
    argv = ['ipfs', 'add', '--only-hash']
    if tracked_path.is_dir() or add_config.get('recursive'):
        argv.append('--recursive')
    if 'cid_version' in add_config and add_config['cid_version'] is not None:
        argv.append('--cid-version={}'.format(json.dumps(add_config['cid_version'])))
    if 'raw_leaves' in add_config and add_config['raw_leaves'] is not None:
        argv.append('--raw-leaves={}'.format('true' if add_config['raw_leaves'] else 'false'))
    argv.append(os.fspath(tracked_path))
    return argv


def _ipfs_only_hash_cid(tracked_path: Path, add_config: dict[str, Any] | None = None) -> str:
    """Recompute a CID using kubo without writing blocks."""
    argv = _build_rehash_argv(tracked_path, add_config or {})
    info = _run(argv, verbose=0)
    return _parse_ipfs_add_root_cid(_cmd_text(info.stdout))


def _compute_quickstat(tracked_path: os.PathLike | str) -> dict[str, Any] | None:
    """
    Compute a cheap local fingerprint: size + maximum mtime.
    """
    tracked_path = Path(tracked_path)
    if not tracked_path.exists():
        return None
    if tracked_path.is_file():
        st = tracked_path.stat()
        return {
            'kind': 'file',
            'bytes': int(st.st_size),
            'mtime': float(st.st_mtime),
        }

    total = 0
    max_mtime = 0.0
    nfiles = 0
    for fpath in tracked_path.rglob('*'):
        if fpath.is_file():
            st = fpath.stat()
            nfiles += 1
            total += int(st.st_size)
            max_mtime = max(max_mtime, float(st.st_mtime))
    return {
        'kind': 'dir',
        'bytes': int(total),
        'mtime': float(max_mtime),
        'nfiles': int(nfiles),
    }


def _print_status_table(rows: list[dict[str, Any]]) -> None:
    from rich.console import Console
    from rich.table import Table

    table = Table(title='IPFS Sidecar Status', show_lines=False)
    table.add_column('status', no_wrap=True)
    table.add_column('sidecar', overflow='fold')
    table.add_column('tracked', overflow='fold')
    table.add_column('bytes', justify='right')
    table.add_column('mtime', justify='right')
    table.add_column('cid', overflow='fold')
    table.add_column('cid_recomputed', overflow='fold')
    for row in rows:
        table.add_row(
            str(row['status']),
            str(row['sidecar']),
            str(row['tracked']),
            '' if row.get('bytes') is None else str(row['bytes']),
            '' if row.get('mtime') is None else f"{row['mtime']:.3f}",
            str(row.get('cid') or ''),
            str(row.get('cid_recomputed') or ''),
        )
    Console().print(table)


@_register_modal(IPFSCLI)
class IPFSDoctor(scfg.DataConfig):
    """Check the local git/IPFS environment and explain what is missing."""
    __command__ = 'doctor'

    path = scfg.Value('.', help='path inside the repository to inspect', position=1)
    strict = scfg.Flag(False, help='raise an error if any required check fails')

    @classmethod
    def main(cls, argv=1, **kwargs):
        argv = kwargs.pop('cmdline', argv)
        config = cls.cli(argv=argv, data=kwargs, strict=True)
        from rich.console import Console
        from rich.table import Table

        rows: list[tuple[str, bool, str]] = []

        def add(label: str, ok: bool, detail: str) -> None:
            rows.append((label, ok, detail))

        start = Path(config.path)
        git_root = _git_toplevel(start)
        add('git worktree', git_root is not None, os.fspath(git_root) if git_root else 'not inside a git worktree')

        ipfs_exe = shutil.which('ipfs')
        add('ipfs executable', ipfs_exe is not None, ipfs_exe or 'not found on PATH')

        if ipfs_exe:
            version = _run(['ipfs', 'version'], verbose=0, check=False)
            version_text = (_cmd_text(version.stdout) or _cmd_text(version.stderr)).strip()
            found_version = _parse_kubo_version_text(version_text)
            add('ipfs version', version.returncode == 0, version_text)
            add(
                f'kubo >= {MIN_KUBO_VERSION_TEXT}',
                version.returncode == 0 and _version_gte(found_version, MIN_KUBO_VERSION),
                'needed for `ipfs add --pin-name`' if version.returncode == 0 else version_text,
            )

            repo_stat = _run(['ipfs', 'repo', 'stat'], verbose=0, check=False)
            repo_detail = 'initialized' if repo_stat.returncode == 0 else (
                _cmd_text(repo_stat.stderr).strip() or
                'not initialized; run `ipfs init` for your user-level Kubo repo'
            )
            add('ipfs repo', repo_stat.returncode == 0, repo_detail)

            daemon = _run(['ipfs', 'swarm', 'peers'], verbose=0, check=False)
            daemon_detail = 'online API reachable' if daemon.returncode == 0 else (
                _cmd_text(daemon.stderr).strip() or KUBO_DAEMON_HINT
            )
            add('ipfs daemon', daemon.returncode == 0, daemon_detail)

            remotes = _run(['ipfs', 'pin', 'remote', 'service', 'ls'], verbose=0, check=False)
            remote_stdout = _cmd_text(remotes.stdout).strip()
            remote_stderr = _cmd_text(remotes.stderr).strip()
            detail = remote_stdout if remote_stdout else remote_stderr or 'no remote pinning services listed'
            add('remote pinning', remotes.returncode == 0, detail)

        git_ipfs = shutil.which('git-ipfs')
        add('git-ipfs command', git_ipfs is not None, git_ipfs or 'not installed as a standalone git subcommand')

        table = Table(title='git ipfs doctor', show_lines=False)
        table.add_column('check')
        table.add_column('ok')
        table.add_column('detail', overflow='fold')
        for label, ok, detail in rows:
            table.add_row(label, 'yes' if ok else 'no', detail)
        Console().print(table)

        failed = [label for label, ok, _detail in rows if not ok]
        if failed and config.strict:
            raise SystemExit('failed checks: ' + ', '.join(failed))


@_register_modal(IPFSCLI)
class IPFSPeers(scfg.DataConfig):
    """List or connect to peer hints recorded in sidecars."""
    __command__ = 'peers'

    paths = scfg.Value([], position=1, nargs='*', help='paths/globs/dirs/.ipfs files; default: .')
    recursive = scfg.Flag(True, help='recurse into directories when scanning')
    connect = scfg.Flag(False, help='attempt to connect to each suggested peer')
    dry_run = scfg.Flag(False, short_alias=['n'], help='print connect commands without running them')

    @classmethod
    def main(cls, argv=1, **kwargs):
        argv = kwargs.pop('cmdline', argv)
        config = cls.cli(argv=argv, data=kwargs, strict=True)
        paths = list(config.paths) if config.paths else ['.']
        rows: list[tuple[Path, str]] = []
        for path in paths:
            for sidecar_fpath in _find_sidecars(path, recursive=config.recursive):
                meta = _read_sidecar(sidecar_fpath)
                for peer_hint in _sidecar_suggested_peers(meta):
                    rows.append((sidecar_fpath, peer_hint))

        for sidecar_fpath, peer_hint in rows:
            print(f'{sidecar_fpath}: {peer_hint}')

        if config.connect:
            for sidecar_fpath in sorted({row[0] for row in rows}, key=lambda p: os.fspath(p)):
                meta = _read_sidecar(sidecar_fpath)
                _connect_suggested_peers(meta, dry_run=config.dry_run, verbose=1)




@_register_modal(IPFSCLI)
class IPFSPush(scfg.DataConfig):
    """Push sidecar CIDs to a configured Kubo remote pinning service."""
    __command__ = 'push'

    paths = scfg.Value([], position=1, nargs='*', help='paths/globs/dirs/.ipfs files; default: .')
    service = scfg.Value(None, help='remote pinning service name known to `ipfs pin remote service ls`')
    name = scfg.Value(None, help='override pin name for all pushed CIDs')
    recursive = scfg.Flag(True, help='ask the remote service to pin recursively')
    background = scfg.Flag(True, help='queue the pin request in the background')
    dry_run = scfg.Flag(False, short_alias=['n'], help='print commands without executing')
    dedupe = scfg.Flag(True, help='deduplicate by CID')

    @classmethod
    def main(cls, argv=1, **kwargs):
        argv = kwargs.pop('cmdline', argv)
        config = cls.cli(argv=argv, data=kwargs, strict=True)
        if config.service is None:
            raise ValueError('Specify --service=<name> for the remote pinning service')
        paths = list(config.paths) if config.paths else ['.']
        items: list[tuple[str, str | None, Path]] = []
        for path in paths:
            for sidecar_fpath in _find_sidecars(path, recursive=True):
                meta = _read_sidecar(sidecar_fpath)
                cid = meta.get('cid')
                if cid:
                    pin_name = config.name or _sidecar_pin_name(meta) or sidecar_fpath.with_suffix('').name
                    items.append((str(cid), pin_name, sidecar_fpath))

        if config.dedupe:
            seen: dict[str, tuple[str, str | None, Path]] = {}
            for item in items:
                seen.setdefault(item[0], item)
            items = list(seen.values())

        for cid, pin_name, _sidecar_fpath in items:
            argv2 = ['ipfs', 'pin', 'remote', 'add', f'--service={config.service}']
            if pin_name:
                argv2.append(f'--name={pin_name}')
            if config.recursive:
                argv2.append('--recursive=true')
            if config.background:
                argv2.append('--background=true')
            argv2.append(cid)
            _run(argv2, dry_run=config.dry_run, verbose=3)


@_register_modal(IPFSCLI)
class IPFSAdd(scfg.DataConfig):
    """Add a file/directory to IPFS and optionally write a sidecar."""
    __command__ = 'add'
    __alias__ = 'snapshot'

    path = scfg.Value(None, help='file or directory to add to IPFS', position=1)
    name = scfg.Value(None, help='optional human-readable pin name')
    suggested_peers = scfg.Value(
        [], nargs='*', alias=['suggested-peer', 'suggested-peers'],
        help='peer IDs or multiaddrs likely to provide this CID')
    recursive = scfg.Flag(True, help='add directory paths recursively')
    progress = scfg.Flag(True, short_alias=['p'], help='stream progress data')
    cid_version = scfg.Value(1, help='CID version')
    raw_leaves = scfg.Flag(False, help='use raw blocks for leaf nodes')
    only_hash = scfg.Flag(False, short_alias=['n'], help='chunk/hash only; do not write IPFS blocks')
    pin = scfg.Flag(True, help='pin locally to protect added files from garbage collection')
    sidecar = scfg.Value(True, help='true, false, or explicit sidecar path')
    update_gitignore = scfg.Flag(True, help='add the tracked path to a nearby .gitignore')
    git_add_sidecar = scfg.Flag(True, help='git-add the sidecar when inside a git worktree')
    dry_run = scfg.Flag(False, help='print the generated ipfs command without running it')

    _build_add_command = _build_add_argv

    @classmethod
    def main(cls, argv=1, **kwargs):
        argv = kwargs.pop('cmdline', argv)
        config = cls.cli(argv=argv, data=kwargs, strict=True)
        if config.path is None:
            raise ValueError('Path must be specified')

        path = Path(config.path)
        sidecar_fpath: Path | None
        if config.sidecar:
            if config.sidecar is True:
                sidecar_fpath = path.with_name(path.name + '.ipfs')
            else:
                sidecar_fpath = Path(config.sidecar)
            if sidecar_fpath.exists() and sidecar_fpath.is_dir():
                raise IsADirectoryError(f'Sidecar path conflicts with directory: {sidecar_fpath}')
        else:
            sidecar_fpath = None

        add_argv = _build_add_argv(config)
        if config.dry_run:
            print(argv_to_str(add_argv))
            if sidecar_fpath is not None:
                print(f'would write sidecar: {sidecar_fpath}')
            return

        info = _run(add_argv, verbose=3)

        if config.only_hash:
            return

        add_stdout = _cmd_text(info.stdout)
        cid = _parse_ipfs_add_root_cid(add_stdout)
        lines = [ln for ln in add_stdout.splitlines() if ln.strip()]

        if sidecar_fpath is not None:
            sidecar_dpath = sidecar_fpath.parent
            rel_path = os.path.relpath(path, sidecar_dpath)
            sidecar_metadata = _sidecar_metadata(
                cid=cid,
                rel_path=rel_path,
                path=path,
                config=config,
                num_items=len(lines),
            )
            sidecar_text = _YamlCodec.dumps(sidecar_metadata)
            print(f'write to: sidecar_fpath={sidecar_fpath}')
            sidecar_fpath.write_text(sidecar_text)

            gitignore_changed = False
            if config.update_gitignore:
                ignore_fpath = sidecar_dpath / '.gitignore'
                try:
                    ignore_pattern = _gitignore_pattern_for(rel_path)
                except ValueError as ex:
                    print(f'skipping .gitignore update: {ex}')
                else:
                    if _append_unique_line(ignore_fpath, ignore_pattern):
                        gitignore_changed = True
                        print(f'updated: {ignore_fpath}')
                    else:
                        print(f'gitignore already contains: {ignore_pattern}')

            if config.git_add_sidecar:
                if _git_toplevel(sidecar_dpath) is not None:
                    add_paths = [sidecar_fpath.name]
                    if gitignore_changed:
                        add_paths.append('.gitignore')
                    _run(['git', 'add'] + add_paths, cwd=sidecar_dpath, verbose=2)
                else:
                    print('not in a git worktree; skipping git add of sidecar')

            print(sidecar_text)
            print(f'Wrote to: sidecar_fpath={sidecar_fpath}')


@_register_modal(IPFSCLI)
class IPFSPull(scfg.DataConfig):
    """Materialize content described by one or more ``*.ipfs`` sidecars."""
    __command__ = 'pull'

    path = scfg.Value('.', help='path/glob/directory containing .ipfs sidecars', position=1)
    dry_run = scfg.Flag(False, short_alias=['n'], help='inspect without downloading or modifying files')
    recursive = scfg.Flag(True, help='recurse into directories when scanning')
    delete = scfg.Flag(True, help='delete stale files when syncing an existing directory with rsync')
    connect_peers = scfg.Flag(True, help='best-effort connect to sidecar suggested_peers before downloading')

    @classmethod
    def main(cls, argv=1, **kwargs):
        argv = kwargs.pop('cmdline', argv)
        config = cls.cli(argv=argv, data=kwargs, strict=True)
        sidecars = _find_sidecars(config.path, recursive=config.recursive)
        print(f'Found {len(sidecars)} sidecar(s)')
        for sidecar_fpath in sidecars:
            meta = _read_sidecar(sidecar_fpath)
            root_cid = meta['cid']
            tracked_path = _tracked_path(sidecar_fpath, meta)
            if config.dry_run:
                print(f'sidecar={sidecar_fpath}')
                print(f'target={tracked_path}')
                print(_YamlCodec.dumps(meta))
            else:
                if config.connect_peers:
                    _connect_suggested_peers(meta, dry_run=False, verbose=1)
                sync_ipfs_pull(root_cid, tracked_path, delete=config.delete)


@_register_modal(IPFSCLI)
class IPFSStatus(scfg.DataConfig):
    """Check whether local content tracked by sidecars appears changed."""
    __command__ = 'status'

    path = scfg.Value('.', help='path/glob/directory containing .ipfs sidecars', position=1)
    recursive = scfg.Flag(True, help='recurse into directories when scanning')
    strict = scfg.Flag(False, help='error on missing tracked paths')
    full = scfg.Flag(False, help='recompute CID using ipfs add --only-hash')
    write_baseline = scfg.Flag(False, help='update quickstat baseline in each sidecar')
    baseline_key = scfg.Value('local_quickstat', help='sidecar key containing quickstat baseline')

    @classmethod
    def main(cls, argv=1, **kwargs):
        argv = kwargs.pop('cmdline', argv)
        config = cls.cli(argv=argv, data=kwargs, strict=True)
        sidecars = _find_sidecars(config.path, recursive=config.recursive)
        rows: list[dict[str, Any]] = []
        for sidecar_fpath in sidecars:
            meta = _read_sidecar(sidecar_fpath)
            root_cid = meta.get('cid')
            tracked_path = _tracked_path(sidecar_fpath, meta)
            cur_quick = _compute_quickstat(tracked_path)
            base_quick = meta.get(config.baseline_key)
            if cur_quick is None:
                status = 'MISSING'
            elif isinstance(base_quick, dict):
                changed = cur_quick.get('bytes') != base_quick.get('bytes')
                if 'mtime' in cur_quick and 'mtime' in base_quick:
                    changed = changed or cur_quick.get('mtime') != base_quick.get('mtime')
                status = 'CHANGED' if changed else 'OK'
            elif meta.get('size_bytes') is not None:
                status = 'OK_SIZE' if cur_quick.get('bytes') == meta.get('size_bytes') else 'CHANGED'
            else:
                status = 'NO_BASELINE'

            new_cid = None
            if config.full and cur_quick is not None:
                try:
                    new_cid = _ipfs_only_hash_cid(tracked_path, _sidecar_import_config(meta))
                    status = 'OK' if new_cid == root_cid else 'CHANGED'
                except Exception as ex:
                    new_cid = f'ERROR: {ex}'
                    status = 'FULL_CHECK_ERROR'

            if config.write_baseline and cur_quick is not None:
                meta = dict(meta)
                meta[config.baseline_key] = cur_quick
                sidecar_fpath.write_text(_YamlCodec.dumps(meta))

            rows.append({
                'sidecar': os.fspath(sidecar_fpath),
                'tracked': os.fspath(tracked_path),
                'status': status,
                'cid': root_cid,
                'cid_recomputed': new_cid,
                'bytes': None if cur_quick is None else cur_quick.get('bytes'),
                'mtime': None if cur_quick is None else cur_quick.get('mtime'),
            })
            if config.strict and status == 'MISSING':
                raise FileNotFoundError(f'Missing tracked path: {tracked_path}')
        _print_status_table(rows)


@_register_modal(IPFSCLI)
class IPFSExportPins(scfg.DataConfig):
    """Export ``ipfs pin add`` commands for sidecars."""
    __command__ = 'export'

    paths = scfg.Value([], position=1, nargs='*', help='paths/globs/dirs/.ipfs files; default: .')
    recurse = scfg.Flag(True, help='recurse into directories when scanning')
    dedupe = scfg.Flag(True, help='deduplicate by CID')
    sort = scfg.Flag(True, help='sort output for stable scripts')
    name = scfg.Value(None, help='override pin name for all emitted commands')
    prefer_sidecar_name = scfg.Flag(True, help='use add_config.name when present')
    progress = scfg.Flag(False, short_alias=['p'], help='include --progress')
    recursive = scfg.Flag(True, help='include --recursive')
    emit_bash = scfg.Flag(False, help='emit a bash header')

    @classmethod
    def main(cls, argv=1, **kwargs):
        argv = kwargs.pop('cmdline', argv)
        config = cls.cli(argv=argv, data=kwargs, strict=True)
        paths = list(config.paths) if config.paths else ['.']
        items: list[tuple[str, str | None, Path]] = []
        for path in paths:
            for sidecar_fpath in _find_sidecars(path, recursive=config.recurse):
                meta = _read_sidecar(sidecar_fpath)
                cid = meta.get('cid')
                if not cid:
                    continue
                pin_name = config.name
                if pin_name is None and config.prefer_sidecar_name:
                    pin_name = _sidecar_pin_name(meta)
                items.append((str(cid), pin_name, sidecar_fpath))

        if config.dedupe:
            seen: dict[str, tuple[str, str | None, Path]] = {}
            for item in items:
                seen.setdefault(item[0], item)
            items = list(seen.values())
        if config.sort:
            items = sorted(items, key=lambda t: (t[0], t[1] or '', os.fspath(t[2])))

        if config.emit_bash:
            print('#!/usr/bin/env bash')
            print('set -euo pipefail')
            print('')

        for cid, name, _fpath in items:
            pin_argv = ['ipfs', 'pin', 'add']
            if config.progress:
                pin_argv.append('--progress')
            if config.recursive:
                pin_argv.append('--recursive')
            pin_argv.append(cid)
            if name:
                pin_argv.append(f'--name={name}')
            print(argv_to_str(pin_argv))


class IPFSPin(scfg.ModalCLI):
    """Wrapped ``ipfs pin`` helpers."""
    __command__ = 'pin'


@_register_modal(IPFSPin)
class IPFSPinAdd(scfg.DataConfig):
    """Pin a CID or the CID referenced by a sidecar."""
    __command__ = 'add'

    path = scfg.Value(None, help='path to a .ipfs sidecar or raw CID', position=1)
    recursive = scfg.Flag(True, help='pin recursively')
    progress = scfg.Flag(True, short_alias=['p'], help='stream progress data')
    name = scfg.Value(None, help='optional pin name; defaults to add_config.name')
    dry_run = scfg.Flag(False, short_alias=['n'], help='print command without executing')

    @classmethod
    def main(cls, argv=1, **kwargs):
        argv = kwargs.pop('cmdline', argv)
        config = cls.cli(argv=argv, data=kwargs, strict=True)
        if config.path is None:
            raise ValueError('Path must be specified')
        candidate = Path(config.path)
        if candidate.exists():
            meta = _read_sidecar(candidate)
            root_cid = meta['cid']
            if config.name is None:
                config.name = _sidecar_pin_name(meta)
        else:
            root_cid = config.path

        pin_argv = ['ipfs', 'pin', 'add']
        if config.name is not None:
            pin_argv += ['--name', config.name]
        if config.progress:
            pin_argv.append('--progress')
        if config.recursive:
            pin_argv.append('--recursive')
        pin_argv.append(root_cid)
        _run(pin_argv, dry_run=config.dry_run, verbose=3)


IPFSCLI.register(IPFSPin)


@_register_modal(IPFSCLI)
class IPFSCheckCID(scfg.DataConfig):
    """Compare CIDs produced by common CID-version/raw-leaves settings."""
    __command__ = 'check-cid'

    path = scfg.Value(None, help='file or directory to hash', position=1)
    recursive = scfg.Flag(False, help='pass --recursive')

    @classmethod
    def main(cls, argv=1, **kwargs):
        argv = kwargs.pop('cmdline', argv)
        config = cls.cli(argv=argv, data=kwargs, strict=True)
        if config.path is None:
            raise ValueError('Path must be specified')
        labels = [
            ('CID_V0_DEFAULT', ['--cid-version=0']),
            ('CID_V1_DEFAULT', ['--cid-version=1']),
            ('CID_V0_RLT', ['--cid-version=0', '--raw-leaves=true']),
            ('CID_V0_RLF', ['--cid-version=0', '--raw-leaves=false']),
            ('CID_V1_RLT', ['--cid-version=1', '--raw-leaves=true']),
            ('CID_V1_RLF', ['--cid-version=1', '--raw-leaves=false']),
        ]
        rows = []
        for label, flags in labels:
            argv2 = ['ipfs', 'add', '-q', '--only-hash']
            if config.recursive:
                argv2.append('--recursive')
            argv2 += flags + [os.fspath(config.path)]
            info = _run(argv2, verbose=0)
            cid = _cmd_text(info.stdout).strip().splitlines()[-1]
            rows.append((label, cid))
        width = max(len(k) for k, _ in rows)
        for label, cid in rows:
            print(f'{label:<{width}} = {cid}')


def sync_ipfs_pull(root_cid: str, out_path: os.PathLike | str, *, delete: bool = True) -> None:
    """
    Download a CID into ``out_path`` using a temporary staging path.

    Existing directories are updated with rsync when available, and replaced via
    a conservative backup/swap fallback otherwise.  Symlink targets are never
    followed for replacement.
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_root = Path(tempfile.mkdtemp(prefix='git-well-ipfs-', dir=os.fspath(out_path.parent)))
    tmp_path = tmp_root / 'payload'
    try:
        _run(['ipfs', 'get', '--progress=true', f'--output={tmp_path}', root_cid], verbose=3)
        if out_path.exists() or out_path.is_symlink():
            if (
                out_path.is_dir() and
                not out_path.is_symlink() and
                tmp_path.is_dir() and
                shutil.which('rsync')
            ):
                rsync_argv = ['rsync', '-avprP']
                if delete:
                    rsync_argv.append('--delete')
                rsync_argv += [os.fspath(tmp_path) + '/', os.fspath(out_path) + '/']
                _run(rsync_argv, verbose=3)
            else:
                backup = out_path.with_name(out_path.name + '.old')
                if backup.exists() or backup.is_symlink():
                    if backup.is_dir() and not backup.is_symlink():
                        shutil.rmtree(backup)
                    else:
                        backup.unlink()
                out_path.rename(backup)
                try:
                    tmp_path.rename(out_path)
                except Exception:
                    backup.rename(out_path)
                    raise
                else:
                    if backup.is_dir() and not backup.is_symlink():
                        shutil.rmtree(backup)
                    else:
                        backup.unlink()
        else:
            tmp_path.rename(out_path)
    finally:
        if tmp_root.exists():
            shutil.rmtree(tmp_root, ignore_errors=True)


def main(argv=1, **kwargs):
    """Entry point for ``git-ipfs`` and ``python -m git_well ipfs``."""
    return IPFSCLI.main(argv=argv, **kwargs)


__cli__ = IPFSCLI


if __name__ == '__main__':
    main()
