# Git Epoch Archive: Design for Bounded Active Git History

## Status

Implemented as the version-one `git epoch` / `git-epoch` workflow in
`git_well.git_epoch` and `git_well.epoch`. This document remains the design
contract for the implementation. The deliberately deferred items in section 66
remain out of scope.

The implementation uses a prepare/publish transaction boundary: archival refs,
boundary verification, optional bundle backup, and successor roots are prepared
before active refs are rewritten. Prepared manifest records become committed
only after publication succeeds. A prepared transaction may be resumed or
aborted before any successor ref is adopted.

## 1. Purpose

Large, long-lived repositories can accumulate enough valid Git history that ordinary clones, source archives containing `.git`, and automated-agent handoffs become unnecessarily expensive.

This is especially pronounced in repositories with frequent AI-assisted development, where commit metadata remains valuable but the number of commits and historical objects can grow much faster than in a traditional human-only workflow.

The objective of this tool is to separate:

1. **active history**, which should remain small and cheap to clone;
2. **archival history**, which should preserve complete historical Git information;
3. **reconstructed history**, which should allow the archived epochs to be traversed as an approximately continuous historical lineage when archaeology is required.

The tool should support periodic history compaction without discarding old commits.

A repository may therefore evolve as:

```text
original repository:

A---B---C---D---E
                ^
               main
```

After the first checkpoint:

```text
archive:

epoch 0:
A---B---C---D---E


active repository:

R1---F---G---H
^
new root
```

where:

```text
tree(E) == tree(R1)
```

After a later checkpoint:

```text
archive:

epoch 0:
A---B---C---D---E

epoch 1:
                R1---F---G---H


active:

                            R2---I---J
```

with:

```text
tree(E)  == tree(R1)
tree(H)  == tree(R2)
```

The archive retains the exact original commits.

The active repository contains only the current epoch.

The complete lineage can later be reconstructed locally by loading the archived epochs and applying recorded boundary relationships.

---

# 2. Primary goals

The tool should provide the following properties.

## 2.1 Cheap ordinary clones

A new developer should be able to run:

```bash
git clone <repository>
```

without knowing about shallow clones, partial clones, special refspecs, or archive infrastructure.

The amount of Git history reachable from ordinary active refs should therefore remain bounded.

This is the primary reason for actually cutting active history instead of relying solely on shallow client clones.

---

## 2.2 Exact archival preservation

When an epoch is retired, its Git commits should be preserved exactly.

This includes:

* commit object IDs;
* commit messages;
* author information;
* committer information;
* timestamps;
* parent relationships;
* merge topology;
* tree objects;
* blobs;
* annotated tags;
* relevant branch tips;
* other explicitly archived refs.

The archive should preserve Git objects rather than export history into an alternate representation.

---

## 2.3 Verifiable epoch continuity

The tool must be able to demonstrate that an epoch rollover did not alter the repository snapshot.

For a repository without epochized submodules:

```text
tree(previous_epoch_tip) == tree(next_epoch_root)
```

should hold exactly.

For repositories containing managed submodules that are themselves rolled into new epochs, a recursive snapshot-equivalence relation is required. This is defined later.

---

## 2.4 Reconstructible complete history

Given:

* all epoch archives;
* the epoch manifest;
* all required managed submodule archives;

the tool should be able to construct a local repository in which ordinary Git history traversal can cross epoch boundaries.

The preferred reconstruction should preserve the original commit object IDs from every epoch.

Git replacement refs provide a native mechanism for this. `git replace` stores replacement relationships under `refs/replace/`, and Git commands normally consult those replacements when interpreting objects.

A second optional reconstruction mode may materialize a conventional continuous DAG at the cost of generating new commit IDs.

---

## 2.5 Recursive submodule support

Repositories may contain:

* managed submodules that should also use epochs;
* managed submodules that should retain continuous history;
* external upstream submodules that the project does not control;
* nested combinations of the above.

The system must model these cases explicitly.

---

## 2.6 Repeated operation

The design must support indefinite use:

```text
epoch 0
epoch 1
epoch 2
...
epoch N
```

A checkpoint should be an ordinary lifecycle operation rather than a one-time migration.

---

# 3. Non-goals

## 3.1 Atomic publication across multiple remotes

A recursive rollover may require forced updates to several repositories.

This design assumes a maintenance window in which collaborators and automation are instructed not to mutate or consume affected repositories.

The implementation should still avoid leaving ambiguous state, but distributed atomic publication is outside the design.

---

## 3.2 Transparent use of archival history by every Git client

Normal users should interact only with the active repository.

Historical reconstruction is an explicit archaeology operation.

The system should not require ordinary clones to understand replacement refs, archive manifests, or epoch storage.

---

## 3.3 Rewriting old epochs into a single canonical DAG

Archived commits should remain immutable by default.

The system should preserve the history that actually existed within each epoch and record the relationships between epochs separately.

---

## 3.4 Replacing Git

Git remains the storage format for Git objects and commit history.

The tool supplies lifecycle management, verification, manifests, reconstruction, and recursive repository coordination.

---

# 4. Terminology

## Repository identity

A stable logical identifier assigned by the epoch system to a Git repository.

Example:

```text
ambition
ambition-music-renderer
ambition-assets
```

Repository identity must survive:

* repository URL changes;
* host migrations;
* submodule path changes;
* repository renames.

Paths and URLs are attributes of repository identities rather than identities themselves.

---

## Active repository

The normal repository cloned by developers.

It contains the current epoch and normal active refs.

Old epoch DAGs should be unreachable from its ordinary refs.

---

## Epoch

A contiguous Git DAG retained exactly as it existed during some period of development.

