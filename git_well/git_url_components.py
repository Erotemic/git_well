#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
from __future__ import annotations

from typing import Any

import kwconf
import ubelt as ub


class GitUrlComponentsCLI(kwconf.Config):
    """
    Access components of a git URL.

    Usage
    -----
    python -m git_well url "https://foo.bar/user/repo.git"
    python -m git_well url "https://foo.bar/user/repo.git" repo_name

    SeeAlso
    -------
    :class:`GitURL`
    """

    __command__ = 'url'

    url = kwconf.Value(None, help='the git url to parse', position=1)
    component = kwconf.Value(
        None,
        help='The component to access and print. If unspecified all info is printed in json format',
        position=2,
    )
    protocol = kwconf.Value(
        None, help='If specified, convert to the specified protocol first'
    )
    verbose = kwconf.Flag(False, help='verbosity level')

    @classmethod
    def main(
        cls, argv: list[str] | str | bool | None = True, **kwargs: Any
    ) -> None:
        """
        Example:
            >>> # xdoctest: +SKIP
            >>> from git_well.git_url_components import *  # NOQA
            >>> argv = False
            >>> kwargs = dict(url='https://foo.bar/user/repo.git')
            >>> cls = GitUrlComponentsCLI
            >>> config = cls(**kwargs)
            >>> cls.main(argv=argv, **config)
            >>> cls.main(argv=False, url='https://foo.bar/user/repo.git', component='repo_name')
            >>> cls.main(argv=False, url='host:path/to/my/repo/.git')
        """
        config = cls.cli(argv=argv, data=kwargs, strict=True)
        if config.verbose:
            import rich
            from rich.markup import escape

            rich.print('config = ' + escape(ub.urepr(config, nl=1)))
        import json

        if config['url'] is None:
            raise ValueError('A url must be specified')
        from git_well._utils import GitURL

        url = GitURL(config['url'])
        if config.protocol is not None:
            url = url.to_protocol(config.protocol)
        if config.component is None:
            print(json.dumps(url.info, indent='    '))
        else:
            info = dict(url.info)
            print(info[config.component])


__cli__ = GitUrlComponentsCLI

if __name__ == '__main__':
    """

    CommandLine:
        python ~/code/git_well/git_well/git_url_components.py
        python -m git_well.git_url_components
    """
    __cli__.main()
