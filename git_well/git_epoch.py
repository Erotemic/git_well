#!/usr/bin/env python3
# PYTHON_ARGCOMPLETE_OK
from __future__ import annotations

from typing import Any

import kwconf
import yaml

from git_well.epoch import (
    abort_latest,
    abort_plan,
    apply_sandbox,
    apply_plan,
    build_plan,
    checkpoint,
    configure_submodule,
    create_sandbox,
    gc_history_store,
    history_store_stats,
    initialize_config,
    inspect_sandbox,
    inspect_manifest,
    load_plan,
    plan_sandbox,
    plan_summary,
    publish_latest,
    publish_sandbox,
    publish_plan,
    reconstruct,
    run_sandbox,
    sandbox_stats,
    save_plan,
    status,
    verify,
    verify_sandbox,
)


def _print_yaml(data: Any) -> None:
    print(yaml.safe_dump(data, sort_keys=False, width=100), end='')


class EpochInitCLI(kwconf.Config):
    """Initialize epoch management and prepare the first rollover."""

    __command__ = 'init'

    repo_dpath = kwconf.Value('.', help='repository to initialize')
    repository = kwconf.Value(None, help='stable logical repository id')
    history_store = kwconf.Value(
        None,
        help='bare Git history-store path or ordinary Git remote URL',
    )
    branch = kwconf.Value(None, help='primary branch; defaults to current branch')
    remote = kwconf.Value('origin', help='active publication remote')
    config_only = kwconf.Value(
        False,
        isflag=True,
        help='write configuration only; do not archive/prepare epoch zero',
    )
    recursive = kwconf.Value(
        False,
        isflag=True,
        short_alias=['r'],
        help='roll configured epoch submodules leaf-first',
    )
    publish = kwconf.Value(
        False,
        isflag=True,
        help='publish successor refs after archival verification',
    )
    bundle = kwconf.Value(
        False,
        isflag=True,
        help='create and verify an immutable bundle backup',
    )
    bundle_dir = kwconf.Value(None, help='bundle backup directory')
    no_fresh_clone = kwconf.Value(
        False,
        isflag=True,
        help='skip ordinary-clone acceptance validation after publication',
    )

    @classmethod
    def main(cls, argv: list[str] | str | bool | None = True, **kwargs: Any) -> Any:
        config = cls.cli(argv=argv, data=kwargs)
        if not config.history_store:
            raise ValueError('--history-store is required')
        init = initialize_config(
            config.repo_dpath,
            repository_id=config.repository,
            history_store=config.history_store,
            primary_branch=config.branch,
            active_remote=config.remote,
        )
        if config.config_only:
            _print_yaml(init)
            return init
        result = checkpoint(
            config.repo_dpath,
            recursive=config.recursive,
            publish=config.publish,
            bundle=config.bundle,
            bundle_dir=config.bundle_dir,
            fresh_clone=not config.no_fresh_clone,
        )
        printable = {k: v for k, v in result.items() if k != 'plan'}
        printable['plan_digest'] = result['plan']['digest']
        _print_yaml(printable)
        return result


class EpochConfigureSubmoduleCLI(kwconf.Config):
    """Set the policy and logical identity for a current submodule."""

    __command__ = 'configure-submodule'

    path = kwconf.Value(None, position=1, help='submodule path')
    policy = kwconf.Value(
        None,
        position=2,
        choices=['epoch', 'continuous', 'external'],
        help='repository policy',
    )
    repository = kwconf.Value(None, help='logical repository id for the child')
    repo_dpath = kwconf.Value('.', help='superproject repository')

    @classmethod
    def main(cls, argv: list[str] | str | bool | None = True, **kwargs: Any) -> Any:
        config = cls.cli(argv=argv, data=kwargs)
        result = configure_submodule(
            config.repo_dpath,
            config.path,
            policy=config.policy,
            repository_id=config.repository,
        )
        _print_yaml(result)
        return result


class EpochStatusCLI(kwconf.Config):
    """Report active-history and archive state."""

    __command__ = 'status'

    repo_dpath = kwconf.Value('.', help='repository to inspect')

    @classmethod
    def main(cls, argv: list[str] | str | bool | None = True, **kwargs: Any) -> Any:
        config = cls.cli(argv=argv, data=kwargs)
        result = status(config.repo_dpath)
        _print_yaml(result)
        return result