An epoch usually has one principal root and one principal tip on the default branch, although additional branches and merges may exist within it.

---

## Epoch root

The root commit from which development in an epoch begins.

For epoch zero this may be the historical repository's original roots.

For later epochs this is normally a synthetic root created during the preceding checkpoint.

---

## Epoch tip

The final active commit archived when an epoch is closed.

---

## Epoch boundary

Metadata connecting:

```text
previous epoch tip
        ↓
next epoch root
```

The relationship asserts snapshot equivalence.

---

## History store

A Git repository containing archived epoch objects.

A history store may contain epochs for one or several logical repositories.

---

## Managed repository

A repository whose epoch lifecycle is under control of this tool.

---

## External repository

A repository referenced by the project but whose history should remain untouched.

External upstream submodules are the main example.

---

## Recursive snapshot

The logical content of a repository after recursively resolving managed epochized submodules.

---

# 5. Repository policies

Every known repository should have one of three policies.

## 5.1 `epoch`

The repository participates fully in the epoch system.

Its active history may periodically be cut.

Example:

```yaml
policy: epoch
```

Typical uses:

* primary application repositories;
* large asset repositories;
* tightly coupled project extensions;
* internal tools with rapidly growing Git histories.

---

## 5.2 `continuous`

The repository is controlled by the project but keeps ordinary continuous Git history.

Example:

```yaml
policy: continuous
```

Typical uses:

* small shared libraries;
* tools consumed by many unrelated projects;
* repositories where rewriting active history would create more downstream work than it saves.

A continuous repository can still be included in archive completeness checks.

---

## 5.3 `external`

The repository is outside the epoch system.

Example:

```yaml
policy: external
```

Typical uses:

* upstream dependencies;
* third-party repositories;
* public projects whose history the user should not rewrite.

The superproject continues to record exact upstream commit IDs.

---

# 6. Why submodules require repository-level modeling

Git represents a submodule in the superproject tree with a `gitlink` containing the object ID of the exact submodule commit expected by the superproject.

Suppose:

```text
renderer old epoch:

...---M
```

A new root is created:

```text
renderer new epoch:

R
```

with:

```text
tree(M) == tree(R)
```

A superproject before rollover contains:

```text
renderer -> M
```

The compact active superproject must eventually contain:

```text
renderer -> R
```

The raw superproject tree therefore changes even though the recursively checked-out source does not.

This is why raw tree equality alone cannot express continuity for recursive epoch rollover.

---

# 7. Snapshot equivalence

Define:

```text
snapshot(repository, commit)
```

as a recursively interpreted repository state.

For normal files, directories, executable bits, and symlinks, equivalence requires exact Git tree identity.

For gitlinks, behavior depends on repository policy.

## 7.1 External or continuous submodule

The gitlink must remain identical:

```text
old_gitlink == new_gitlink
```

No translation is permitted.

---

## 7.2 Epochized managed submodule

The gitlinks may differ if the two commits form a verified epoch boundary.

Example:

```text
old:
renderer -> M

new:
renderer -> R
```

is equivalent if:

```text
boundary(M, R)
```

exists and:

```text
snapshot(renderer, M)
    ==
snapshot(renderer, R)
```

---

## 7.3 Recursive definition

Therefore:

```text
snapshot_equal(A, B)
```

means:

1. all ordinary entries are identical;
2. all non-epochized gitlinks are identical;
3. every changed epochized gitlink corresponds to a registered repository boundary whose recursive snapshots are equivalent.

This gives a recursive proof of source continuity.

---

# 8. Epoch-boundary record

A boundary should contain enough information to verify itself independently.

Conceptually:

```yaml
boundary:
  repository: ambition-music-renderer

  previous:
    epoch: 3
    commit: aab52c...
    tree: 182fa1...

  successor:
    epoch: 4
    commit: c3d419...
    tree: 182fa1...

  equivalence:
    kind: exact-tree

  created:
    timestamp: 2026-09-01T...
    tool_version: ...
```

A superproject whose raw trees differ because of submodule rollover might have:

```yaml
boundary:
  repository: ambition

  previous:
    epoch: 2
    commit: 184fb0...

  successor:
    epoch: 3
    commit: d82911...

  equivalence:
    kind: recursive
    translations:
      - path: tools/renderer
        repository: ambition-music-renderer
        old_commit: aab52c...
        new_commit: c3d419...
        boundary: renderer-3-to-4
```

---

# 9. Archive storage

## 9.1 One history repository per source repository

The simplest topology is:

```text
ambition.git
ambition-history.git

renderer.git
renderer-history.git
```

Advantages:

* straightforward mental model;
* repository-specific permissions;
* simple disaster recovery;
* easy extraction or deletion of one project.

---

## 9.2 Shared history vault

The system should also allow:

```text
ambition-history.git
```

to hold epochs from:

```text
ambition
renderer
assets
moveset-tools
```

Git can store disconnected DAGs as long as refs retain them.

Suggested ref layout:

```text
refs/epochs/ambition/000/heads/main
refs/epochs/ambition/000/tags/...
refs/epochs/ambition/001/heads/main

refs/epochs/renderer/000/heads/main
refs/epochs/renderer/001/heads/main
```

Repository identities prevent collision.

A shared vault may also deduplicate identical Git objects naturally.

The implementation should verify that participating repositories use compatible Git object formats before storing them together.

---

# 10. Manifest

The history store needs machine-readable metadata in addition to Git refs.

A possible format:

```yaml
format_version: 1

history_store:
  id: ambition-history
  object_format: sha1

repositories:
  ambition:
    policy: epoch
    active_url: git@host:project/ambition.git

  renderer:
    policy: epoch
    active_url: git@host:project/renderer.git

  foo:
    policy: external
    active_url: https://example.com/foo.git

epochs:
  ambition:
    - number: 0
      roots:
        - 9eaba1...
      main_tip: ff12d3...
      refs_namespace: refs/epochs/ambition/000

    - number: 1
      root: a38198...
      main_tip: c72311...
      predecessor:
        epoch: 0
        commit: ff12d3...

  renderer:
    - number: 0
      main_tip: 75a121...

boundaries:
  - id: ambition-0-1
    repository: ambition
    previous_commit: ff12d3...
    successor_root: a38198...
    equivalence: exact-tree
```

The Git objects remain authoritative for Git history.

The manifest describes relationships that Git does not natively encode.

---

# 11. Manifest storage strategy

The manifest should itself be version-controlled.

A useful arrangement is:

```text
history-store.git

refs/epochs/...
refs/meta/main
```

where `refs/meta/main` points to a small Git history containing:

```text
manifest.yaml
schemas/
verification/
```

This avoids requiring the metadata commits to be ancestors of any archived epoch.

The metadata history may evolve while archived epochs remain immutable.

---

# 12. Initial conversion

Command:

```bash
git epoch init
```

or:

```bash
git-epoch init
```

The tool is pointed at an existing repository with normal full history.

Suppose:

```text
A---B---C---D---E
                ^
               main
```

## 12.1 Preflight

The tool should verify:

* repository is valid;
* worktree state satisfies configured policy;
* current branch is known;
* remotes are recorded;
* object format is known;
* refs are inventoried;
* submodules are discovered;
* submodule policies are resolved;
* required history stores are accessible;
* existing history-store repository identity does not conflict.

By default, initialization should require a clean index and working tree.

---

## 12.2 Archive epoch zero

Copy all selected refs and reachable objects into the history store.

At minimum:

```text
main
other explicitly retained branches
tags
```

The exact ref policy should be configurable.

Record the original names in metadata.

Do not rewrite the existing archived commits.

---

## 12.3 Verify archive

Before changing the active repository:

```bash
git fsck
```

should pass on the archived object graph.

The system should verify every archived ref resolves to the expected OID.

For optional bundle backup, create and verify a bundle. Git bundles are explicitly designed to transfer Git objects and refs and can represent full or incremental backups.

---

## 12.4 Create successor root

Create a root commit whose tree equals the current tip tree.

Conceptually:

```text
old:
...---E

new:
R1
```

with:

```text
tree(E) == tree(R1)
```

The root commit message should include machine-readable trailers such as:

```text
Git-Epoch-Repository: ambition
Git-Epoch-Number: 1
Git-Epoch-Predecessor: <E>
Git-Epoch-Predecessor-Tree: <tree>
Git-Epoch-History-Store: <logical-store-id>
```

The archive manifest remains authoritative; the commit trailers provide local discoverability.

---

# 13. Periodic checkpoint

Command:

```bash
git epoch checkpoint
```

Suppose active history is:

```text
R1---F---G---H
             ^
            main
```

The checkpoint closes epoch 1 at `H`.

It archives the epoch unchanged and creates:

```text
R2
```

with equivalent content.

Development continues:

```text
R2---I---J...
```

---

# 14. Checkpoint criteria

The tool should never assume checkpoints are calendar-based.

Possible policies:

```yaml
checkpoint:
  max_active_pack_size: 25MiB
```

or:

```yaml
checkpoint:
  max_commits: 5000
```

or:

```yaml
checkpoint:
  max_age: 180d
```

or combinations.

Useful command:

```bash
git epoch status
```

could report:

```text
Repository: ambition
Current epoch: 4

Active history:
  commits: 8,421
  packed reachable size: 23.8 MiB
  age: 147 days

Configured threshold:
  packed size: 25 MiB

Recommendation:
  checkpoint soon
```

The tool should measure the thing the user cares about directly where possible.

A stronger check is to perform an ordinary fresh clone into temporary storage and measure:

```text
.git size
source archive size
```

because server negotiation and packing may differ from local estimates.

---

# 15. Ref policy

A critical invariant is that old epochs must no longer be reachable from ordinary active refs.

Otherwise a normal clone can acquire them.

The tool must inventory:

```text
refs/heads/*
refs/tags/*
```

and any configured custom active namespaces.

Every ref should be classified as:

```text
retain active
archive
delete
```

A checkpoint should fail if an unclassified active ref makes retired history reachable.

Example:

```text
main             -> current epoch
feature/foo      -> old epoch
v0.8             -> old epoch
```

would prevent completion until policy resolves `feature/foo` and `v0.8`.

---

# 16. Tags

Tags require explicit handling.

Historical tags pointing into archived epochs should normally move into the archive namespace.

For example:

```text
refs/tags/v0.8
```

may become:

```text
refs/epochs/ambition/000/tags/v0.8
```

The tag object itself remains unchanged.

If retaining a tag under active `refs/tags/*` would make historical objects reachable during normal transfer, the tool should reject that configuration or warn according to an explicit override policy.

---

# 17. Branches

Branches may span checkpoint boundaries conceptually, but their old Git identities cannot remain ordinary ancestors of the successor root.

Default policy:

* archive all branch tips from the closing epoch;
* create only explicitly selected successor branches;
* normally create a successor for the default branch;
* require explicit handling for long-lived feature branches.

A future extension might support translating several active branches independently into successor roots.

Version one should favor a single active default branch at checkpoint time.

---

# 18. Submodule discovery

The tool should discover submodules recursively.

For each submodule occurrence, record:

```text
superproject repository identity
superproject commit
submodule path
logical submodule name
.gitmodules URL
gitlink OID
resolved repository identity
policy
```

