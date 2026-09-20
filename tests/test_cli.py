import os
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
        # Older kwconf versions do not expose the child classes. Keep the
        # modal-dispatch fallback for those versions.
        for item in modal._subconfig_metadata:
            try:
                modal.run(argv=[item['command'], '--help'])
            except SystemExit:
                ...
    else:
        # Calling the child directly avoids rebuilding the complete modal
        # parser once for every command while still exercising every CLI help.
        for cli in sub_clis:
            try:
                cli.main(argv=['--help'])
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


def _demo_git_dir(repo):
    marker = repo / '.git'
    if marker.is_dir():
        return marker
    text = marker.read_text().strip()
    prefix = 'gitdir:'
    assert text.lower().startswith(prefix)
    git_dir = marker.parent / text[len(prefix):].strip()
    return git_dir.resolve()


def _configure_demo_identity(repo):
    config = _demo_git_dir(repo) / 'config'
    with config.open('a') as file:
        file.write(
            '\n[user]\n'
            '\tname = Test User\n'
            '\temail = test@example.com\n'
        )


def _init_demo_repo(repo):
    import ubelt as ub

    repo.mkdir()
    ub.cmd(['git', 'init'], cwd=repo, check=True)
    _configure_demo_identity(repo)


def test_archive_source_depth_zero_source_only(tmp_path):
    """
    Build a source-only archive with --depth 0 and ensure untracked files and
    .git metadata are excluded.
    """
    import tarfile

    import ubelt as ub

    from git_well.archive_source import archive_source

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


def test_archive_source_default_name_preserves_symlinked_repo_basename(
    tmp_path, monkeypatch
):
    """Default archive names should reflect the logical shell path."""
    import tarfile

    import pytest
    import ubelt as ub

    from git_well.archive_source import archive_source

    repo = tmp_path / 'physical_repo_name'
    _init_demo_repo(repo)
    nested = repo / 'nested'
    nested.mkdir()
    (nested / 'tracked.txt').write_text('tracked\n')
    ub.cmd(['git', 'add', 'nested/tracked.txt'], cwd=repo, check=True)
    ub.cmd(['git', 'commit', '-m', 'initial'], cwd=repo, check=True)

    alias = tmp_path / 'logical_repo_name'
    try:
        alias.symlink_to(repo, target_is_directory=True)
    except OSError as ex:
        pytest.skip(f'symlinks are unavailable: {ex}')

    logical_cwd = alias / 'nested'
    monkeypatch.chdir(logical_cwd)
    monkeypatch.setenv('PWD', os.fspath(logical_cwd))

    archive = archive_source(depth=0, verbose=0)

    assert archive.parent == repo.resolve()
    assert archive.name.startswith('logical_repo_name-source-')
    with tarfile.open(archive, 'r:gz') as tar:
        root_names = {name.split('/', 1)[0] for name in tar.getnames()}
    assert len(root_names) == 1
    archive_root = root_names.pop()
    assert archive_root.startswith('logical_repo_name-source-')


def test_archive_source_auto_zip(tmp_path):
    """
    Build a source-only zip archive by inferring the format from the extension.
    """
    import zipfile

    import ubelt as ub

    from git_well.archive_source import archive_source

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

    from git_well.archive_source import archive_source

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

    config = ArchiveSourceCLI.cli(argv=['--patch', 'auto'], strict=True)
    assert config.patch == 'auto'

    config = ArchiveSourceCLI.cli(
        argv=[
            '--depth',
            '100',
            '--submodule-depth',
            '{"*": 0, special/submod: 100}',
            '--exclude-submodule',
            'external/big-data',
            '--exclude-path',
            'external/big-data/notebooks',
            'assets/*.bin',
            '--history-blobs',
            'sparse',
            '--no-submodules',
            '--all-branches',
            '--redact-local-paths',
        ],
        strict=True,
    )
    assert str(config.submodule_depth) == '{"*": 0, special/submod: 100}'
    assert config.exclude_submodule == ['external/big-data']
    assert config.exclude_path == [
        'external/big-data/notebooks',
        'assets/*.bin',
    ]
    assert config.history_blobs == 'sparse'
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

    from git_well.archive_source import archive_source

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

    from git_well.archive_source import archive_source

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


def test_source_patch_overlay_preserves_implicit_parent_mode(tmp_path):
    import stat

    from git_well.archive_source.patch_apply import _apply_overlay

    overlay_root = tmp_path / 'overlay'
    target_root = tmp_path / 'target'
    overlay_parent = overlay_root / 'nested'
    target_parent = target_root / 'nested'
    overlay_parent.mkdir(parents=True)
    target_parent.mkdir(parents=True)
    overlay_parent.chmod(0o755)
    target_parent.chmod(0o700)
    target_parent_mode = stat.S_IMODE(target_parent.stat().st_mode)
    (overlay_parent / 'updated.txt').write_text('updated\n')

    _apply_overlay(
        overlay_root,
        target_root,
        paths=['nested/updated.txt'],
    )

    assert (target_parent / 'updated.txt').read_text() == 'updated\n'
    # Windows only exposes a limited chmod model. The invariant is that an
    # implicit parent is not touched, not that Windows can represent 0o700.
    assert stat.S_IMODE(target_parent.stat().st_mode) == target_parent_mode
    if os.name != 'nt':
        assert target_parent_mode == 0o700


