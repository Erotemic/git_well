#!/usr/bin/env python3
from __future__ import annotations

from typing import Any

import scriptconfig as scfg
import ubelt as ub
import json


class GitApplyPatchCLI(scfg.DataConfig):
    """
    Applies a saved patch from the patch directory.
    """

    __command__ = 'apply'
    patch = scfg.Value(
        None, help='Path to a specific patch file or JSON metadata file'
    )
    patch_dpath = scfg.Value('patches', help='Directory of saved patches')
    list_only = scfg.Flag(False, help='Only list available patches')
    dry = scfg.Flag(False, help='Show what would be applied without applying')
    restore_patch = scfg.Flag(
        False, help='Restore (unstage) patch files after applying'
    )

    @classmethod
    def main(
        cls, argv: list[str] | str | bool | None = True, **kwargs: Any
    ) -> None:
        """
        Example:
            >>> from git_well.patchdir.git_patchdir_apply import GitApplyPatchCLI
            >>> from git_well.patchdir.git_patchdir_save import GitSavePatchCLI
            >>> from git_well.repo import Repo
            >>> repo = Repo.demo()
            >>> file_fpath = repo.dpath / 'foo.txt'
            >>> file_fpath.write_text('hello world\\n')
            >>> repo.cmd('git add foo.txt')
            >>> repo.cmd('git commit -m "init commit"')
            >>> # Modify the file
            >>> file_fpath.write_text('hello patch\\n')
            >>> # Save the patch
            >>> import os
            >>> # TODO: handle resolving the relative git path
            >>> os.chdir(repo.dpath)
            >>> GitSavePatchCLI.main(argv=[
            ...     str(file_fpath),
            ...     '--out-dpath', str(repo.dpath / 'patches'),
            ...     '--message', 'changing hello world to hello patch'
            ... ])
            >>> # Restore to original (clean) state
            >>> repo.cmd('git restore foo.txt')

            >>> # Check file content reverted
            >>> assert file_fpath.read_text() == 'hello world\\n'
            >>> # Apply the patch
            >>> patch_files = list((repo.dpath / 'patches').glob('*.patch'))
            >>> assert len(patch_files) == 1
            >>> patch_fpath = patch_files[0]
            >>> GitApplyPatchCLI.main(argv=[
            ...     '--patch', str(patch_fpath)
            ... ])
            >>> # Now check the file has the patched content
            >>> assert file_fpath.read_text() == 'hello patch\\n'

        """
        import rich
        from rich.markup import escape

        config = cls.cli(argv=argv, data=kwargs, strict=True)
        rich.print('config = ' + escape(ub.urepr(config, nl=1)))

        patch_dpath = ub.Path(config.patch_dpath)
        if not patch_dpath.exists():
            from git_well._utils import rich_print_path

            rich_print_path('No patch directory found at ', patch_dpath)
            return

        # List all available patches
        patch_files = sorted(patch_dpath.glob('*.patch'))

        if config.list_only:
            for patch_fpath in patch_files:
                base = patch_fpath.stem
                meta_fpath = patch_dpath / (base + '.json')
                msg = ''
                if meta_fpath.exists():
                    meta = json.loads(meta_fpath.read_text())
                    msg = meta.get('message', '')
                print(f'- {patch_fpath.name} : {msg}')
            return

        if config.patch is None:
            print(
                'You must specify a patch file to apply (or use --list-only).'
            )
            return

        patch_input = ub.Path(config.patch)
        if not patch_input.exists():
            # Try to resolve relative to patch_dpath
            patch_input = patch_dpath / config.patch
            if not patch_input.exists():
                raise FileNotFoundError(f'Could not find patch: {config.patch}')

        # Normalize to .patch if JSON provided
        if patch_input.suffix == '.json':
            with open(patch_input) as f:
                meta = json.load(f)
            patch_fpath = patch_dpath / meta['patch_file']
        elif patch_input.suffix == '.patch':
            patch_fpath = patch_input
        else:
            raise ValueError(f'Unsupported patch file type: {patch_input}')

        if not patch_fpath.exists():
            raise FileNotFoundError(f'Patch file does not exist: {patch_fpath}')

        # Show metadata if available
        meta_fpath = patch_fpath.augment(ext='.json')
        if meta_fpath.exists():
            meta = json.loads(meta_fpath.read_text())
            rich.print(f'[green]Applying patch:[/green] {patch_fpath.name}')
            if 'message' in meta:
                print(f'Message: {meta["message"]}')
            if 'files' in meta:
                print(f'Affected files: {meta["files"]}')
        else:
            print(f'Applying patch: {patch_fpath.name}')

        if config.dry:
            print(f'[dry-run] Would apply: {patch_fpath}')
            return

        # Apply the patch
        result = ub.cmd(['git', 'apply', str(patch_fpath)], verbose=True)
        if result['ret'] != 0:
            print('[!] Patch failed to apply.')
            print(result['err'])
        else:
            print('[✓] Patch applied.')

        if config.restore_patch:
            # Optionally revert changes made by patch (e.g., for testing)
            result = ub.cmd(
                ['git', 'restore', '--source=HEAD', '--staged', '--worktree']
                + meta.get('files', []),
                verbose=True,
            )
            print('[~] Patch restored from working tree (for dry testing).')


__cli__ = GitApplyPatchCLI

if __name__ == '__main__':
    """
    CommandLine:
        python ~/code/git_well/git_well/git_apply_patch.py --list-only
        python ~/code/git_well/git_well/git_apply_patch.py --patch patch_20250714T183201Z_4e5f3a12.patch
        python -m git_well.git_apply_patch --patch patches/patch_20250714T183201Z_4e5f3a12.json
    """
    __cli__.main()