Historical `.gitmodules` files must be treated as historical data.

A submodule path or URL must never become the sole repository identity.

---

# 19. Submodule policy configuration

Example:

```yaml
repositories:
  ambition:
    policy: epoch

    submodules:
      tools/renderer:
        repository: renderer
        policy: epoch

      third_party/foo:
        repository: foo
        policy: external

      libs/ubelt:
        repository: ubelt
        policy: continuous
```

Policy may be stored centrally in the epoch manifest rather than committed into every source repository.

---

# 20. Recursive checkpoint algorithm

Consider:

```text
ambition
└── renderer
    └── shader-assets
```

where all three use `epoch`.

The rollover should be evaluated from leaves toward roots.

## Phase A: establish old states

Record:

```text
shader_old
renderer_old
ambition_old
```

including all original gitlinks.

---

## Phase B: create successor for deepest repository

Create:

```text
shader_new_root
```

and prove:

```text
tree(shader_old) == tree(shader_new_root)
```

Archive the closed shader epoch.

---

## Phase C: construct successor renderer snapshot

The old renderer contains:

```text
shader -> shader_old
```

The successor renderer snapshot should contain:

```text
shader -> shader_new_root
```

Everything else remains identical.

Create:

```text
renderer_new_root
```

Record the translation:

```text
shader_old -> shader_new_root
```

Then prove:

```text
snapshot(renderer_old)
    ==
snapshot(renderer_new_root)
```

---

## Phase D: construct successor superproject snapshot

Repeat:

```text
renderer_old -> renderer_new_root
```

inside the Ambition tree.

Create:

```text
ambition_new_root
```

Then prove:

```text
snapshot(ambition_old)
    ==
snapshot(ambition_new_root)
```

---

# 21. Recursive continuity certificate

Every recursive checkpoint should produce a certificate containing the proof inputs.

Example:

```yaml
repository: ambition
old_commit: abc123
new_root: def456

ordinary_tree_changes: none

submodule_translations:
  - path: tools/renderer
    repository: renderer
    old_commit: 111aaa
    new_commit: 222bbb
    verified_boundary: renderer-4-5

result:
  recursive_snapshot_equal: true
```

This certificate need not be cryptographically signed initially.

All referenced Git object IDs already provide content-addressed verification.

---

# 22. Independent submodule checkpointing

A managed submodule may be useful independently of its parent.

The system should permit:

```bash
git epoch checkpoint renderer
```

but only if the resulting active state remains valid for known managed parents.

There are several possible cases.

### Case A: no active parent references the old epoch tip

Checkpoint normally.

### Case B: active parent references a commit that remains available

Checkpoint may proceed.

### Case C: active parent would reference a commit moved exclusively into cold history

The tool should require coordinated parent rollover or an explicit override.

Version one may simply require:

```text
all active managed parents must participate in the checkpoint
```

This is easier to reason about.

---

# 23. External submodules

External repositories never undergo automatic epoch transformation.

If:

```text
third_party/foo -> abc123
```

before rollover, then:

```text
third_party/foo -> abc123
```

must remain after rollover.

Recursive verification treats external gitlinks as exact values.

The tool may optionally verify that `abc123` is still obtainable from the configured upstream.

Failure to obtain an external historical commit should affect archive completeness reporting but should never cause the tool to rewrite the upstream repository.

---

# 24. Continuous managed submodules

A `continuous` repository behaves similarly to an external repository with respect to gitlinks:

```text
old_gitlink == new_gitlink
```

The difference is that archive tooling may know how to back it up or mirror it.

This policy is useful for internally maintained libraries that should retain normal continuous history.

---

# 25. Historical archive completeness

For every archived superproject epoch, the tool should enumerate all gitlink OIDs reachable within the epoch.

For each gitlink, it should determine whether the corresponding repository is:

```text
epoch
continuous
external
unknown
```

For `epoch` repositories, every historical gitlink should be present in the appropriate archive.

For `continuous` repositories, the commit should be obtainable from configured storage if full reconstruction is promised.

For `external` repositories, the tool may record availability without assuming ownership.

Report:

```text
archive completeness:
  managed repositories: complete
  external repositories: 14/14 currently obtainable
```

or:

```text
archive completeness:
  managed repositories: incomplete

missing:
  repository: renderer
  commit: abc123
  referenced by:
    ambition epoch 2 commit def456
```

---

# 26. Reconstruction

Command:

```bash
git epoch reconstruct
```

should build an archaeology checkout.

Suggested layout:

```text
reconstructed/
├── ambition/
├── repositories/
│   ├── renderer/
│   ├── shader-assets/
│   └── ...
└── reconstruction.yaml
```

---

# 27. Reconstruction of a single repository

For a repository with:

```text
epoch 0:
A---B---C

epoch 1:
        R1---D---E

epoch 2:
                R2---F
```

the archive records:

```text
R1 predecessor C
R2 predecessor E
```

The preferred reconstructed view should use replacement relationships so Git history traversal sees the intended connection while original epoch commits remain intact.

Git supports replacement refs under `refs/replace/`; replacements are consulted by most Git commands, though reachability transfer operations such as packing and fetching have special behavior.

The implementation should experimentally validate the exact replacement strategy used for root commits before freezing the format.

The conceptual result is:

```text
A---B---C---D---E---F
```

while preserving archived commit objects.

---

# 28. Replacement refs are derived state

`refs/replace/*` should never be considered archival truth.

They should always be regenerable from the manifest.

This avoids depending on local replacement-ref state for correctness.

The manifest owns:

```text
epoch successor root -> predecessor tip
```

relationships.

The reconstruction process generates whatever replacement objects and refs are required.

---

