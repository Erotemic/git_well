# Git epoch tutorial 2: recursive submodules with Ambition

This tutorial performs a recursive epoch rollover on
[Ambition](https://github.com/Erotemic/ambition) and all submodules that are
checked out beneath it. The entire write side of the experiment is confined to
one new temporary directory.

The tutorial intentionally does **not** encode Ambition's current submodule
names or layout. It discovers the committed submodule graph at runtime. That
keeps this as a test of the Git epoch mechanism rather than a test tied to one
snapshot of Ambition's dependency tree.

For this sandbox demonstration, every recursively checked-out submodule is
classified as `epoch`. That exercises the hardest recursive path: leaf
repositories are rolled first, parent gitlinks are translated to the child
successor roots, and the top-level Ambition successor root records the resulting
recursive snapshot. Production policy can instead classify individual
submodules as `continuous` or `external`.

GitHub is used only to clone the initial source graph. Before checkpointing,
every participating repository gets a disposable local bare `origin` under the
temporary directory. No command in the rollover pushes to GitHub.

## Prerequisites

Install `git_well` and verify that `git epoch` is available:

```bash
uv pip install -e /path/to/git_well
git epoch --help
```

The setup helper below uses Python 3 only for graph discovery and for invoking
Git commands without fragile shell parsing of nested submodule paths.

## 1. Clone Ambition recursively into a temporary workspace

```bash
TMP_DPATH=$(mktemp -d "${TMPDIR:-/tmp}/git-well-epoch-ambition.XXXXXX")
echo "workspace: $TMP_DPATH"

git -c protocol.file.allow=always clone \
    --single-branch \
    --recurse-submodules \
    https://github.com/Erotemic/ambition.git \
    "$TMP_DPATH/ambition"

cd "$TMP_DPATH/ambition"
test -z "$(git status --porcelain --ignore-submodules=none)"

git submodule status --recursive || true
```

Everything after this point writes only beneath `TMP_DPATH`.

## 2. Turn the clone into a fully local publication sandbox

Submodules normally end up on detached `HEAD`s. A recursive epoch checkpoint
requires each managed child to have a primary branch ref that identifies the
same commit as the parent's old gitlink. The helper below therefore gives every
submodule a local `epoch-active` branch exactly at its currently pinned commit.
It also removes other local child branches so the version-one single-active-
branch rule is satisfied.

The top-level Ambition checkout keeps the branch selected by the initial clone.

For every repository in the recursive graph, the helper then:

- records a stable logical repository id derived from its recursive path;
- preserves the GitHub remote as `upstream`;
- creates a bare active-history remote under `TMP_DPATH/active-remotes`;
- installs that local bare repository as `origin`;
- pushes the one active branch and local tags only to that local bare repo;
- initializes Git epoch configuration against one shared local history store;
- configures every direct child as an `epoch` submodule;
- writes a JSON inventory used by the later assertions.

Create the helper:

```bash
cat > "$TMP_DPATH/setup_epoch_demo.py" <<'PY'
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys


def run(*args, cwd=None, check=True):
    proc = subprocess.run(
        [str(a) for a in args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and proc.returncode:
        raise RuntimeError(
            f"command failed: {args}\n"
            f"cwd={cwd}\n"
            f"stdout={proc.stdout}\n"
            f"stderr={proc.stderr}"
        )
    return proc


def git(repo, *args, check=True):
    return run('git', *args, cwd=repo, check=check)


def out(repo, *args):
    return git(repo, *args).stdout.strip()


def direct_submodules(repo: pathlib.Path):
    gm = repo / '.gitmodules'
    if not gm.exists():
        return []
    proc = git(
        repo,
        'config',
        '-f',
        '.gitmodules',
        '--get-regexp',
        r'^submodule\..*\.path$',
        check=False,
    )
    if proc.returncode == 1:
        return []
    if proc.returncode:
        raise RuntimeError(proc.stderr)
    items = []
    for line in proc.stdout.splitlines():
        key, path = line.split(None, 1)
        match = re.match(r'^submodule\.(.*)\.path$', key)
        if match is None:
            raise AssertionError(key)
        items.append({'name': match.group(1), 'path': path})
    return items


def safe_id(text: str) -> str:
    text = re.sub(r'[^A-Za-z0-9._-]+', '-', text).strip('-')
    return text or 'repository'


def collect(repo: pathlib.Path, rel: pathlib.PurePosixPath, nodes):
    logical_id = 'ambition' if not rel.parts else safe_id(
        'ambition--' + '--'.join(rel.parts)
    )
    node = {
        'repo': repo,
        'relpath': rel.as_posix() if rel.parts else '.',
        'repository': logical_id,
        'children': [],
    }
    nodes.append(node)
    for item in direct_submodules(repo):
        child_repo = repo / item['path']
        if not child_repo.exists():
            raise RuntimeError(f"submodule worktree is missing: {child_repo}")
        child_rel = rel / pathlib.PurePosixPath(item['path'])
        child = collect(child_repo, child_rel, nodes)
        node['children'].append(
            {
                'name': item['name'],
                'path': item['path'],
                'repository': child['repository'],
            }
        )
    return node


def normalize_branch(node, is_root):
    repo = node['repo']

    # Successor roots are real Git commits, so provide a local identity even on
    # machines without global user.name / user.email configuration.
    git(repo, 'config', 'user.name', 'Git Epoch Tutorial')
    git(repo, 'config', 'user.email', 'git-epoch-tutorial@example.com')

    if is_root:
        branch = out(repo, 'branch', '--show-current')
        if not branch:
            raise RuntimeError('top-level Ambition clone has detached HEAD')
    else:
        old_tip = out(repo, 'rev-parse', 'HEAD')
        git(repo, 'checkout', '--detach', old_tip)
        branches = [
            line for line in out(repo, 'for-each-ref', '--format=%(refname:short)', 'refs/heads').splitlines()
            if line
        ]
        for branch in branches:
            git(repo, 'branch', '-D', branch)
        branch = 'epoch-active'
        git(repo, 'branch', branch, old_tip)
        assert out(repo, 'rev-parse', f'refs/heads/{branch}') == old_tip
    node['branch'] = branch


def install_local_origin(node, active_root: pathlib.Path):
    repo = node['repo']
    branch = node['branch']
    remote = active_root / f"{node['repository']}.git"
    run('git', 'init', '--bare', remote)
    run(
        'git',
        '--git-dir',
        remote,
        'symbolic-ref',
        'HEAD',
        f'refs/heads/{branch}',
    )

    existing = out(repo, 'remote').splitlines()
    if 'origin' not in existing:
        raise RuntimeError(f"expected clone remote 'origin' in {repo}")
    if 'upstream' in existing:
        raise RuntimeError(f"refusing to overwrite existing upstream remote in {repo}")

    upstream_url = out(repo, 'remote', 'get-url', 'origin')
    git(repo, 'remote', 'rename', 'origin', 'upstream')
    git(repo, 'remote', 'add', 'origin', remote)

    # This push targets the local bare path directly. There is no GitHub write.
    git(repo, 'push', '-u', 'origin', f'HEAD:refs/heads/{branch}')
    git(repo, 'push', 'origin', '--tags')

    node['upstream_url'] = upstream_url
    node['active_remote'] = str(remote)


def initialize_epoch(node, history_store: pathlib.Path):
    repo = node['repo']
    git(
        repo,
        'epoch',
        'init',
        '--repository',
        node['repository'],
        '--history-store',
        history_store,
        '--branch',
        node['branch'],
        '--config-only',
    )


def main():
    root = pathlib.Path(sys.argv[1]).resolve()
    workspace = pathlib.Path(sys.argv[2]).resolve()
    active_root = workspace / 'active-remotes'
    active_root.mkdir()
    history_store = workspace / 'history.git'

    nodes = []
    root_node = collect(root, pathlib.PurePosixPath(), nodes)

    # Normalize all refs and replace all publication remotes before git epoch
    # is allowed to prepare or publish anything.
    for node in nodes:
        normalize_branch(node, node is root_node)
        install_local_origin(node, active_root)

    # Initialize leaves first. The shared store does not need to exist yet;
    # preparation will create it.
    for node in reversed(nodes):
        initialize_epoch(node, history_store)

    # Each superproject records the policy for its immediate occurrences.
    for node in reversed(nodes):
        for child in node['children']:
            git(
                node['repo'],
                'epoch',
                'configure-submodule',
                child['path'],
                'epoch',
                '--repository',
                child['repository'],
            )

    # First record each repository independently. Cross-repository checks use
    # these values in a second pass.
    for node in nodes:
        repo = node['repo']
        node['old_tip'] = out(repo, 'rev-parse', f"refs/heads/{node['branch']}")
        node['old_tree'] = out(repo, 'rev-parse', f"{node['old_tip']}^{{tree}}")
        node['old_commit_count'] = int(
            out(repo, 'rev-list', '--count', node['old_tip'])
        )

    for node in nodes:
        repo = node['repo']
        remote_tip = run(
            'git',
            '--git-dir',
            node['active_remote'],
            'rev-parse',
            f"refs/heads/{node['branch']}",
        ).stdout.strip()
        assert remote_tip == node['old_tip']

        # Every managed child primary branch must identify the exact gitlink
        # selected by the parent before recursive planning.
        for child in node['children']:
            child_node = next(
                n for n in nodes if n['repository'] == child['repository']
            )
            gitlink = out(repo, 'ls-tree', 'HEAD', child['path']).split()[2]
            assert gitlink == child_node['old_tip'], (
                node['repository'], child['path'], gitlink, child_node['old_tip']
            )

    serializable = []
    for node in nodes:
        item = dict(node)
        item['repo'] = str(node['repo'])
        serializable.append(item)

    inventory = {
        'root': root_node['repository'],
        'history_store': str(history_store),
        'nodes': serializable,
    }
    inventory_fpath = workspace / 'inventory-before.json'
    inventory_fpath.write_text(json.dumps(inventory, indent=2) + '\n')

    print(f"repositories: {len(nodes)}")
    for node in nodes:
        print(
            f"  {node['repository']}: {node['relpath']} "
            f"[{node['branch']}] {node['old_tip'][:12]}"
        )
    print(f"inventory: {inventory_fpath}")


if __name__ == '__main__':
    main()
PY

python "$TMP_DPATH/setup_epoch_demo.py" \
    "$TMP_DPATH/ambition" \
    "$TMP_DPATH"
```

Inspect the resulting local-only topology:

```bash
cat "$TMP_DPATH/inventory-before.json"

git -C "$TMP_DPATH/ambition" remote -v
find "$TMP_DPATH/active-remotes" -maxdepth 1 -type d -name '*.git' -print
```

At this point every publication `origin` points beneath `TMP_DPATH`. The
original clone URL is retained only as an `upstream` remote.

## 3. Review the recursive checkpoint plan

Planning walks the configured epoch graph leaf-first. Save the exact plan that
will later be applied and published:

```bash
cd "$TMP_DPATH/ambition"

git epoch plan \
    --recursive \
    --bundle \
    --summary \
    -o "$TMP_DPATH/checkpoint.yaml"
```

The printed order should list deepest submodules before their parents and
Ambition last. Parent entries should show gitlink translations from each child
old tip to that child's predicted successor root.

Planning is read-only. Confirm that every repository and every disposable active
remote still points at its recorded old tip:

```bash
python - "$TMP_DPATH/inventory-before.json" <<'PY'
import json
import pathlib
import subprocess
import sys

inventory = json.loads(pathlib.Path(sys.argv[1]).read_text())
for node in inventory['nodes']:
    repo = pathlib.Path(node['repo'])
    branch = node['branch']
    old = node['old_tip']
    local = subprocess.check_output(
        ['git', '-C', repo, 'rev-parse', f'refs/heads/{branch}'],
        text=True,
    ).strip()
    remote = subprocess.check_output(
        [
            'git', '--git-dir', node['active_remote'],
            'rev-parse', f'refs/heads/{branch}',
        ],
        text=True,
    ).strip()
    assert local == old, (node['repository'], local, old)
    assert remote == old, (node['repository'], remote, old)
print('PASS: recursive planning changed no active refs')
PY
```

## 4. Prepare every retiring epoch without publishing

Preparation archives and verifies each repository in leaf-first plan order, then
materializes the successor objects needed for the recursive parent trees.

```bash
cd "$TMP_DPATH/ambition"
git epoch apply "$TMP_DPATH/checkpoint.yaml"
```

The shared history store should now contain epoch-zero namespaces for Ambition
and every managed submodule:

```bash
git --git-dir="$TMP_DPATH/history.git" show-ref
git --git-dir="$TMP_DPATH/history.git" fsck --full

git epoch inspect
```

Run deep verification for every managed repository:

```bash
python - "$TMP_DPATH/inventory-before.json" <<'PY'
import json
import pathlib
import subprocess
import sys

inventory = json.loads(pathlib.Path(sys.argv[1]).read_text())
for node in inventory['nodes']:
    print(f"\n=== verify {node['repository']} ===")
    subprocess.run(
        ['git', 'epoch', 'verify', '--deep'],
        cwd=node['repo'],
        check=True,
    )
PY
```

Preparation still must not change any active branch or disposable remote ref:

```bash
python - "$TMP_DPATH/inventory-before.json" <<'PY'
import json
import pathlib
import subprocess
import sys

inventory = json.loads(pathlib.Path(sys.argv[1]).read_text())
for node in inventory['nodes']:
    branch = node['branch']
    old = node['old_tip']
    local = subprocess.check_output(
        ['git', '-C', node['repo'], 'rev-parse', f'refs/heads/{branch}'],
        text=True,
    ).strip()
    remote = subprocess.check_output(
        [
            'git', '--git-dir', node['active_remote'],
            'rev-parse', f'refs/heads/{branch}',
        ],
        text=True,
    ).strip()
    assert local == old
    assert remote == old
print('PASS: all recursive epochs are archived but not published')
PY
```

## 5. Publish leaf-first into the local active remotes

This is the destructive phase, but every active publication remote was replaced
with a bare repository in `TMP_DPATH` before the plan was built.

```bash
cd "$TMP_DPATH/ambition"
git epoch publish --plan "$TMP_DPATH/checkpoint.yaml"
```

`publish` processes the prepared entries in plan order. Managed children move to
their successor roots before the parent that points at those roots is
published.

## 6. Verify the recursive rollover independently

The checker below proves several properties for every participating repository:

- its active branch moved away from the retired tip;
- the new active commit is a root commit;
- its disposable bare remote points at the same successor root;
- an ordinary fresh clone from that local active remote cannot obtain the old
  tip;
- every parent gitlink now equals the corresponding child successor root;
- `git epoch verify --deep` passes for each repository.

```bash
cat > "$TMP_DPATH/check_epoch_demo.py" <<'PY'
from __future__ import annotations

import json
import pathlib
import subprocess
import sys


def run(*args, cwd=None, check=True):
    proc = subprocess.run(
        [str(a) for a in args],
        cwd=cwd,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    if check and proc.returncode:
        raise RuntimeError(
            f"command failed: {args}\n"
            f"cwd={cwd}\n"
            f"stdout={proc.stdout}\n"
            f"stderr={proc.stderr}"
        )
    return proc


def git(repo, *args, check=True):
    return run('git', *args, cwd=repo, check=check)


def out(repo, *args):
    return git(repo, *args).stdout.strip()


def main():
    inventory_fpath = pathlib.Path(sys.argv[1])
    workspace = pathlib.Path(sys.argv[2]).resolve()
    inventory = json.loads(inventory_fpath.read_text())
    nodes = inventory['nodes']
    by_id = {node['repository']: node for node in nodes}

    fresh_root = workspace / 'fresh-active-clones'
    fresh_root.mkdir()

    for node in nodes:
        repo = pathlib.Path(node['repo'])
        branch = node['branch']
        old = node['old_tip']
        new = out(repo, 'rev-parse', f'refs/heads/{branch}')
        new_tree = out(repo, 'rev-parse', f'{new}^{{tree}}')
        parents = out(repo, 'rev-list', '--parents', '-n', '1', new).split()

        assert new != old, node['repository']
        assert parents == [new], (node['repository'], parents)

        # Repositories with no epochized children preserve the raw tree exactly.
        # Parents may have a different raw tree because child gitlinks are
        # translated to successor roots.
        if not node['children']:
            assert new_tree == node['old_tree'], node['repository']

        remote_tip = run(
            'git',
            '--git-dir',
            node['active_remote'],
            'rev-parse',
            f'refs/heads/{branch}',
        ).stdout.strip()
        assert remote_tip == new, node['repository']

        for child in node['children']:
            child_node = by_id[child['repository']]
            child_new = out(
                child_node['repo'],
                'rev-parse',
                f"refs/heads/{child_node['branch']}",
            )
            gitlink = out(repo, 'ls-tree', new, child['path']).split()[2]
            assert gitlink == child_new, (
                node['repository'], child['path'], gitlink, child_new
            )

        fresh = fresh_root / node['repository']
        run(
            'git',
            'clone',
            '--no-local',
            '--branch',
            branch,
            node['active_remote'],
            fresh,
        )
        assert out(fresh, 'rev-parse', 'HEAD') == new
        old_probe = git(fresh, 'cat-file', '-e', f'{old}^{{commit}}', check=False)
        assert old_probe.returncode != 0, (
            f"retired tip leaked into ordinary clone for {node['repository']}: {old}"
        )

        subprocess.run(
            ['git', 'epoch', 'verify', '--deep'],
            cwd=repo,
            check=True,
        )

        node['new_tip'] = new
        node['new_tree'] = new_tree

    output = workspace / 'inventory-after.json'
    output.write_text(json.dumps(inventory, indent=2) + '\n')
    print(f"PASS: verified {len(nodes)} recursively managed repositories")
    print(f"post-rollover inventory: {output}")


if __name__ == '__main__':
    main()
PY

python "$TMP_DPATH/check_epoch_demo.py" \
    "$TMP_DPATH/inventory-before.json" \
    "$TMP_DPATH"
```

Inspect the before/after inventory if desired:

```bash
cat "$TMP_DPATH/inventory-after.json"
```

## 7. Inspect the new Ambition tree

The top-level repository now begins at a new root. Unlike the simple Ubelt case,
the raw Ambition tree can differ across the boundary because managed submodule
gitlinks have changed from each child retired tip to that child's successor
root.

```bash
cd "$TMP_DPATH/ambition"

git log --graph --decorate --oneline -10
git submodule status --recursive
```

The source snapshot is preserved recursively: ordinary files are unchanged, and
each translated gitlink selects the successor child root that represents the
same child source snapshot.

## 8. Reconstruct Ambition and its managed submodules

Build a recursive archaeology checkout:

```bash
cd "$TMP_DPATH/ambition"

git epoch reconstruct \
    --recursive \
    -o "$TMP_DPATH/reconstructed"
```

The top-level replacement refs reconnect the Ambition successor root to the
retired Ambition tip:

```bash
git -C "$TMP_DPATH/reconstructed" replace -l

git -C "$TMP_DPATH/reconstructed" \
    log --graph --decorate --oneline --all -30
```

The recursive reconstruction also creates reconstructed child repositories
under `.git-epoch-repositories` and configures submodule URL overrides to point
at them:

```bash
find "$TMP_DPATH/reconstructed/.git-epoch-repositories" \
    -maxdepth 2 \
    -type f \
    -name reconstruction.yaml \
    -print

cat "$TMP_DPATH/reconstructed/reconstruction.yaml"
```

Confirm that the original Ambition tip is available in the reconstructed object
store even though it is absent from an ordinary active clone:

```bash
OLD_AMBITION=$(python - "$TMP_DPATH/inventory-before.json" <<'PY'
import json
import pathlib
import sys
inventory = json.loads(pathlib.Path(sys.argv[1]).read_text())
root = inventory['root']
node = next(n for n in inventory['nodes'] if n['repository'] == root)
print(node['old_tip'])
PY
)

test "$(
    git -C "$TMP_DPATH/reconstructed" cat-file -t "$OLD_AMBITION"
)" = commit

echo 'PASS: retired Ambition tip is available through reconstruction'
```

## 9. What this demonstrated

If all assertions passed, this exercised the recursive epoch mechanism against
the real Ambition repository graph while keeping every write local:

- Ambition and its recursive submodules were cloned from their real Git state;
- all publication remotes were replaced with temporary bare repositories before
  checkpointing;
- every child primary branch was pinned to the exact old gitlink selected by its
  parent;
- recursive planning ordered child repositories before parents;
- preparation archived and verified every retiring repository before any active
  ref moved;
- publication moved child active refs before the parent refs that depend on
  them;
- each parent successor tree translated managed gitlinks to the matching child
  successor roots;
- ordinary fresh clones of every active repository cannot resolve the retired
  tips;
- the shared history store retains those retired objects and refs;
- recursive reconstruction restores archaeology across the Ambition and child
  epoch boundaries.

The all-`epoch` policy in this tutorial is deliberately aggressive because the
purpose is to exercise recursive translation. For an actual Ambition rollover,
classify third-party upstream dependencies as `external` and internally managed
repositories that should retain continuous history as `continuous` before
building the production checkpoint plan.

The workspace is left intact for inspection. Remove it when finished:

```bash
rm -rf "$TMP_DPATH"
```