def test_archive_source_patch_explicit_base_roundtrip(tmp_path):
    import json
    import tarfile

    import ubelt as ub

    from git_well.archive_source import apply_source_patch
    from git_well.archive_source import archive_source

    repo = tmp_path / 'demo_patch_explicit'
    _init_demo_repo(repo)
    (repo / 'tracked.txt').write_text('base\n')
    ub.cmd(['git', 'add', 'tracked.txt'], cwd=repo, check=True)
    ub.cmd(['git', 'commit', '-m', 'base'], cwd=repo, check=True)

    base_archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'base-source.tar.gz',
        verbose=0,
    )
    (repo / 'tracked.txt').write_text('base\ntarget\n')
    (repo / 'new.txt').write_text('new\n')
    ub.cmd(['git', 'add', '.'], cwd=repo, check=True)
    ub.cmd(['git', 'commit', '-m', 'target'], cwd=repo, check=True)
    target_head = _stdout_text(
        ub.cmd(['git', 'rev-parse', 'HEAD'], cwd=repo, check=True)
    ).strip()

    patch_archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'incremental-source.tar.gz',
        patch=base_archive,
        verbose=0,
    )
    with tarfile.open(patch_archive, 'r:gz') as tar:
        manifest_member = next(
            member
            for member in tar.getmembers()
            if member.name.endswith('/GIT_WELL_SOURCE_PATCH.json')
        )
        manifest_file = tar.extractfile(manifest_member)
        assert manifest_file is not None
        manifest = json.load(manifest_file)
    assert manifest['base']['archive_name'] == base_archive.name
    assert manifest['target']['head_sha'] == target_head
    assert manifest['apply_script'] == 'APPLY_SOURCE_PATCH.py'

    # The patch must be self-applicable by an agent that has Python + Git but
    # no installed git-well. Run the exact embedded script under isolated mode
    # so the development checkout cannot satisfy accidental git_well imports.
    import subprocess
    import sys
    from pathlib import Path

    from git_well.archive_source import patch_apply

    patch_extract = tmp_path / 'standalone-patch-extract'
    patch_root = _extract_tar_root(patch_archive, patch_extract)
    apply_script = patch_root / 'APPLY_SOURCE_PATCH.py'
    assert apply_script.read_bytes() == Path(patch_apply.__file__).read_bytes()
    standalone_output = tmp_path / 'standalone-applied-patch'
    proc = subprocess.run(
        [
            sys.executable,
            '-I',
            str(apply_script),
            str(base_archive),
            str(standalone_output),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.returncode == 0, proc.stderr
    standalone_root = Path(proc.stdout.strip())
    assert standalone_root.is_dir()
    standalone_head = _stdout_text(
        ub.cmd(['git', 'rev-parse', 'HEAD'], cwd=standalone_root, check=True)
    ).strip()
    assert standalone_head == target_head
    assert (standalone_root / 'tracked.txt').read_text() == 'base\ntarget\n'
    assert (standalone_root / 'new.txt').read_text() == 'new\n'

    applied = apply_source_patch(
        base_archive, patch_archive, tmp_path / 'applied-patch'
    )
    applied_head = _stdout_text(
        ub.cmd(['git', 'rev-parse', 'HEAD'], cwd=applied, check=True)
    ).strip()
    assert applied_head == target_head
    assert (applied / 'tracked.txt').read_text() == 'base\ntarget\n'
    assert (applied / 'new.txt').read_text() == 'new\n'
    status = ub.cmd(['git', 'status', '--short'], cwd=applied, check=True)
    assert _stdout_text(status).strip() == ''


def test_archive_source_patch_auto_chooses_closest_full_base(tmp_path):
    import json
    import tarfile

    import ubelt as ub

    from git_well.archive_source import archive_source

    repo = tmp_path / 'demo_patch_auto'
    _init_demo_repo(repo)
    (repo / 'tracked.txt').write_text('one\n')
    ub.cmd(['git', 'add', 'tracked.txt'], cwd=repo, check=True)
    ub.cmd(['git', 'commit', '-m', 'one'], cwd=repo, check=True)
    archive_source(
        repo_dpath=repo,
        output=tmp_path / 'one-source.tar.gz',
        verbose=0,
    )

    (repo / 'tracked.txt').write_text('two\n')
    ub.cmd(['git', 'commit', '-am', 'two'], cwd=repo, check=True)
    second_head = _stdout_text(
        ub.cmd(['git', 'rev-parse', 'HEAD'], cwd=repo, check=True)
    ).strip()
    second_archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'two-source.tar.gz',
        verbose=0,
    )

    (repo / 'tracked.txt').write_text('three\n')
    ub.cmd(['git', 'commit', '-am', 'three'], cwd=repo, check=True)
    patch_archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'auto-patch.tar.gz',
        patch='auto',
        verbose=0,
    )

    with tarfile.open(patch_archive, 'r:gz') as tar:
        member = next(
            member
            for member in tar.getmembers()
            if member.name.endswith('/GIT_WELL_SOURCE_PATCH.json')
        )
        file = tar.extractfile(member)
        assert file is not None
        manifest = json.load(file)
    assert manifest['base']['head_sha'] == second_head
    assert manifest['base']['archive_name'] == second_archive.name


def test_archive_source_patch_rejects_source_only(tmp_path):
    import pytest
    import ubelt as ub

    from git_well.archive_source import SourcePatchError
    from git_well.archive_source import archive_source

    repo = tmp_path / 'demo_patch_source_only'
    _init_demo_repo(repo)
    (repo / 'tracked.txt').write_text('tracked\n')
    ub.cmd(['git', 'add', 'tracked.txt'], cwd=repo, check=True)
    ub.cmd(['git', 'commit', '-m', 'initial'], cwd=repo, check=True)

    with pytest.raises(SourcePatchError, match='requires Git history'):
        archive_source(
            repo_dpath=repo,
            output=tmp_path / 'invalid-patch.tar.gz',
            depth=0,
            patch='auto',
            verbose=0,
        )


def test_archive_source_patch_programmatic_hooks_roundtrip(tmp_path):
    import ubelt as ub

    from git_well.archive_source import apply_source_patch
    from git_well.archive_source import archive_source

    repo = tmp_path / 'demo_patch_hooks'
    _init_demo_repo(repo)
    (repo / 'tracked.txt').write_text('base\n')
    ub.cmd(['git', 'add', 'tracked.txt'], cwd=repo, check=True)
    ub.cmd(['git', 'commit', '-m', 'base'], cwd=repo, check=True)

    events = []

    def prepare(context):
        events.append(('prepare', context.head_sha))
        generated = context.archive_root / '.agent' / 'generation.txt'
        generated.parent.mkdir(exist_ok=True)
        generated.write_text(context.head_sha + '\n')
        context.add_generated_excludes('.agent/')

    def validate(context):
        events.append(('validate', context.head_sha))
        assert context.manifest_path.exists()
        generated = context.archive_root / '.agent' / 'generation.txt'
        assert generated.read_text() == context.head_sha + '\n'
        status = ub.cmd(
            ['git', 'status', '--short'], cwd=context.archive_root, check=True
        )
        assert _stdout_text(status).strip() == ''

    base_archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'hook-base.tar.gz',
        prepare=prepare,
        validate=validate,
        archive_root_name='ambition-like-source',
        verbose=0,
    )

    (repo / 'tracked.txt').write_text('target\n')
    ub.cmd(['git', 'commit', '-am', 'target'], cwd=repo, check=True)
    target_head = _stdout_text(
        ub.cmd(['git', 'rev-parse', 'HEAD'], cwd=repo, check=True)
    ).strip()
    patch_archive = archive_source(
        repo_dpath=repo,
        patch=base_archive,
        prepare=prepare,
        validate=validate,
        archive_root_name='ambition-like-source',
        verbose=0,
    )
    assert patch_archive.name == 'ambition-like-source-patch.tar.gz'

    assert [phase for phase, _head in events] == [
        'prepare',
        'validate',
        'prepare',
        'validate',
    ]
    applied = apply_source_patch(
        base_archive, patch_archive, tmp_path / 'hook-applied'
    )
    assert (applied / '.agent' / 'generation.txt').read_text() == target_head + '\n'
    assert (applied / 'tracked.txt').read_text() == 'target\n'
    status = ub.cmd(['git', 'status', '--short'], cwd=applied, check=True)
    assert _stdout_text(status).strip() == ''


