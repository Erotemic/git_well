from typing import Any


def _stdout_text(info: Any) -> str:
    from git_well._utils import cmd_output_text

    return cmd_output_text(info.stdout)


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

    sub_clis = getattr(modal, 'sub_clis', None)
    if sub_clis is None:
        sub_commands = [d['command'] for d in modal._subconfig_metadata]
    else:
        sub_commands = [c.__command__ for c in sub_clis]
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
            '--all-branches',
            '--redact-local-paths',
        ],
        strict=True,
    )
    assert str(config.submodule_depth) == '{"*": 0, special/submod: 100}'
    assert config.exclude_submodule == ['external/big-data']
    assert config.submodules is False
    assert config.all_branches is True
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

    depth_info = ub.cmd(
        ['git', 'config', '--local', '--get', 'git-well.archive-source.depth'],
        cwd=repo,
        check=True,
    )
    depth = _stdout_text(depth_info).strip()
    format_info = ub.cmd(
        ['git', 'config', '--local', '--get', 'git-well.archive-source.format'],
        cwd=repo,
        check=True,
    )
    format = _stdout_text(format_info).strip()
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
    assert 'initial' in _stdout_text(proc)
    status = ub.cmd(['git', 'status', '--short'], cwd=unpacked, check=True)
    assert _stdout_text(status).strip() == ''
    exclude_text = (unpacked / '.git' / 'info' / 'exclude').read_text()
    assert exclude_text.count('/GIT_WELL_ARCHIVE_INFO.txt') == 1
    info_text = _tar_manifest_text(archive)
    assert 'Superproject history: full' in info_text
    assert 'Content pruning: none' in info_text


def test_archive_source_programmatic_hooks(tmp_path):
    """Prepare hooks enrich the tree before validation and serialization."""
    import tarfile

    import ubelt as ub

    from git_well.git_archive_source import archive_source

    repo = tmp_path / 'demo_hooks'
    _init_demo_repo(repo)
    (repo / 'tracked.txt').write_text('tracked\n')
    ub.cmd(['git', 'add', 'tracked.txt'], cwd=repo, check=True)
    ub.cmd(['git', 'commit', '-m', 'initial'], cwd=repo, check=True)

    events = []
    observed = {}

    def prepare_first(context):
        events.append('prepare-first')
        observed['stage'] = context.stage_dpath
        assert context.depth == 0
        assert context.include_git_history is False
        (context.archive_root / 'generated.txt').write_text('generated\n')

    def prepare_second(context):
        events.append('prepare-second')
        assert (context.archive_root / 'generated.txt').exists()

    def validate(context):
        events.append('validate')
        assert context.manifest_path.exists()
        assert (context.archive_root / 'generated.txt').exists()

    archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'hooked-source.tar.gz',
        depth=0,
        prepare=[prepare_first, prepare_second],
        validate=validate,
        archive_root_name='custom-source-root',
        verbose=0,
    )

    assert events == ['prepare-first', 'prepare-second', 'validate']
    assert not observed['stage'].exists()
    with tarfile.open(archive, 'r:gz') as tar:
        names = set(tar.getnames())
    assert 'custom-source-root/generated.txt' in names
    assert 'custom-source-root/GIT_WELL_ARCHIVE_INFO.txt' in names


def test_archive_source_hook_generated_excludes(tmp_path):
    """Generated hook payloads can keep staged Git checkouts clean."""
    import tarfile

    import ubelt as ub

    from git_well.git_archive_source import archive_source

    repo = tmp_path / 'demo_hook_excludes'
    _init_demo_repo(repo)
    (repo / 'tracked.txt').write_text('tracked\n')
    ub.cmd(['git', 'add', 'tracked.txt'], cwd=repo, check=True)
    ub.cmd(['git', 'commit', '-m', 'initial'], cwd=repo, check=True)

    def prepare(context):
        generated = context.archive_root / '.agent' / 'index.json'
        generated.parent.mkdir()
        generated.write_text('{}\n')
        context.add_generated_excludes('.agent/')

    def validate(context):
        status = ub.cmd(
            ['git', 'status', '--short'],
            cwd=context.archive_root,
            check=True,
        )
        assert _stdout_text(status).strip() == ''

    archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'hook-excludes.tar.gz',
        prepare=prepare,
        validate=validate,
        verbose=0,
    )

    extract = tmp_path / 'hook-excludes-extract'
    with tarfile.open(archive, 'r:gz') as tar:
        try:
            tar.extractall(extract, filter='fully_trusted')
        except TypeError:
            tar.extractall(extract)
    unpacked = next(extract.iterdir())
    exclude_text = (unpacked / '.git' / 'info' / 'exclude').read_text()
    assert exclude_text.count('/GIT_WELL_ARCHIVE_INFO.txt') == 1
    assert exclude_text.count('/.agent/') == 1
    assert (unpacked / '.agent' / 'index.json').exists()


