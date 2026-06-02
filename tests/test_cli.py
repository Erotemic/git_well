def test_cli_main_help():
    """
    Run help for each modal CLI
    """
    from git_well import main

    modal = main.GitWellModalCLI()

    try:
        modal.run(argv=['--help'])
    except SystemExit:
        ...

    try:
        sub_commands = [c.__command__ for c in modal.sub_clis]
    except AttributeError:
        sub_commands = [d['command'] for d in modal._subconfig_metadata]
    for command in sub_commands:
        try:
            modal.run(argv=[command, '--help'])
        except SystemExit:
            ...


def test_archive_source_help_mentions_git_archive(capsys):
    """
    The help should explain how this command relates to git-archive and the
    default full-history/submodule behavior.
    """
    from git_well.git_archive_source import ArchiveSourceCLI

    try:
        ArchiveSourceCLI.main(argv=['--help'])
    except SystemExit:
        ...
    captured = capsys.readouterr()
    assert 'git archive' in captured.out
    assert 'full Git history' in captured.out
    assert 'initialized submodules' in captured.out


def _init_demo_repo(repo):
    import ubelt as ub

    repo.mkdir()
    ub.cmd(['git', 'init'], cwd=repo, check=True)
    ub.cmd(
        ['git', 'config', 'user.email', 'test@example.com'],
        cwd=repo,
        check=True,
    )
    ub.cmd(['git', 'config', 'user.name', 'Test User'], cwd=repo, check=True)


def test_archive_source_depth_zero_source_only(tmp_path):
    """
    Build a source-only archive with --depth 0 and ensure untracked files and
    .git metadata are excluded.
    """
    import tarfile
    import ubelt as ub
    from git_well.git_archive_source import archive_source

    repo = tmp_path / 'demo'
    _init_demo_repo(repo)
    (repo / 'tracked.txt').write_text('tracked\n')
    (repo / 'untracked.txt').write_text('untracked\n')
    ub.cmd(['git', 'add', 'tracked.txt'], cwd=repo, check=True)
    ub.cmd(['git', 'commit', '-m', 'initial'], cwd=repo, check=True)

    archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'demo-source.tar.gz',
        depth=0,
        verbose=0,
    )

    with tarfile.open(archive, 'r:gz') as tar:
        names = set(tar.getnames())
    assert any(name.endswith('/tracked.txt') for name in names)
    assert not any(name.endswith('/untracked.txt') for name in names)
    assert any(name.endswith('/SOURCE_ARCHIVE_MANIFEST.txt') for name in names)
    assert not any('/.git/' in name for name in names)


def test_archive_source_auto_zip(tmp_path):
    """
    Build a source-only zip archive by inferring the format from the extension.
    """
    import zipfile
    import ubelt as ub
    from git_well.git_archive_source import archive_source

    repo = tmp_path / 'demo_zip'
    _init_demo_repo(repo)
    (repo / 'tracked.txt').write_text('tracked\n')
    (repo / 'untracked.txt').write_text('untracked\n')
    ub.cmd(['git', 'add', 'tracked.txt'], cwd=repo, check=True)
    ub.cmd(['git', 'commit', '-m', 'initial'], cwd=repo, check=True)

    archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'demo-source.zip',
        depth=0,
        format='auto',
        verbose=0,
    )

    with zipfile.ZipFile(archive, 'r') as zfile:
        names = set(zfile.namelist())
    assert any(name.endswith('/tracked.txt') for name in names)
    assert not any(name.endswith('/untracked.txt') for name in names)
    assert any(name.endswith('/SOURCE_ARCHIVE_MANIFEST.txt') for name in names)
    assert not any('/.git/' in name for name in names)


def test_archive_source_cli_options():
    from git_well.git_archive_source import ArchiveSourceCLI

    config = ArchiveSourceCLI.cli(
        argv=['--depth', '0', '-o', 'foo.tar.gz'], strict=True
    )
    assert str(config.depth) == '0'
    assert config.output == 'foo.tar.gz'

    config = ArchiveSourceCLI.cli(
        argv=['--depth', '0', '--format', 'zip', '-o', 'foo.any'], strict=True
    )
    assert str(config.format) == 'zip'
    assert config.output == 'foo.any'