def test_archive_source_hook_generated_excludes(tmp_path):
    """Generated hook payloads can keep staged Git checkouts clean."""
    import tarfile

    import ubelt as ub

    from git_well.archive_source import archive_source

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

    from git_well.archive_source import (
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

    from git_well.archive_source import (
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

    from git_well.archive_source import stage_source_archive

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

    from git_well.archive_source import archive_source

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

    from git_well.archive_source import archive_source

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
    topic_head = _stdout_text(
        ub.cmd(['git', 'rev-parse', 'topic'], cwd=repo, check=True)
    ).strip()
    ub.cmd(['git', 'checkout', default_branch], cwd=repo, check=True)

    archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'all-branches-shallow.tar.gz',
        depth=1,
        all_branches=True,
        verbose=0,
    )
    # On Windows, exercise a packed-object path beyond the traditional
    # MAX_PATH boundary without making the repository directory itself too
    # long for Python 3.10's CreateProcess ``cwd`` handling. Git must be able
    # to start in the checkout and then use the archive-local long-path config
    # while opening deeper object paths.
    if os.name == 'nt':
        import tarfile

        with tarfile.open(archive, 'r:gz') as tar:
            root_names = {
                name.split('/', 1)[0] for name in tar.getnames() if name
            }
        assert len(root_names) == 1
        archive_root_name = next(iter(root_names))
        target_unpack_len = 220
        fixed_len = len(str(tmp_path.resolve())) + 2 + len(archive_root_name)
        padding_len = max(1, target_unpack_len - fixed_len)
        extract_dpath = tmp_path / ('x' * padding_len)
    else:
        extract_dpath = tmp_path / 'all-branches-shallow-extract'

    unpacked = _extract_tar_root(archive, extract_dpath)
    longpaths = _stdout_text(
        ub.cmd(
            ['git', 'config', '--local', '--get', 'core.longpaths'],
            cwd=unpacked,
            check=True,
        )
    ).strip()
    assert longpaths == 'true'
    if os.name == 'nt':
        unpacked_text = str(unpacked.resolve())
        representative_pack = str(
            unpacked
            / '.git'
            / 'objects'
            / 'pack'
            / ('pack-' + ('0' * 40) + '.pack')
        )
        assert len(unpacked_text) < 240
        assert len(representative_pack) > 260
    archived_topic = _stdout_text(
        ub.cmd(
            ['git', 'rev-parse', '--verify', 'refs/heads/topic'],
            cwd=unpacked,
            check=True,
        )
    ).strip()
    assert archived_topic == topic_head
    count = _stdout_text(
        ub.cmd(
            ['git', 'rev-list', '--count', 'refs/heads/topic'],
            cwd=unpacked,
            check=True,
        )
    ).strip()
    assert count == '1'


def test_archive_source_all_branches_rejects_source_only(tmp_path):
    import pytest
    import ubelt as ub

    from git_well.archive_source import archive_source

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

    from git_well.archive_source import archive_source

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

    from git_well.archive_source import archive_source

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

    from git_well.archive_source import archive_source

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



def test_archive_source_exclude_path_history_is_clean_and_restorable(tmp_path):
    import subprocess

    from git_well.archive_source import archive_source

    repo = tmp_path / 'exclude_history'
    _init_demo_repo(repo)
    (repo / 'payload').mkdir()
    (repo / 'payload' / 'keep.txt').write_text('keep\n')
    (repo / 'payload' / 'large.json').write_text('x' * 10000)
    _commit_all(repo, 'add payload')

    archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'exclude-history.tar.gz',
        exclude_path=['payload/large.json'],
        verbose=0,
    )
    root = _extract_tar_root(archive, tmp_path / 'extract-history')

    assert (root / 'payload' / 'keep.txt').exists()
    assert not (root / 'payload' / 'large.json').exists()
    status = subprocess.run(
        ['git', 'status', '--porcelain=v1'],
        cwd=root,
        check=True,
        text=True,
        capture_output=True,
    )
    assert status.stdout == ''
    subprocess.run(['git', 'fsck', '--full'], cwd=root, check=True)
    shown = subprocess.run(
        ['git', 'show', 'HEAD:payload/large.json'],
        cwd=root,
        check=True,
        text=True,
        capture_output=True,
    )
    assert len(shown.stdout) == 10000
    subprocess.run(['git', 'sparse-checkout', 'disable'], cwd=root, check=True)
    assert (root / 'payload' / 'large.json').read_text() == 'x' * 10000

    manifest = (root / 'GIT_WELL_ARCHIVE_INFO.txt').read_text()
    assert 'Worktree path exclusions:' in manifest
    assert '- payload/large.json' in manifest
    assert 'Git history rewritten: no' in manifest


def test_archive_source_exclude_path_source_only_and_directory(tmp_path):
    from git_well.archive_source import archive_source

    repo = tmp_path / 'exclude_source_only'
    _init_demo_repo(repo)
    (repo / 'notebooks').mkdir()
    (repo / 'notebooks' / 'a.ipynb').write_text('A' * 1000)
    (repo / 'notebooks' / 'nested').mkdir()
    (repo / 'notebooks' / 'nested' / 'b.ipynb').write_text('B' * 1000)
    (repo / 'keep.py').write_text('print(1)\n')
    _commit_all(repo, 'add source-only payload')

    archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'exclude-source-only.tar.gz',
        depth=0,
        exclude_path=['notebooks'],
        history_blobs='sparse',
        verbose=0,
    )
    root = _extract_tar_root(archive, tmp_path / 'extract-source-only')
    assert not (root / '.git').exists()
    assert not (root / 'notebooks').exists()
    assert (root / 'keep.py').exists()


def test_archive_source_exclude_path_inside_history_submodule(tmp_path):
    import subprocess

    from git_well.archive_source import archive_source

    sub_repo = _make_submodule_repo(
        tmp_path,
        'exclude_sub_src',
        filename='notebooks/demo.ipynb',
        content='large notebook\n',
    )
    (sub_repo / 'code.py').write_text('print(1)\n')
    _commit_all(sub_repo, 'add code')
    super_repo = _make_repo_with_submodules(
        tmp_path, {'tpl/lib': sub_repo}
    )
    archive = archive_source(
        repo_dpath=super_repo,
        output=tmp_path / 'exclude-submodule-path.tar.gz',
        exclude_path=['tpl/lib/notebooks'],
        verbose=0,
    )
    root = _extract_tar_root(archive, tmp_path / 'extract-submodule-path')
    sub = root / 'tpl/lib'
    assert not (sub / 'notebooks/demo.ipynb').exists()
    assert (sub / 'code.py').exists()
    status = subprocess.run(
        ['git', 'status', '--porcelain=v1'],
        cwd=sub,
        check=True,
        text=True,
        capture_output=True,
    )
    assert status.stdout == ''
    subprocess.run(['git', 'sparse-checkout', 'disable'], cwd=sub, check=True)
    assert (sub / 'notebooks/demo.ipynb').exists()