def test_archive_source_hook_failure_aborts_and_cleans(tmp_path):
    import pytest
    import ubelt as ub

    from git_well.git_archive_source import (
        ArchiveSourceHookError,
        archive_source,
    )

    repo = tmp_path / 'demo_hook_failure'
    _init_demo_repo(repo)
    (repo / 'tracked.txt').write_text('tracked\n')
    ub.cmd(['git', 'add', 'tracked.txt'], cwd=repo, check=True)
    ub.cmd(['git', 'commit', '-m', 'initial'], cwd=repo, check=True)

    output = tmp_path / 'should-not-exist.tar.gz'
    observed = {}

    def broken_prepare(context):
        observed['stage'] = context.stage_dpath
        raise ValueError('deliberate failure')

    with pytest.raises(ArchiveSourceHookError) as exc_info:
        archive_source(
            repo_dpath=repo,
            output=output,
            depth=0,
            prepare=broken_prepare,
            verbose=0,
        )

    error = exc_info.value
    assert error.phase == 'prepare'
    assert error.hook is broken_prepare
    assert error.hook_name.endswith('broken_prepare')
    assert isinstance(error.__cause__, ValueError)
    assert error.stage_dpath == observed['stage']
    assert error.stage_retained is False
    assert not output.exists()
    assert not observed['stage'].exists()


def test_archive_source_hook_failure_can_retain_stage(tmp_path):
    import shutil

    import pytest
    import ubelt as ub

    from git_well.git_archive_source import (
        ArchiveSourceHookError,
        archive_source,
    )

    repo = tmp_path / 'demo_hook_retained_failure'
    _init_demo_repo(repo)
    (repo / 'tracked.txt').write_text('tracked\n')
    ub.cmd(['git', 'add', 'tracked.txt'], cwd=repo, check=True)
    ub.cmd(['git', 'commit', '-m', 'initial'], cwd=repo, check=True)

    def broken_validate(context):
        raise ValueError('inspect retained stage')

    try:
        with pytest.raises(ArchiveSourceHookError) as exc_info:
            archive_source(
                repo_dpath=repo,
                output=tmp_path / 'retained-failure.tar.gz',
                depth=0,
                validate=broken_validate,
                keep_stage=True,
                verbose=0,
            )
        error = exc_info.value
        assert error.phase == 'validate'
        assert error.stage_retained is True
        assert error.stage_dpath.exists()
        assert error.archive_root.exists()
        assert error.archive_root.joinpath(
            'GIT_WELL_ARCHIVE_INFO.txt'
        ).exists()
    finally:
        if 'error' in locals():
            shutil.rmtree(error.stage_dpath.parent, ignore_errors=True)


def test_stage_source_archive_direct_api(tmp_path):
    import tarfile

    import ubelt as ub

    from git_well.git_archive_source import stage_source_archive

    repo = tmp_path / 'demo_direct_stage'
    _init_demo_repo(repo)
    (repo / 'tracked.txt').write_text('tracked\n')
    ub.cmd(['git', 'add', 'tracked.txt'], cwd=repo, check=True)
    ub.cmd(['git', 'commit', '-m', 'initial'], cwd=repo, check=True)

    output = tmp_path / 'direct-stage.tar.gz'
    with stage_source_archive(
        repo_dpath=repo,
        output=output,
        depth=0,
        archive_root_name='direct-root',
        verbose=0,
    ) as context:
        stage = context.stage_dpath
        assert context.archive_root == stage / 'direct-root'
        (context.archive_root / 'direct.txt').write_text('direct\n')
        assert context.write_archive() == output

    assert not stage.exists()
    with tarfile.open(output, 'r:gz') as tar:
        names = set(tar.getnames())
    assert 'direct-root/direct.txt' in names


