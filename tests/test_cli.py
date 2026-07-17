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
    assert any(name.endswith('/GIT_WELL_ARCHIVE_INFO.txt') for name in names)
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
    assert any(name.endswith('/GIT_WELL_ARCHIVE_INFO.txt') for name in names)
    assert not any('/.git/' in name for name in names)


def test_archive_source_auto_unknown_extension_falls_back_to_tar_gz(tmp_path):
    import tarfile
    import ubelt as ub
    from git_well.git_archive_source import archive_source

    repo = tmp_path / 'demo_auto_fallback'
    _init_demo_repo(repo)
    (repo / 'tracked.txt').write_text('tracked\n')
    ub.cmd(['git', 'add', 'tracked.txt'], cwd=repo, check=True)
    ub.cmd(['git', 'commit', '-m', 'initial'], cwd=repo, check=True)

    archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'demo-source.custom',
        depth=0,
        format='auto',
        verbose=0,
    )

    with tarfile.open(archive, 'r:gz') as tar:
        names = set(tar.getnames())
    assert any(name.endswith('/tracked.txt') for name in names)


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
            '--redact-local-paths',
        ],
        strict=True,
    )
    assert str(config.submodule_depth) == '{"*": 0, special/submod: 100}'
    assert config.exclude_submodule == ['external/big-data']
    assert config.submodules is False
    assert config.redact_local_paths is True


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
    status = ub.cmd(['git', 'status', '--short'], cwd=unpacked, check=True)
    assert status.stdout.strip() == ''
    exclude_text = (unpacked / '.git' / 'info' / 'exclude').read_text()
    assert exclude_text.count('/GIT_WELL_ARCHIVE_INFO.txt') == 1
    info_text = _tar_manifest_text(archive)
    assert 'Superproject history: full' in info_text
    assert 'Content pruning: none' in info_text


def test_archive_source_info_paths_status_and_redaction(tmp_path):
    import ubelt as ub
    from git_well.git_archive_source import archive_source

    repo = tmp_path / 'demo_info'
    _init_demo_repo(repo)
    (repo / 'tracked.txt').write_text('tracked\n')
    (repo / 'private-untracked-name.txt').write_text('private\n')
    ub.cmd(['git', 'add', 'tracked.txt'], cwd=repo, check=True)
    ub.cmd(['git', 'commit', '-m', 'initial'], cwd=repo, check=True)

    default_output = tmp_path / 'default-info.tar.gz'
    default_archive = archive_source(
        repo_dpath=repo,
        output=default_output,
        depth=1,
        verbose=0,
    )
    default_info = _tar_manifest_text(default_archive)
    assert f'Source repository path: {repo.resolve()}' in default_info
    assert f'Archive output path: {default_output.resolve()}' in default_info
    assert 'private-untracked-name.txt' not in default_info
    assert 'Archive policy:' not in default_info

    default_root = _extract_tar_root(
        default_archive, tmp_path / 'default-extract'
    )
    origin = ub.cmd(
        ['git', 'remote', 'get-url', 'origin'],
        cwd=default_root,
        check=True,
    ).stdout.strip()
    assert origin == str(repo.resolve())

    redacted_output = tmp_path / 'redacted-info.tar.gz'
    redacted_archive = archive_source(
        repo_dpath=repo,
        output=redacted_output,
        depth=1,
        redact_local_paths=True,
        verbose=0,
    )
    redacted_info = _tar_manifest_text(redacted_archive)
    assert str(repo.resolve()) not in redacted_info
    assert str(redacted_output.resolve()) not in redacted_info
    assert redacted_info.count('(redacted by --redact-local-paths)') == 2

    redacted_root = _extract_tar_root(
        redacted_archive, tmp_path / 'redacted-extract'
    )
    remotes = ub.cmd(
        ['git', 'remote'], cwd=redacted_root, check=True
    ).stdout.strip()
    assert remotes == ''


def test_archive_source_info_path_collision_is_safe(tmp_path):
    import os
    import pytest
    import ubelt as ub
    from git_well.git_archive_source import archive_source

    repo = tmp_path / 'demo_collision'
    target = tmp_path / 'outside-target.txt'
    target.write_text('sentinel\n')
    _init_demo_repo(repo)
    os.symlink(target, repo / 'GIT_WELL_ARCHIVE_INFO.txt')
    ub.cmd(['git', 'add', 'GIT_WELL_ARCHIVE_INFO.txt'], cwd=repo, check=True)
    ub.cmd(['git', 'commit', '-m', 'add collision'], cwd=repo, check=True)

    with pytest.raises(FileExistsError, match='committed repository'):
        archive_source(
            repo_dpath=repo,
            output=tmp_path / 'collision.tar.gz',
            depth=0,
            verbose=0,
        )
    assert target.read_text() == 'sentinel\n'


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
            name for name in tar.getnames() if name.endswith('/GIT_WELL_ARCHIVE_INFO.txt')
        )
        file = tar.extractfile(manifest_member)
        assert file is not None
        return file.read().decode('utf8')