def _git_object_exists(repo, oid, *, no_lazy=True):
    import subprocess

    env = os.environ.copy()
    if no_lazy:
        env['GIT_NO_LAZY_FETCH'] = '1'
    proc = subprocess.run(
        ['git', 'cat-file', '-e', oid],
        cwd=repo,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return proc.returncode == 0


def _git_rev_parse(repo, spec):
    import subprocess

    return subprocess.run(
        ['git', 'rev-parse', spec],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()


def test_archive_source_history_blobs_sparse_promisor_roundtrip(tmp_path):
    import subprocess

    from git_well.archive_source import archive_source

    repo = tmp_path / 'promisor_roundtrip'
    _init_demo_repo(repo)
    (repo / 'payload').mkdir()
    (repo / 'payload' / 'large.bin').write_bytes(b'A' * 12000)
    (repo / 'keep.txt').write_text('keep-v1\n')
    _commit_all(repo, 'first payload')
    old_large = _git_rev_parse(repo, 'HEAD:payload/large.bin')
    old_keep = _git_rev_parse(repo, 'HEAD:keep.txt')

    (repo / 'payload' / 'large.bin').write_bytes(b'B' * 13000)
    (repo / 'keep.txt').write_text('keep-v2\n')
    _commit_all(repo, 'second payload')
    new_large = _git_rev_parse(repo, 'HEAD:payload/large.bin')
    new_keep = _git_rev_parse(repo, 'HEAD:keep.txt')

    archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'promisor-roundtrip.tar.gz',
        exclude_path=['payload/large.bin'],
        history_blobs='sparse',
        verbose=0,
    )
    root = _extract_tar_root(archive, tmp_path / 'extract-promisor-roundtrip')

    assert not (root / 'payload' / 'large.bin').exists()
    assert (root / 'keep.txt').read_text() == 'keep-v2\n'
    assert not _git_object_exists(root, old_large)
    assert not _git_object_exists(root, new_large)
    assert _git_object_exists(root, old_keep)
    assert _git_object_exists(root, new_keep)

    no_lazy_env = os.environ.copy()
    no_lazy_env['GIT_NO_LAZY_FETCH'] = '1'
    subprocess.run(
        ['git', 'fsck', '--full'],
        cwd=root,
        env=no_lazy_env,
        check=True,
    )
    status = subprocess.run(
        ['git', 'status', '--porcelain=v1'],
        cwd=root,
        env=no_lazy_env,
        check=True,
        text=True,
        capture_output=True,
    )
    assert status.stdout == ''

    promisor = subprocess.run(
        ['git', 'config', '--get', 'extensions.partialClone'],
        cwd=root,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    assert promisor == 'git-well-promisor'
    assert list((root / '.git' / 'objects' / 'pack').glob('*.promisor'))

    # The source checkout is the conservative fallback promisor when there is
    # no published configured remote, so restoring the sparse checkout proves
    # that omitted current blobs are lazily recoverable without rewritten IDs.
    subprocess.run(['git', 'sparse-checkout', 'disable'], cwd=root, check=True)
    assert (root / 'payload' / 'large.bin').read_bytes() == b'B' * 13000
    assert _git_rev_parse(root, 'HEAD:payload/large.bin') == new_large

    manifest = (root / 'GIT_WELL_ARCHIVE_INFO.txt').read_text()
    assert 'History blob retention: sparse' in manifest
    assert 'Promisor history pruning:' in manifest
    assert 'omitted blobs: 2' in manifest
    assert 'Git history rewritten: no' in manifest


def test_archive_source_history_blobs_sparse_covers_deleted_history(tmp_path):
    from git_well.archive_source import archive_source

    repo = tmp_path / 'promisor_deleted_history'
    _init_demo_repo(repo)
    (repo / 'notebooks').mkdir()
    (repo / 'notebooks' / 'old.ipynb').write_bytes(b'old-notebook' * 1000)
    (repo / 'keep.py').write_text('print("v1")\n')
    _commit_all(repo, 'old notebook')
    old_oid = _git_rev_parse(repo, 'HEAD:notebooks/old.ipynb')

    (repo / 'notebooks' / 'old.ipynb').unlink()
    (repo / 'notebooks' / 'current.ipynb').write_bytes(
        b'current-notebook' * 1000
    )
    _commit_all(repo, 'replace notebook')
    current_oid = _git_rev_parse(repo, 'HEAD:notebooks/current.ipynb')

    archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'promisor-deleted-history.tar.gz',
        exclude_path=['notebooks'],
        history_blobs='sparse',
        verbose=0,
    )
    root = _extract_tar_root(archive, tmp_path / 'extract-promisor-history')

    assert not (root / 'notebooks').exists()
    assert not _git_object_exists(root, old_oid)
    assert not _git_object_exists(root, current_oid)
    manifest = (root / 'GIT_WELL_ARCHIVE_INFO.txt').read_text()
    assert 'matched historical paths: 2' in manifest
    assert 'omitted blobs: 2' in manifest


def test_archive_source_history_blobs_sparse_inside_submodule(tmp_path):
    import subprocess

    from git_well.archive_source import archive_source

    sub_repo = _make_submodule_repo(
        tmp_path,
        'promisor_sub_src',
        filename='notebooks/demo.ipynb',
        content='notebook-v1\n',
    )
    old_oid = _git_rev_parse(sub_repo, 'HEAD:notebooks/demo.ipynb')
    (sub_repo / 'notebooks' / 'demo.ipynb').write_text('notebook-v2\n')
    (sub_repo / 'code.py').write_text('print(1)\n')
    _commit_all(sub_repo, 'update notebook and code')
    new_oid = _git_rev_parse(sub_repo, 'HEAD:notebooks/demo.ipynb')

    super_repo = _make_repo_with_submodules(tmp_path, {'tpl/lib': sub_repo})
    archive = archive_source(
        repo_dpath=super_repo,
        output=tmp_path / 'promisor-submodule.tar.gz',
        exclude_path=['tpl/lib/notebooks'],
        history_blobs='sparse',
        verbose=0,
    )
    root = _extract_tar_root(archive, tmp_path / 'extract-promisor-submodule')
    sub = root / 'tpl/lib'

    assert not (sub / 'notebooks' / 'demo.ipynb').exists()
    assert (sub / 'code.py').exists()
    assert not _git_object_exists(sub, old_oid)
    assert not _git_object_exists(sub, new_oid)
    no_lazy_env = os.environ.copy()
    no_lazy_env['GIT_NO_LAZY_FETCH'] = '1'
    subprocess.run(
        ['git', 'fsck', '--full'], cwd=sub, env=no_lazy_env, check=True
    )
    subprocess.run(['git', 'sparse-checkout', 'disable'], cwd=sub, check=True)
    assert (sub / 'notebooks' / 'demo.ipynb').read_text() == 'notebook-v2\n'


def test_archive_source_history_blobs_sparse_all_branches(tmp_path):
    import subprocess

    from git_well.archive_source import archive_source

    repo = tmp_path / 'promisor_all_branches'
    _init_demo_repo(repo)
    (repo / 'large.bin').write_bytes(b'main-large' * 1000)
    (repo / 'keep.txt').write_text('main\n')
    _commit_all(repo, 'main payload')
    main_branch = subprocess.run(
        ['git', 'branch', '--show-current'],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    main_large = _git_rev_parse(repo, 'HEAD:large.bin')

    subprocess.run(['git', 'checkout', '-q', '-b', 'topic'], cwd=repo, check=True)
    (repo / 'large.bin').write_bytes(b'topic-large' * 1000)
    (repo / 'topic.txt').write_text('topic source\n')
    _commit_all(repo, 'topic payload')
    topic_large = _git_rev_parse(repo, 'HEAD:large.bin')
    topic_keep = _git_rev_parse(repo, 'HEAD:topic.txt')
    subprocess.run(['git', 'checkout', '-q', main_branch], cwd=repo, check=True)

    archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'promisor-all-branches.tar.gz',
        exclude_path=['large.bin'],
        history_blobs='sparse',
        all_branches=True,
        verbose=0,
    )
    root = _extract_tar_root(archive, tmp_path / 'extract-promisor-branches')

    assert not _git_object_exists(root, main_large)
    assert not _git_object_exists(root, topic_large)
    assert _git_object_exists(root, topic_keep)
    shown = subprocess.run(
        ['git', 'show', 'topic:topic.txt'],
        cwd=root,
        env={**os.environ, 'GIT_NO_LAZY_FETCH': '1'},
        check=True,
        text=True,
        capture_output=True,
    )
    assert shown.stdout == 'topic source\n'
    subprocess.run(
        ['git', 'fsck', '--full'],
        cwd=root,
        env={**os.environ, 'GIT_NO_LAZY_FETCH': '1'},
        check=True,
    )


def test_promisor_remote_local_url_detection():
    from git_well.archive_source._prune import _looks_like_local_remote_url

    assert _looks_like_local_remote_url('/tmp/repo.git')
    assert _looks_like_local_remote_url('../repo.git')
    assert _looks_like_local_remote_url('relative/repo.git')
    assert _looks_like_local_remote_url('file:///tmp/repo.git')
    assert _looks_like_local_remote_url(r'C:\\repo')
    assert not _looks_like_local_remote_url('https://example.com/org/repo.git')
    assert not _looks_like_local_remote_url('ssh://example.com/org/repo.git')
    assert not _looks_like_local_remote_url('git@example.com:org/repo.git')


def test_archive_source_history_blobs_sparse_redaction_uses_published_remote(
    tmp_path,
):
    import subprocess

    from git_well.archive_source import archive_source

    repo = tmp_path / 'promisor_redacted_published'
    _init_demo_repo(repo)
    (repo / 'large.bin').write_bytes(b'large' * 1000)
    (repo / 'keep.py').write_text('print(1)\n')
    _commit_all(repo, 'payload')
    large_oid = _git_rev_parse(repo, 'HEAD:large.bin')
    branch = subprocess.run(
        ['git', 'branch', '--show-current'],
        cwd=repo,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    remote_url = 'https://example.invalid/org/promisor-test.git'
    subprocess.run(
        ['git', 'remote', 'add', 'origin', remote_url], cwd=repo, check=True
    )
    subprocess.run(
        ['git', 'update-ref', f'refs/remotes/origin/{branch}', 'HEAD'],
        cwd=repo,
        check=True,
    )

    archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'promisor-redacted-published.tar.gz',
        exclude_path=['large.bin'],
        history_blobs='sparse',
        redact_local_paths=True,
        verbose=0,
    )
    root = _extract_tar_root(
        archive, tmp_path / 'extract-promisor-redacted-published'
    )
    assert not _git_object_exists(root, large_oid)
    configured = subprocess.run(
        ['git', 'config', '--get', 'remote.git-well-promisor.url'],
        cwd=root,
        check=True,
        text=True,
        capture_output=True,
    ).stdout.strip()
    assert configured == remote_url
    subprocess.run(
        ['git', 'fsck', '--full'],
        cwd=root,
        env={**os.environ, 'GIT_NO_LAZY_FETCH': '1'},
        check=True,
    )
    manifest = (root / 'GIT_WELL_ARCHIVE_INFO.txt').read_text()
    assert str(repo.resolve()) not in manifest
    assert remote_url in manifest


def test_archive_source_history_blobs_sparse_redaction_requires_remote(tmp_path):
    import pytest

    from git_well.archive_source import archive_source

    repo = tmp_path / 'promisor_redacted'
    _init_demo_repo(repo)
    (repo / 'large.bin').write_bytes(b'large' * 1000)
    _commit_all(repo, 'payload')

    with pytest.raises(RuntimeError, match='promisor remote'):
        archive_source(
            repo_dpath=repo,
            output=tmp_path / 'should-not-exist.tar.gz',
            exclude_path=['large.bin'],
            history_blobs='sparse',
            redact_local_paths=True,
            verbose=0,
        )


def test_archive_source_history_blobs_sparse_patch_roundtrip_offline(tmp_path):
    import json
    import subprocess

    from git_well.archive_source import apply_source_patch
    from git_well.archive_source import archive_source

    repo = tmp_path / 'promisor_patch_roundtrip'
    _init_demo_repo(repo)
    (repo / 'large.bin').write_bytes(b'A' * 12000)
    (repo / 'keep.txt').write_text('base\n')
    _commit_all(repo, 'base payload')
    old_large = _git_rev_parse(repo, 'HEAD:large.bin')

    base_archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'promisor-patch-base.tar.gz',
        exclude_path=['large.bin'],
        history_blobs='sparse',
        verbose=0,
    )

    (repo / 'large.bin').write_bytes(b'B' * 13000)
    (repo / 'keep.txt').write_text('target\n')
    _commit_all(repo, 'target payload')
    new_large = _git_rev_parse(repo, 'HEAD:large.bin')

    patch_archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'promisor-patch-update.tar.gz',
        exclude_path=['large.bin'],
        history_blobs='sparse',
        patch=base_archive,
        verbose=0,
    )
    patch_root = _extract_tar_root(
        patch_archive, tmp_path / 'extract-promisor-patch'
    )
    manifest = json.loads(
        (patch_root / 'GIT_WELL_SOURCE_PATCH.json').read_text()
    )
    assert manifest['schema_version'] == 2
    assert manifest['target']['history_blobs'] == 'sparse'
    assert manifest['target']['exclude_path_selectors'] == ['large.bin']
    super_entry = next(
        item for item in manifest['git_repositories'] if item['path'] == '.'
    )
    assert super_entry['bundle'] is None
    assert super_entry['object_pack'] is not None
    assert super_entry['object_pack_promisor'] is True
    assert super_entry['missing_promisor_objects'] >= 1
    assert len(super_entry['missing_promisor_oid_sha256']) == 64
    assert (patch_root / super_entry['object_pack']).is_file()

    # Make the fallback promisor unreachable. Patch application must not need
    # the omitted blob merely to advance the sparse checkout.
    offline_repo = tmp_path / 'promisor_patch_roundtrip.offline'
    repo.rename(offline_repo)

    applied = apply_source_patch(
        base_archive,
        patch_archive,
        tmp_path / 'promisor-patch-applied',
    )
    assert (applied / 'keep.txt').read_text() == 'target\n'
    assert not (applied / 'large.bin').exists()
    assert not _git_object_exists(applied, old_large)
    assert not _git_object_exists(applied, new_large)
    no_lazy_env = {**os.environ, 'GIT_NO_LAZY_FETCH': '1'}
    subprocess.run(
        ['git', 'fsck', '--full'], cwd=applied, env=no_lazy_env, check=True
    )
    status = subprocess.run(
        ['git', 'status', '--porcelain=v1'],
        cwd=applied,
        env=no_lazy_env,
        text=True,
        capture_output=True,
        check=True,
    )
    assert status.stdout == ''


