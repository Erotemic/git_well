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

Export pin commands for all sidecars under the current repo:

    python -m git_well ipfs export . --emit_bash
"""
from __future__ import annotations

import glob
import json
import os
import re
import shlex
import shutil
import tempfile
from pathlib import Path
from urllib.parse import quote, urlparse
from typing import Any, Iterable

import scriptconfig as scfg
import ubelt as ub


class IPFSCLI(scfg.ModalCLI):
    """Utilities for git-tracked IPFS sidecar files."""
    __command__ = 'ipfs'


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


def _run(argv: list[str], *, cwd: os.PathLike | str | None = None,
         dry_run: bool = False, verbose: int = 3, check: bool = True) -> Any:
    """Run or print a command using ubelt's command wrapper.

    ubelt's ``cmd`` helper is a function, not a public return-type alias.
    Annotating this wrapper as ``Any`` keeps this module independent of
    ubelt's internal result class while still allowing callers to access the
    command result object returned by ``ub.cmd``.
    """
    if dry_run:
        print(argv_to_str(argv))
        return None
    info = ub.cmd(argv, cwd=cwd, verbose=verbose)
    if check:
        info.check_returncode()
    return info


def _cmd_stdout_text(stdout: str | bytes | None) -> str:
    """Normalize ``ub.cmd(...).stdout`` into text for typed callers."""
    if stdout is None:
        return ''
    if isinstance(stdout, bytes):
        return stdout.decode()
    return stdout


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


def _tracked_path(sidecar_fpath: os.PathLike | str, meta: dict[str, Any]) -> Path:
    sidecar_fpath = Path(sidecar_fpath)
    rel_path = meta.get('rel_path')
    if rel_path is None:
        raise KeyError(f'Missing required "rel_path" field in {sidecar_fpath!s}')
    return sidecar_fpath.parent / os.fspath(rel_path)


def _git_toplevel(start: os.PathLike | str) -> Path | None:
    """Return the enclosing git worktree root, or None outside git."""
    info = ub.cmd(['git', 'rev-parse', '--show-toplevel'], cwd=start, verbose=0)
    if info.returncode:
        return None
    toplevel = _cmd_stdout_text(info.stdout).strip()
    if not toplevel:
        return None
    return Path(toplevel)


def _git_search_dir(path: os.PathLike | str) -> Path:
    """Return an existing directory suitable as a git command cwd."""
    path = Path(path)
    if path.exists() and path.is_dir():
        search_dir = path
    else:
        search_dir = path.parent
    while not search_dir.exists() and search_dir != search_dir.parent:
        search_dir = search_dir.parent
    return search_dir


def _git_origin_url(repo_root: os.PathLike | str) -> str | None:
    """Return the configured origin URL for a git worktree, if available."""
    info = ub.cmd(
        ['git', 'config', '--get', 'remote.origin.url'],
        cwd=repo_root, verbose=0)
    if info.returncode:
        return None
    origin_url = _cmd_stdout_text(info.stdout).strip()
    return origin_url or None


def _strip_dot_git(name: str) -> str:
    """Strip the conventional .git suffix from a repository path/name."""
    if name.endswith('.git'):
        name = name[:-4]
    return name


def _parse_git_remote_origin(origin_url: str) -> tuple[str, list[str]] | None:
    """
    Parse common git remote URL forms into ``(host, path_parts)``.

    Credentials are intentionally discarded. Local filesystem remotes return
    ``None`` because they are machine-local and should not be embedded in a
    generated pin name.
    """
    origin_url = origin_url.strip()
    if not origin_url:
        return None

    host: str | None = None
    repo_path: str | None = None

    # SCP-like git syntax, e.g. git@github.com:owner/repo.git.
    scp_match = re.match(r'^(?:[^@/]+@)?([^:/]+):(.+)$', origin_url)
    if scp_match is not None and '://' not in origin_url:
        host = scp_match.group(1)
        repo_path = scp_match.group(2)
    else:
        parsed = urlparse(origin_url)
        if parsed.scheme in {'ssh', 'git', 'http', 'https'}:
            host = parsed.hostname
            if host and parsed.port:
                host = f'{host}:{parsed.port}'
            repo_path = parsed.path.lstrip('/')
        else:
            return None

    if not host or not repo_path:
        return None

    repo_path = _strip_dot_git(repo_path.rstrip('/'))
    path_parts = [part for part in repo_path.split('/') if part]
    if len(path_parts) < 2:
        return None
    return host.lower(), path_parts


def _purl_quote(component: str) -> str:
    """Percent-encode one PURL component/path segment."""
    return quote(component, safe='')


def _git_origin_url_to_purl_base(origin_url: str) -> str | None:
    """
    Convert a common git origin URL into a PURL-shaped package identifier.

    Recognized public forge hosts use their forge-specific PURL type. Other
    network remotes use the ``generic`` PURL type with the host in the
    namespace.
    Local filesystem remotes return ``None``.
    """
    parsed = _parse_git_remote_origin(origin_url)
    if parsed is None:
        return None
    host, path_parts = parsed
    repo_name = path_parts[-1]
    namespace_parts = path_parts[:-1]
    type_map = {
        'github.com': 'github',
        'gitlab.com': 'gitlab',
        'bitbucket.org': 'bitbucket',
    }
    purl_type = type_map.get(host, 'generic')
    if purl_type == 'generic':
        namespace_parts = [host] + namespace_parts
    encoded_parts = [
        _purl_quote(part) for part in namespace_parts + [repo_name]]
    return 'pkg:{}{}'.format(purl_type, '/' + '/'.join(encoded_parts))


def _path_to_purl_subpath(repo_rel_path: os.PathLike | str) -> str:
    """Encode a repo-relative path for use as a PURL subpath."""
    path = Path(repo_rel_path).as_posix()
    parts = [part for part in path.split('/') if part not in {'', '.'}]
    return '/'.join(_purl_quote(part) for part in parts)


def _generated_ipfs_pin_name(tracked_path: os.PathLike | str) -> str | None:
    """
    Generate a stable, PURL-shaped pin name for a path in a git worktree.

    The generated name is based on the repository origin and the current
    repo-relative path. It intentionally avoids local absolute paths, so it
    returns ``None`` outside a git worktree or when the origin is local or
    otherwise unparseable.
    """
    tracked_path = Path(tracked_path)
    search_dir = _git_search_dir(tracked_path)
    repo_root = _git_toplevel(search_dir)
    if repo_root is None:
        return None
    origin_url = _git_origin_url(repo_root)
    if origin_url is None:
        return None
    purl_base = _git_origin_url_to_purl_base(origin_url)
    if purl_base is None:
        return None
    try:
        repo_rel_path = tracked_path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return None
    subpath = _path_to_purl_subpath(repo_rel_path)
    if subpath:
        return f'{purl_base}#{subpath}'
    else:
        return purl_base


def _sidecar_pin_name(sidecar_fpath: os.PathLike | str,
                      meta: dict[str, Any],
                      *,
                      override_name: str | None = None,
                      prefer_sidecar_name: bool = True,
                      generated_names: bool = True) -> str | None:
    """
    Resolve the pin name for a sidecar.

    Precedence is:
        1. explicit caller override,
        2. explicit user name recorded in ``add_config.name``,
        3. generated PURL-shaped repo/path name.
    """
    if override_name is not None:
        return override_name
    if prefer_sidecar_name:
        pin_name = meta.get('add_config', {}).get('name')
        if pin_name:
            return pin_name
    if generated_names:
        return _generated_ipfs_pin_name(_tracked_path(sidecar_fpath, meta))
    return None


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
    if len(parts) < 2:
        raise RuntimeError(f'Unexpected ipfs add output line: {last!r}')
    # Typical format: ``added <cid> <path>``.
    if parts[0] == 'added':
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


def _build_add_argv(config: Any) -> list[str]:
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
    return _parse_ipfs_add_root_cid(info.stdout)


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


@IPFSCLI.register
class IPFSAdd(scfg.DataConfig):
    """Add a file/directory to IPFS and optionally write a sidecar."""
    __command__ = 'add'
    __alias__ = 'snapshot'

    path = scfg.Value(None, help='file or directory to add to IPFS', position=1)
    name = scfg.Value(None, help='optional human-readable pin name')
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
        if config.name and not config.pin:
            raise ValueError('--name requires --pin')

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
        pin_name = config.name or _generated_ipfs_pin_name(path)
        if config.dry_run:
            print(argv_to_str(add_argv))
            if sidecar_fpath is not None:
                print(f'would write sidecar: {sidecar_fpath}')
            if config.pin and not config.only_hash and pin_name:
                pin_argv = ['ipfs', 'pin', 'add', '--name', pin_name]
                if config.progress:
                    pin_argv.append('--progress')
                pin_argv.append('<cid-from-ipfs-add>')
                print(argv_to_str(pin_argv))
            return

        with ub.Timer() as timer:
            info = _run(add_argv, verbose=3)

        if config.only_hash:
            return

        cid = _parse_ipfs_add_root_cid(info.stdout)
        size_str = _parse_ipfs_progress_size(info.stderr)
        lines = [ln for ln in info.stdout.splitlines() if ln.strip()]

        if sidecar_fpath is not None:
            sidecar_dpath = sidecar_fpath.parent
            rel_path = os.path.relpath(path, sidecar_dpath)
            sidecar_metadata = {
                'type': 'ipfs-sidecar',
                'cid': cid,
                'rel_path': rel_path,
                'size': size_str,
                'num_items': len(lines),
                'add_config': dict(config),
                'add_datetime': ub.timestamp(),
                'add_duration': timer.elapsed,
                'local_quickstat': _compute_quickstat(path),
            }
            sidecar_text = _YamlCodec.dumps(sidecar_metadata)
            print(f'write to: sidecar_fpath={sidecar_fpath}')
            sidecar_fpath.write_text(sidecar_text)

            if config.update_gitignore:
                ignore_fpath = sidecar_dpath / '.gitignore'
                if _append_unique_line(ignore_fpath, rel_path):
                    print(f'updated: {ignore_fpath}')
                else:
                    print(f'gitignore already contains: {rel_path}')

            if config.git_add_sidecar:
                if _git_toplevel(sidecar_dpath) is not None:
                    _run(['git', 'add', sidecar_fpath.name], cwd=sidecar_dpath, verbose=2)
                else:
                    print('not in a git worktree; skipping git add of sidecar')

            print(sidecar_text)
            print(f'Wrote to: sidecar_fpath={sidecar_fpath}')

        if config.pin and pin_name:
            pin_argv = ['ipfs', 'pin', 'add', '--name', pin_name]
            if config.progress:
                pin_argv.append('--progress')
            pin_argv.append(cid)
            _run(pin_argv, verbose=3)


@IPFSCLI.register
class IPFSPull(scfg.DataConfig):
    """Materialize content described by one or more ``*.ipfs`` sidecars."""
    __command__ = 'pull'

    path = scfg.Value(None, help='path/glob/directory containing .ipfs sidecars', position=1)
    dry_run = scfg.Flag(False, short_alias=['n'], help='inspect without downloading or modifying files')
    recursive = scfg.Flag(True, help='recurse into directories when scanning')

    @classmethod
    def main(cls, argv=1, **kwargs):
        argv = kwargs.pop('cmdline', argv)
        config = cls.cli(argv=argv, data=kwargs, strict=True)
        if config.path is None:
            raise ValueError('Path must be specified')
        sidecars = _find_sidecars(config.path, recursive=config.recursive)
        print(f'Found {len(sidecars)} sidecar(s)')
        for sidecar_fpath in sidecars:
            meta = _read_sidecar(sidecar_fpath)
            root_cid = meta['cid']
            rel_path = meta['rel_path']
            dpath = sidecar_fpath.parent
            if config.dry_run:
                print(f'sidecar={sidecar_fpath}')
                print(_YamlCodec.dumps(meta))
            else:
                sync_ipfs_pull(root_cid, dpath, rel_path)


@IPFSCLI.register
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
            elif base_quick is None:
                status = 'NO_BASELINE'
            else:
                changed = (
                    cur_quick.get('bytes') != base_quick.get('bytes') or
                    cur_quick.get('mtime') != base_quick.get('mtime')
                )
                status = 'CHANGED' if changed else 'OK'

            new_cid = None
            if config.full and cur_quick is not None:
                try:
                    new_cid = _ipfs_only_hash_cid(tracked_path, meta.get('add_config', {}))
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


@IPFSCLI.register
class IPFSExportPins(scfg.DataConfig):
    """Export ``ipfs pin add`` commands for sidecars."""
    __command__ = 'export'

    paths = scfg.Value([], position=1, nargs='*', help='paths/globs/dirs/.ipfs files; default: .')
    recurse = scfg.Flag(True, help='recurse into directories when scanning')
    dedupe = scfg.Flag(True, help='deduplicate by CID')
    sort = scfg.Flag(True, help='sort output for stable scripts')
    name = scfg.Value(None, help='override pin name for all emitted commands')
    prefer_sidecar_name = scfg.Flag(True, help='use add_config.name when present')
    generated_names = scfg.Flag(
        True,
        help='generate PURL-shaped names from git origin and repo-relative '
             'path when no explicit name is available')
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
                pin_name = _sidecar_pin_name(
                    sidecar_fpath, meta,
                    override_name=config.name,
                    prefer_sidecar_name=config.prefer_sidecar_name,
                    generated_names=config.generated_names,
                )
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


@IPFSPin.register
class IPFSPinAdd(scfg.DataConfig):
    """Pin a CID or the CID referenced by a sidecar."""
    __command__ = 'add'

    path = scfg.Value(None, help='path to a .ipfs sidecar or raw CID', position=1)
    recursive = scfg.Flag(True, help='pin recursively')
    progress = scfg.Flag(True, short_alias=['p'], help='stream progress data')
    name = scfg.Value(
        None,
        help='optional pin name; defaults to add_config.name or a generated '
             'PURL-shaped repo/path name')
    generated_names = scfg.Flag(
        True,
        help='generate a PURL-shaped name from git origin and repo-relative '
             'path when no explicit name is available')
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
            pin_name = config.name
            if pin_name is None:
                pin_name = _sidecar_pin_name(
                    candidate, meta, generated_names=config.generated_names)
        else:
            root_cid = config.path
            pin_name = config.name

        pin_argv = ['ipfs', 'pin', 'add']
        if pin_name is not None:
            pin_argv += ['--name', pin_name]
        if config.progress:
            pin_argv.append('--progress')
        if config.recursive:
            pin_argv.append('--recursive')
        pin_argv.append(root_cid)
        _run(pin_argv, dry_run=config.dry_run, verbose=3)


IPFSCLI.register(IPFSPin)


@IPFSCLI.register
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
            cid = info.stdout.strip().splitlines()[-1]
            rows.append((label, cid))
        width = max(len(k) for k, _ in rows)
        for label, cid in rows:
            print(f'{label:<{width}} = {cid}')


def sync_ipfs_pull(root_cid: str, dpath: os.PathLike | str, rel_path: os.PathLike | str) -> None:
    """
    Download a CID into ``dpath / rel_path`` using a temporary staging path.

    Existing directories are updated with rsync when available, and replaced via
    a conservative backup/swap fallback otherwise.
    """
    dpath = Path(dpath)
    out_path = dpath / rel_path
    dpath.mkdir(parents=True, exist_ok=True)
    tmp_root = Path(tempfile.mkdtemp(prefix='git-well-ipfs-', dir=os.fspath(dpath)))
    tmp_path = tmp_root / 'payload'
    try:
        _run(['ipfs', 'get', '--progress=true', f'--output={tmp_path}', root_cid], verbose=3)
        if out_path.exists():
            if out_path.is_dir() and tmp_path.is_dir() and shutil.which('rsync'):
                _run(['rsync', '-avprP', os.fspath(tmp_path) + '/', os.fspath(out_path)], verbose=3)
            else:
                backup = out_path.with_name(out_path.name + '.old')
                if backup.exists():
                    if backup.is_dir():
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
                    if backup.is_dir():
                        shutil.rmtree(backup)
                    else:
                        backup.unlink()
        else:
            out_path.parent.mkdir(parents=True, exist_ok=True)
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
