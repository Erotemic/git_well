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

    config = ArchiveSourceCLI.cli(
        argv=[
            '--depth',
            '100',
            '--submodule-depth',
            '{"*": 0, special/submod: 100}',
            '--exclude-submodule',
            'external/big-data',
            '--no-submodules',
        ],
        strict=True,
    )
    assert str(config.submodule_depth) == '{"*": 0, special/submod: 100}'
    assert config.exclude_submodule == ['external/big-data']
    assert config.submodules is False


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


def _commit_all(repo, message):
    import ubelt as ub

    ub.cmd(['git', 'add', '.'], cwd=repo, check=True)
    ub.cmd(['git', 'commit', '-m', message], cwd=repo, check=True)


def _make_submodule_repo(tmp_path, name, filename='tracked.txt', content='submodule\n'):
    """Create a standalone Git repository suitable for submodule tests."""
    repo = tmp_path / name
    _init_demo_repo(repo)
    (repo / filename).parent.mkdir(parents=True, exist_ok=True)
    (repo / filename).write_text(content)
    _commit_all(repo, 'initial submodule content')
    return repo


def _make_repo_with_submodules(tmp_path, submodules):
    """
    Create a superproject with local submodules.

    Args:
        tmp_path: pytest tmp path.
        submodules: mapping from submodule path to source repo path.
    """
    import ubelt as ub

    super_repo = tmp_path / 'super'
    _init_demo_repo(super_repo)
    (super_repo / 'root.txt').write_text('root\n')
    _commit_all(super_repo, 'initial superproject content')
    for path, src in submodules.items():
        ub.cmd(
            [
                'git',
                '-c',
                'protocol.file.allow=always',
                'submodule',
                'add',
                str(src),
                path,
            ],
            cwd=super_repo,
            check=True,
        )
    _commit_all(super_repo, 'add submodules')
    return super_repo


def _tar_names(archive):
    import tarfile

    with tarfile.open(archive, 'r:gz') as tar:
        return set(tar.getnames())


def _tar_manifest_text(archive):
    import tarfile

    with tarfile.open(archive, 'r:gz') as tar:
        manifest_member = next(
            name for name in tar.getnames() if name.endswith('/SOURCE_ARCHIVE_MANIFEST.txt')
        )
        file = tar.extractfile(manifest_member)
        assert file is not None
        return file.read().decode('utf8')


def test_archive_source_submodule_depth_spec_resolution():
    import pytest
    from git_well.git_archive_source import (
        _depth_label,
        _parse_submodule_depth_spec,
    )

    policy = _parse_submodule_depth_spec('{"*": 0, special/submod: 100}')
    assert _depth_label(policy.resolve('special/submod', 25)) == '100'
    assert _depth_label(policy.resolve('other/submod', 25)) == '0'

    policy = _parse_submodule_depth_spec('{__default__: 0, special/*: full}')
    assert _depth_label(policy.resolve('special/lib', 25)) == 'full'
    assert _depth_label(policy.resolve('plain/lib', 25)) == '0'

    policy = _parse_submodule_depth_spec('{special/submod: 100}')
    assert _depth_label(policy.resolve('other/submod', 25)) == '25'

    policy = _parse_submodule_depth_spec('{special/*: 25, "*/submod": 25}')
    assert _depth_label(policy.resolve('special/submod', 1)) == '25'

    policy = _parse_submodule_depth_spec('{special/*: 25, "*/submod": 100}')
    with pytest.raises(ValueError, match='ambiguous submodule depth'):
        policy.resolve('special/submod', 25)


def test_archive_source_submodule_depth_zero_source_only(tmp_path):
    from git_well.git_archive_source import archive_source

    sub_repo = _make_submodule_repo(tmp_path, 'lib_src')
    super_repo = _make_repo_with_submodules(
        tmp_path, {'external/lib': sub_repo}
    )

    archive = archive_source(
        repo_dpath=super_repo,
        output=tmp_path / 'submodule-depth-zero.tar.gz',
        depth=1,
        submodule_depth=0,
        verbose=0,
    )

    names = _tar_names(archive)
    assert any(name.endswith('/external/lib/tracked.txt') for name in names)
    assert any(name.endswith('/.git/HEAD') for name in names)
    assert not any('/external/lib/.git/' in name for name in names)

    manifest_text = _tar_manifest_text(archive)
    assert 'path: external/lib' in manifest_text
    assert 'mode: source-only-git-archive' in manifest_text
    assert 'depth: 0' in manifest_text