def test_archive_source_history_blobs_sparse_patch_new_selector_match(tmp_path):
    import subprocess

    from git_well.archive_source import apply_source_patch
    from git_well.archive_source import archive_source

    repo = tmp_path / 'promisor_patch_new_match'
    _init_demo_repo(repo)
    (repo / 'keep.txt').write_text('base\n')
    _commit_all(repo, 'base without payload')

    # The selector is intentionally unmatched in the base. A later commit may
    # add a matching file, so the patch must install target sparse/promisor
    # metadata before resetting to the target commit.
    base_archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'promisor-new-match-base.tar.gz',
        exclude_path=['payload'],
        history_blobs='sparse',
        verbose=0,
    )

    (repo / 'payload').mkdir()
    (repo / 'payload' / 'large.bin').write_bytes(b'new-large' * 5000)
    (repo / 'keep.txt').write_text('target\n')
    _commit_all(repo, 'add excluded payload')
    large_oid = _git_rev_parse(repo, 'HEAD:payload/large.bin')

    patch_archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'promisor-new-match-update.tar.gz',
        exclude_path=['payload'],
        history_blobs='sparse',
        patch=base_archive,
        verbose=0,
    )
    offline_repo = tmp_path / 'promisor_patch_new_match.offline'
    repo.rename(offline_repo)
    applied = apply_source_patch(
        base_archive,
        patch_archive,
        tmp_path / 'promisor-new-match-applied',
    )
    assert (applied / 'keep.txt').read_text() == 'target\n'
    assert not (applied / 'payload' / 'large.bin').exists()
    assert not _git_object_exists(applied, large_oid)
    configured = subprocess.run(
        ['git', 'config', '--get', 'extensions.partialClone'],
        cwd=applied,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    assert configured == 'git-well-promisor'
    subprocess.run(
        ['git', 'fsck', '--full'],
        cwd=applied,
        env={**os.environ, 'GIT_NO_LAZY_FETCH': '1'},
        check=True,
    )


def test_archive_source_history_blobs_sparse_patch_backfills_moved_blob(tmp_path):
    import subprocess

    from git_well.archive_source import apply_source_patch
    from git_well.archive_source import archive_source

    repo = tmp_path / 'promisor_patch_moved_blob'
    _init_demo_repo(repo)
    (repo / 'large.bin').write_bytes(b'stable-large-payload' * 4000)
    (repo / 'keep.txt').write_text('base\n')
    _commit_all(repo, 'base excluded payload')
    large_oid = _git_rev_parse(repo, 'HEAD:large.bin')

    base_archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'promisor-moved-blob-base.tar.gz',
        exclude_path=['large.bin'],
        history_blobs='sparse',
        verbose=0,
    )

    # Keep the exact blob object but move it outside the exclusion. The base
    # intentionally lacks this OID even though it is reachable from base HEAD,
    # so a revision-only delta would fail to provide the now-required object.
    (repo / 'src').mkdir()
    subprocess.run(
        ['git', 'mv', 'large.bin', 'src/large.bin'], cwd=repo, check=True
    )
    (repo / 'keep.txt').write_text('target\n')
    _commit_all(repo, 'move payload into retained source')
    assert _git_rev_parse(repo, 'HEAD:src/large.bin') == large_oid

    patch_archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'promisor-moved-blob-update.tar.gz',
        exclude_path=['large.bin'],
        history_blobs='sparse',
        patch=base_archive,
        verbose=0,
    )

    # Remove access to the fallback promisor. The patch itself must carry the
    # newly-required old blob even though its object ID predates the base.
    offline_repo = tmp_path / 'promisor_patch_moved_blob.offline'
    repo.rename(offline_repo)
    applied = apply_source_patch(
        base_archive,
        patch_archive,
        tmp_path / 'promisor-moved-blob-applied',
    )
    assert not (applied / 'large.bin').exists()
    assert (applied / 'src' / 'large.bin').read_bytes() == (
        b'stable-large-payload' * 4000
    )
    assert _git_object_exists(applied, large_oid)
    subprocess.run(
        ['git', 'fsck', '--full'],
        cwd=applied,
        env={**os.environ, 'GIT_NO_LAZY_FETCH': '1'},
        check=True,
    )


