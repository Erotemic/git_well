# Git epoch tutorial 1: a single repository

This tutorial performs a real epoch rollover on
[Ubelt](https://github.com/Erotemic/ubelt), but confines every write to a
new temporary directory. GitHub is used only for the initial clone. Before any
`git epoch` operation that can publish refs, the checkout's `origin` is replaced
with a local bare repository in the temporary directory.

The result is an integration-style demonstration of the complete single-repo
workflow:

1. clone a real repository;
2. construct a disposable active-history remote;
3. initialize epoch management;
4. produce a read-only checkpoint plan;
5. archive and verify epoch zero without changing active refs;
6. publish the successor root to the disposable remote;
7. prove an ordinary fresh clone cannot see the retired epoch;
8. reconstruct the joined history from the history store.

Nothing in this tutorial pushes to GitHub.

## Prerequisites

Install `git_well` so that Git can find the `git-epoch` executable, then check
that the command is available:

```bash
python -m pip install -e /path/to/git_well
git epoch --help
```

If you normally use `uv`, the editable install can instead be:

```bash
uv pip install -e /path/to/git_well
```

## 1. Create a disposable workspace and clone Ubelt

The source repository is cloned from GitHub. No existing local Ubelt checkout
is used.

```bash
TMP_DPATH=$(mktemp -d "${TMPDIR:-/tmp}/git-well-epoch-ubelt.XXXXXX")
echo "workspace: $TMP_DPATH"

git clone --single-branch \
    https://github.com/Erotemic/ubelt.git \
    "$TMP_DPATH/ubelt"

cd "$TMP_DPATH/ubelt"
BRANCH=$(git branch --show-current)

# Keep the demonstration independent of global Git identity configuration.
git config user.name "Git Epoch Tutorial"
git config user.email "git-epoch-tutorial@example.com"

test -n "$BRANCH"
test -z "$(git status --porcelain)"

echo "branch: $BRANCH"
echo "initial tip: $(git rev-parse HEAD)"
```

The remainder of the tutorial operates only inside `TMP_DPATH`.

## 2. Replace `origin` with a disposable local remote

Keep the GitHub URL under the name `upstream`, then create a bare active-history
remote under the temporary directory and make that local repository the new
`origin`.

```bash
UPSTREAM_URL=$(git remote get-url origin)
git remote rename origin upstream

git init --bare "$TMP_DPATH/active.git"
git --git-dir="$TMP_DPATH/active.git" \
    symbolic-ref HEAD "refs/heads/$BRANCH"

git remote add origin "$TMP_DPATH/active.git"
git push -u origin "$BRANCH"
git push origin --tags
```

Verify the safety boundary before continuing:

```bash
test "$UPSTREAM_URL" = "https://github.com/Erotemic/ubelt.git"
test "$(git remote get-url upstream)" = "$UPSTREAM_URL"
test "$(git remote get-url origin)" = "$TMP_DPATH/active.git"
test "$(git ls-remote --heads origin | wc -l)" -eq 1

git remote -v
```

Any force-push performed by `git epoch publish` will now target
`$TMP_DPATH/active.git`, not GitHub.

## 3. Record the pre-epoch state

Save the current tip and tree so the rollover can be checked independently of
`git epoch`'s own verification.

```bash
OLD=$(git rev-parse HEAD)
OLD_TREE=$(git rev-parse 'HEAD^{tree}')
OLD_COUNT=$(git rev-list --count HEAD)

REMOTE_OLD=$(
    git --git-dir="$TMP_DPATH/active.git" \
        rev-parse "refs/heads/$BRANCH"
)

test "$REMOTE_OLD" = "$OLD"

printf 'old tip:    %s\n' "$OLD"
printf 'old tree:   %s\n' "$OLD_TREE"
printf 'old commits: %s\n' "$OLD_COUNT"
```

## 4. Initialize epoch management

Use a second bare repository in the temporary directory as the cold history
store. `--config-only` writes local epoch configuration but does not archive or
rewrite refs yet.

```bash
git epoch init \
    --repository ubelt \
    --history-store "$TMP_DPATH/history.git" \
    --config-only

git epoch status
```

The configuration lives under the checkout's Git metadata. The Ubelt working
tree remains unchanged.

```bash
test "$(git rev-parse HEAD)" = "$OLD"
test "$(git rev-parse 'HEAD^{tree}')" = "$OLD_TREE"
test -z "$(git status --porcelain)"
```

## 5. Build a read-only rollover plan

Ask for a bundle as a second immutable representation of the retired epoch.
The plan is saved so the prepare and publish phases use exactly the same
inputs.

```bash
git epoch plan \
    --bundle \
    --summary \
    -o "$TMP_DPATH/checkpoint.yaml"
```

Planning must not move either the local branch or the disposable active remote:

```bash
test "$(git rev-parse HEAD)" = "$OLD"
test "$(git rev-parse 'HEAD^{tree}')" = "$OLD_TREE"

test "$(
    git --git-dir="$TMP_DPATH/active.git" \
        rev-parse "refs/heads/$BRANCH"
)" = "$OLD"

echo 'PASS: planning changed no active refs'
```

## 6. Prepare the checkpoint without publishing it

`apply` performs the archival half of the transaction. It copies the retiring
refs and reachable objects into the history store, verifies the archive,
materializes the predicted successor root, records the prepared manifest, and
leaves active history at the old tip.

```bash
git epoch apply "$TMP_DPATH/checkpoint.yaml"
```

Inspect and deeply verify the prepared archive:

```bash
git epoch status
git epoch inspect
git epoch verify --deep

git --git-dir="$TMP_DPATH/history.git" show-ref
git --git-dir="$TMP_DPATH/history.git" fsck --full
```

The active refs must still be untouched:

```bash
test "$(git rev-parse HEAD)" = "$OLD"
test "$(
    git --git-dir="$TMP_DPATH/active.git" \
        rev-parse "refs/heads/$BRANCH"
)" = "$OLD"

echo 'PASS: preparation archived the epoch without publishing it'
```

At this point `git epoch abort --plan "$TMP_DPATH/checkpoint.yaml"` would discard
the prepared transaction. This tutorial continues to publication instead.

## 7. Publish the successor epoch

Publication is the destructive boundary, but the only publication remote in
this checkout is the local bare repository created in step 2.

```bash
git epoch publish --plan "$TMP_DPATH/checkpoint.yaml"
```

Capture the new state and check the core single-repository invariant: the new
root has no parent and contains exactly the same tree as the retired tip.

```bash
NEW=$(git rev-parse HEAD)
NEW_TREE=$(git rev-parse 'HEAD^{tree}')

printf 'old tip: %s\n' "$OLD"
printf 'new tip: %s\n' "$NEW"

test "$NEW" != "$OLD"
test "$NEW_TREE" = "$OLD_TREE"
test "$(git rev-list --parents -n 1 HEAD | awk '{print NF}')" -eq 1

test "$(
    git --git-dir="$TMP_DPATH/active.git" \
        rev-parse "refs/heads/$BRANCH"
)" = "$NEW"

test -z "$(git status --porcelain)"

echo 'PASS: successor is a root commit with the same source tree'
```

The active log is now bounded at the epoch boundary:

```bash
git log --graph --decorate --oneline -10
```

Immediately after this first rollover the active branch consists of the new
epoch root only.

```bash
test "$(git rev-list --count HEAD)" -eq 1
echo 'PASS: active history contains one commit'
```

## 8. Prove an ordinary clone cannot see epoch zero

The disposable bare active remote may still physically contain unreachable old
objects until it is garbage-collected. An ordinary protocol clone should not
receive those objects because no active ref reaches them.

`--no-local` is important here: it prevents Git's local-clone object sharing
optimizations from defeating the test.

```bash
git clone --no-local \
    --branch "$BRANCH" \
    "$TMP_DPATH/active.git" \
    "$TMP_DPATH/fresh"

test "$(git -C "$TMP_DPATH/fresh" rev-parse HEAD)" = "$NEW"
test "$(git -C "$TMP_DPATH/fresh" rev-list --all --count)" -eq 1

if git -C "$TMP_DPATH/fresh" cat-file -e "$OLD^{commit}" 2>/dev/null; then
    echo "FAIL: retired epoch tip leaked into an ordinary clone: $OLD" >&2
    exit 1
else
    echo 'PASS: retired epoch tip is absent from an ordinary clone'
fi
```

This is the property that bounds ordinary clone history.

For a rough size comparison:

```bash
du -sh "$TMP_DPATH/ubelt/.git"
du -sh "$TMP_DPATH/fresh/.git"
du -sh "$TMP_DPATH/history.git"
```

The working checkout may still retain unreachable old objects locally. The
fresh clone is the meaningful measurement of active-history transfer size.

## 9. Reconstruct the retired history

The history store retained the original Git objects and refs. Build an
archaeology checkout that fetches those objects and derives replacement refs to
join the successor root back to the retired tip:

```bash
cd "$TMP_DPATH/ubelt"

git epoch reconstruct \
    -o "$TMP_DPATH/reconstructed"
```

The reconstructed log should cross the epoch boundary into Ubelt's original
history:

```bash
git -C "$TMP_DPATH/reconstructed" \
    log --graph --decorate --oneline --all -30

git -C "$TMP_DPATH/reconstructed" replace -l

test "$(
    git -C "$TMP_DPATH/reconstructed" cat-file -t "$OLD"
)" = commit

echo 'PASS: retired tip is available in the reconstruction'
```

Run the deep verifier once more from the managed checkout:

```bash
cd "$TMP_DPATH/ubelt"
git epoch verify --deep
```

## 10. What this demonstrated

If every assertion above passed, the test established all of the main
single-repository epoch properties against a real repository:

- the plan phase changed no refs;
- preparation archived and verified the old epoch before publication;
- publication changed only the disposable local active remote;
- the successor root preserved the exact source tree;
- the active branch no longer reaches the retired history;
- an ordinary fresh clone cannot obtain the retired tip;
- the history store retained the exact retired objects;
- reconstruction restores a traversable view across the epoch boundary.

The temporary workspace is intentionally retained so it can be inspected.
Remove it when finished:

```bash
rm -rf "$TMP_DPATH"
```