def test_archive_source_submodule_depth_glob_and_exact_override(tmp_path):
    from git_well.git_archive_source import archive_source

    ordinary_src = _make_submodule_repo(tmp_path, 'ordinary_src')
    special_src = _make_submodule_repo(tmp_path, 'special_src')
    super_repo = _make_repo_with_submodules(
        tmp_path,
        {
            'external/ordinary': ordinary_src,
            'special/submod': special_src,
        },
    )

    archive = archive_source(
        repo_dpath=super_repo,
        output=tmp_path / 'submodule-depth-glob.tar.gz',
        depth=1,
        submodule_depth='{"*": 0, special/submod: 1}',
        verbose=0,
    )

    names = _tar_names(archive)
    assert any(name.endswith('/external/ordinary/tracked.txt') for name in names)
    assert any(name.endswith('/special/submod/tracked.txt') for name in names)
    assert not any('/external/ordinary/.git/' in name for name in names)
    assert any('/special/submod/.git/' in name for name in names)

    manifest_text = _tar_manifest_text(archive)
    assert 'path: external/ordinary' in manifest_text
    assert 'mode: source-only-git-archive' in manifest_text
    assert 'path: special/submod' in manifest_text
    assert 'mode: shallow-git-checkout' in manifest_text


def test_archive_source_exclude_submodule(tmp_path):
    from git_well.git_archive_source import archive_source

    keep_src = _make_submodule_repo(tmp_path, 'keep_src')
    data_src = _make_submodule_repo(
        tmp_path, 'data_src', filename='payload.bin', content='heavy\n'
    )
    super_repo = _make_repo_with_submodules(
        tmp_path,
        {
            'external/keep': keep_src,
            'external/big-data': data_src,
        },
    )

    archive = archive_source(
        repo_dpath=super_repo,
        output=tmp_path / 'exclude-submodule.tar.gz',
        depth=0,
        submodule_depth=0,
        exclude_submodule=['external/*data'],
        verbose=0,
    )

    names = _tar_names(archive)
    assert any(name.endswith('/external/keep/tracked.txt') for name in names)
    assert not any(name.endswith('/external/big-data/payload.bin') for name in names)

    manifest_text = _tar_manifest_text(archive)
    assert 'path: external/big-data' in manifest_text
    assert 'status: omitted' in manifest_text
    assert 'reason: excluded by --exclude-submodule' in manifest_text


def test_archive_source_exclude_submodule_glob_resolution():
    import pytest
    from git_well.git_archive_source import (
        SubmoduleStatus,
        _resolve_exclude_submodule_paths,
    )

    infos = [
        SubmoduleStatus(' ', 'a' * 40, 'external/keep', ''),
        SubmoduleStatus(' ', 'b' * 40, 'external/big-data', ''),
        SubmoduleStatus(' ', 'c' * 40, 'vendor/big-data', ''),
    ]

    assert _resolve_exclude_submodule_paths(
        infos, ['external/*'], no_submodules=False
    ) == {'external/keep', 'external/big-data'}
    assert _resolve_exclude_submodule_paths(
        infos, ['*/big-data'], no_submodules=False
    ) == {'external/big-data', 'vendor/big-data'}
    assert _resolve_exclude_submodule_paths(
        infos, ['external/keep'], no_submodules=False
    ) == {'external/keep'}

    with pytest.raises(ValueError, match='selector does not match'):
        _resolve_exclude_submodule_paths(
            infos, ['external/missing-*'], no_submodules=False
        )


def test_archive_source_no_submodules(tmp_path):
    from git_well.git_archive_source import archive_source

    sub_src = _make_submodule_repo(tmp_path, 'sub_src')
    super_repo = _make_repo_with_submodules(
        tmp_path, {'external/lib': sub_src}
    )

    archive = archive_source(
        repo_dpath=super_repo,
        output=tmp_path / 'no-submodules.tar.gz',
        depth=0,
        no_submodules=True,
        verbose=0,
    )

    names = _tar_names(archive)
    assert any(name.endswith('/.gitmodules') for name in names)
    assert not any(name.endswith('/external/lib/tracked.txt') for name in names)

    manifest_text = _tar_manifest_text(archive)
    assert 'No submodules: yes' in manifest_text
    assert 'path: external/lib' in manifest_text
    assert 'reason: omitted by --no-submodules' in manifest_text