def test_archive_source_history_blobs_sparse_patch_shallow_promise_ages_out(tmp_path):
    import subprocess

    from git_well.archive_source import apply_source_patch
    from git_well.archive_source import archive_source

    repo = tmp_path / 'promisor_patch_shallow_ageout'
    _init_demo_repo(repo)
    (repo / 'large.bin').write_bytes(b'old-large' * 4000)
    (repo / 'keep.txt').write_text('one\n')
    _commit_all(repo, 'one with excluded payload')
    initial_commit = _git_rev_parse(repo, 'HEAD')

    (repo / 'large.bin').unlink()
    for index in range(2, 5):
        (repo / 'keep.txt').write_text(f'{index}\n')
        _commit_all(repo, f'commit {index}')

    # Depth four still retains commit one, so the base intentionally promises
    # large.bin even though it is absent from the current worktree.
    base_archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'promisor-shallow-ageout-base.tar.gz',
        depth=4,
        exclude_path=['large.bin'],
        history_blobs='sparse',
        verbose=0,
    )

    (repo / 'keep.txt').write_text('five\n')
    _commit_all(repo, 'commit five')
    patch_archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'promisor-shallow-ageout-update.tar.gz',
        depth=4,
        exclude_path=['large.bin'],
        history_blobs='sparse',
        patch=base_archive,
        verbose=0,
    )

    offline_repo = tmp_path / 'promisor_patch_shallow_ageout.offline'
    repo.rename(offline_repo)
    applied = apply_source_patch(
        base_archive,
        patch_archive,
        tmp_path / 'promisor-shallow-ageout-applied',
    )
    assert (applied / 'keep.txt').read_text() == 'five\n'
    assert not (applied / 'large.bin').exists()
    # The old object may remain as unreachable storage from the base, but it is
    # no longer promised by any retained target history and must not be needed.
    missing = subprocess.run(
        ['git', 'rev-list', '--objects', '--all', '--missing=print'],
        cwd=applied,
        env={**os.environ, 'GIT_NO_LAZY_FETCH': '1'},
        text=True,
        capture_output=True,
        check=True,
    ).stdout
    assert not any(line.startswith('?') for line in missing.splitlines())
    subprocess.run(
        ['git', 'fsck', '--full'],
        cwd=applied,
        env={**os.environ, 'GIT_NO_LAZY_FETCH': '1'},
        check=True,
    )
    reachable = subprocess.run(
        ['git', 'rev-list', '--all'],
        cwd=applied,
        text=True,
        capture_output=True,
        check=True,
    ).stdout.splitlines()
    assert initial_commit not in reachable


def test_archive_source_history_blobs_sparse_patch_rejects_policy_change(tmp_path):
    import pytest

    from git_well.archive_source import archive_source
    from git_well.archive_source.patch import SourcePatchError

    repo = tmp_path / 'promisor_patch_policy_change'
    _init_demo_repo(repo)
    (repo / 'large.bin').write_bytes(b'base-large')
    (repo / 'keep.txt').write_text('base\n')
    _commit_all(repo, 'base')
    base_archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'promisor-policy-base.tar.gz',
        exclude_path=['large.bin'],
        history_blobs='sparse',
        verbose=0,
    )

    (repo / 'keep.txt').write_text('target\n')
    _commit_all(repo, 'target')
    with pytest.raises(SourcePatchError, match='same --exclude-path policy'):
        archive_source(
            repo_dpath=repo,
            output=tmp_path / 'invalid-policy-patch.tar.gz',
            exclude_path=['other.bin'],
            history_blobs='sparse',
            patch=base_archive,
            verbose=0,
        )


def test_archive_source_history_blobs_sparse_patch_auto_policy_aware(tmp_path):
    import json

    from git_well.archive_source import archive_source

    repo = tmp_path / 'promisor_patch_auto'
    _init_demo_repo(repo)
    (repo / 'large.bin').write_bytes(b'one' * 1000)
    (repo / 'keep.txt').write_text('one\n')
    _commit_all(repo, 'one')
    sparse_base = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'promisor-auto-sparse-base.tar.gz',
        exclude_path=['large.bin'],
        history_blobs='sparse',
        verbose=0,
    )

    # A closer full-blob archive is incompatible with a sparse target and must
    # not win merely because its HEAD is newer.
    (repo / 'keep.txt').write_text('two\n')
    _commit_all(repo, 'two')
    archive_source(
        repo_dpath=repo,
        output=tmp_path / 'promisor-auto-full-base.tar.gz',
        exclude_path=['large.bin'],
        history_blobs='full',
        verbose=0,
    )

    (repo / 'large.bin').write_bytes(b'three' * 1000)
    (repo / 'keep.txt').write_text('three\n')
    _commit_all(repo, 'three')
    patch_archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'promisor-auto-patch.tar.gz',
        exclude_path=['large.bin'],
        history_blobs='sparse',
        patch='auto',
        verbose=0,
    )
    patch_root = _extract_tar_root(
        patch_archive, tmp_path / 'extract-promisor-auto-patch'
    )
    manifest = json.loads(
        (patch_root / 'GIT_WELL_SOURCE_PATCH.json').read_text()
    )
    assert manifest['base']['archive_name'] == sparse_base.name


def test_archive_source_history_blobs_sparse_patch_submodule(tmp_path):
    import json
    import subprocess

    import ubelt as ub

    from git_well.archive_source import apply_source_patch
    from git_well.archive_source import archive_source

    sub_repo = _make_submodule_repo(
        tmp_path,
        'promisor_patch_sub_src',
        filename='notebooks/demo.ipynb',
        content='notebook-v1\n',
    )
    super_repo = _make_repo_with_submodules(
        tmp_path, {'tpl/lib': sub_repo}
    )
    base_archive = archive_source(
        repo_dpath=super_repo,
        output=tmp_path / 'promisor-patch-sub-base.tar.gz',
        exclude_path=['tpl/lib/notebooks'],
        history_blobs='sparse',
        submodule_depth=10,
        verbose=0,
    )

    (sub_repo / 'notebooks' / 'demo.ipynb').write_text('notebook-v2\n')
    (sub_repo / 'code.py').write_text('print(2)\n')
    _commit_all(sub_repo, 'advance sparse submodule')
    new_large = _git_rev_parse(sub_repo, 'HEAD:notebooks/demo.ipynb')
    target_sub_sha = _git_rev_parse(sub_repo, 'HEAD')
    sub_checkout = super_repo / 'tpl/lib'
    ub.cmd(['git', 'fetch', str(sub_repo)], cwd=sub_checkout, check=True)
    ub.cmd(['git', 'checkout', target_sub_sha], cwd=sub_checkout, check=True)
    _commit_all(super_repo, 'advance sparse submodule gitlink')

    patch_archive = archive_source(
        repo_dpath=super_repo,
        output=tmp_path / 'promisor-patch-sub-update.tar.gz',
        exclude_path=['tpl/lib/notebooks'],
        history_blobs='sparse',
        submodule_depth=10,
        patch=base_archive,
        verbose=0,
    )
    patch_root = _extract_tar_root(
        patch_archive, tmp_path / 'extract-promisor-patch-sub'
    )
    manifest = json.loads(
        (patch_root / 'GIT_WELL_SOURCE_PATCH.json').read_text()
    )
    sub_entry = next(
        item for item in manifest['git_repositories']
        if item['path'] == 'tpl/lib'
    )
    assert sub_entry['bundle'] is None
    assert sub_entry['object_pack'] is not None
    assert sub_entry['object_pack_promisor'] is True

    applied = apply_source_patch(
        base_archive,
        patch_archive,
        tmp_path / 'promisor-patch-sub-applied',
    )
    applied_sub = applied / 'tpl/lib'
    assert (applied_sub / 'code.py').read_text() == 'print(2)\n'
    assert not (applied_sub / 'notebooks' / 'demo.ipynb').exists()
    assert not _git_object_exists(applied_sub, new_large)
    subprocess.run(
        ['git', 'fsck', '--full'],
        cwd=applied_sub,
        env={**os.environ, 'GIT_NO_LAZY_FETCH': '1'},
        check=True,
    )


