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



Archiving pull-request review state
-----------------------------------

``git archive-source`` normally archives the committed checkout and the
history reachable from the current ``HEAD``. When reviewing a pull request
from a contributor fork, use ``--all-branches`` to also preserve every local
branch and every remote-tracking branch that has already been fetched into the
superproject repository:

.. code:: bash

   git remote add contributor git@github.com:contributor/project.git
   git fetch contributor
   git archive-source --all-branches

The archive operation itself does not contact ``origin``, ``contributor``, or
any other configured remote. It copies the locally cached ``refs/heads/*`` and
``refs/remotes/*`` state, so the unpacked archive can run commands such as
``git branch --all``, ``git log contributor/topic``, and
``git diff main...contributor/topic`` even if the original fork is no longer
reachable. The archived working tree remains detached at the exact original
``HEAD`` commit. ``--all-branches`` is opt-in, applies to the superproject,
honors positive ``--depth`` values from each included branch tip, and cannot
be combined with source-only ``--depth 0`` archives.


Tracking large files with IPFS
------------------------------

``git-well`` also exposes an experimental ``git ipfs`` helper for keeping
large payloads out of Git while tracking reproducible IPFS CIDs in small
``*.ipfs`` sidecar files.  There is intentionally no repository-local IPFS
store and no required ``git ipfs init`` step: Kubo still owns its normal
``$IPFS_PATH`` repository, while Git only tracks small sidecar metadata.

The minimum supported Kubo version is **0.37.0**.  This is the first Kubo
release with ``ipfs add --pin-name``, which ``git ipfs add --name`` uses to
assign a human-readable name at import time.

The intended happy path is:

.. code:: bash

   git ipfs doctor
   git ipfs add data/ --name my-data
   git commit -m "Track data with IPFS"

When you know the peer that is likely to provide the content, record it during
add:

.. code:: bash

   git ipfs add data/ --name my-data --suggested-peers 12D3KooW...

A collaborator can then materialize the payloads with:

.. code:: bash

   git clone <repo-url>
   cd <repo>
   git ipfs doctor
   git ipfs pull

The sidecar stores the CID, relative path, object kind, byte size, and the
CID-affecting import settings.  Volatile local details such as mtimes, command
elapsed time, and machine-specific cache state are intentionally left out of
the committed sidecar so repeated runs stay reviewable.

Sidecar schema v1
~~~~~~~~~~~~~~~~~

The ``schema_version: 1`` contract is intentionally small and YAML-first.
Unknown fields should be ignored by readers, and fields that are not needed to
materialize content should be treated as advisory.  This keeps hand-written
sidecars easy to review and gives future versions room to add optional metadata
without breaking old repositories.

.. code:: yaml

   schema_version: 1
   type: ipfs-sidecar
   cid: bafy...
   rel_path: data
   kind: directory
   size_bytes: 123456
   num_files: 42
   pin_name: my-data
   import:
     recursive: true
     cid_version: 1
     raw_leaves: false
   suggested_peers:
     - 12D3KooW...
     - /ip4/203.0.113.10/tcp/4001/p2p/12D3KooW...

Required fields for materialization are ``cid`` and ``rel_path``.  The
``import`` mapping records options that affect CID reproducibility.
``pin_name``, sizes, counts, and ``suggested_peers`` are useful metadata, but a
reader should still be able to pull content without them.

Sidecars may include peer hints.  Bare peer IDs are useful hints when routing
can discover addresses; full multiaddrs are more reliable when known.
``git ipfs pull`` makes a best-effort attempt to connect to ``suggested_peers``
before downloading.  Peer hints can also be inspected or connected manually:

.. code:: bash

   git ipfs peers
   git ipfs peers --connect

To make the data retrievable by others, configure a Kubo remote pinning service
and push sidecar CIDs:

.. code:: bash

   ipfs pin remote service add <service-name> <endpoint> <key>
   git ipfs push --service <service-name>

Useful inspection commands:

.. code:: bash

   git ipfs status
   git ipfs status --full
   git ipfs export --emit_bash

Dogfood checklist
~~~~~~~~~~~~~~~~~

When changing this workflow, test it on a scratch branch with a small real
payload before publishing the change:

.. code:: bash

   git ipfs doctor
   mkdir -p .git-well-ipfs-smoke
   printf 'hello from git ipfs\n' > .git-well-ipfs-smoke/payload.txt
   git ipfs add .git-well-ipfs-smoke --name git-well-ipfs-smoke
   git status --short .gitignore .git-well-ipfs-smoke.ipfs
   rm -rf .git-well-ipfs-smoke
   git ipfs pull .git-well-ipfs-smoke.ipfs
   git ipfs status .git-well-ipfs-smoke.ipfs

The helper script ``dev/ipfs_dogfood_smoke.sh`` runs the same smoke flow and
accepts an optional peer hint as its second argument.

Troubleshooting
~~~~~~~~~~~~~~~

``git ipfs doctor`` is the first command to run when retrieval is confusing.
Common failure modes are reported with actionable hints:

* ``ipfs`` is missing from ``PATH``: install Kubo >= 0.37.0.
* The Kubo repo is missing: run ``ipfs init`` for Kubo itself.  This does not
  create anything inside the Git repository.
* The daemon/API is offline: start ``ipfs daemon`` or check ``IPFS_PATH``.
* Retrieval cannot find a CID: check that someone is pinning the content, then
  try ``git ipfs peers --connect`` or add more specific ``suggested_peers``.
* Named pins fail: upgrade to Kubo >= 0.37.0 or omit ``--name``.


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
