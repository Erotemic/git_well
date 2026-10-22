"""Unit coverage for opt-in checked-out submodule revisions.

These checks use only the Python standard library plus pytest. The larger
source-archive roundtrip tests in test_cli.py also exercise real Git clones.
"""

from types import SimpleNamespace

import pytest


def test_submodule_revision_checkout_walks_selected_nested_tree(tmp_path, monkeypatch):
    from git_well.archive_source import _repo

    old_parent_sha = 'a' * 40
    checkout_parent_sha = 'b' * 40
    old_child_sha = 'c' * 40
    checkout_child_sha = 'd' * 40
    root = SimpleNamespace(
        working_tree_dir=str(tmp_path),
        head=SimpleNamespace(commit=SimpleNamespace(hexsha='1' * 40)),
    )
    parent = SimpleNamespace(
        working_tree_dir=str(tmp_path / 'child'),
        head=SimpleNamespace(commit=SimpleNamespace(hexsha=checkout_parent_sha)),
    )
    child = SimpleNamespace(
        working_tree_dir=str(tmp_path / 'child' / 'nested'),
        head=SimpleNamespace(commit=SimpleNamespace(hexsha=checkout_child_sha)),
    )
    repos = {
        tmp_path / 'child': parent,
        tmp_path / 'child' / 'nested': child,
    }
    requested_trees = []

    def gitlinks(repo, treeish):
        requested_trees.append((repo, treeish))
        if repo is root and treeish == 'HEAD':
            return [(old_parent_sha, 'child')]
        if repo is parent and treeish == checkout_parent_sha:
            return [(old_child_sha, 'nested')]
        return []

    def mappings(repo, treeish):
        if repo is root:
            return {'child'}
        if repo is parent and treeish == checkout_parent_sha:
            return {'nested'}
        return set()

    monkeypatch.setattr(_repo, '_committed_gitlinks', gitlinks)
    monkeypatch.setattr(_repo, '_committed_gitmodule_paths', mappings)
    monkeypatch.setattr(_repo, '_open_exact_repo', lambda path: repos.get(path))
    monkeypatch.setattr(
        _repo, '_repo_has_commit',
        lambda repo, sha: repo.head.commit.hexsha == sha,
    )

    strict = _repo._submodule_status(root)
    assert [(item.path, item.sha) for item in strict] == [
        ('child', old_parent_sha),
    ]
    assert strict[0].parent_gitlink_sha is None

    requested_trees.clear()
    selected = _repo._submodule_status(root, submodule_revision='checkout')
    assert [(item.path, item.sha, item.parent_gitlink_sha) for item in selected] == [
        ('child', checkout_parent_sha, old_parent_sha),
        ('child/nested', checkout_child_sha, old_child_sha),
    ]
    assert (parent, checkout_parent_sha) in requested_trees
    assert (parent, old_parent_sha) not in requested_trees

    with pytest.raises(ValueError, match='submodule_revision'):
        _repo._submodule_status(root, submodule_revision='something-else')


def test_manifest_records_mismatched_parent_gitlink(tmp_path):
    from git_well.archive_source._common import SubmoduleArchiveDecision
    from git_well.archive_source._common import SubmoduleStatus
    from git_well.archive_source._io import _write_manifest

    sha = 'b' * 40
    parent_sha = 'a' * 40
    info = SubmoduleStatus(
        '+', sha, 'child', f'+{sha} child', parent_gitlink_sha=parent_sha
    )
    manifest = tmp_path / 'manifest.txt'
    _write_manifest(
        manifest=manifest,
        repo_root=tmp_path,
        archive_path=tmp_path / 'result.tar.gz',
        repo_name='demo',
        prefix='demo',
        timestamp='2026-10-22',
        head_sha='f' * 40,
        short_sha='f' * 12,
        include_git_history=True,
        clone_depth=None,
        branch_refs=None,
        branches=(),
        submodule_decisions=[
            SubmoduleArchiveDecision(
                info=info, omitted=False, depth=None,
                mode='full-git-checkout', reason='included',
            ),
        ],
        submodule_revision='checkout',
        exclude_path_selectors=(),
        excluded_worktree_paths=(),
        unmatched_exclude_path_selectors=(),
        excluded_worktree_bytes=0,
        history_blobs='full',
        promisor_prune_results=(),
        redact_local_paths=True,
    )
    text = manifest.read_text()
    assert 'Submodule revision policy: checkout' in text
    assert f'  commit: {sha}' in text
    assert f'  parent gitlink: {parent_sha}' in text