def test_archive_source_all_branches_preserves_cached_refs(tmp_path):
    """Archive locally fetched contributor refs without contacting remotes."""
    import ubelt as ub

    from git_well.git_archive_source import archive_source

    repo = tmp_path / 'demo_all_branches'
    _init_demo_repo(repo)
    (repo / 'tracked.txt').write_text('base\n')
    ub.cmd(['git', 'add', 'tracked.txt'], cwd=repo, check=True)
    ub.cmd(['git', 'commit', '-m', 'base'], cwd=repo, check=True)
    default_branch = _stdout_text(
        ub.cmd(
            ['git', 'symbolic-ref', '--short', 'HEAD'],
            cwd=repo,
            check=True,
        )
    ).strip()
    head_sha = _stdout_text(
        ub.cmd(['git', 'rev-parse', 'HEAD'], cwd=repo, check=True)
    ).strip()

    ub.cmd(['git', 'checkout', '-b', 'local-review'], cwd=repo, check=True)
    (repo / 'local-review.txt').write_text('local branch\n')
    ub.cmd(['git', 'add', 'local-review.txt'], cwd=repo, check=True)
    ub.cmd(['git', 'commit', '-m', 'local review'], cwd=repo, check=True)
    ub.cmd(['git', 'checkout', default_branch], cwd=repo, check=True)

    contributor = tmp_path / 'contributor.git'
    ub.cmd(['git', 'init', '--bare', str(contributor)], check=True)
    ub.cmd(
        ['git', 'remote', 'add', 'contributor', str(contributor)],
        cwd=repo,
        check=True,
    )
    ub.cmd(['git', 'checkout', '-b', 'temporary-pr'], cwd=repo, check=True)
    (repo / 'contributor.txt').write_text('pull request branch\n')
    ub.cmd(['git', 'add', 'contributor.txt'], cwd=repo, check=True)
    ub.cmd(['git', 'commit', '-m', 'contributor change'], cwd=repo, check=True)
    ub.cmd(
        ['git', 'push', 'contributor', 'HEAD:refs/heads/pr/topic'],
        cwd=repo,
        check=True,
    )
    ub.cmd(['git', 'checkout', default_branch], cwd=repo, check=True)
    ub.cmd(['git', 'branch', '-D', 'temporary-pr'], cwd=repo, check=True)
    ub.cmd(['git', 'fetch', 'contributor'], cwd=repo, check=True)
    ub.cmd(
        [
            'git',
            'update-ref',
            'refs/remotes/origin/review-copy',
            'refs/remotes/contributor/pr/topic',
        ],
        cwd=repo,
        check=True,
    )

    # A broken configured URL proves archive_source only copies locally cached
    # refs and does not contact the contributor remote.
    ub.cmd(
        ['git', 'remote', 'set-url', 'contributor', str(tmp_path / 'missing')],
        cwd=repo,
        check=True,
    )

    archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'all-branches.tar.gz',
        all_branches=True,
        verbose=0,
    )
    unpacked = _extract_tar_root(archive, tmp_path / 'all-branches-extract')

    refs_info = ub.cmd(
        [
            'git',
            'for-each-ref',
            '--format=%(refname)',
            'refs/heads',
            'refs/remotes',
        ],
        cwd=unpacked,
        check=True,
    )
    refs = set(_stdout_text(refs_info).splitlines())
    assert f'refs/heads/{default_branch}' in refs
    assert 'refs/heads/local-review' in refs
    assert 'refs/heads/temporary-pr' not in refs
    assert 'refs/remotes/contributor/pr/topic' in refs
    assert 'refs/remotes/origin/review-copy' in refs

    archived_head = _stdout_text(
        ub.cmd(['git', 'rev-parse', 'HEAD'], cwd=unpacked, check=True)
    ).strip()
    assert archived_head == head_sha
    symbolic_head = ub.cmd(
        ['git', 'symbolic-ref', '-q', 'HEAD'], cwd=unpacked, check=False
    )
    assert symbolic_head.returncode != 0
    contributor_text = _stdout_text(
        ub.cmd(
            ['git', 'show', 'contributor/pr/topic:contributor.txt'],
            cwd=unpacked,
            check=True,
        )
    )
    assert contributor_text == 'pull request branch\n'

    info_text = _tar_manifest_text(archive)
    assert 'Superproject branches: all locally cached' in info_text
    assert 'Remote network access during archive: none' in info_text
    assert '- local-review' in info_text
    assert '- contributor/pr/topic' in info_text

    redacted_archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'all-branches-redacted.tar.gz',
        all_branches=True,
        redact_local_paths=True,
        verbose=0,
    )
    redacted = _extract_tar_root(
        redacted_archive, tmp_path / 'all-branches-redacted-extract'
    )
    redacted_refs_info = ub.cmd(
        [
            'git',
            'for-each-ref',
            '--format=%(refname)',
            'refs/remotes',
        ],
        cwd=redacted,
        check=True,
    )
    redacted_refs = set(_stdout_text(redacted_refs_info).splitlines())
    assert 'refs/remotes/contributor/pr/topic' in redacted_refs
    assert 'refs/remotes/origin/review-copy' in redacted_refs
    remotes = _stdout_text(
        ub.cmd(['git', 'remote'], cwd=redacted, check=True)
    ).strip()
    assert remotes == ''
    assert str(repo.resolve()) not in (redacted / '.git' / 'config').read_text()
    assert not (redacted / '.git' / 'FETCH_HEAD').exists()