def test_archive_source_history_blobs_sparse_patch_unrelated_submodule(tmp_path):
    import json
    import subprocess

    import ubelt as ub

    from git_well.archive_source import apply_source_patch
    from git_well.archive_source import archive_source

    sub_repo = _make_submodule_repo(
        tmp_path,
        'promisor_patch_unrelated_sub_src',
        filename='notebooks/demo.ipynb',
        content='notebook-base\n',
    )
    (sub_repo / 'code.py').write_text('print("base")\n')
    _commit_all(sub_repo, 'base retained code')
    super_repo = _make_repo_with_submodules(
        tmp_path, {'tpl/lib': sub_repo}
    )
    base_archive = archive_source(
        repo_dpath=super_repo,
        output=tmp_path / 'promisor-patch-unrelated-sub-base.tar.gz',
        exclude_path=['tpl/lib/notebooks'],
        history_blobs='sparse',
        submodule_depth=10,
        verbose=0,
    )

    # Move the submodule to an unrelated orphan history. Sparse object packs
    # can still transport the target's locally-present objects exactly; falling
    # back to copying the complete .git object store would make the patch much
    # larger and could hide transport bugs.
    ub.cmd(['git', 'checkout', '--orphan', 'replacement'], cwd=sub_repo, check=True)
    ub.cmd(['git', 'rm', '-rf', '.'], cwd=sub_repo, check=True)
    (sub_repo / 'notebooks').mkdir(parents=True, exist_ok=True)
    (sub_repo / 'notebooks' / 'demo.ipynb').write_bytes(b'new-notebook' * 4000)
    (sub_repo / 'code.py').write_text('print("replacement")\n')
    _commit_all(sub_repo, 'replacement history')
    target_sub_sha = _git_rev_parse(sub_repo, 'HEAD')
    omitted_oid = _git_rev_parse(sub_repo, 'HEAD:notebooks/demo.ipynb')

    sub_checkout = super_repo / 'tpl/lib'
    ub.cmd(['git', 'fetch', str(sub_repo), target_sub_sha], cwd=sub_checkout, check=True)
    ub.cmd(['git', 'checkout', target_sub_sha], cwd=sub_checkout, check=True)
    _commit_all(super_repo, 'switch submodule to unrelated history')

    patch_archive = archive_source(
        repo_dpath=super_repo,
        output=tmp_path / 'promisor-patch-unrelated-sub-update.tar.gz',
        exclude_path=['tpl/lib/notebooks'],
        history_blobs='sparse',
        submodule_depth=10,
        patch=base_archive,
        verbose=0,
    )
    patch_root = _extract_tar_root(
        patch_archive, tmp_path / 'extract-promisor-unrelated-sub-patch'
    )
    manifest = json.loads(
        (patch_root / 'GIT_WELL_SOURCE_PATCH.json').read_text()
    )
    sub_entry = next(
        item for item in manifest['git_repositories']
        if item['path'] == 'tpl/lib'
    )
    assert sub_entry['object_pack'] is not None
    assert sub_entry['bundle'] is None
    assert sub_entry['missing_promisor_objects'] >= 1

    offline_super = tmp_path / 'promisor_patch_unrelated_super.offline'
    offline_sub = tmp_path / 'promisor_patch_unrelated_source.offline'
    super_repo.rename(offline_super)
    sub_repo.rename(offline_sub)
    applied = apply_source_patch(
        base_archive,
        patch_archive,
        tmp_path / 'promisor-patch-unrelated-sub-applied',
    )
    applied_sub = applied / 'tpl/lib'
    assert _git_rev_parse(applied_sub, 'HEAD') == target_sub_sha
    assert (applied_sub / 'code.py').read_text() == 'print("replacement")\n'
    assert not (applied_sub / 'notebooks' / 'demo.ipynb').exists()
    assert not _git_object_exists(applied_sub, omitted_oid)
    subprocess.run(
        ['git', 'fsck', '--full'],
        cwd=applied_sub,
        env={**os.environ, 'GIT_NO_LAZY_FETCH': '1'},
        check=True,
    )


def test_archive_source_history_blobs_sparse_patch_all_branches(tmp_path):
    import json
    import subprocess

    from git_well.archive_source import apply_source_patch
    from git_well.archive_source import archive_source

    repo = tmp_path / 'promisor_patch_all_branches'
    _init_demo_repo(repo)
    (repo / 'large.bin').write_bytes(b'main-large' * 1000)
    (repo / 'keep.txt').write_text('main\n')
    _commit_all(repo, 'main base')
    main_branch = subprocess.run(
        ['git', 'branch', '--show-current'], cwd=repo, text=True,
        capture_output=True, check=True,
    ).stdout.strip()
    subprocess.run(['git', 'checkout', '-q', '-b', 'topic'], cwd=repo, check=True)
    (repo / 'topic.txt').write_text('topic-v1\n')
    _commit_all(repo, 'topic base')
    subprocess.run(['git', 'checkout', '-q', main_branch], cwd=repo, check=True)

    base_archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'promisor-all-branches-base.tar.gz',
        exclude_path=['large.bin'],
        history_blobs='sparse',
        all_branches=True,
        verbose=0,
    )

    subprocess.run(['git', 'checkout', '-q', 'topic'], cwd=repo, check=True)
    (repo / 'large.bin').write_bytes(b'topic-large-v2' * 1000)
    (repo / 'topic.txt').write_text('topic-v2\n')
    _commit_all(repo, 'topic target')
    topic_large = _git_rev_parse(repo, 'HEAD:large.bin')
    subprocess.run(['git', 'checkout', '-q', main_branch], cwd=repo, check=True)

    patch_archive = archive_source(
        repo_dpath=repo,
        output=tmp_path / 'promisor-all-branches-update.tar.gz',
        exclude_path=['large.bin'],
        history_blobs='sparse',
        all_branches=True,
        patch=base_archive,
        verbose=0,
    )
    patch_root = _extract_tar_root(
        patch_archive, tmp_path / 'extract-promisor-all-branches-patch'
    )
    manifest = json.loads(
        (patch_root / 'GIT_WELL_SOURCE_PATCH.json').read_text()
    )
    super_entry = next(
        item for item in manifest['git_repositories'] if item['path'] == '.'
    )
    assert super_entry['object_pack'] is not None
    assert super_entry['missing_promisor_objects'] >= 1

    applied = apply_source_patch(
        base_archive,
        patch_archive,
        tmp_path / 'promisor-all-branches-applied',
    )
    shown = subprocess.run(
        ['git', 'show', 'topic:topic.txt'],
        cwd=applied,
        env={**os.environ, 'GIT_NO_LAZY_FETCH': '1'},
        text=True,
        capture_output=True,
        check=True,
    )
    assert shown.stdout == 'topic-v2\n'
    assert not _git_object_exists(applied, topic_large)


def test_patch_manifest_reader_accepts_schema_v1(tmp_path):
    import json

    from git_well.archive_source.patch_apply import _read_patch_manifest

    patch_root = tmp_path / 'legacy-patch'
    patch_root.mkdir()
    manifest = {
        'schema_version': 1,
        'kind': 'git-well-source-patch',
    }
    (patch_root / 'GIT_WELL_SOURCE_PATCH.json').write_text(
        json.dumps(manifest)
    )
    assert _read_patch_manifest(patch_root)['schema_version'] == 1