def test_archive_source_repo_local_config_defaults(tmp_path):
    """
    --set_config should persist explicit repo-local defaults that later
    invocations use when the corresponding CLI options are omitted.
    """
    import pytest
    pytest.skip('TODO: re-enable when kwconf fixes modal default semantics')

    import zipfile
    import ubelt as ub
    from git_well.git_archive_source import ArchiveSourceCLI

    repo = tmp_path / 'demo_config'
    _init_demo_repo(repo)
    (repo / 'tracked.txt').write_text('tracked\n')
    ub.cmd(['git', 'add', 'tracked.txt'], cwd=repo, check=True)
    ub.cmd(['git', 'commit', '-m', 'initial'], cwd=repo, check=True)

    first_archive = tmp_path / 'first.zip'
    config_fpath = ArchiveSourceCLI.main(
        argv=[
            str(repo),
            '--set_config',
            'depth=0',
            'format=zip',
            '-o',
            str(first_archive),
            '--verbose',
            '0',
        ]
    )
    assert config_fpath == repo / '.git' / 'config'
    assert not first_archive.exists()

    depth = ub.cmd(
        ['git', 'config', '--local', '--get', 'git-well.archive-source.depth'],
        cwd=repo,
        check=True,
    ).stdout.strip()
    format = ub.cmd(
        ['git', 'config', '--local', '--get', 'git-well.archive-source.format'],
        cwd=repo,
        check=True,
    ).stdout.strip()
    assert depth == '0'
    assert format == 'zip'

    archive = ArchiveSourceCLI.main(
        argv=[
            str(repo),
            '-o',
            str(tmp_path / 'second.any'),
            '--verbose',
            '0',
        ]
    )

    with zipfile.ZipFile(archive, 'r') as zfile:
        names = set(zfile.namelist())
    assert any(name.endswith('/tracked.txt') for name in names)
    assert not any('/.git/' in name for name in names)


def test_archive_source_with_history(tmp_path):
    """
    Build a history-preserving archive and ensure it unpacks as a Git checkout.
    """
    import tarfile
    import ubelt as ub
    from git_well.git_archive_source import archive_source

    repo = tmp_path / 'demo_history'
    _init_demo_repo(repo)
    (repo / 'tracked.txt').write_text('tracked\n')
    ub.cmd(['git', 'add', 'tracked.txt'], cwd=repo, check=True)
    ub.cmd(['git', 'commit', '-m', 'initial'], cwd=repo, check=True)

    archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'demo-history-source.tar.gz',
        depth=1,
        verbose=0,
    )

    with tarfile.open(archive, 'r:gz') as tar:
        try:
            tar.extractall(tmp_path / 'extract', filter='fully_trusted')
        except TypeError:
            tar.extractall(tmp_path / 'extract')
    roots = [p for p in (tmp_path / 'extract').iterdir() if p.is_dir()]
    assert len(roots) == 1
    unpacked = roots[0]
    assert (unpacked / '.git').exists()
    proc = ub.cmd(['git', 'log', '--oneline', '-1'], cwd=unpacked, check=True)
    assert 'initial' in proc.stdout


def test_rich_link_path_markup():
    from git_well._utils import rich_link_path

    assert rich_link_path('/tmp/demo') == '[link=/tmp/demo]/tmp/demo[/link]'


def test_archive_source_prints_output_directory(tmp_path, monkeypatch):
    import os
    import ubelt as ub
    from git_well.git_archive_source import archive_source

    linked_paths = []

    def fake_rich_print_path(prefix, path, suffix=''):
        linked_paths.append((prefix, os.fspath(path), suffix))

    monkeypatch.setattr(
        'git_well._utils.rich_print_path', fake_rich_print_path
    )

    repo = tmp_path / 'demo_prints'
    output_dpath = tmp_path / 'archives'
    _init_demo_repo(repo)
    (repo / 'tracked.txt').write_text('tracked\n')
    ub.cmd(['git', 'add', 'tracked.txt'], cwd=repo, check=True)
    ub.cmd(['git', 'commit', '-m', 'initial'], cwd=repo, check=True)

    archive_source(
        repo_dpath=repo,
        output=output_dpath / 'demo-source.tar.gz',
        depth=0,
        verbose=1,
    )
    assert (
        '[source-archive] output directory: ',
        os.fspath(output_dpath.resolve()),
        '',
    ) in linked_paths