# 29. Reconstruction limitations

Git documents several caveats for replacement objects.

Replacement refs affect normal object interpretation but are excluded from some reachability operations, and their presence disables commit-graph use.

Therefore the reconstructed checkout should be documented as:

```text
historical archaeology environment
```

rather than the recommended everyday development clone.

Operations should be tested explicitly, including:

```text
git log
git show
git blame
git diff
git merge-base
git bisect
git rev-list
```

The tool's test suite should define which reconstruction operations are guaranteed.

---

# 30. Materialized reconstruction

Optional command:

```bash
git epoch reconstruct --materialize
```

may create an ordinary continuous Git DAG with real parent relationships.

Because commit IDs include parent information, stitching real parent relationships into previously root commits necessarily generates new commit IDs downstream.

The tool must emit mappings:

```text
archived OID -> materialized OID
```

for every rewritten commit.

This mode is useful for exporting history into systems that do not understand replacement refs.

It should never replace the exact archival store.

---

# 31. Recursive reconstruction

For every managed submodule repository:

1. assemble its epoch objects;
2. generate its replacement relationships;
3. configure the reconstructed superproject to use that local reconstructed repository;
4. preserve the exact gitlink OIDs from each historical superproject commit.

Because old commits still contain their original gitlinks, all original epoch commit OIDs must remain available in the reconstructed submodule repository.

A historical checkout should therefore be able to resolve:

```text
old superproject -> old submodule commit
new superproject -> successor submodule root
```

within the same reconstructed submodule object store.

---

# 32. Reconstruction and `.gitmodules`

Historical `.gitmodules` contents should remain untouched.

The reconstruction tool should override URLs using local Git configuration where necessary.

This allows historical source state to remain exact while making local reconstruction practical.

For example:

```text
historical .gitmodules URL:
git@old-host:project/renderer.git

local reconstruction override:
../repositories/renderer
```

---

# 33. Bundle backups

History-store Git repositories are the primary browsable archive.

Bundles provide an additional immutable transport and disaster-recovery representation.

Git supports creating, verifying, cloning from, and fetching from bundles.

Suggested structure:

```text
backups/
  ambition/
    epoch-000.bundle
    epoch-001.bundle

  renderer/
    epoch-000.bundle
```

or periodic whole-vault bundles.

Every generated bundle should immediately be verified.

---

# 34. Command-line interface

Potential command family:

```text
git epoch ...
```

or executable:

```text
git-epoch ...
```

Git automatically exposes `git-foo` executables as:

```text
git foo
```

so implementation can use a standalone executable.

## 34.1 Sandbox rehearsal

The command family should provide a first-class rehearsal environment for a
repository graph before production publication. A sandbox is not a dry plan:
it performs the real epoch operations against cloned worktrees, disposable
local bare active remotes, and a local history store.

The sandbox layer owns only containment and fixture construction. It must call
the same plan, archive, publication, reconstruction, and verification
authorities used by production operations rather than reimplementing them.

Useful staged commands are:

```text
git epoch sandbox create
git epoch sandbox inspect
git epoch sandbox plan
git epoch sandbox apply
git epoch sandbox publish
git epoch sandbox verify
git epoch sandbox stats
git epoch sandbox run
```

Before sandbox publication, every configured active publication remote and
history-store location must resolve beneath the sandbox root. Publication must
refuse to continue if any target escapes that root. Original source remote URLs
may be retained as metadata for inspection, but sandbox clones should not keep
them as writable Git remotes.

---

# 35. `git epoch init`

Initialize epoch management for an existing repository.

Example:

```bash
git epoch init \
    --repository ambition \
    --history-store ../ambition-history.git
```

Responsibilities:

* inventory repository;
* establish logical identity;
* classify submodules;
* archive current history as epoch zero;
* verify archive;
* generate successor active root;
* prepare publication plan.

---

# 36. `git epoch status`

Show:

```text
repository identity
policy
active epoch
active root
active tip
commit count
reachable object size
history-store location
last checkpoint
configured threshold
submodule policies
archive verification status
```

For example:

```text
ambition

policy: epoch
active epoch: 3
root: 93ab1c2
tip: 41702ef
commits: 6,281
packed reachable size: 18.4 MiB

history:
  epochs: 0..2
  store: ../ambition-history.git
  last verified: 2026-09-01

submodules:
  renderer      epoch
  assets        epoch
  ubelt         continuous
  upstream-foo  external
```

---

# 37. `git epoch plan`

Produce a read-only rollover plan.

Example:

```bash
git epoch plan --recursive
```

Output:

```text
CHECKPOINT PLAN

1. renderer-assets
   epoch 2 -> 3
   old tip: ...
   new root tree: ...

2. renderer
   epoch 4 -> 5
   translate:
       assets old -> assets new

3. ambition
   epoch 3 -> 4
   translate:
       renderer old -> renderer new

Active refs requiring rewrite:
   ambition/main
   renderer/main
   renderer-assets/main

Historical refs to archive:
   ...

No changes have been made.
```

The planning phase should perform as much verification as possible before mutation.

---

# 38. `git epoch checkpoint`

Execute a previously determined rollover.

Useful options:

```text
--recursive
--dry-run
--plan <file>
--bundle
--no-publish
```

Publication may be kept separate:

```bash
git epoch checkpoint --no-publish
git epoch publish
```

even though distributed publication atomicity is outside the design.

This makes inspection before force updates easier.

---

# 39. `git epoch verify`

Modes:

```bash
git epoch verify
git epoch verify --archive
git epoch verify --recursive
git epoch verify --deep
```

Checks should include:

* Git object integrity;
* referenced epoch refs exist;
* manifest OIDs exist;
* boundary invariants hold;
* recursive snapshot equivalence;
* all required managed gitlinks are archived;
* successor roots correspond to recorded predecessors;
* active refs do not retain retired history;
* optional bundles verify;
* optional external dependency availability.

---

# 40. `git epoch reconstruct`

Examples:

```bash
git epoch reconstruct
git epoch reconstruct ambition
git epoch reconstruct ambition --recursive
git epoch reconstruct --materialize
```

Default behavior should avoid altering the user's existing development checkout.

---

# 41. `git epoch inspect`

Useful archaeology helpers:

```bash
git epoch list
git epoch show ambition:2
git epoch log ambition
git epoch boundary ambition:2..3
```

Example:

```text
ambition epoch 2

root: ...
tip: ...
commits: 12,483
started: ...
closed: ...
history store: ...

successor:
  epoch 3
  root: ...

boundary:
  recursive equivalence
  renderer:
      old ...
      new ...
```

---

# 42. `git epoch gc`

The tool should distinguish:

* normal Git garbage collection;
* archive deletion.

`git epoch gc` should never discard archived epochs merely because they are unreachable from ordinary branches.

Archive refs must pin every retained epoch.

Any destructive archival pruning should use a separate explicit operation.

---

# 43. Checkpoint transaction directory

Before mutation, create a persistent transaction description:

```text
.git/epoch/transactions/<uuid>/
```

containing:

```text
plan.yaml
state.yaml
old-refs.yaml
new-refs.yaml
verification.yaml
```

If interrupted, a subsequent command can report:

```text
incomplete checkpoint found
```

and offer:

```text
resume
abort
inspect
```

Even with a maintenance-window publication model, local crash recovery should be deterministic.

---

# 44. Operation ordering

A checkpoint should roughly perform:

```text
DISCOVER
    ↓
PLAN
    ↓
VERIFY INPUTS
    ↓
ARCHIVE OLD EPOCHS
    ↓
VERIFY ARCHIVES
    ↓
CREATE SUCCESSOR ROOTS
    ↓
VERIFY BOUNDARIES
    ↓
PREPARE ACTIVE REFS
    ↓
OPTIONALLY CREATE BUNDLES
    ↓
VERIFY FINAL MODEL
    ↓
PUBLISH
    ↓
FRESH-CLONE TEST
    ↓
COMPLETE TRANSACTION
```

At no point should old active history be discarded before archive verification succeeds.

---

# 45. Fresh-clone acceptance test

A checkpoint is not complete merely because force pushes succeed.

The tool should clone the resulting active repositories into temporary directories using ordinary clone behavior.

It should verify:

```text
expected HEAD
expected submodule commits
recursive checkout works
old epochs are unreachable from normal refs
clone size
archive/package size
```

Example output:

```text
POST-CHECKPOINT VALIDATION

ambition:
  ordinary clone: 14.2 MiB
  archive tar.gz: 17.6 MiB
  HEAD: verified

renderer:
  ordinary clone: 6.4 MiB
  HEAD: verified

recursive submodules: verified

retired epoch reachable from active refs: no

result: PASS
```

---

# 46. Measuring active history and archived epochs

The tool should distinguish:

```text
working-tree size
reachable Git-object size
fresh clone size
compressed source-package size
shared history-store file bytes
shared history-store allocated disk bytes
standalone per-epoch bundle size
per-epoch reachable object bytes
per-epoch exclusive object bytes
```

These answer different questions. Archived epochs may share objects in one
history store, so per-epoch reachable sizes are not additive. A standalone
bundle is the cleanest independently restorable size for one epoch. Exclusive
object bytes identify the part of the shared store reachable from only that
epoch record. The history-store directory size remains the physical whole-store
measurement, and allocated filesystem bytes are important before `git gc`
because many loose objects can consume much more block space than their file
contents.

`git epoch stats` should report those measures without mutating the archive.
`git epoch gc` should report the before/after measurements and deep-verify the
archive after repacking. Sandbox statistics may additionally build a temporary
full-history `archive_source` package from the verified recursive fresh clone so
the user can compare the post-checkpoint handoff artifact directly against a
size target.

A configurable acceptance policy might be:

```yaml
limits:
  fresh_clone_git_size: 25MiB
  agent_source_tarball: 20MiB
```

A checkpoint command can fail acceptance if the resulting repository remains too large.

This may reveal that current tracked assets rather than history dominate the clone.

---

# 47. Dirty worktrees

Default:

```text
checkpoint requires clean working tree
```

An implementation may later support capturing dirty state, but it adds little value to the archival model and complicates verification.

Version one should reject it.

---

# 48. Multiple worktrees

The tool must detect Git linked worktrees.

A destructive checkpoint should refuse to proceed if other worktrees make assumptions about branches being rewritten unless explicitly handled.

At minimum it should report all worktrees.

---

# 49. Hooks and CI

Checkpointing may trigger:

* server hooks;
* GitHub Actions;
* branch protection;
* deployment automation;
* bots.

These are operational concerns rather than archive semantics, but `git epoch plan` should list affected remotes and refs before publication.

---

# 50. Signed commits and tags

Archived signatures remain valid because archived commit and tag objects remain unchanged.

Synthetic successor roots may be signed independently if desired.

A materialized reconstruction necessarily changes commit IDs and therefore should not claim to preserve original commit signatures.

---

# 51. Merge commits

Epochs retain merge topology exactly.

The tool should avoid checkpointing in the middle of an unresolved merge or rebase.

The successor epoch begins from the final selected snapshot rather than attempting to reproduce historical parent topology across the boundary directly.

---

# 52. Multiple roots in epoch zero

Existing repositories may already contain disconnected histories.

