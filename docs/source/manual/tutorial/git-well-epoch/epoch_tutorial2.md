# Git epoch tutorial 2: recursive submodules with Ambition

This tutorial rehearses a recursive Git epoch rollover using
[Ambition](https://github.com/Erotemic/ambition) and its initialized submodules.
It uses the real repository graph, but every publication target is replaced by a
bare Git repository inside a temporary sandbox before any epoch plan is built.

GitHub is used only to clone the source graph. The tutorial publishes through
`git epoch sandbox publish`, which rechecks that every active remote and history
store is contained beneath the sandbox directory immediately before
publication.

For this integration test, every recursively discovered submodule is classified
as `epoch`. This deliberately exercises the hardest path: leaves roll first,
parents translate their gitlinks to child successor roots, and Ambition rolls
last. A production Ambition configuration should instead classify each
submodule deliberately as `epoch`, `continuous`, or `external`.

## Prerequisites

Define the `git_well` checkout in one place. The default matches the usual
checkout location:

```bash
GIT_WELL_DPATH="${GIT_WELL_DPATH:-$HOME/code/git_well}"
uv pip install -e "$GIT_WELL_DPATH"
git epoch sandbox --help
```

## 1. Clone Ambition recursively into a temporary source workspace

The source clone is disposable too. It exists only to provide the exact real
repository and submodule state that the sandbox will rehearse.

```bash
TMP_DPATH=$(mktemp -d "${TMPDIR:-/tmp}/git-well-epoch-ambition.XXXXXX")
SOURCE_DPATH="$TMP_DPATH/source/ambition"
SANDBOX_DPATH="$TMP_DPATH/sandbox"
SANDBOX_REPO="$SANDBOX_DPATH/work/ambition"

echo "workspace: $TMP_DPATH"

if git clone \
    --single-branch \
    --recurse-submodules \
    https://github.com/Erotemic/ambition.git \
    "$SOURCE_DPATH"
then
    printf 'PASS: recursive source clone completed\n'
    cd "$SOURCE_DPATH"
    git submodule status --recursive

    SOURCE_STATUS=$(git status --porcelain)
    if [ -z "$SOURCE_STATUS" ]; then
        printf 'PASS: recursive source checkout is clean\n'
    else
        printf 'FAIL: recursive source checkout is not clean\n' >&2
        printf '%s\n' "$SOURCE_STATUS" >&2
        printf 'Do not continue to sandbox creation until this is resolved.\n' >&2
    fi
else
    printf 'FAIL: recursive source clone did not reproduce the committed graph\n' >&2
    printf 'Do not continue to sandbox creation until the clone succeeds.\n' >&2
fi
```

A recursive clone can fail even when the superproject itself cloned correctly.
For example, a parent can contain a gitlink to a submodule commit that is no
longer available from the configured submodule repository. Git reports this as
`not our ref`, leaves a partial submodule checkout behind, and the superproject
then appears modified. That source graph is not a valid rehearsal input.

The recursive clone must be clean, and every initialized submodule must be at
the exact commit selected by its parent gitlink. Do not substitute a nearby
branch tip for an unavailable gitlink commit. The sandbox command independently
checks these conditions before it creates any disposable remotes.

## 2. Build a fully local publication sandbox

Create a sandbox from the local source clone:

```bash
cd "$SOURCE_DPATH"

git epoch sandbox create \
    --recursive \
    --all-submodules=epoch \
    --output "$SANDBOX_DPATH"
```

If the source graph is inconsistent, this command stops before creating the
sandbox and reports the offending submodule directly. A gitlink mismatch is
reported with the parent repository, submodule path, expected gitlink commit,
checked-out child `HEAD`, and whether the expected commit is available in the
local child repository. This is more specific than treating the parent as a
generically dirty worktree.

The command performs the fixture setup that would otherwise require a large
custom script. It:

- discovers the initialized recursive submodule graph from the checked-out
  commits;
- clones each participating worktree from the local source checkout;
- preserves the exact commit selected by every parent gitlink;
- gives detached submodules a disposable primary branch in the sandbox;
- creates one local bare active-history remote per repository;
- removes every other remote from the sandbox clones;
- records the original source `origin` URLs only as metadata;
- creates a shared local history-store location;
- initializes epoch configuration in the sandbox clones;
- configures every discovered submodule as `epoch` for this stress test.

Inspect the result:

```bash
git epoch sandbox inspect "$SANDBOX_DPATH"

git -C "$SOURCE_DPATH" remote -v
git -C "$SANDBOX_REPO" remote -v
find "$SANDBOX_DPATH/active-remotes" \
    -maxdepth 1 \
    -type d \
    -name '*.git' \
    -print
```

The source clone should still show its GitHub `origin`. The sandbox Ambition
clone should show only a local `origin` beneath `SANDBOX_DPATH`.

The complete mapping, including the original source URLs, is recorded in:

```bash
cat "$SANDBOX_DPATH/sandbox.yaml"
```

Nothing has been epoch-archived or published yet.

## 3. Build and review the recursive epoch plan

Use the sandbox wrapper so containment is checked before planning. The wrapper
calls the same `build_plan` implementation as `git epoch plan` and saves the
real plan to `SANDBOX_DPATH/checkpoint.yaml`.

```bash
git epoch sandbox plan \
    "$SANDBOX_DPATH" \
    --bundle \
    --summary
```

The printed plan should be leaf-first. Ambition should appear last. Parent
entries should contain gitlink translations from each child retired tip to the
predicted child successor root.

Planning is read-only. Inspect the sandbox again if desired:

```bash
git epoch sandbox inspect "$SANDBOX_DPATH"
git -C "$SANDBOX_REPO" epoch status
```

The sandbox state should be `planned`, and active refs should still identify the
old epoch.

## 4. Archive and verify without publishing

Prepare the saved plan:

```bash
git epoch sandbox apply "$SANDBOX_DPATH"
```

This archives every retiring epoch, verifies the archived refs, runs the archive
integrity checks, verifies any requested bundles, and materializes the successor
objects. Active branches and active remotes have not moved yet.

Inspect the prepared transaction and shared archive:

```bash
git epoch sandbox inspect "$SANDBOX_DPATH"
git -C "$SANDBOX_REPO" epoch status
git -C "$SANDBOX_REPO" epoch inspect

git --git-dir="$SANDBOX_DPATH/history.git" show-ref
git --git-dir="$SANDBOX_DPATH/history.git" fsck --full
```

At this point the sandbox state should be `prepared`.

## 5. Publish inside the sandbox

Publication is destructive to the sandbox active remotes, but those remotes are
disposable. The wrapper performs a fresh containment check immediately before it
calls the ordinary epoch publication code.

```bash
git epoch sandbox publish "$SANDBOX_DPATH"
```

If any sandbox repository's `origin` has been changed to a path outside the
sandbox, or any managed history-store path escapes the sandbox, the command
refuses to publish.

The actual recursive publication still uses the normal epoch engine. Managed
children publish before the parents whose successor trees reference them.

## 6. Verify the complete recursive rehearsal

Run the sandbox verifier:

```bash
git epoch sandbox verify "$SANDBOX_DPATH"
```

For every epoch-managed repository this independently checks that:

- the active branch moved away from its retired tip;
- the successor is a root commit;
- the local bare active remote points at that successor;
- epoch-managed parent gitlinks point at the corresponding child successor;
- non-epoch gitlinks, if present in another policy configuration, remain at
  their old commits;
- a fresh ordinary clone from the sandbox active remote cannot resolve the
  retired tip;
- `git epoch verify --deep` succeeds.

It then performs one additional **combined recursive fresh clone** of the root
repository. Each committed `.gitmodules` URL is overridden only in that clone's
local Git config with the corresponding sandbox `file://` active remote. This
proves that every submodule can actually initialize at the translated gitlink
without contacting GitHub and without local-clone hardlink/copy behavior leaking
unreachable retired objects into the result.

The verifier writes its results back into `sandbox.yaml`. It leaves the
independent active-history clones under `verification/fresh` and the combined
recursive checkout under `verification/fresh-recursive`:

```bash
find "$SANDBOX_DPATH/verification/fresh" -maxdepth 2 -type d -print
find "$SANDBOX_DPATH/verification/fresh-recursive" -maxdepth 3 -type d -print
```

Inspect Ambition's new active state:

```bash
git -C "$SANDBOX_REPO" log --graph --decorate --oneline -10
git -C "$SANDBOX_REPO" submodule status --recursive
```

The top-level Ambition successor is a root commit. Its raw tree can differ from
the retired Ambition tree because epoch-managed gitlinks have been translated
to the corresponding child successor roots. Those child roots represent the
same child source snapshots.

## 7. Measure the archive and active handoff size

The shared history store and the generated per-epoch bundles answer different
size questions. Measure the current state first:

```bash
git epoch sandbox stats "$SANDBOX_DPATH"
```

The report includes:

- `history.history_store.directory_bytes`: sum of file contents in the shared
  bare history store;
- `history.history_store.directory_disk_bytes`: allocated filesystem space,
  which can be much larger before loose objects are packed;
- each archived epoch's `reachable_object_disk_bytes`: compressed object
  representations reachable from that epoch's immutable refs;
- `exclusive_object_disk_bytes`: the subset used by only that one archived
  epoch record;
- `standalone_bundle.bytes`: exact size of the independently-restorable bundle
  for that epoch when `--bundle` was used;
- `bundles.bytes`: total size of all standalone bundle backups;
- `recursive_fresh_clone.git_directory_bytes`: Git metadata for the complete
  newly-cloned active repository graph.

Per-epoch reachable sizes can overlap, so do not add them together to estimate
the shared store. The standalone bundle is the cleanest answer to "how large is
this epoch by itself?" The history-store directory size is the answer to "how
large is the deduplicated shared archive?"

The history store may still contain many loose objects after archival. Pack it
and deep-verify it:

```bash
git -C "$SANDBOX_REPO" epoch gc
```

`gc` reports before/after file bytes, allocated disk bytes, and the allocated-disk
reduction percentage. It retains every archived epoch ref and runs deep verification after
packing.

Finally, build the same kind of full-history compressed source handoff that
`archive_source` normally produces, but from the verified recursive active
checkout:

```bash
git epoch sandbox stats "$SANDBOX_DPATH" --source-archive
```

The `source_archive.bytes` field is the direct post-checkpoint `tar.gz` package
measurement. Use that value when the operational goal is a handoff archive under
a specific threshold such as 20 MiB. The generated package is retained beneath
`SANDBOX_DPATH/verification/source-archives` for inspection.

## 8. One-command rehearsal

The staged commands above are useful when learning or inspecting the protocol.
For an arbitrary initialized repository graph, the same sequence can be run in
one command after sandbox creation:

```bash
git epoch sandbox run "$SANDBOX_DPATH" --bundle
```

`run` composes the same sandbox `plan`, `apply`, `publish`, and `verify` paths.
It does not use a separate checkpoint implementation.

Running this on an already-published or already-verified sandbox is safe. The
completed plan/apply/publish phases are reported as explicit `skipped` actions
and verification is run again.

## 9. Reconstruct the recursive history

Build an archaeology checkout from the published sandbox:

```bash
git -C "$SANDBOX_REPO" epoch reconstruct \
    --recursive \
    -o "$TMP_DPATH/reconstructed"
```

The reconstruction contains the active Ambition epoch, archived Ambition epoch
refs, replacement refs that provide a logically joined archaeology view, and
reconstructed managed children beneath `.git-epoch-repositories`.

To inspect the **physical Git topology**, with epoch histories shown as the
actual disconnected trees, disable replacement semantics and exclude the
synthetic replacement refs from the revision set:

```bash
cd "$TMP_DPATH/reconstructed"

GIT_NO_REPLACE_OBJECTS=1 git log \
    --graph \
    --decorate \
    --oneline \
    --exclude='refs/replace/*' \
    --all
```

With `gitk`, use the same revision selection:

```bash
GIT_NO_REPLACE_OBJECTS=1 gitk \
    --exclude='refs/replace/*' \
    --all
```

That view shows each archived epoch as its own real commit DAG. To traverse the
logical history across epoch boundaries instead, leave replacement semantics
enabled and follow the active branch:

```bash
git log --graph --decorate --oneline main
```

## 10. What this demonstrated

If the sandbox verifier passed, the rehearsal exercised the recursive epoch
mechanism against the real checked-out Ambition repository graph while keeping
every write local:

- repository discovery used the real recursive Git state;
- all publication remotes were replaced before planning;
- sandbox publication was guarded by an explicit path-containment check;
- child and parent retirement happened through the normal epoch implementation;
- parent successor trees translated managed gitlinks leaf-first;
- fresh active-history clones cannot obtain retired tips;
- the shared history store retains the retired refs and objects;
- recursive reconstruction restores both the physical disconnected epoch DAGs
  and the optional logically joined archaeology view.

The all-`epoch` policy is intentionally aggressive for this integration test.
For an actual Ambition rollout, classify each submodule according to who owns
its history and whether that history should roll with the superproject.

The workspace is retained for inspection. Remove it when finished:

```bash
rm -rf "$TMP_DPATH"
```
