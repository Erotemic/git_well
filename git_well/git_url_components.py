#!/usr/bin/env python3
import scriptconfig as scfg
import ubelt as ub
from git_well._utils import GitURL


class GitUrlComponentsCLI(scfg.DataConfig):
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

    url = scfg.Value(None, help='the git url to parse', position=1)
    component = scfg.Value(None, help='The component to access and print. If unspecified all info is printed in json format', position=2)
    protocol = scfg.Value(None, help='If specified, convert to the specified protocol first')
    verbose = scfg.Flag(0, help='verbosity level')

    @classmethod
    def main(cls, argv=1, **kwargs):
        """
        Example:
            >>> # xdoctest: +SKIP
            >>> from git_well.git_url_components import *  # NOQA
            >>> argv = 0
            >>> kwargs = dict(url='https://foo.bar/user/repo.git')
            >>> cls = GitUrlComponentsCLI
            >>> config = cls(**kwargs)
            >>> cls.main(argv=argv, **config)
            >>> cls.main(argv=0, url='https://foo.bar/user/repo.git', component='repo_name')
            >>> cls.main(argv=0, url='host:path/to/my/repo/.git')
        """
        config = cls.cli(argv=argv, data=kwargs, strict=True)
        if config.verbose:
            import rich
            from rich.markup import escape
            rich.print('config = ' + escape(ub.urepr(config, nl=1)))
        import json
        if config['url'] is None:
            raise ValueError('A url must be specified')
        url = GitURL(config['url'])
        if config.protocol is not None:
            url = url.to_protocol(config.protocol)
        if config.component is None:
            print(json.dumps(url.info, indent='    '))
        else:
            print(url.info[config.component])

__cli__ = GitUrlComponentsCLI

if __name__ == '__main__':
    """

    CommandLine:
        python ~/code/git_well/git_well/git_url_components.py
        python -m git_well.git_url_components
    """
    __cli__.main()