Epoch zero should simply archive all selected reachable refs and their DAGs.

The archive model does not require one root.

---

# 53. Object retention

Archived epochs remain alive because archive refs point into them.

No archival correctness should depend on reflogs or Git's unreachable-object grace period.

The history store should survive aggressive:

```bash
git gc
```

without losing archived content.

---

# 54. History-store immutability policy

Archived epoch Git objects are content-addressed and therefore immutable individually.

Refs and manifests can still be changed.

The default tool policy should prohibit changing a closed epoch's archived ref mapping after verification except through an explicit repair command.

Example:

```bash
git epoch repair ...
```

Repair should create an audit record describing what changed.

---

# 55. Can epochs themselves later be reorganized?

Technically yes.

Suppose an archive initially contains:

```text
epoch 0:
A---B---C---D

epoch 1:
R1---E---F
```

The system could later divide epoch zero into multiple archival namespaces without changing commit objects.

For example:

```text
epoch 0a -> B
epoch 0b -> D
```

provided all necessary boundary metadata is recomputed and verified.

However, this should be considered archive maintenance rather than normal checkpoint operation.

Commit objects should remain untouched whenever possible.

---

# 56. Repository identity versus history-store placement

A repository must retain the same logical identity even if its epochs move between stores.

For example:

```text
renderer epoch 0 -> old-company-vault
renderer epoch 1 -> ambition-history
renderer epoch 2 -> ambition-history
```

The manifest may resolve all three.

This avoids tying logical history to one physical archive repository forever.

---

# 57. History-store federation

Long term, the manifest could support:

```yaml
stores:
  store-a:
    url: ...

  store-b:
    url: ...

epochs:
  renderer:
    - number: 0
      store: store-a
    - number: 1
      store: store-b
```

Version one does not need federation, but identifiers should avoid preventing it.

---

# 58. Security and integrity

The tool should treat history stores as archival data.

Recommended verification data:

```text
Git object IDs
epoch ref OIDs
manifest commit OID
bundle checksum
optional external SHA-256 checksum
```

Git object verification and bundle verification should be run before active-history removal.

---

# 59. Archive manifest schema versioning

Every manifest must contain:

```yaml
format_version: 1
```

Schema migrations should be explicit.

Old manifests should remain readable where possible.

Archive interpretation must never depend only on the currently installed implementation.

---

# 60. Implementation architecture

A clean implementation can be separated into layers.

## Git plumbing layer

Operations such as:

```text
resolve refs
read commits
read trees
create commits
copy/fetch objects
create/update refs
bundle
fsck
clone
```

Use Git CLI plumbing rather than implementing Git object semantics directly unless a compelling reason appears.

---

## Repository model layer

Represents:

```text
RepositoryIdentity
RepositoryPolicy
Epoch
EpochRef
Boundary
SubmoduleOccurrence
HistoryStore
```

---

## Snapshot verifier

Implements:

```text
exact_tree_equal()
recursive_snapshot_equal()
```

This is one of the core correctness components and should have strong tests.

---

## Planner

Computes the complete desired transition without modifying repositories.

Input:

```text
repository graph
current refs
policies
history stores
```

Output:

```text
CheckpointPlan
```

---

## Executor

Applies a previously validated plan.

It should avoid rediscovering policy during execution.

---

## Reconstructor

Loads epochs and generates:

```text
replacement refs
local repository mappings
submodule URL overrides
```

---

## Validator

Performs:

```text
preflight
archive verification
boundary verification
post-publication fresh-clone validation
```

---

# 61. Data structures

Conceptually:

```python
class Repository:
    id
    policy
    active_urls
    history_store
    object_format


class Epoch:
    repository_id
    number
    refs
    roots
    primary_tip
    history_store


class Boundary:
    repository_id
    predecessor_commit
    successor_root
    equivalence
    submodule_translations


class SubmoduleTranslation:
    path
    repository_id
    old_commit
    new_commit
    boundary_id
```

The actual implementation language can differ.

---

# 62. Plan/apply workflow

A particularly useful UX is:

```bash
git epoch plan --recursive > checkpoint-plan.yaml
```

Inspect it.

Then:

```bash
git epoch apply checkpoint-plan.yaml
```

The plan should contain exact OIDs, making stale plans detectable.

If any referenced source ref changes between plan and apply:

```text
ERROR: repository changed since plan generation
```

The user regenerates the plan.

This fits the maintenance-window model well.

---

# 63. Idempotency

Commands should be safe to retry after partial failure.

For example, if an epoch has already been copied to a history store with the expected refs:

```text
archive step already complete
```

should be recognized rather than treated as a conflicting second archive.

Successor root creation should similarly use recorded OIDs where possible.

---

# 64. Failure model

Potential failures include:

```text
archive unavailable
bundle creation failure
fsck failure
submodule commit unavailable
manifest write failure
successor verification failure
force-push rejection
fresh clone failure
machine crash
```

For every step, the implementation should know whether:

```text
active repository has changed
archive has changed
remote refs have changed
```

and report a precise recovery path.

---

# 65. Strong safety rule

The central destructive safety rule should be:

> No active ref that provides the only ordinary reachability path to an epoch may be removed or force-updated until that epoch has been copied into configured archival storage and verified.

This should be enforced in code rather than left to workflow documentation.

---

# 66. First implementation scope

Version one should deliberately keep scope constrained.

Support:

* SHA-1 Git repositories;
* one main worktree;
* clean working tree;
* one primary branch per epoch;
* local or ordinary Git remote history stores;
* `epoch`, `continuous`, and `external` repository policies;
* recursive submodules;
* plan/apply;
* exact and recursive boundary verification;
* reconstruction using derived replace refs;
* bundle backup;
* fresh-clone acceptance testing.