class EpochPlanCLI(kwconf.Config):
    """Create a stale-detectable checkpoint plan without changing refs."""

    __command__ = 'plan'

    repo_dpath = kwconf.Value('.', help='repository to plan')
    recursive = kwconf.Value(
        False,
        isflag=True,
        short_alias=['r'],
        help='plan configured epoch submodules leaf-first',
    )
    bundle = kwconf.Value(False, isflag=True, help='include bundle backups')
    bundle_dir = kwconf.Value(None, help='bundle backup directory')
    output = kwconf.Value(None, short_alias=['o'], help='write plan YAML to this path')
    summary = kwconf.Value(
        False,
        isflag=True,
        help='print a human-readable summary instead of YAML',
    )

    @classmethod
    def main(cls, argv: list[str] | str | bool | None = True, **kwargs: Any) -> Any:
        config = cls.cli(argv=argv, data=kwargs)
        plan = build_plan(
            config.repo_dpath,
            recursive=config.recursive,
            bundle=config.bundle,
            bundle_dir=config.bundle_dir,
        )
        if config.output:
            save_plan(plan, config.output)
        if config.summary:
            print(plan_summary(plan))
        else:
            _print_yaml(plan)
        return plan


class EpochApplyCLI(kwconf.Config):
    """Archive and verify a checkpoint plan, optionally publishing it."""

    __command__ = 'apply'

    plan = kwconf.Value(None, position=1, help='checkpoint plan YAML')
    publish = kwconf.Value(
        False,
        isflag=True,
        help='also rewrite active refs after archival verification',
    )
    no_fresh_clone = kwconf.Value(
        False,
        isflag=True,
        help='skip ordinary-clone acceptance validation after publication',
    )

    @classmethod
    def main(cls, argv: list[str] | str | bool | None = True, **kwargs: Any) -> Any:
        config = cls.cli(argv=argv, data=kwargs)
        result = apply_plan(
            config.plan,
            publish=config.publish,
            fresh_clone=not config.no_fresh_clone,
        )
        _print_yaml(result)
        return result


class EpochCheckpointCLI(kwconf.Config):
    """Plan and prepare a checkpoint in one command."""

    __command__ = 'checkpoint'

    repo_dpath = kwconf.Value('.', help='repository to checkpoint')
    recursive = kwconf.Value(
        False,
        isflag=True,
        short_alias=['r'],
        help='checkpoint configured epoch submodules leaf-first',
    )
    publish = kwconf.Value(
        False,
        isflag=True,
        help='publish successor refs after preparation',
    )
    bundle = kwconf.Value(False, isflag=True, help='create bundle backups')
    bundle_dir = kwconf.Value(None, help='bundle backup directory')
    no_fresh_clone = kwconf.Value(
        False,
        isflag=True,
        help='skip ordinary-clone acceptance validation after publication',
    )
    plan_output = kwconf.Value(None, help='also save the generated plan here')

    @classmethod
    def main(cls, argv: list[str] | str | bool | None = True, **kwargs: Any) -> Any:
        config = cls.cli(argv=argv, data=kwargs)
        plan = build_plan(
            config.repo_dpath,
            recursive=config.recursive,
            bundle=config.bundle,
            bundle_dir=config.bundle_dir,
        )
        if config.plan_output:
            save_plan(plan, config.plan_output)
        result = apply_plan(
            plan,
            publish=config.publish,
            fresh_clone=not config.no_fresh_clone,
        )
        _print_yaml(result)
        return result


class EpochPublishCLI(kwconf.Config):
    """Publish a prepared transaction."""

    __command__ = 'publish'

    plan = kwconf.Value(None, help='explicit checkpoint plan; otherwise use latest prepared')
    repo_dpath = kwconf.Value('.', help='repository containing the prepared transaction')
    no_fresh_clone = kwconf.Value(
        False,
        isflag=True,
        help='skip ordinary-clone acceptance validation',
    )

    @classmethod
    def main(cls, argv: list[str] | str | bool | None = True, **kwargs: Any) -> Any:
        config = cls.cli(argv=argv, data=kwargs)
        if config.plan:
            result = publish_plan(
                load_plan(config.plan),
                fresh_clone=not config.no_fresh_clone,
            )
        else:
            result = publish_latest(
                config.repo_dpath,
                fresh_clone=not config.no_fresh_clone,
            )
        _print_yaml(result)
        return result


class EpochAbortCLI(kwconf.Config):
    """Abort a prepared transaction before any successor is published."""

    __command__ = 'abort'

    plan = kwconf.Value(None, help='explicit checkpoint plan; otherwise use latest prepared')
    repo_dpath = kwconf.Value('.', help='repository containing the prepared transaction')

    @classmethod
    def main(cls, argv: list[str] | str | bool | None = True, **kwargs: Any) -> Any:
        config = cls.cli(argv=argv, data=kwargs)
        if config.plan:
            result = abort_plan(load_plan(config.plan))
        else:
            result = abort_latest(config.repo_dpath)
        _print_yaml(result)
        return result


