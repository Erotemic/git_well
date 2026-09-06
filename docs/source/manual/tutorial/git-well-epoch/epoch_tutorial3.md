# Git epoch tutorial 3: production rollover with a remote history store

This tutorial is the production counterpart to the local sandbox rehearsal. It
uses `Erotemic/ambition-history` as the shared cold-history repository and keeps
current development in the existing Ambition repositories.

Do not copy the sandbox's `--all-submodules=epoch` policy into production. Decide
which submodules are `epoch`, `continuous`, or `external` first. The current
Ambition occurrences that need an explicit decision are:

| Path | Suggested stable repository ID |
| --- | --- |
| `tools/ambition_music_renderer` | `ambition_music_renderer` |
| `tools/ambition_sfx_renderer` | `ambition_sfx_renderer` |
| `tools/ambition_sprite2d_renderer` | `ambition_sprite2d_renderer` |
| `dev/ambition_dev_measurements` | `ambition_dev_measurements` |
| `game/ambition_map_assets` | `ambition_map_assets` |

Create the GitHub repository `Erotemic/ambition-history` before continuing. It
may have an ordinary `main` README; Git epoch stores archival refs under
`refs/epochs/...` and its versioned manifest under `refs/meta/main`.

Define the shared remote identities once:

```bash
HISTORY_STORE_ID=ambition-history
HISTORY_PUSH_URL=git@github.com:Erotemic/ambition-history.git
HISTORY_FETCH_URL=https://github.com/Erotemic/ambition-history.git
HISTORY_BROWSE_URL=https://github.com/Erotemic/ambition-history
```

## 1. Finish each epoch-managed child first

For every child that will use `epoch` policy, make sure its intended primary
branch exactly matches the gitlink that Ambition should preserve. Then initialize
it against the shared history store. For example:

```bash
cd "$HOME/code/ambition/tools/ambition_music_renderer"

git epoch init \
    --repository ambition_music_renderer \
    --history-store "$HISTORY_PUSH_URL" \
    --history-store-id "$HISTORY_STORE_ID" \
    --public-history-url "$HISTORY_FETCH_URL" \
    --public-history-browse-url "$HISTORY_BROWSE_URL" \
    --branch main \
    --config-only

git diff -- .git-epoch.yaml
git add .git-epoch.yaml
git commit -m "Record Git epoch history location"
git push origin main
```

Repeat only for the children you intend to epoch-manage. `.git-epoch.yaml` is
tracked public metadata. `.git/epoch/config.yaml` remains machine-local.

Before the production plan, synchronize the complete active branch/tag namespace
for every epoch-managed repository. This does not merge or rewrite branches; it
only makes the remote tips locally available for exact archival:

```bash
git fetch --prune --tags origin \
    '+refs/heads/*:refs/remotes/origin/*'
```

If a child was checked out detached, do not invent a different state merely to
obtain a branch. The child's `main`, its published `origin/main`, and the parent
gitlink must all identify the intended retiring state before the recursive plan
is accepted. Auxiliary remote branches may remain present at this stage; the
explicit retirement plan below archives them under their original branch names
before publication removes them from the active remote.

## 2. Update the Ambition superproject

After the managed children have committed their public locators, first update
Ambition's gitlinks to those exact child commits and return the superproject to
a clean committed state. `git epoch init` deliberately refuses a dirty source
repository:

```bash
cd "$HOME/code/ambition"
git status --short
# Stage exactly the managed child gitlinks that moved.
git add tools/ambition_music_renderer tools/ambition_sfx_renderer
git commit -m "Advance epoch-managed submodules to final pre-epoch tips"
git push origin main
```

Adjust those paths to the policies you actually chose. Then initialize the root:

```bash
git epoch init \
    --repository ambition \
    --history-store "$HISTORY_PUSH_URL" \
    --history-store-id "$HISTORY_STORE_ID" \
    --public-history-url "$HISTORY_FETCH_URL" \
    --public-history-browse-url "$HISTORY_BROWSE_URL" \
    --branch main \
    --config-only
```

Classify every current submodule occurrence explicitly. Example commands for
children chosen for epoch management are:

```bash
git epoch configure-submodule \
    tools/ambition_music_renderer epoch \
    --repository ambition_music_renderer

git epoch configure-submodule \
    tools/ambition_sfx_renderer epoch \
    --repository ambition_sfx_renderer
```

Use `continuous` or `external` for occurrences that should not be rolled into
new roots. The policy is local management metadata. The remaining committed public change
is the root locator:

```bash
git status --short
git add .git-epoch.yaml
git commit -m "Record Git epoch history location"
git push origin main
```

Before planning, `git status --short` should be empty and every managed child
gitlink should equal the child's configured primary-branch tip. Also synchronize
the root remote branch/tag namespace:

```bash
git fetch --prune --tags origin \
    '+refs/heads/*:refs/remotes/origin/*'
```

## 3. Plan without changing refs

Keep a local immutable bundle backup during the first production rollover:

```bash
BUNDLE_DPATH="${BUNDLE_DPATH:-$HOME/ambition-epoch-backups}"
mkdir -p "$BUNDLE_DPATH"

cd "$HOME/code/ambition"

git epoch plan \
    --recursive \
    --retire-extra-branches \
    --bundle \
    --bundle-dir "$BUNDLE_DPATH" \
    --summary \
    -o checkpoint.yaml
```

Review every repository, old tip, successor root, translated gitlink, and every
branch listed under `retire branches after archival verification`. The retirement
flag is explicit authorization to preserve those branch tips in the closing
epoch and remove the corresponding active branch refs during publication. The
command must end with `No refs have been changed.`

## 4. Archive and verify before publication

```bash
git epoch apply checkpoint.yaml
git epoch inspect
git epoch verify --deep
```

At this point the retiring history should exist in `ambition-history`, but none
of the active repositories should have adopted their successor roots. Treat any
archive or verification failure as a stop condition.

## 5. Publish during the maintenance window

GitHub rules that reject non-fast-forward updates must be disabled for the
managed default branches during this narrow publication window. Do this only
after `apply` and deep verification have succeeded. Record which rulesets were
disabled so they can be restored immediately afterward.

When the prepared receipt is correct, the active remotes have not changed, and
the non-fast-forward rules are temporarily disabled:

```bash
git epoch publish --plan checkpoint.yaml
```

Publication updates managed children leaf-first and then updates the Ambition
root to the successor whose gitlinks reference those child successor roots. It
also removes every auxiliary branch explicitly listed by the plan, in the same
atomic push that installs that repository's successor root. Re-enable the
GitHub rulesets immediately after publication.

## 6. Prove the public post-cutover experience

Use a new directory rather than the rollover worktree:

```bash
VERIFY_DPATH=$(mktemp -d "${TMPDIR:-/tmp}/ambition-epoch-production.XXXXXX")

git clone \
    --recurse-submodules \
    https://github.com/Erotemic/ambition.git \
    "$VERIFY_DPATH/ambition"

cd "$VERIFY_DPATH/ambition"
git epoch status
```

A fresh clone has no `.git/epoch/config.yaml`, but `status` should report the
active epoch, `ambition-history`, its public URL, and `attached: false` from the
committed locator and successor-root trailers.

Read-only archive operations can follow the public locator directly:

```bash
git epoch inspect
git epoch reconstruct --recursive -o "$VERIFY_DPATH/reconstructed"
```

To create local management state, attach and verify the archive:

```bash
git epoch attach

git epoch status
git epoch verify --deep
```

A maintainer who prefers the SSH endpoint for later archival writes can instead
attach with:

```bash
git epoch attach \
    --history-store git@github.com:Erotemic/ambition-history.git
```

`attach` accepts that endpoint only after the remote manifest proves the same
logical store ID and the same successor/predecessor lineage recorded in the
active root.

## 7. Make the split obvious to humans too

`.git-epoch.yaml` is the machine-readable authority for locating cold history.
Also add a short note near the top-level project documentation along these
lines:

> This repository uses Git epochs to keep active clones small. Earlier Git
> history is preserved in `Erotemic/ambition-history`. See `.git-epoch.yaml` and
> `git epoch reconstruct` for historical access.

The history repository should contain the inverse note: current development is
in `Erotemic/ambition`; this repository is the cold Git-history store.