def test_archive_source_all_branches_shallow_depth(tmp_path):
    """Apply --depth independently from every included branch tip."""
    import ubelt as ub

    from git_well.git_archive_source import archive_source

    repo = tmp_path / 'demo_all_branches_shallow'
    _init_demo_repo(repo)
    (repo / 'tracked.txt').write_text('base\n')
    ub.cmd(['git', 'add', 'tracked.txt'], cwd=repo, check=True)
    ub.cmd(['git', 'commit', '-m', 'base'], cwd=repo, check=True)
    default_branch = _stdout_text(
        ub.cmd(
            ['git', 'symbolic-ref', '--short', 'HEAD'],
            cwd=repo,
            check=True,
        )
    ).strip()
    ub.cmd(['git', 'checkout', '-b', 'topic'], cwd=repo, check=True)
    for index in range(3):
        (repo / 'topic.txt').write_text(f'{index}\n')
        ub.cmd(['git', 'add', 'topic.txt'], cwd=repo, check=True)
        ub.cmd(['git', 'commit', '-m', f'topic {index}'], cwd=repo, check=True)
    ub.cmd(['git', 'checkout', default_branch], cwd=repo, check=True)

    archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'all-branches-shallow.tar.gz',
        depth=1,
        all_branches=True,
        verbose=0,
    )
    unpacked = _extract_tar_root(
        archive, tmp_path / 'all-branches-shallow-extract'
    )
    count = _stdout_text(
        ub.cmd(
            ['git', 'rev-list', '--count', 'topic'],
            cwd=unpacked,
            check=True,
        )
    ).strip()
    assert count == '1'


def test_archive_source_all_branches_rejects_source_only(tmp_path):
    import pytest
    import ubelt as ub

    from git_well.git_archive_source import archive_source

    repo = tmp_path / 'demo_all_branches_source_only'
    _init_demo_repo(repo)
    (repo / 'tracked.txt').write_text('base\n')
    ub.cmd(['git', 'add', 'tracked.txt'], cwd=repo, check=True)
    ub.cmd(['git', 'commit', '-m', 'base'], cwd=repo, check=True)

    with pytest.raises(ValueError, match='--all-branches requires Git history'):
        archive_source(
            repo_dpath=repo,
            output=tmp_path / 'invalid.tar.gz',
            depth=0,
            all_branches=True,
            verbose=0,
        )


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
    origin_info = ub.cmd(
        ['git', 'remote', 'get-url', 'origin'],
        cwd=default_root,
        check=True,
    )
    origin = _stdout_text(origin_info).strip()
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
    remote_info = ub.cmd(['git', 'remote'], cwd=redacted_root, check=True)
    remotes = _stdout_text(remote_info).strip()
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
        sub_checkout = super_repo / path
        ub.cmd(
            ['git', 'config', 'user.email', 'test@example.com'],
            cwd=sub_checkout,
            check=True,
        )
        ub.cmd(
            ['git', 'config', 'user.name', 'Test User'],
            cwd=sub_checkout,
            check=True,
        )
    _commit_all(super_repo, 'add submodules')
    return super_repo


def _tar_names(archive):
    import tarfile

    with tarfile.open(archive, 'r:gz') as tar:
        return set(tar.getnames())


