#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
import scriptconfig as scfg


class GitWellModalCLI(scfg.ModalCLI):
    # When adding a new top-level CLI, need to update:
    # ~/code/git_well/pyproject.toml
    # ~/code/git_well/setup.py
    from git_well.git_squash_streaks import __cli__ as squash_streaks
    from git_well.git_branch_upgrade import __cli__ as branch_upgrade
    from git_well.git_sync import __cli__ as sync
    from git_well.git_branch_cleanup import __cli__ as branch_cleanup
    from git_well.git_track_upstream import __cli__ as track_upstream
    from git_well.git_rebase_add_continue import __cli__ as rebase_add_continue
    from git_well.git_remote_protocol import __cli__ as remote_protocol
    from git_well.git_discover_remote import __cli__ as discover_remote


def main():
    modal = GitWellModalCLI()
    modal.main()


if __name__ == '__main__':
    """
    CommandLine:
        python ~/code/git_well/git_well/main.py --help
    """
    main()
