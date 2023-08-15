The git_well Module
===================


|Pypi| |PypiDownloads| |GithubActions| |Codecov|

Git Well is a collection of git command line tools and is also a Python
module..

Installing this module installs the ``git-well`` command, which is a modal CLI
into several new git commands. These git commands are also exposed as
standalone "git" executables.

In other words after you:

.. code:: bash

   pip install git-well

To get CLI argument completion install `autocomplete
<https://pypi.org/project/argcomplete/>`_, and you can enable global
auto-completion

.. code:: bash

    pip install argcomplete
    mkdir -p ~/.bash_completion.d
    activate-global-python-argcomplete --dest ~/.bash_completion.d
    source ~/.bash_completion.d/python-argcomplete

And add this to your .bashrc

.. code:: bash

    if [ -f "$HOME/.bash_completion.d/python-argcomplete" ]; then
        # shellcheck disable=SC1091
        source "$HOME"/.bash_completion.d/python-argcomplete
    fi


NOTE: if you know of a way to make this easier please let me know!


Then you can run

.. code:: bash

   # Show all the commands exposed by this repo.
   git well --help

   git well sync --help

   # OR

   git sync


Top Level CLI:

.. code::

    usage: git-well [-h] {squash_streaks,branch_upgrade,sync,branch_cleanup,track_upstream,rebase_add_continue,remote_protocol,discover_remote} ...

    options:
      -h, --help            show this help message and exit

    commands:
      {squash_streaks,branch_upgrade,sync,branch_cleanup,track_upstream,rebase_add_continue,remote_protocol,discover_remote}
                            specify a command to run
        squash_streaks      Squashes consecutive commits that meet a specified criteiron.
        branch_upgrade      Upgrade to the latest "dev" branch. I.e. search for the branch
        sync                Sync a git repo with a remote server via ssh
        branch_cleanup      Cleanup branches that have been merged into main.
        track_upstream      Set the branch upstream with sensible defaults if possible.
        rebase_add_continue
                            A single step to make rebasing easier.
        remote_protocol     Helper to change a remote from https to ssh / git for a specific user /
        discover_remote     Attempt to discover a ssh remote based on an ssh host.



The tools in this module are derived from:

* https://github.com/Erotemic/git-sync
* https://github.com/Erotemic/local/tree/main/git_tools



.. |Pypi| image:: https://img.shields.io/pypi/v/git_well.svg
    :target: https://pypi.python.org/pypi/git_well

.. |PypiDownloads| image:: https://img.shields.io/pypi/dm/git_well.svg
    :target: https://pypistats.org/packages/git_well

.. |GithubActions| image:: https://github.com/Erotemic/git_well/actions/workflows/tests.yml/badge.svg?branch=main
    :target: https://github.com/Erotemic/git_well/actions?query=branch%3Amain

.. |Codecov| image:: https://codecov.io/github/Erotemic/git_well/badge.svg?branch=main&service=github
    :target: https://codecov.io/github/Erotemic/git_well?branch=main