class EpochVerifyCLI(kwconf.Config):
    """Verify manifest refs and optionally run full archive fsck."""

    __command__ = 'verify'

    repo_dpath = kwconf.Value('.', help='repository to verify')
    deep = kwconf.Value(False, isflag=True, help='fetch archives and run git fsck')

    @classmethod
    def main(cls, argv: list[str] | str | bool | None = True, **kwargs: Any) -> Any:
        config = cls.cli(argv=argv, data=kwargs)
        result = verify(config.repo_dpath, deep=config.deep)
        _print_yaml(result)
        return result


class EpochReconstructCLI(kwconf.Config):
    """Build an archaeology checkout joined with derived replace refs."""

    __command__ = 'reconstruct'

    repo_dpath = kwconf.Value('.', help='active repository')
    output = kwconf.Value(None, short_alias=['o'], help='destination checkout')
    recursive = kwconf.Value(
        False,
        isflag=True,
        short_alias=['r'],
        help='also reconstruct configured epoch submodules',
    )

    @classmethod
    def main(cls, argv: list[str] | str | bool | None = True, **kwargs: Any) -> Any:
        config = cls.cli(argv=argv, data=kwargs)
        result = reconstruct(
            config.repo_dpath,
            output=config.output,
            recursive=config.recursive,
        )
        _print_yaml(result)
        return result


class EpochInspectCLI(kwconf.Config):
    """Print the versioned archive manifest."""

    __command__ = 'inspect'

    repo_dpath = kwconf.Value('.', help='repository to inspect')

    @classmethod
    def main(cls, argv: list[str] | str | bool | None = True, **kwargs: Any) -> Any:
        config = cls.cli(argv=argv, data=kwargs)
        result = inspect_manifest(config.repo_dpath)
        _print_yaml(result)
        return result


class EpochStatsCLI(kwconf.Config):
    """Report physical active-history and archived-epoch sizes."""

    __command__ = 'stats'

    repo_dpath = kwconf.Value('.', help='managed repository')

    @classmethod
    def main(cls, argv: list[str] | str | bool | None = True, **kwargs: Any) -> Any:
        config = cls.cli(argv=argv, data=kwargs)
        result = history_store_stats(config.repo_dpath)
        _print_yaml(result)
        return result


class EpochGcCLI(kwconf.Config):
    """Garbage-collect a local history store without dropping epoch refs."""

    __command__ = 'gc'

    repo_dpath = kwconf.Value('.', help='managed repository')

    @classmethod
    def main(cls, argv: list[str] | str | bool | None = True, **kwargs: Any) -> Any:
        config = cls.cli(argv=argv, data=kwargs)
        result = gc_history_store(config.repo_dpath)
        _print_yaml(result)
        return result


class EpochSandboxCLI(kwconf.ModalCLI):
    """Rehearse Git epoch operations against contained local remotes."""

    __command__ = 'sandbox'


@EpochSandboxCLI.register
class EpochSandboxCreateCLI(kwconf.Config):
    """Create a disposable local publication sandbox from a repository."""

    __command__ = 'create'

    repo_dpath = kwconf.Value('.', help='source repository to rehearse')
    output = kwconf.Value(
        None,
        short_alias=['o'],
        help='sandbox directory; defaults to a new temporary directory',
    )
    recursive = kwconf.Value(
        False,
        isflag=True,
        short_alias=['r'],
        help='clone and configure initialized submodules recursively',
    )
    all_submodules = kwconf.Value(
        None,
        alias=['all-submodules'],
        choices=['epoch', 'continuous', 'external'],
        help='override every discovered submodule policy in the sandbox',
    )

    @classmethod
    def main(cls, argv: list[str] | str | bool | None = True, **kwargs: Any) -> Any:
        config = cls.cli(argv=argv, data=kwargs)
        result = create_sandbox(
            config.repo_dpath,
            output=config.output,
            recursive=config.recursive,
            all_submodules=config.all_submodules,
        )
        _print_yaml(result)
        return result


@EpochSandboxCLI.register
class EpochSandboxRunCLI(kwconf.Config):
    """Run plan, prepare, publish, and deep verification in a sandbox."""

    __command__ = 'run'

    sandbox = kwconf.Value(None, position=1, help='sandbox directory')
    bundle = kwconf.Value(
        False,
        isflag=True,
        help='create and verify immutable bundle backups during the rehearsal',
    )

    @classmethod
    def main(cls, argv: list[str] | str | bool | None = True, **kwargs: Any) -> Any:
        config = cls.cli(argv=argv, data=kwargs)
        result = run_sandbox(config.sandbox, bundle=config.bundle)
        _print_yaml(result)
        return result