def test_parse_legacy_archive_manifest_defaults_history_blobs_full():
    from git_well.archive_source.patch import _parse_archive_manifest

    manifest = """Git Well Source Archive
Archive prefix: legacy-source
Superproject commit: 0123456789abcdef
Superproject short commit: 0123456
Superproject history: full
Superproject branches: current HEAD history only
Generated timestamp: 2026-09-20T00:00:00+00:00
"""
    parsed = _parse_archive_manifest(manifest)
    assert parsed['history_blobs'] == 'full'
    assert parsed['exclude_path_selectors'] == ()


def test_parse_legacy_sparse_archive_manifest_recovers_selectors():
    from git_well.archive_source.patch import _parse_archive_manifest

    manifest = """Git Well Source Archive
Archive prefix: legacy-source
Superproject commit: 0123456789abcdef
Superproject short commit: 0123456
Superproject history: full
History blob retention: sparse
Superproject branches: current HEAD history only

Worktree path exclusions:
Git history rewritten: no
Matched tracked paths omitted: 1
Raw materialized bytes omitted: 123
Selectors:
- notebooks
- data/large.json
Unmatched selectors:
- never-present.bin

Promisor history pruning:
Git history rewritten: no
"""
    parsed = _parse_archive_manifest(manifest)
    assert parsed['history_blobs'] == 'sparse'
    assert parsed['exclude_path_selectors'] == (
        'notebooks', 'data/large.json'
    )


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
                src.resolve().as_uri(),
                path,
            ],
            cwd=super_repo,
            check=True,
        )
        sub_checkout = super_repo / path
        _configure_demo_identity(sub_checkout)
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

    from git_well.archive_source._policy import (
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
    from git_well.archive_source import archive_source

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


def test_archive_source_patch_git_submodule_bundle_standalone_roundtrip(tmp_path):
    import json
    import subprocess
    import sys
    from pathlib import Path

    import ubelt as ub

    from git_well.archive_source import archive_source

    sub_repo = _make_submodule_repo(tmp_path, 'patch_git_sub')
    super_repo = _make_repo_with_submodules(
        tmp_path, {'external/lib': sub_repo}
    )
    base_archive = archive_source(
        repo_dpath=super_repo,
        output=tmp_path / 'patch-git-sub-base.tar.gz',
        submodule_depth=10,
        verbose=0,
    )

    (sub_repo / 'tracked.txt').write_text('updated git submodule\n')
    _commit_all(sub_repo, 'update git-bearing submodule')
    target_sub_sha = _stdout_text(
        ub.cmd(['git', 'rev-parse', 'HEAD'], cwd=sub_repo, check=True)
    ).strip()
    sub_checkout = super_repo / 'external/lib'
    ub.cmd(['git', 'fetch', str(sub_repo)], cwd=sub_checkout, check=True)
    ub.cmd(['git', 'checkout', target_sub_sha], cwd=sub_checkout, check=True)
    _commit_all(super_repo, 'advance git-bearing submodule')
    target_super_sha = _stdout_text(
        ub.cmd(['git', 'rev-parse', 'HEAD'], cwd=super_repo, check=True)
    ).strip()

    patch_archive = archive_source(
        repo_dpath=super_repo,
        output=tmp_path / 'patch-git-sub-update.tar.gz',
        submodule_depth=10,
        patch=base_archive,
        verbose=0,
    )

    extract = tmp_path / 'patch-git-sub-extract'
    patch_root = _extract_tar_root(patch_archive, extract)
    manifest = json.loads(
        (patch_root / 'GIT_WELL_SOURCE_PATCH.json').read_text()
    )
    sub_entry = next(
        item for item in manifest['git_repositories']
        if item['path'] == 'external/lib'
    )
    assert sub_entry['target_head'] == target_sub_sha
    assert sub_entry['bundle'] is not None
    assert (patch_root / sub_entry['bundle']).is_file()

    output = tmp_path / 'patch-git-sub-standalone-applied'
    proc = subprocess.run(
        [
            sys.executable,
            '-I',
            str(patch_root / 'APPLY_SOURCE_PATCH.py'),
            str(base_archive),
            str(output),
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    assert proc.returncode == 0, proc.stderr
    applied = Path(proc.stdout.strip())
    applied_super_sha = _stdout_text(
        ub.cmd(['git', 'rev-parse', 'HEAD'], cwd=applied, check=True)
    ).strip()
    applied_sub_sha = _stdout_text(
        ub.cmd(
            ['git', 'rev-parse', 'HEAD'],
            cwd=applied / 'external/lib',
            check=True,
        )
    ).strip()
    assert applied_super_sha == target_super_sha
    assert applied_sub_sha == target_sub_sha
    assert (
        applied / 'external/lib/tracked.txt'
    ).read_text() == 'updated git submodule\n'
    status = ub.cmd(['git', 'status', '--short'], cwd=applied, check=True)
    assert _stdout_text(status).strip() == ''


def test_archive_source_patch_source_only_submodule_roundtrip(tmp_path):
    import ubelt as ub

    from git_well.archive_source import apply_source_patch
    from git_well.archive_source import archive_source

    sub_repo = _make_submodule_repo(tmp_path, 'patch_source_only_sub')
    super_repo = _make_repo_with_submodules(
        tmp_path, {'external/lib': sub_repo}
    )
    base_archive = archive_source(
        repo_dpath=super_repo,
        output=tmp_path / 'patch-source-only-base.tar.gz',
        submodule_depth=0,
        verbose=0,
    )

    (sub_repo / 'tracked.txt').write_text('updated submodule\n')
    _commit_all(sub_repo, 'update source-only submodule')
    target_sub_sha = _stdout_text(
        ub.cmd(['git', 'rev-parse', 'HEAD'], cwd=sub_repo, check=True)
    ).strip()
    sub_checkout = super_repo / 'external/lib'
    ub.cmd(['git', 'fetch', str(sub_repo)], cwd=sub_checkout, check=True)
    ub.cmd(['git', 'checkout', target_sub_sha], cwd=sub_checkout, check=True)
    _commit_all(super_repo, 'advance source-only submodule')

    patch_archive = archive_source(
        repo_dpath=super_repo,
        output=tmp_path / 'patch-source-only-update.tar.gz',
        submodule_depth=0,
        patch=base_archive,
        verbose=0,
    )
    applied = apply_source_patch(
        base_archive, patch_archive, tmp_path / 'patch-source-only-applied'
    )
    applied_sub = applied / 'external/lib'
    assert (applied_sub / 'tracked.txt').read_text() == 'updated submodule\n'
    assert not (applied_sub / '.git').exists()


def test_archive_source_recovers_unadvertised_local_submodule_commit(tmp_path):
    import ubelt as ub

    from git_well.archive_source import archive_source

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
    archived_count = _stdout_text(
        ub.cmd(
            ['git', 'rev-list', '--count', 'HEAD'],
            cwd=archived_submodule,
            check=True,
        )
    ).strip()
    assert archived_count == '1'


def test_archive_source_recovers_submodule_commit_from_source_remote(tmp_path):
    import ubelt as ub

    from git_well.archive_source import archive_source

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

    from git_well.archive_source import archive_source

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

    from git_well.archive_source import archive_source

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
            sub_repo.resolve().as_uri(),
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
    from git_well.archive_source import archive_source

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

    from git_well.archive_source import archive_source

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
    from git_well.archive_source import archive_source

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
    from git_well.archive_source import archive_source

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
    from git_well.archive_source import archive_source

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

    from git_well.archive_source._common import SubmoduleStatus
    from git_well.archive_source._policy import _resolve_exclude_submodule_paths

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
    from git_well.archive_source import archive_source

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
