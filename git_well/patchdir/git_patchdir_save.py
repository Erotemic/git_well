#!/usr/bin/env python3
from __future__ import annotations

from typing import Any

import kwconf
import ubelt as ub


class GitSavePatchCLI(kwconf.Config):
    """
    Saves the diff into a folder of patches that can be applied later.
    """

    __command__ = 'save'
    paths = kwconf.Value(
        [], nargs='+', help='one or more files to save patches from', position=1
    )
    out_dpath = kwconf.Value('patches', help='output directory to save patches')
    message = kwconf.Value(
        None,
        help='if specified also associates a message with this patch in a sidecar file',
        short_alias=['m'],
    )

    @classmethod
    def main(
        cls, argv: list[str] | str | bool | None = True, **kwargs: Any
    ) -> None:
        """
        Example:
            >>> # xdoctest: +SKIP
            >>> from git_well.git_save_patch import *  # NOQA
            >>> argv = False
            >>> kwargs = dict()
            >>> cls = GitSavePatchCLI
            >>> config = cls(**kwargs)
            >>> cls.main(argv=argv, **config)
        """
        import rich
        from rich.markup import escape

        config = cls.cli(argv=argv, data=kwargs, strict=True)
        rich.print('config = ' + escape(ub.urepr(config, nl=1)))

        import json

        out_dpath = ub.Path(config.out_dpath).ensuredir()

        # TODO: handle resolving the relative git path
        file_list = [ub.Path(p) for p in config.paths]
        rel_file_paths = [str(p) for p in file_list]

        # Get unified diff from git
        git_cmd = ['git', 'diff', 'HEAD', '--'] + rel_file_paths
        info = ub.cmd(git_cmd, verbose=0, check=True)

        if not info['out'].strip():
            print('No changes found in specified files. Nothing to save.')
            return

        # Create a unique ID using hash of diff
        diff_text = info['out']
        hash_prefix = ub.hash_data(diff_text)[0:8]
        timestamp = ub.timestamp()
        base_name = f'patch_{timestamp}_{hash_prefix}'

        patch_fpath = out_dpath / (base_name + '.patch')
        meta_fpath = out_dpath / (base_name + '.json')

        # Write the patch file
        patch_fpath.write_text(diff_text)

        # Build metadata
        metadata = {
            'files': rel_file_paths,
            'timestamp': timestamp,
            'hash': hash_prefix,
            'patch_file': patch_fpath.name,
        }

        if config.message:
            metadata['message'] = config.message

        # Write metadata
        meta_fpath.write_text(json.dumps(metadata, indent=4))

        print(f'Saved patch to: {patch_fpath}')
        print(f'Saved metadata to: {meta_fpath}')


__cli__ = GitSavePatchCLI

if __name__ == '__main__':
    """

    CommandLine:
        python ~/code/git_well/git_well/git_save_patch.py
        python -m git_well.git_save_patch
    """
    __cli__.main()