Defer:

* automatic distributed locking;
* server-side transactional publication;
* arbitrary partial branch rollover;
* sophisticated release migration;
* Git LFS special handling;
* SHA-1/SHA-256 mixed shared stores;
* graphical UI;
* automatic materialized reconstruction;
* archive federation.

---

# 67. Essential test scenarios

The test suite should build real temporary Git repositories.

## Basic repository

```text
A---B---C
```

Checkpoint.

Verify:

```text
archive contains A/B/C
active contains only successor epoch
tree(C) == tree(root)
```

---

## Repeated checkpoint

Create:

```text
epoch 0
epoch 1
epoch 2
```

Verify all boundaries.

---

## Merge history

Create a branch and merge within an epoch.

Verify archive topology remains exact.

---

## Historical tags

Tag commits in old epoch.

Verify tag object preservation and active-reachability policy.

---

## Managed epoch submodule

Create:

```text
parent
└── child
```

Checkpoint both.

Verify recursive snapshot equivalence.

---

## External submodule

Checkpoint parent while leaving child untouched.

Verify gitlink equality.

---

## Mixed submodules

Create:

```text
parent
├── epoch-child
├── continuous-child
└── external-child
```

Verify only the epoch-child gitlink translates.

---

## Nested managed submodules

Create:

```text
A
└── B
    └── C
```

Checkpoint recursively.

Verify leaf-to-root construction.

---

## Reconstruction

Generate several epochs.

Reconstruct.

Test:

```text
git log
git show
git diff
git blame
checkout commits from every epoch
```

---

## Historical recursive checkout

Create old parent commits referring to old child commits.

After reconstruction, verify those exact child commits can still be checked out.

---

## Interrupted checkpoint

Terminate execution after each major phase and verify resume or abort behavior.

---

## Garbage collection

Aggressively GC history stores.

Verify every archived epoch remains intact.

---

## Fresh clone

After checkpoint, ordinary clone the active repo and verify retired epochs are absent.

---

# 68. Acceptance criteria

The design is successful when all of the following can hold simultaneously.

### Active use

```bash
git clone <active-repo>
```

produces a small normal repository requiring no epoch-specific knowledge.

### Archive

Every retired commit remains available by its original OID.

### Continuity

Every boundary is mechanically verified.

### Recursive continuity

Managed submodule rollover preserves the recursively interpreted source snapshot.

### Reconstruction

A user can explicitly assemble all epochs and traverse historical lineage locally.

### External dependencies

Upstream repositories remain untouched.

### Repeatability

The operation can be performed indefinitely as active history grows.

### Recovery

No failure can cause the sole verified copy of a retired epoch to be discarded.

---

# 69. Example complete lifecycle

Initial state:

```text
ambition:
A---B---C

renderer:
X---Y
```

Ambition points to `renderer@Y`.

Both repositories use epoch policy.

Run:

```bash
git epoch init ambition --recursive
```

Archive:

```text
ambition epoch 0:
A---B---C

renderer epoch 0:
X---Y
```

Create:

```text
renderer epoch 1:
RY
```

where:

```text
tree(Y) == tree(RY)
```

Then create:

```text
ambition epoch 1:
RA
```

whose gitlink points to:

```text
renderer@RY
```

Verify:

```text
snapshot(C) == snapshot(RA)
```

Development continues:

```text
renderer:
RY---Z---Q

ambition:
RA---D---E
```

where Ambition's appropriate commits record the corresponding renderer commits.

Six months later:

```bash
git epoch checkpoint ambition --recursive
```

Archive exact epoch-one histories.

Generate renderer successor:

```text
RQ
```

Generate Ambition successor:

```text
RE
```

with the translated renderer gitlink.

Verify recursively.

Force-update the active remotes during the maintenance window.

Fresh-clone everything.

Develop again.

Years later:

```bash
git epoch reconstruct ambition --recursive
```

The system loads:

```text
Ambition epochs 0..N
Renderer epochs 0..M
```

recreates epoch-boundary replacement relationships, configures reconstructed submodule repositories, and provides a full archaeology checkout containing all original archived Git objects.

---

# 70. Design principle

The tool should make a distinction between three forms of identity:

### Commit identity

The actual Git object ID.

Archived commits preserve this forever.

### Snapshot identity

The recursively interpreted project state.

Different epoch-root commits may represent the same snapshot.

### Lineage identity

The recorded relationship saying that a successor epoch continues from a predecessor epoch.

Git ancestry represents lineage within an epoch.

The epoch manifest represents lineage across epochs.

This separation is the core abstraction that makes the system work with submodules.

---

# 71. Final recommended architecture

Use:

```text
active Git repositories
    bounded current history

history-store Git repository/repositories
    exact archived Git objects
    namespaced epoch refs

versioned manifest
    repository identities
    policies
    epoch metadata
    boundary relationships
    submodule mappings

optional bundles
    immutable offline backups

reconstruction tooling
    derived refs/replace relationships
    local submodule URL mappings
```

Treat:

```text
epoch
continuous
external
```

as explicit repository policies.

Require exact raw-tree continuity where possible.

Use recursive snapshot continuity only where managed submodule epoch transitions require gitlink translation.

Keep archived Git commits unchanged.

Treat reconstruction metadata as derived from the manifest.

Perform recursive rollover leaf-first.

Use a maintenance window for publication.

Require archive verification before destructive active-history updates.

Finally, verify the result by performing the operation users actually care about:

```bash
git clone <active-repository>
```

and measure that clone.

That keeps the fundamental purpose of the system testable: complete historical provenance remains available, while the repository used for current development stays cheap.