@EpochSandboxCLI.register
class EpochSandboxPlanCLI(kwconf.Config):
    """Build and save the real recursive epoch plan inside a sandbox."""

    __command__ = 'plan'

    sandbox = kwconf.Value(None, position=1, help='sandbox directory')
    bundle = kwconf.Value(
        False,
        isflag=True,
        help='create immutable bundle backups during preparation',
    )
    summary = kwconf.Value(
        False,
        isflag=True,
        help='print the human-readable checkpoint summary',
    )

    @classmethod
    def main(cls, argv: list[str] | str | bool | None = True, **kwargs: Any) -> Any:
        config = cls.cli(argv=argv, data=kwargs)
        result = plan_sandbox(config.sandbox, bundle=config.bundle)
        if config.summary:
            print(plan_summary(result['plan']))
            print(f"\nSaved sandbox plan: {result['plan_path']}")
        else:
            _print_yaml({k: v for k, v in result.items() if k != 'plan'})
        return result


@EpochSandboxCLI.register
class EpochSandboxApplyCLI(kwconf.Config):
    """Archive and verify the saved sandbox plan without publishing."""

    __command__ = 'apply'

    sandbox = kwconf.Value(None, position=1, help='sandbox directory')

    @classmethod
    def main(cls, argv: list[str] | str | bool | None = True, **kwargs: Any) -> Any:
        config = cls.cli(argv=argv, data=kwargs)
        result = apply_sandbox(config.sandbox)
        _print_yaml(result)
        return result


@EpochSandboxCLI.register
class EpochSandboxPublishCLI(kwconf.Config):
    """Publish the prepared plan only after rechecking sandbox containment."""

    __command__ = 'publish'

    sandbox = kwconf.Value(None, position=1, help='sandbox directory')
    no_fresh_clone = kwconf.Value(
        False,
        isflag=True,
        help='skip ordinary-clone acceptance validation',
    )

    @classmethod
    def main(cls, argv: list[str] | str | bool | None = True, **kwargs: Any) -> Any:
        config = cls.cli(argv=argv, data=kwargs)
        result = publish_sandbox(
            config.sandbox,
            fresh_clone=not config.no_fresh_clone,
        )
        _print_yaml(result)
        return result


@EpochSandboxCLI.register
class EpochSandboxVerifyCLI(kwconf.Config):
    """Verify a sandbox after manual or automatic publication."""

    __command__ = 'verify'

    sandbox = kwconf.Value(None, position=1, help='sandbox directory')

    @classmethod
    def main(cls, argv: list[str] | str | bool | None = True, **kwargs: Any) -> Any:
        config = cls.cli(argv=argv, data=kwargs)
        result = verify_sandbox(config.sandbox)
        _print_yaml(result)
        return result


@EpochSandboxCLI.register
class EpochSandboxStatsCLI(kwconf.Config):
    """Report sandbox archive, epoch, active-remote, and package sizes."""

    __command__ = 'stats'

    sandbox = kwconf.Value(None, position=1, help='sandbox directory')
    source_archive = kwconf.Value(
        False,
        isflag=True,
        alias=['source-archive'],
        help='build and measure a full-history active source tar.gz',
    )

    @classmethod
    def main(cls, argv: list[str] | str | bool | None = True, **kwargs: Any) -> Any:
        config = cls.cli(argv=argv, data=kwargs)
        result = sandbox_stats(
            config.sandbox,
            source_archive=bool(config.source_archive),
        )
        _print_yaml(result)
        return result


@EpochSandboxCLI.register
class EpochSandboxInspectCLI(kwconf.Config):
    """Show sandbox topology and verify publication containment."""

    __command__ = 'inspect'

    sandbox = kwconf.Value(None, position=1, help='sandbox directory')

    @classmethod
    def main(cls, argv: list[str] | str | bool | None = True, **kwargs: Any) -> Any:
        config = cls.cli(argv=argv, data=kwargs)
        result = inspect_sandbox(config.sandbox)
        _print_yaml(result)
        return result


class GitEpochModalCLI(kwconf.ModalCLI):
    """Bound active Git history while preserving exact archived epochs."""

    __command__ = 'epoch'

    init = EpochInitCLI
    configure_submodule = EpochConfigureSubmoduleCLI
    status = EpochStatusCLI
    plan = EpochPlanCLI
    apply = EpochApplyCLI
    checkpoint = EpochCheckpointCLI
    publish = EpochPublishCLI
    abort = EpochAbortCLI
    verify = EpochVerifyCLI
    reconstruct = EpochReconstructCLI
    inspect = EpochInspectCLI
    stats = EpochStatsCLI
    gc = EpochGcCLI


GitEpochModalCLI.register(EpochSandboxCLI)


__cli__ = GitEpochModalCLI


def main(argv=None):
    """Console-script entry point with a process-style exit code."""
    result = __cli__.main(argv=argv)
    if isinstance(result, int):
        return result
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