def _extract_tar_root(archive, dst):
    import tarfile

    with tarfile.open(archive, 'r:gz') as tar:
        try:
            tar.extractall(dst, filter='fully_trusted')
        except TypeError:
            tar.extractall(dst)
    roots = [path for path in dst.iterdir() if path.is_dir()]
    assert len(roots) == 1
    return roots[0]


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
    assert 'history: source-only (depth 0)' in manifest_text
    assert 'Content pruning: yes' in manifest_text


def test_archive_source_uses_committed_submodules_not_staged_index(tmp_path):
    import ubelt as ub
    from git_well.git_archive_source import archive_source

    sub_repo = _make_submodule_repo(tmp_path, 'staged_only_src')
    super_repo = tmp_path / 'staged_only_super'
    _init_demo_repo(super_repo)
    (super_repo / 'root.txt').write_text('root\n')
    _commit_all(super_repo, 'initial superproject content')
    ub.cmd(
        [
            'git',
            '-c',
            'protocol.file.allow=always',
            'submodule',
            'add',
            str(sub_repo),
            'external/staged-only',
        ],
        cwd=super_repo,
        check=True,
    )

    archive = archive_source(
        repo_dpath=super_repo,
        output=tmp_path / 'staged-only.tar.gz',
        depth=0,
        verbose=0,
    )

    names = _tar_names(archive)
    assert not any('/external/staged-only/' in name for name in names)
    assert not any(name.endswith('/.gitmodules') for name in names)
    manifest_text = _tar_manifest_text(archive)
    assert 'Submodules:\n(none)' in manifest_text


def test_archive_source_submodule_path_with_spaces(tmp_path):
    from git_well.git_archive_source import archive_source

    sub_repo = _make_submodule_repo(tmp_path, 'space_src')
    super_repo = _make_repo_with_submodules(
        tmp_path, {'external/lib space': sub_repo}
    )

    archive = archive_source(
        repo_dpath=super_repo,
        output=tmp_path / 'space-submodule.tar.gz',
        depth=0,
        submodule_depth=0,
        verbose=0,
    )

    names = _tar_names(archive)
    assert any(
        name.endswith('/external/lib space/tracked.txt') for name in names
    )
    manifest_text = _tar_manifest_text(archive)
    assert 'path: external/lib space' in manifest_text


def test_archive_source_missing_gitmodules_mapping_fails(tmp_path):
    import pytest
    import ubelt as ub
    from git_well.git_archive_source import archive_source

    sub_repo = _make_submodule_repo(tmp_path, 'broken_mapping_src')
    super_repo = _make_repo_with_submodules(
        tmp_path, {'broken-sub': sub_repo}
    )
    ub.cmd(['git', 'rm', '.gitmodules'], cwd=super_repo, check=True)
    ub.cmd(
        ['git', 'commit', '-m', 'remove submodule mapping'],
        cwd=super_repo,
        check=True,
    )

    with pytest.raises(RuntimeError, match='no .gitmodules path mapping'):
        archive_source(
            repo_dpath=super_repo,
            output=tmp_path / 'broken-mapping.tar.gz',
            depth=0,
            verbose=0,
        )


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
    assert 'history: source-only (depth 0)' in manifest_text
    assert 'path: special/submod' in manifest_text
    assert 'history: shallow (depth 1)' in manifest_text


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


def test_archive_source_excluding_parent_omits_nested_submodules(tmp_path):
    from git_well.git_archive_source import archive_source

    inner_src = _make_submodule_repo(
        tmp_path,
        'inner_src',
        filename='inner.txt',
        content='inner\n',
    )
    parent_src = _make_repo_with_submodules(
        tmp_path, {'nested/inner': inner_src}
    )
    parent_src = parent_src.rename(tmp_path / 'parent_src')
    super_repo = _make_repo_with_submodules(
        tmp_path, {'external/parent': parent_src}
    )

    archive = archive_source(
        repo_dpath=super_repo,
        output=tmp_path / 'exclude-parent.tar.gz',
        depth=0,
        exclude_submodule=['external/parent'],
        verbose=0,
    )

    names = _tar_names(archive)
    assert not any('/external/parent/root.txt' in name for name in names)
    assert not any('/external/parent/nested/inner/' in name for name in names)
    manifest_text = _tar_manifest_text(archive)
    assert 'path: external/parent\n' in manifest_text
    assert 'path: external/parent/nested/inner\n' in manifest_text
    assert manifest_text.count('reason: excluded by --exclude-submodule') == 2


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
        SubmoduleStatus(
            ' ', 'd' * 40, 'external/keep/nested/inner', ''
        ),
    ]

    assert _resolve_exclude_submodule_paths(
        infos, ['external/*'], no_submodules=False
    ) == {
        'external/keep',
        'external/keep/nested/inner',
        'external/big-data',
    }
    assert _resolve_exclude_submodule_paths(
        infos, ['*/big-data'], no_submodules=False
    ) == {'external/big-data', 'vendor/big-data'}
    assert _resolve_exclude_submodule_paths(
        infos, ['external/keep'], no_submodules=False
    ) == {'external/keep', 'external/keep/nested/inner'}

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
    assert 'Content pruning: yes' in manifest_text
    assert 'path: external/lib' in manifest_text
    assert 'reason: omitted by --no-submodules' in manifest_text
