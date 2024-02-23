The git_well Module
===================


|Pypi| |PypiDownloads| |GithubActions| |Codecov|  |ReadTheDocs|

+------------------+----------------------------------------------+
| Read the docs    | https://python-git-well.readthedocs.io       |
+------------------+----------------------------------------------+
| Github           | https://github.com/Erotemic/git_well         |
+------------------+----------------------------------------------+
| Pypi             | https://pypi.org/project/git_well            |
+------------------+----------------------------------------------+

Git Well is a collection of git command line tools and is also a Python
module.

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



Use Cases
---------

Have you ever run into this error when you run ``git pull``?

.. code::

    There is no tracking information for the current branch.
    Please specify which branch you want to merge with.
    See git-pull(1) for details.

        git pull <remote> <branch>

    If you wish to set tracking information for this branch you can do so with:

        git branch --set-upstream-to=origin/<branch> the_current_branch

I find this a huge pain because it can't even be bothered to fill in `<branch>` so you have to munge the command to type:

.. code::

        git branch --set-upstream-to=origin/the_current_branch the_current_branch


I get why they did this, the branch on the remote might have a different name.
But... it usually doesn't. That's why I implemented ``git-well track-upstream``.


Running this instead will detect if you are in the simple case and just do it
for you. Otherwise it will enumerate your options and ask you to pick one.

I've found this command to prevent so much disruption that instaling git-well
install ``git track-upstream`` as its own command.




.. |Pypi| image:: https://img.shields.io/pypi/v/git_well.svg
    :target: https://pypi.python.org/pypi/git_well

.. |PypiDownloads| image:: https://img.shields.io/pypi/dm/git_well.svg
    :target: https://pypistats.org/packages/git_well

.. |GithubActions| image:: https://github.com/Erotemic/git_well/actions/workflows/tests.yml/badge.svg?branch=main
    :target: https://github.com/Erotemic/git_well/actions?query=branch%3Amain

.. |Codecov| image:: https://codecov.io/github/Erotemic/git_well/badge.svg?branch=main&service=github
    :target: https://codecov.io/github/Erotemic/git_well?branch=main

.. |ReadTheDocs| image:: https://readthedocs.org/projects/python-git_well/badge/?version=latest
    :target: http://python-git-well.readthedocs.io/en/latest/
