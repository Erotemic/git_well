The git_well Module
===================

Git Well is a collection of git command line tools and is also a Python
module..

Installing this module installs the ``git-well`` command, which is a modal CLI
into several new git commands. These git commands are also exposed as
standalone "git" executables.

In other words after you:

.. code:: bash

   pip install git-well


Then you can run

.. code:: bash

   # Show all the commands exposed by this repo.
   git well --help

   git well sync --help

   # OR

   git sync



|Pypi| |PypiDownloads| |GithubActions| |Codecov|



.. |Pypi| image:: https://img.shields.io/pypi/v/git_well.svg
    :target: https://pypi.python.org/pypi/git_well

.. |PypiDownloads| image:: https://img.shields.io/pypi/dm/git_well.svg
    :target: https://pypistats.org/packages/git_well

.. |GithubActions| image:: https://github.com/Erotemic/git_well/actions/workflows/tests.yml/badge.svg?branch=main
    :target: https://github.com/Erotemic/git_well/actions?query=branch%3Amain

.. |Codecov| image:: https://codecov.io/github/Erotemic/git_well/badge.svg?branch=main&service=github
    :target: https://codecov.io/github/Erotemic/git_well?branch=main
