from __future__ import annotations

import kwconf as kw


class GitWellPatchDirModalCLI(kw.ModalCLI):
    """
    Subcommands for handling patch directories
    """

    __command__ = 'patchdir'
    from git_well.patchdir.git_patchdir_save import __cli__ as save
    from git_well.patchdir.git_patchdir_apply import __cli__ as apply


__cli__ = GitWellPatchDirModalCLI