def _tar_manifest_bytes(archive):
    import tarfile

    with tarfile.open(archive, 'r:gz') as tar:
        manifest_member = next(
            name for name in tar.getnames() if name.endswith('/GIT_WELL_ARCHIVE_INFO.txt')
        )
        file = tar.extractfile(manifest_member)
        assert file is not None
        return file.read()


def _tar_manifest_text(archive):
    return _tar_manifest_bytes(archive).decode('utf8')


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


def test_archive_source_recovers_unadvertised_local_submodule_commit(tmp_path):
    import ubelt as ub

    from git_well.git_archive_source import archive_source

    sub_repo = _make_submodule_repo(tmp_path, 'local_recovery_src')
    super_repo = _make_repo_with_submodules(
        tmp_path, {'external/lib': sub_repo}
    )
    sub_checkout = super_repo / 'external/lib'
    committed_sha = _stdout_text(
        ub.cmd(['git', 'rev-parse', 'HEAD'], cwd=sub_checkout, check=True)
    ).strip()

    ub.cmd(
        ['git', 'checkout', '--orphan', 'replacement'],
        cwd=sub_checkout,
        check=True,
    )
    ub.cmd(['git', 'rm', '-rf', '.'], cwd=sub_checkout, check=True)
    (sub_checkout / 'replacement.txt').write_text('replacement\n')
    _commit_all(sub_checkout, 'replacement history')
    replacement_sha = _stdout_text(
        ub.cmd(['git', 'rev-parse', 'HEAD'], cwd=sub_checkout, check=True)
    ).strip()

    for ref in ['refs/heads/master', 'refs/remotes/origin/master']:
        ub.cmd(
            ['git', 'update-ref', '-d', ref],
            cwd=sub_checkout,
            check=False,
        )
    ub.cmd(['git', 'remote', 'remove', 'origin'], cwd=sub_checkout, check=True)

    assert committed_sha != replacement_sha
    ub.cmd(
        ['git', 'cat-file', '-e', f'{committed_sha}^{{commit}}'],
        cwd=sub_checkout,
        check=True,
    )

    archive = archive_source(
        repo_dpath=super_repo,
        output=tmp_path / 'local-recovery.tar.gz',
        depth=1,
        submodule_depth=1,
        verbose=0,
    )

    extracted_root = _extract_tar_root(archive, tmp_path / 'local-extracted')
    archived_submodule = extracted_root / 'external/lib'
    archived_sha = _stdout_text(
        ub.cmd(
            ['git', 'rev-parse', 'HEAD'],
            cwd=archived_submodule,
            check=True,
        )
    ).strip()
    assert archived_sha == committed_sha
    assert (archived_submodule / 'tracked.txt').read_text() == 'submodule\n'


def test_archive_source_recovers_submodule_commit_from_source_remote(tmp_path):
    import ubelt as ub

    from git_well.git_archive_source import archive_source

    remote_work = _make_submodule_repo(tmp_path, 'remote_recovery_work')
    committed_sha = _stdout_text(
        ub.cmd(['git', 'rev-parse', 'HEAD'], cwd=remote_work, check=True)
    ).strip()
    ub.cmd(['git', 'branch', 'archive-required', committed_sha], cwd=remote_work, check=True)
    (remote_work / 'tracked.txt').write_text('new tip\n')
    _commit_all(remote_work, 'advance default branch')

    remote_bare = tmp_path / 'remote_recovery.git'
    ub.cmd(['git', 'clone', '--bare', str(remote_work), str(remote_bare)], check=True)

    super_repo = tmp_path / 'remote_super'
    _init_demo_repo(super_repo)
    (super_repo / 'root.txt').write_text('root\n')
    _commit_all(super_repo, 'initial superproject content')
    (super_repo / '.gitmodules').write_text(
        '[submodule "external/lib"]\n'
        '\tpath = external/lib\n'
        f'\turl = {remote_bare.as_uri()}\n'
    )
    (super_repo / 'external').mkdir()
    ub.cmd(
        [
            'git',
            'clone',
            '--depth',
            '1',
            remote_bare.as_uri(),
            'lib',
        ],
        cwd=super_repo / 'external',
        check=True,
    )
    ub.cmd(['git', 'add', '.gitmodules'], cwd=super_repo, check=True)
    ub.cmd(
        [
            'git',
            'update-index',
            '--add',
            '--cacheinfo',
            f'160000,{committed_sha},external/lib',
        ],
        cwd=super_repo,
        check=True,
    )
    ub.cmd(
        ['git', 'commit', '-m', 'record older submodule commit'],
        cwd=super_repo,
        check=True,
    )

    local_submodule = super_repo / 'external/lib'
    missing = ub.cmd(
        ['git', 'cat-file', '-e', f'{committed_sha}^{{commit}}'],
        cwd=local_submodule,
        check=False,
    )
    assert missing.returncode != 0

    archive = archive_source(
        repo_dpath=super_repo,
        output=tmp_path / 'remote-recovery.tar.gz',
        depth=1,
        submodule_depth=1,
        verbose=0,
    )

    extracted_root = _extract_tar_root(archive, tmp_path / 'remote-extracted')
    archived_submodule = extracted_root / 'external/lib'
    archived_sha = _stdout_text(
        ub.cmd(
            ['git', 'rev-parse', 'HEAD'],
            cwd=archived_submodule,
            check=True,
        )
    ).strip()
    assert archived_sha == committed_sha
    assert (archived_submodule / 'tracked.txt').read_text() == 'submodule\n'


