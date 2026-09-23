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

Define the `git_well` checkout once. The default matches the usual checkout
location; change this one assignment if your checkout lives elsewhere. Then
install it so Git can find the `git-epoch` executable:

```bash
GIT_WELL_DPATH="${GIT_WELL_DPATH:-$HOME/code/git_well}"
uv pip install -e "$GIT_WELL_DPATH"
git epoch --help

check_eq() {
    label=$1
    actual=$2
    expected=$3
    if [ "$actual" = "$expected" ]; then
        printf 'PASS: %s\n' "$label"
    else
        printf 'FAIL: %s\n' "$label" >&2
        printf '  expected: %s\n' "$expected" >&2
        printf '  actual:   %s\n' "$actual" >&2
        return 1
    fi
}

check_ne() {
    label=$1
    actual=$2
    unexpected=$3
    if [ "$actual" != "$unexpected" ]; then
        printf 'PASS: %s\n' "$label"
    else
        printf 'FAIL: %s\n' "$label" >&2
        printf '  value must differ from: %s\n' "$unexpected" >&2
        printf '  actual:                 %s\n' "$actual" >&2
        return 1
    fi
}

check_empty() {
    label=$1
    actual=$2
    if [ -z "$actual" ]; then
        printf 'PASS: %s\n' "$label"
    else
        printf 'FAIL: %s\n' "$label" >&2
        printf '  expected empty output, got: %s\n' "$actual" >&2
        return 1
    fi
}

check_fails() {
    label=$1
    shift
    if "$@" >/dev/null 2>&1; then
        printf 'FAIL: %s\n' "$label" >&2
        printf '  command unexpectedly succeeded: %s\n' "$*" >&2
        return 1
    else
        printf 'PASS: %s\n' "$label"
    fi
}
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
git config --local user.name "Git Epoch Tutorial"
git config --local user.email "git-epoch-tutorial@example.com"

check_ne "clone selected an active branch" "$BRANCH" ""
check_empty "initial worktree is clean" "$(git status --porcelain)"

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
check_eq "captured the expected Ubelt upstream" \
    "$UPSTREAM_URL" \
    "https://github.com/Erotemic/ubelt.git"
check_eq "upstream still points at GitHub" \
    "$(git remote get-url upstream)" \
    "$UPSTREAM_URL"
check_eq "origin points at the disposable bare repository" \
    "$(git remote get-url origin)" \
    "$TMP_DPATH/active.git"
check_eq "disposable origin has one active branch" \
    "$(git ls-remote --heads origin | awk 'END {print NR}')" \
    "1"

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

check_eq "disposable origin starts at the cloned tip" "$REMOTE_OLD" "$OLD"

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
check_eq "initialization did not move HEAD" "$(git rev-parse HEAD)" "$OLD"
check_eq "initialization preserved the tree" "$(git rev-parse 'HEAD^{tree}')" "$OLD_TREE"
check_empty "initialization left the worktree clean" "$(git status --porcelain)"
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
check_eq "planning did not move HEAD" "$(git rev-parse HEAD)" "$OLD"
check_eq "planning preserved the tree" "$(git rev-parse 'HEAD^{tree}')" "$OLD_TREE"
check_eq "planning did not move the disposable remote" "$(
    git --git-dir="$TMP_DPATH/active.git" \
        rev-parse "refs/heads/$BRANCH"
)" "$OLD"
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
check_eq "preparation did not move HEAD" "$(git rev-parse HEAD)" "$OLD"
check_eq "preparation did not move the disposable remote" "$(
    git --git-dir="$TMP_DPATH/active.git" \
        rev-parse "refs/heads/$BRANCH"
)" "$OLD"
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

check_ne "publication created a new commit" "$NEW" "$OLD"
check_eq "successor preserves the retired source tree" "$NEW_TREE" "$OLD_TREE"
check_eq "successor has no parent" \
    "$(git rev-list --parents -n 1 HEAD | awk '{print NF}')" \
    "1"
check_eq "disposable origin points at the successor" "$(
    git --git-dir="$TMP_DPATH/active.git" \
        rev-parse "refs/heads/$BRANCH"
)" "$NEW"
check_empty "publication left the worktree clean" "$(git status --porcelain)"
```

The active log is now bounded at the epoch boundary:

```bash
git log --graph --decorate --oneline -10
```

Immediately after this first rollover the active branch consists of the new
epoch root only.

```bash
check_eq "active history contains one commit" "$(git rev-list --count HEAD)" "1"
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

check_eq "fresh clone points at the successor" \
    "$(git -C "$TMP_DPATH/fresh" rev-parse HEAD)" \
    "$NEW"
check_eq "fresh clone contains one active commit" \
    "$(git -C "$TMP_DPATH/fresh" rev-list --all --count)" \
    "1"

check_fails "retired epoch tip is absent from an ordinary clone" \
    git -C "$TMP_DPATH/fresh" cat-file -e "$OLD^{commit}"
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

The reconstruction supports two useful views. The first is the physical Git
topology: the active epoch and archived epoch are real disconnected commit
trees. Disable replacement semantics and omit the synthetic replacement refs
from the revision set:

```bash
cd "$TMP_DPATH/reconstructed"

GIT_NO_REPLACE_OBJECTS=1 git log \
    --graph \
    --decorate \
    --oneline \
    --exclude='refs/replace/*' \
    --all

GIT_NO_REPLACE_OBJECTS=1 gitk \
    --exclude='refs/replace/*' \
    --all
```

The second view uses the derived replacement ref to traverse the logical
history across the epoch boundary:

```bash
git log --graph --decorate --oneline main -30

git replace -l

check_eq "retired tip is available in the reconstruction" "$(
    git cat-file -t "$OLD"
)" "commit"
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
