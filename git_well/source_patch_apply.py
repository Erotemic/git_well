"""
Dependency-free application of git-well incremental source patches.

This module intentionally imports only the Python standard library and invokes
Git through the ``git`` executable. Patch generation copies this exact file into
each patch artifact as ``APPLY_SOURCE_PATCH.py``, so recipients do not need an
installed copy of git-well to reconstruct the target source archive.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable

PathLike = str | os.PathLike[str]
PATCH_MANIFEST_FNAME = 'GIT_WELL_SOURCE_PATCH.json'
PATCH_SCHEMA = 1


class SourcePatchError(RuntimeError):
    """Raised when an incremental source patch cannot be applied safely."""


@dataclass(frozen=True)
class _Entry:
    kind: str
    mode: int
    digest: str | None = None
    link_target: str | None = None


def _run(
    args: Iterable[PathLike],
    *,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    cmd = [os.fspath(part) for part in args]
    proc = subprocess.run(
        cmd,
        cwd=os.fspath(cwd) if cwd is not None else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and proc.returncode != 0:
        rendered = ' '.join(cmd)
        raise SourcePatchError(
            f'command failed with code {proc.returncode}: {rendered}\n'
            f'stdout:\n{proc.stdout}\nstderr:\n{proc.stderr}'
        )
    return proc


def _git_command(repo: Path, *args: PathLike) -> list[PathLike]:
    # Archives are often unpacked under a uid different from the uid stored in
    # their tar metadata. Trust only this exact extracted repository for this
    # invocation; never modify global Git configuration.
    return [
        'git',
        '-c',
        f'safe.directory={repo.resolve()}',
        '-C',
        repo,
        *args,
    ]


def _git(repo: Path, *args: str, check: bool = True) -> str:
    return _run(_git_command(repo, *args), check=check).stdout.rstrip('\n')


def _sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open('rb') as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def _safe_target(root: Path, relpath: str) -> Path:
    pure = PurePosixPath(relpath)
    if pure.is_absolute() or any(part in {'', '.', '..'} for part in pure.parts):
        raise SourcePatchError(f'unsafe patch path: {relpath!r}')
    path = root.joinpath(*pure.parts)
    resolved_parent = path.parent.resolve()
    root_resolved = root.resolve()
    if resolved_parent != root_resolved and root_resolved not in resolved_parent.parents:
        raise SourcePatchError(f'patch path escapes target root: {relpath!r}')
    return path


def _safe_extract_tar(tar, dst: Path) -> None:
    for member in tar.getmembers():
        pure = PurePosixPath(member.name)
        if pure.is_absolute() or '..' in pure.parts:
            raise SourcePatchError(f'unsafe archive member: {member.name!r}')
    try:
        tar.extractall(dst, filter='fully_trusted')
    except TypeError:
        tar.extractall(dst)


def _extract_archive(path: Path, dst: Path) -> None:
    import tarfile
    import zipfile

    dst.mkdir(parents=True, exist_ok=True)
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path, 'r') as zfile:
            for info in zfile.infolist():
                pure = PurePosixPath(info.filename)
                if pure.is_absolute() or '..' in pure.parts:
                    raise SourcePatchError(
                        f'unsafe archive member: {info.filename!r}'
                    )
                target = dst.joinpath(*pure.parts)
                mode = (info.external_attr >> 16) & 0xFFFF
                if info.is_dir() or stat.S_ISDIR(mode):
                    target.mkdir(parents=True, exist_ok=True)
                    if mode:
                        os.chmod(target, stat.S_IMODE(mode))
                elif stat.S_ISLNK(mode):
                    target.parent.mkdir(parents=True, exist_ok=True)
                    if os.path.lexists(target):
                        _remove_path(target)
                    os.symlink(zfile.read(info).decode(), target)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with zfile.open(info, 'r') as src, target.open('wb') as file:
                        shutil.copyfileobj(src, file)
                    if mode:
                        os.chmod(target, stat.S_IMODE(mode))
    else:
        try:
            with tarfile.open(path, 'r:*') as tar:
                _safe_extract_tar(tar, dst)
        except tarfile.TarError as ex:
            raise SourcePatchError(f'unsupported or invalid archive: {path}') from ex


def _repo_head(repo: Path) -> str:
    return _git(repo, 'rev-parse', 'HEAD')


def _repo_has_commit(repo: Path, commit: str) -> bool:
    proc = _run(
        _git_command(repo, 'cat-file', '-e', f'{commit}^{{commit}}'),
        check=False,
    )
    return proc.returncode == 0


def _apply_git_delta(repo: Path, bundle: Path | None, target_head: str) -> None:
    if bundle is not None:
        if not bundle.is_file():
            raise SourcePatchError(f'patch Git bundle is missing: {bundle}')
        _run(_git_command(repo, 'bundle', 'unbundle', bundle))
    if not _repo_has_commit(repo, target_head):
        raise SourcePatchError(
            f'patch did not provide target commit {target_head} for {repo}'
        )
    _run(_git_command(repo, 'reset', '--hard', '--quiet', target_head))


def _entry_for(path: Path) -> _Entry:
    st = path.lstat()
    mode = stat.S_IMODE(st.st_mode)
    if stat.S_ISLNK(st.st_mode):
        return _Entry('symlink', mode, link_target=os.readlink(path))
    if stat.S_ISDIR(st.st_mode):
        return _Entry('dir', mode)
    if stat.S_ISREG(st.st_mode):
        hasher = hashlib.sha256()
        with path.open('rb') as file:
            for chunk in iter(lambda: file.read(1024 * 1024), b''):
                hasher.update(chunk)
        return _Entry('file', mode, digest=hasher.hexdigest())
    raise SourcePatchError(f'unsupported filesystem entry in source archive: {path}')


def _path_is_excluded(
    rel: PurePosixPath, excluded: tuple[PurePosixPath, ...]
) -> bool:
    for prefix in excluded:
        if rel == prefix or prefix in rel.parents:
            return True
    return False


def _tree_entries(
    root: Path, *, excluded: tuple[PurePosixPath, ...]
) -> dict[str, _Entry]:
    entries: dict[str, _Entry] = {}

    def walk(path: Path, rel: PurePosixPath) -> None:
        if rel.parts and _path_is_excluded(rel, excluded):
            return
        if rel.parts:
            entries[rel.as_posix()] = _entry_for(path)
        st = path.lstat()
        if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode):
            return
        for child in sorted(path.iterdir(), key=lambda item: item.name):
            child_rel = rel / child.name if rel.parts else PurePosixPath(child.name)
            walk(child, child_rel)

    walk(root, PurePosixPath())
    return entries


def _remove_path(path: Path) -> None:
    if not os.path.lexists(path):
        return
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _copy_entry(src: Path, dst: Path, entry: _Entry) -> None:
    if entry.kind == 'dir':
        if os.path.lexists(dst) and (dst.is_symlink() or not dst.is_dir()):
            _remove_path(dst)
        dst.mkdir(parents=True, exist_ok=True)
        os.chmod(dst, entry.mode)
    elif entry.kind == 'symlink':
        if os.path.lexists(dst):
            _remove_path(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.symlink(os.readlink(src), dst)
    elif entry.kind == 'file':
        if os.path.lexists(dst) and (dst.is_symlink() or dst.is_dir()):
            _remove_path(dst)
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dst)
        os.chmod(dst, entry.mode)
    else:  # pragma: no cover
        raise AssertionError(entry.kind)


def _apply_deletions(root: Path, deletions: Iterable[str]) -> None:
    for relpath in sorted(
        deletions, key=lambda value: (value.count('/'), value), reverse=True
    ):
        _remove_path(_safe_target(root, relpath))


def _apply_overlay(overlay_root: Path, target_root: Path) -> None:
    if not overlay_root.exists():
        return
    entries = _tree_entries(overlay_root, excluded=())
    for relpath in sorted(entries, key=lambda value: (value.count('/'), value)):
        src = _safe_target(overlay_root, relpath)
        dst = _safe_target(target_root, relpath)
        _copy_entry(src, dst, entries[relpath])


def _read_patch_manifest(patch_root: Path) -> dict:
    path = patch_root / PATCH_MANIFEST_FNAME
    try:
        data = json.loads(path.read_text(encoding='utf8'))
    except (OSError, json.JSONDecodeError) as ex:
        raise SourcePatchError(f'invalid source patch manifest: {path}') from ex
    if data.get('schema_version') != PATCH_SCHEMA:
        raise SourcePatchError(
            f'unsupported source patch schema: {data.get("schema_version")!r}'
        )
    if data.get('kind') != 'git-well-source-patch':
        raise SourcePatchError('archive is not a git-well source patch')
    return data


def _find_patch_root(extract_root: Path) -> Path:
    candidates = [
        path
        for path in extract_root.iterdir()
        if path.is_dir() and (path / PATCH_MANIFEST_FNAME).is_file()
    ]
    if len(candidates) != 1:
        raise SourcePatchError(
            'source patch archive must contain exactly one top-level patch root'
        )
    return candidates[0]


def apply_extracted_source_patch(
    base_archive: PathLike,
    patch_root: PathLike,
    output_dpath: PathLike,
) -> Path:
    """Apply an already-extracted patch directory to ``base_archive``."""
    base_archive = Path(base_archive).expanduser().resolve()
    patch_root = Path(patch_root).expanduser().resolve()
    output_dpath = Path(output_dpath).expanduser().resolve()
    if not base_archive.is_file():
        raise SourcePatchError(f'base source archive does not exist: {base_archive}')
    if not (patch_root / PATCH_MANIFEST_FNAME).is_file():
        raise SourcePatchError(f'patch manifest is missing from: {patch_root}')
    if output_dpath.exists() and not output_dpath.is_dir():
        raise SourcePatchError(f'patch output path is not a directory: {output_dpath}')
    if output_dpath.exists() and any(output_dpath.iterdir()):
        raise SourcePatchError(f'patch output directory is not empty: {output_dpath}')
    output_dpath.mkdir(parents=True, exist_ok=True)

    try:
        manifest = _read_patch_manifest(patch_root)
        expected_sha = manifest['base']['archive_sha256']
        actual_sha = _sha256_file(base_archive)
        if actual_sha != expected_sha:
            raise SourcePatchError(
                'source patch base SHA-256 mismatch; expected '
                f'{expected_sha}, got {actual_sha}'
            )

        _extract_archive(base_archive, output_dpath)
        base_root = output_dpath / manifest['base']['root_name']
        if not base_root.is_dir():
            raise SourcePatchError('base archive root is missing after extraction')
        target_root = output_dpath / manifest['target']['root_name']
        if target_root != base_root:
            if target_root.exists():
                raise SourcePatchError(
                    f'target archive root already exists: {target_root}'
                )
            base_root.rename(target_root)

        for item in manifest['git_repositories']:
            relpath = item['path']
            repo = target_root if relpath == '.' else _safe_target(target_root, relpath)
            if not (repo / '.git').is_dir():
                raise SourcePatchError(
                    f'patch expects a Git-bearing repository at {relpath!r}'
                )
            current_head = _repo_head(repo)
            if current_head != item['base_head']:
                raise SourcePatchError(
                    f'patch base HEAD mismatch at {relpath!r}: expected '
                    f'{item["base_head"]}, got {current_head}'
                )
            bundle = (
                patch_root / item['bundle']
                if item.get('bundle') is not None
                else None
            )
            _apply_git_delta(repo, bundle, item['target_head'])

        deletions_path = patch_root / manifest['deletions_file']
        try:
            deletions = json.loads(deletions_path.read_text(encoding='utf8'))
        except (OSError, json.JSONDecodeError) as ex:
            raise SourcePatchError(
                f'invalid source patch deletion manifest: {deletions_path}'
            ) from ex
        if not isinstance(deletions, list) or not all(
            isinstance(item, str) for item in deletions
        ):
            raise SourcePatchError('invalid source patch deletion manifest')
        _apply_deletions(target_root, deletions)
        _apply_overlay(patch_root / manifest['overlay_root'], target_root)

        if _repo_head(target_root) != manifest['target']['head_sha']:
            raise SourcePatchError('applied source patch did not reach target HEAD')
        for item in manifest['git_repositories']:
            relpath = item['path']
            repo = target_root if relpath == '.' else _safe_target(target_root, relpath)
            if _repo_head(repo) != item['target_head']:
                raise SourcePatchError(
                    f'applied source patch HEAD mismatch at {relpath!r}'
                )
        return target_root
    except Exception:
        shutil.rmtree(output_dpath, ignore_errors=True)
        raise


def apply_source_patch(
    base_archive: PathLike,
    patch_archive: PathLike,
    output_dpath: PathLike,
) -> Path:
    """Extract ``patch_archive`` and apply it to ``base_archive``."""
    patch_archive = Path(patch_archive).expanduser().resolve()
    if not patch_archive.is_file():
        raise SourcePatchError(f'source patch archive does not exist: {patch_archive}')

    work = Path(tempfile.mkdtemp(prefix='git-well-apply-source-patch.'))
    try:
        patch_extract = work / 'patch'
        _extract_archive(patch_archive, patch_extract)
        patch_root = _find_patch_root(patch_extract)
        return apply_extracted_source_patch(base_archive, patch_root, output_dpath)
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            'Apply an extracted git-well source patch without requiring git-well. '
            'Run this script from inside the extracted patch directory.'
        )
    )
    parser.add_argument('base_archive', help='exact full base archive named by the patch')
    parser.add_argument(
        'output_dpath',
        help='empty/nonexistent directory where reconstructed source is written',
    )
    parser.add_argument(
        '--patch-archive',
        default=None,
        help=(
            'optional patch archive path; when omitted, use the patch manifest '
            'beside this script'
        ),
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.patch_archive is None:
            patch_root = Path(__file__).resolve().parent
            if not (patch_root / PATCH_MANIFEST_FNAME).is_file():
                parser.error(
                    'no patch manifest beside this script; pass --patch-archive '
                    'when running the installed module directly'
                )
            result = apply_extracted_source_patch(
                args.base_archive, patch_root, args.output_dpath
            )
        else:
            result = apply_source_patch(
                args.base_archive, args.patch_archive, args.output_dpath
            )
    except SourcePatchError as ex:
        parser.exit(2, f'error: {ex}\n')
    print(result)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