def test_archive_source_warns_and_omits_uninitialized_submodule(
    tmp_path, capsys
):
    import ubelt as ub

    from git_well.git_archive_source import archive_source

    sub_repo = _make_submodule_repo(tmp_path, 'uninitialized_src')
    super_repo = _make_repo_with_submodules(
        tmp_path, {'external/uninitialized': sub_repo}
    )
    ub.cmd(
        ['git', 'submodule', 'deinit', '-f', 'external/uninitialized'],
        cwd=super_repo,
        check=True,
    )

    archive = archive_source(
        repo_dpath=super_repo,
        output=tmp_path / 'uninitialized-submodule.tar.gz',
        depth=1,
        verbose=0,
    )

    captured = capsys.readouterr()
    assert '[source-archive] WARNING: omitting submodule' in captured.err
    assert 'external/uninitialized: not initialized locally' in captured.err
    assert 'git submodule update --init --recursive' in captured.err

    names = _tar_names(archive)
    assert not any(
        name.endswith('/external/uninitialized/tracked.txt')
        for name in names
    )

    manifest_text = _tar_manifest_text(archive)
    assert 'Content pruning: yes' in manifest_text
    assert 'path: external/uninitialized' in manifest_text
    assert 'status: omitted' in manifest_text
    assert 'history: omitted' in manifest_text
    assert 'reason: not initialized locally' in manifest_text


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
    manifest_bytes = _tar_manifest_bytes(archive)
    assert b'\r\n' not in manifest_bytes
    assert manifest_bytes.endswith(b'\n')
    manifest_text = manifest_bytes.decode('utf8')
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


def test_standard_short_aliases_and_verbose_counters(capsys):
    from git_well import __version__
    from git_well.git_archive_source import ArchiveSourceCLI
    from git_well.git_branch_cleanup import CleanDevBranchConfig
    from git_well.git_squash import GitSquashCLI
    from git_well.git_squash_streaks import SquashStreakCLI
    from git_well.git_sync import GitSyncCLI
    from git_well.git_track_upstream import TrackUpstreamCLI
    from git_well.git_url_components import GitUrlComponentsCLI
    from git_well.main import GitWellModalCLI
    from git_well.patchdir.git_patchdir_apply import GitApplyPatchCLI

    assert GitSyncCLI.cli(argv=['example.com', '-f']).force is True
    assert GitSyncCLI.cli(argv=['example.com', '-n']).dry is True
    assert TrackUpstreamCLI.cli(argv=['-f']).force is True
    assert CleanDevBranchConfig.cli(argv=['-y']).yes is True
    assert GitApplyPatchCLI.cli(argv=['-n']).dry is True

    assert GitUrlComponentsCLI.cli(argv=['-vv']).verbose == 2
    assert ArchiveSourceCLI.cli(argv=['-vv']).verbose == 3
    assert GitSquashCLI.cli(argv=['-vv']).verbose == 3
    assert SquashStreakCLI.cli(argv=['-vv']).verbose == 3

    modal = GitWellModalCLI(version=__version__)
    assert modal.main(argv=['-V']) == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == __version__
