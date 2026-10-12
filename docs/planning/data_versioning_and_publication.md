# Transport-Neutral Data Versioning and Publication

Status: planning / architecture draft

Initial home: `git-well`

Possible future home: a standalone project if the data-management layer grows beyond the scope of small Git utilities.

## 1. Motivation

`git-well` has an experimental `git ipfs` workflow for keeping large data out of Git while committing small `*.ipfs` sidecars. That experiment exposes a broader problem worth solving deliberately:

> How can a large directory tree be versioned, checked for local changes, published through one or more distributed or centralized storage systems, and materialized for ordinary applications without making any one storage system the permanent definition of the dataset?

The immediate use case is a large ML image collection. The desired properties are broader:

- Data should remain ordinary files on disk so existing ML tools can read it directly.
- A snapshot should be immutable and cryptographically identifiable.
- Individual files should remain independently identifiable and, where a backend permits it, independently retrievable.
- Checking whether a working tree changed should normally require filesystem metadata reads, not rereading terabytes of content.
- A definitive verification path must still exist.
- There should be no required filesystem watcher or persistent daemon.
- There should be no mandatory second local copy of the entire dataset.
- Publishing through IPFS should be possible, but the durable dataset identity should not depend on IPFS surviving as an ecosystem.
- BitTorrent, HTTP/web seeds, S3-compatible object stores, SSH-accessible storage, or future backends should be able to publish the same logical snapshot.
- Adding or changing a publication location must not change the identity of the dataset contents.
- Packing data into an archive may be useful for some transports or workloads, but should be a derived optimization rather than the default logical representation.

The working thesis is:

> **Separate dataset identity and versioning from publication and transport.**

Git can initially version the small metadata that describes snapshots while the large bytes live in the normal filesystem and are published through one or more independent backends.

## 2. Why this belongs in `git-well` first

The current `git ipfs` experiment already contains several pieces of the problem:

- Git-tracked sidecars describe content outside Git.
- IPFS provides content-addressed publication.
- `git ipfs status` attempts a cheap local drift check.
- `git ipfs status --full` can recompute the IPFS root CID with `ipfs add --only-hash`.
- Sidecars can carry retrieval hints such as suggested peers.
- The user workflow is naturally tied to Git commits even though the large data is not stored in Git objects.

That makes `git-well` a good incubation point. However, the intended architecture should not assume that the final implementation must remain a subcommand of a miscellaneous Git utility collection.

A split into a standalone repository should become attractive when several of the following are true:

1. The data layer has multiple serious publication backends rather than only IPFS.
2. It has a persistent schema, migration policy, and local index of its own.
3. Useful workflows no longer require a Git repository.
4. The library/API surface becomes useful independently of the `git-well` CLI.
5. Release cadence and testing requirements materially diverge from the rest of `git-well`.
6. The project needs its own protocol/specification documentation.

If that happens, `git-well` should retain a thin integration layer rather than forcing the implementation to remain inside this repository.

## 3. Current prototype and its limitations

As of this planning draft, the IPFS implementation is a useful prototype rather than the desired end state.

The current quick status path recursively walks the tracked tree and reduces it to an aggregate approximately equivalent to:

```text
kind
sum(file sizes)
max(file mtime)
number of files
```

The current comparison primarily uses total bytes and maximum mtime. A full check recomputes the IPFS CID.

This has several problems:

- It still recursively visits the complete tree.
- Aggregate size and maximum mtime can miss changes.
- Renames can be invisible to the aggregate.
- Equal-size edits can be invisible when timestamps are preserved or restored.
- It cannot report which paths are candidates for change.
- Machine-local quick-status metadata can be written into the portable sidecar even though it is not part of the durable content identity.
- A strong check is all-or-nothing at the root rather than reusing per-file verification state.

The long-term design should keep the useful idea of a cheap heuristic plus a strong verification path, but implement the heuristic as a Git-like local index rather than an aggregate fingerprint.

## 4. Core design principles

### 4.1 Dataset identity is not a transport identity

An IPFS root CID, BitTorrent infohash, object-store URL, or server path is a **publication identity** or locator. None should be the sole durable identity of the logical dataset.

A dataset snapshot should have a transport-neutral identity derived from its logical file tree and file content identities.

Conceptually:

```text
                    logical dataset snapshot
                              |
                  transport-neutral identity
                              |
          +-------------------+-------------------+
          |                   |                   |
        IPFS              BitTorrent          central CAS
      root CID            infohash             manifest
          |                   |                   |
      peer hints        trackers/webseeds      remote URL
```

The publication records may change while the logical snapshot remains identical.

### 4.2 Portable metadata and local acceleration state are different things

Portable committed metadata should contain information necessary to identify and reproduce a snapshot.

Machine-local state such as inode numbers, mtimes, cached directory listings, filesystem capabilities, or the last successful stat scan belongs in a local index, not in the committed snapshot description.

Within `git-well`, a natural location is a worktree-specific Git metadata path such as:

```text
<resolved-git-dir>/git-well/dataset-index.sqlite
```

The implementation should resolve this through Git rather than assuming that `.git` is a directory. Linked worktrees, submodules, and gitfiles must remain possible.

If the project later becomes standalone, the storage location can change without changing the portable snapshot format.

### 4.3 Metadata-clean is not cryptographically verified

A stat cache is an optimization, not a proof.

The UI and internal state model should preserve this distinction. A useful vocabulary is:

```text
CLEAN_METADATA    metadata matches the local index
DIRTY_CANDIDATE   metadata suggests the path changed
VERIFIED          content was cryptographically checked and matches
CHANGED           verified content differs from the snapshot
MISSING           a tracked path is absent
UNKNOWN           the index cannot safely make a metadata-clean claim
```

Exact names are not frozen, but the semantic distinction is important.

### 4.4 No watcher is required

The baseline model should deliberately resemble ordinary Git without FSMonitor:

- stat known files;
- hash only paths whose metadata indicates possible change;
- use cached directory metadata to reduce directory enumeration for additions/removals;
- fall back to a stronger check when cached metadata is ambiguous.

A filesystem watcher or FSMonitor-style integration may be added later as an optional optimization. It must not be a correctness dependency.

### 4.5 No mandatory archive

The logical dataset is a tree of independently identifiable files.

This is especially valuable for already-compressed images because an archive often gives limited compression benefit while making direct item access less natural.

Packing remains useful in some situations:

- millions of very small files;
- high metadata latency;
- object-store request overhead;
- sequential streaming workloads;
- WebDataset-style training;
- transport formats that naturally want packed blocks.

The architecture should therefore allow **derived pack layouts**, but packing should not define the dataset identity.

A future pack record can map logical items to a packed representation without changing the logical snapshot:

```text
logical snapshot
    a.jpg -> sha256:...
    b.jpg -> sha256:...

publication/layout A
    loose objects

publication/layout B
    shard-00017.tar
        a.jpg @ offset ...
        b.jpg @ offset ...
```

### 4.6 Do not require duplicate local storage

The working tree should be a valid primary local representation.

Backends should consume bytes from the existing files directly when possible. A backend may require its own cache or blockstore, but that should be an explicit backend cost rather than a requirement of the versioning model.

For example:

- BitTorrent can seed from the ordinary files.
- A centralized remote can upload directly from the ordinary files.
- HTTP can serve the ordinary files.
- IPFS may use Kubo's normal blockstore, a no-copy/filestore mode, a remote provider, CAR streaming, or future mechanisms. The dataset model should not require one of these.

### 4.7 Publication is additive and replaceable

The same immutable snapshot may have zero, one, or many publications.

A snapshot can start with only an IPFS publication, later gain a torrent and HTTP web seed, and later move to a different centralized service without changing its logical identity.

This is the main hedge against ecosystem risk.

## 5. Proposed conceptual model

The system should distinguish at least four concepts.

### 5.1 Working tree

The ordinary files applications read:

```text
data/
    images/
        a.jpg
        b.jpg
    annotations.json
```

These are not required to be Git-tracked.

### 5.2 Snapshot manifest

A portable description of the immutable logical tree.

Conceptually:

```yaml
schema_version: 1
kind: dataset-snapshot
snapshot_id: sha256:...
root_path_hint: data
entries:
  - path: annotations.json
    type: file
    size: 91822
    content: sha256:...
  - path: images/a.jpg
    type: file
    size: 832771
    content: sha256:...
  - path: images/b.jpg
    type: file
    size: 991334
    content: sha256:...
```

This example is conceptual, not a frozen serialization format.

Important properties:

- Paths are relative to the snapshot root.
- Entries have stable content identities.
- File mtimes, inode numbers, owners, and local absolute paths are not part of content identity.
- Serialization details must not accidentally define semantics unless canonicalization is explicitly specified.
- Empty directories, symlinks, executable bits, and other filesystem details need explicit policy before a v1 format is frozen.

### 5.3 Local dataset index

A disposable acceleration database associated with one checkout/worktree.

A SQLite implementation is an attractive starting point because it is transactional, inspectable, handles millions of rows, and does not require a daemon.

Conceptual tables:

```text
snapshots
    snapshot_id
    manifest_path
    root_path
    indexed_at_ns
    verified_at_ns
    filesystem_fingerprint / capability state

files
    snapshot_id
    relative_path
    content_id
    size
    mtime_ns
    ctime_ns
    mode_or_type
    device        # optional local hint
    inode         # optional local hint
    last_stat_ns
    last_verify_ns

directories
    snapshot_id
    relative_path
    mtime_ns
    ctime_ns
    cached_children_digest
    last_scan_ns
```

The exact schema can change freely because the index is rebuildable.

### 5.4 Publication descriptors

Publication metadata maps a logical snapshot to one or more ways to retrieve it.

This should be semantically separate from the snapshot identity.

Examples:

```yaml
snapshot: sha256:...
publications:
  - backend: ipfs
    root_cid: bafy...
    import_profile:
      cid_version: 1
      raw_leaves: false
    suggested_peers:
      - /dnsaddr/data.example.org/tcp/4001/p2p/12D3KooW...

  - backend: bittorrent-v2
    infohash: ...
    torrent: dataset.torrent
    trackers:
      - ...
    webseeds:
      - https://data.example.org/releases/example/

  - backend: cas-http
    remote: public-data
    manifest_url: https://data.example.org/cas/manifests/sha256-....json
```

The exact packaging of this metadata is open. It could remain in backend-specific sidecars such as `*.ipfs`, with a transport-neutral snapshot reference added gradually.

## 6. Snapshot identity

### 6.1 Per-file identity

The initial durable per-file identity should use a widely supported cryptographic digest. SHA-256 is a strong default candidate because:

- it is broadly available;
- it is not tied to a specific storage network;
- Git LFS and many artifact systems already use it;
- it is suitable for long-lived interoperability.

The schema should be algorithm-tagged rather than assuming SHA-256 forever:

```text
sha256:<hex>
```

A faster digest such as BLAKE3 may also be cached locally or supported as an additional identity, but the portable format should remain algorithm-agile.

### 6.2 Snapshot/root identity

The snapshot ID should be a digest over the **semantic manifest**, not an IPFS importer result and not arbitrary YAML bytes.

Before freezing a format, define an unambiguous canonical encoding of at least:

```text
format/version tag
path
entry type
size where applicable
content digest or symlink target
any filesystem attributes declared identity-bearing
```

Entries should have a deterministic order.

Two reasonable implementation approaches are:

1. Canonical structured serialization with a precisely specified canonicalization rule.
2. A small length-prefixed record stream specifically designed for hashing.

The second has the advantage that human-readable YAML/JSON can change without changing snapshot identity as long as the semantics are unchanged.

Do **not** define the durable snapshot ID as `sha256(manifest.yaml bytes)` unless the manifest serialization itself is intentionally canonical and versioned.

### 6.3 What should not affect identity

By default, these should not affect a data snapshot identity:

- mtime/ctime;
- inode/device numbers;
- local absolute path;
- username/group ownership;
- publication URLs;
- peer addresses;
- trackers;
- remote credentials;
- cache state;
- download timestamps.

Open questions remain around symlinks, executable mode, empty directories, and platform-specific path behavior.

## 7. Git-like local change detection

The core status mechanism should copy the important idea from Git's index: use cached filesystem metadata to decide when file contents need to be inspected.

### 7.1 Known-file scan

For every file recorded in the snapshot/local index:

```text
lstat(path)

missing
    -> MISSING

type differs
    -> DIRTY_CANDIDATE

size / mtime_ns / ctime_ns / relevant mode differs
    -> DIRTY_CANDIDATE

stat metadata safely matches
    -> CLEAN_METADATA

stat metadata matches but is temporally ambiguous
    -> hash / verify this file
```

Device/inode information can be useful local hints but should be treated conservatively. A changed inode can identify a replacement candidate, but an inode match does not prove byte equality.

### 7.2 Why this is useful

For a dataset containing terabytes of images, the cost difference is approximately:

```text
strong full verify:
    read every byte

Git-style status:
    stat every tracked file
    read only candidate-changed / ambiguous files
```

Both may be O(number of files) in path visits, but the I/O volume is radically different.

### 7.3 Detecting new files

Known-file stats cannot discover a new child name. Directory state must be tracked separately.

The local index should cache directory metadata and known children. If a directory's metadata indicates no directory-entry change, its previous child listing can be reused. If it changed, enumerate that directory and compare child names.

Conceptually:

```text
tracked files
    -> lstat known paths
    -> detect modification/deletion/type changes

tracked directories
    -> stat directory
    -> if metadata unchanged, reuse cached child names
    -> if changed, readdir and compare
    -> detect additions/removals/renames
```

Editing bytes inside `dir/file.jpg` normally changes the file metadata but not the directory entry metadata. Adding/removing/renaming entries changes directory metadata. The two caches serve different purposes.

### 7.4 Filesystem capability assumptions

Directory mtime caching is an optimization that depends on filesystem behavior. Network filesystems, unusual mounts, timestamp resolution, and platform semantics can weaken the assumptions.

The system should therefore support:

- a capability/probe result stored locally;
- a configuration knob to disable directory-cache trust;
- a fallback that enumerates directories when confidence is insufficient;
- a strong verify command independent of the metadata cache.

The local index is disposable. Deleting it should degrade performance, not correctness or recoverability.

### 7.5 Racy-clean handling

A stat cache can be fooled when a file changes within the filesystem timestamp-resolution window and ends with metadata identical to the cached record.

Git calls the analogous problem "racy Git" and falls back to content inspection in ambiguous cases.

The data index should use the same principle.

At snapshot/index-write time, record sufficient timing information to determine whether a matching timestamp is potentially ambiguous. If so, verify the file rather than trusting metadata.

The exact rule must account for filesystem timestamp granularity rather than assuming nanosecond fields imply nanosecond accuracy.

A deliberate attacker can restore size and timestamps, so metadata-clean must never be described as cryptographic proof.

### 7.6 Index refresh after a verified candidate

When metadata says a file may have changed:

1. Compute its durable content digest.
2. Compare with the snapshot entry.
3. If the digest is unchanged, refresh the local stat record and mark it metadata-clean again.
4. If the digest differs, report `CHANGED` and retain enough information to show the path-level difference.

This means a harmless metadata-only touch becomes cheap after one rehash rather than making every future status remain dirty.

### 7.7 Strong verification

A strong verify command should ignore metadata-clean shortcuts for the selected scope and cryptographically establish equality with the snapshot manifest.

Conceptually:

```bash
git well data verify <dataset>
```

or, during transition:

```bash
git ipfs status --verify
```

The existing `git ipfs status --full` behavior is an initial strong check for the IPFS-specific representation. A future transport-neutral verify should compare per-file durable digests and the derived snapshot identity, not require IPFS.

### 7.8 No watcher in the initial architecture

A watcher can later reduce status from roughly O(tracked paths) to O(changed paths), but it adds persistent state and failure modes:

- reboot discontinuity;
- queue overflow;
- remounts;
- filesystems without reliable event APIs;
- synchronization across multiple processes.

The first serious implementation should intentionally avoid this complexity. If scale measurements later show metadata traversal is still too expensive, an FSMonitor-like optional layer can be added above the index.

## 8. Local index lifecycle

### 8.1 Build

The index can be populated when a snapshot is created or after a strong verification.

For each path, record both durable content identity and local stat metadata.

### 8.2 Refresh

Normal status operations update local stat records for paths that were reverified and found content-identical.

### 8.3 Rebuild

If the index is missing, corrupt, belongs to a different snapshot, or fails a filesystem capability check, it can be rebuilt from the portable manifest and working tree.

### 8.4 Invalidate conservatively

Cases that should invalidate some or all metadata trust include:

- snapshot manifest changed;
- root path moved across filesystems where local inode/device hints no longer apply;
- filesystem capability assumptions changed;
- detected timestamp anomalies;
- database schema migration failure;
- explicit user request.

### 8.5 Concurrency

The index should use transactional updates and a clear lock policy. Status readers should never observe a half-refreshed state.

SQLite provides a practical basis for this, but concurrency behavior must be tested on the supported filesystems.

## 9. Versioning workflow

The intended workflow is Git-like without requiring Git to track millions of raw files.

A conceptual future CLI is:

```bash
# Create or update the logical snapshot description.
git well data snapshot data/

# Cheap status based on the local index.
git well data status data.dataset

# Strong verification.
git well data verify data.dataset

# Publish the immutable snapshot through one or more backends.
git well data publish data.dataset --backend=ipfs
git well data publish data.dataset --backend=torrent
git well data publish data.dataset --remote=lab-storage

# Materialize all or part of it elsewhere.
git well data pull data.dataset
git well data pull data.dataset --path images/example.jpg
```

Names are intentionally provisional.

Git would normally track only compact metadata such as:

```text
data.dataset
publishing/data.ipfs
data.torrent              # if small enough / appropriate to commit
```

The logical history is then ordinary Git history of immutable snapshot descriptions.

A changed dataset does not mutate an old snapshot. It creates a new snapshot description whose unchanged files keep the same content identities.

## 10. Why not make Git track every large file through a clean/smudge filter?

A Git-LFS-style mode is possible and may eventually be useful, but it should not be the foundation of the first architecture.

A clean/smudge filter could make Git store a small pointer for each large file while the worktree contains real bytes. That gives excellent file-level Git ergonomics, but it also means:

- Git's index contains every dataset path;
- a dataset with millions of files becomes a Git worktree with millions of tracked entries;
- checkout/smudge semantics can accidentally imply materializing huge amounts of data;
- repository operations become more tightly coupled to data availability;
- dataset-level snapshot operations become less explicit.

The manifest/index approach gets most of the desired status behavior without forcing raw data paths into Git's index.

A future optional filter mode can reuse the same content identities and publication backends if file-granular Git tracking proves valuable.

## 11. Publication backend contract

Backends should implement a common conceptual interface over an immutable snapshot.

### 11.1 Publish

Given:

- snapshot manifest;
- local root path;
- backend configuration;

produce:

- a publication identity/locator;
- optional backend metadata;
- enough information to verify that the publication corresponds to the snapshot.

### 11.2 Probe / availability

Determine whether some or all required objects are already available at a publication target.

This enables incremental uploads and publication health checks.

### 11.3 Fetch

Materialize all or a selected subset of snapshot entries into the working tree.

### 11.4 Verify publication

Establish that fetched or remotely available content corresponds to the logical snapshot.

Backend-native hashes are useful but must be mapped back to logical snapshot entries rather than blindly trusted as equivalent identities.

### 11.5 Advisory retrieval hints

Peer addresses, trackers, gateways, mirrors, and endpoints are hints. They are not part of content identity and may become stale.

Readers should ignore unknown advisory fields and use fallback discovery where possible.

## 12. IPFS backend

IPFS remains a useful backend because it naturally represents directory trees as content-addressed DAGs and supports independently addressable descendants.

### 12.1 Keep root CID and import profile as publication metadata

An IPFS publication record should contain at least:

```text
root CID
CID-affecting import parameters
```

Import parameters matter because the same ordinary file tree can have different IPFS CIDs depending on chunking/layout/codec choices.

That is exactly why the IPFS root CID should not be the transport-neutral dataset identity.

### 12.2 Suggested peers

Sidecars should support retrieval hints for peers likely to provide the data.

Full dialable multiaddrs are more useful than bare peer IDs when the purpose is to bypass unreliable provider discovery:

```yaml
suggested_peers:
  - /dnsaddr/storage.example.org/tcp/4001/p2p/12D3KooW...
```

A bare peer ID may remain a weaker hint if routing can discover its addresses.

Pull should make best-effort connections to hints, deduplicate hints across multiple sidecars, then fall back to normal IPFS routing.

Suggested peers should never be required for identity or verification.

### 12.3 Per-item addressing

IPFS already assigns identities to descendants in the UnixFS DAG. A backend-specific index may record path-to-CID mappings when useful for fast selective retrieval.

The transport-neutral layer should still identify files independently of those CIDs.

### 12.4 Duplicate storage concern

Kubo's normal import model may duplicate dataset bytes into the IPFS blockstore. No-copy/filestore-style modes can reduce this but have historically carried operational caveats.

The architecture should not solve this by making the Kubo blockstore the canonical local dataset. Instead:

- keep normal working files authoritative locally;
- expose backend storage cost clearly;
- support no-copy or remote-provider mechanisms where reliable;
- consider streaming CAR generation / remote ingest in the future;
- allow IPFS to be omitted when its local storage cost is not worthwhile.

### 12.5 Migration from existing `*.ipfs`

Existing sidecars should remain readable.

A migration path can be incremental:

1. Improve status with a local index while keeping current sidecars unchanged.
2. Introduce a transport-neutral snapshot manifest next to a sidecar.
3. Allow an IPFS sidecar to reference the snapshot ID.
4. Keep legacy fields sufficient for old readers.
5. Eventually treat IPFS sidecars as one publication descriptor among several.

## 13. BitTorrent backend

BitTorrent v2 is a strong match for loose-file dataset publication because it uses a file tree with per-file Merkle roots rather than requiring the logical dataset to be one archive.

### 13.1 Desired properties

A torrent publication should support:

- the same ordinary loose files as the working tree;
- selective file download;
- verification through the torrent's cryptographic structure;
- seeding directly from the existing files;
- trackers and/or DHT as peer discovery;
- HTTP(S) web seeds for durable origin availability;
- optional hybrid v1/v2 metadata when client compatibility requires it.

### 13.2 Publication metadata

Conceptually:

```yaml
backend: bittorrent-v2
snapshot: sha256:...
infohash: ...
torrent_file: dataset.torrent
trackers:
  - ...
webseeds:
  - https://data.example.org/releases/example/
```

The torrent's infohash is a publication identity, not the logical snapshot ID.

### 13.3 Why web seeds matter

Peer-to-peer publication has a common availability problem: no peer may currently be reachable.

A stable HTTP(S) origin plus web-seed metadata gives a useful hybrid:

- ordinary HTTP remains a dependable source;
- clients can also exchange pieces peer-to-peer;
- the origin need not carry all traffic once peers have data.

This is attractive for public dataset releases.

### 13.4 Efficient regeneration

A new logical snapshot may produce a new torrent infohash even when most files are unchanged.

The local index should eventually cache backend-specific expensive hashes, such as torrent piece/Merkle state, keyed by durable file identity. This can avoid rereading unchanged bytes when generating a new publication.

Backend-specific cached hashes belong in the disposable local index, not in the transport-neutral snapshot identity unless intentionally exposed.

## 14. Centralized content-addressed remotes

A centralized backend is important even if peer-to-peer publication works well. Centralized storage is often simpler operationally and provides a stable seed/source of truth for availability.

The simplest useful remote is a "dumb" content-addressed store.

Conceptual layout:

```text
objects/
    sha256/
        ab/
            cdef...      # file bytes
manifests/
    sha256-<snapshot>.json
```

### 14.1 Push

For each snapshot entry:

1. Determine whether the remote already has the content object.
2. Upload only missing objects.
3. Verify object identity at the remote boundary where practical.
4. Publish the snapshot manifest last as the commit point.

Unchanged files across dataset versions naturally deduplicate on the remote.

### 14.2 Pull

To materialize a snapshot:

1. Obtain the snapshot manifest.
2. Select all or a subset of paths.
3. Fetch missing objects by content identity.
4. Verify bytes before considering the path materialized.
5. Write atomically into the worktree.

### 14.3 Candidate transports

The same CAS semantics could be implemented over:

- local filesystem paths;
- SSH/SFTP;
- rsync-like endpoints;
- S3-compatible object stores;
- generic HTTP for read-only publication;
- institutional object storage;
- future services.

The remote abstraction should not require a specialized server for the simplest cases.

### 14.4 Credentials

Credentials and tokens are configuration, not portable snapshot metadata. Git-tracked publication descriptors may refer to a remote alias, while secrets remain in user/system configuration.

## 15. ML-facing requirements

The design must preserve a low-friction path for machine learning code.

### 15.1 Ordinary paths first

After materialization, a training program should normally see:

```text
/data/dataset/images/a.jpg
/data/dataset/images/b.jpg
```

It should not need to speak IPFS, BitTorrent, or a custom Python filesystem API just to open an image.

### 15.2 Partial materialization

Large datasets benefit from fetching a subset:

- one file;
- one directory prefix;
- a manifest-selected subset;
- perhaps eventually a list of content IDs.

Every backend should expose the strongest selective retrieval it can. A backend that cannot efficiently fetch one item may still be valid, but the limitation should be explicit.

### 15.3 Derived training layouts

A training pipeline may want shards, resized derivatives, chips, or caches. These should normally be modeled as separate derived datasets or publication layouts rather than mutating the raw snapshot.

The versioning layer should make lineage easy to record without forcing all derived products into the same physical tree.

## 16. Archive and packing policy

Do not conflate logical versioning with physical packing.

### 16.1 Default

Use independently addressable logical files.

For JPEG/PNG/WebP and similar data, recompressing them into a generic archive usually does not justify losing direct item semantics.

### 16.2 When packing becomes useful

Offer a derived pack layer when measurements show one or more of:

- directory traversal dominates status or training startup;
- millions of tiny files overload metadata services;
- object-store request costs dominate;
- sequential training benefits from large shards;
- a publication backend strongly prefers contiguous objects.

### 16.3 Pack mapping

A packed publication must retain a deterministic map from logical content identities/paths to pack objects and offsets or members.

The same logical snapshot can therefore have both loose and packed publications.

## 17. Publication durability and ecosystem independence

A key requirement is surviving backend churn over a multi-year horizon.

The system should assume:

- IPFS may become less convenient or disappear from the user's workflow;
- a tracker may die;
- a web host may move;
- an S3 bucket may be migrated;
- a commercial service may change terms;
- a new content-addressed system may become preferable.

The answer is not to predict which backend wins. The answer is to make backends replaceable.

If a snapshot manifest and its per-file hashes remain available, bytes can be republished through a new backend and independently verified.

That is the durable asset.

## 18. Relationship to Git, Git LFS, DVC, Xet, and related systems

This design intentionally borrows ideas from several systems without requiring compatibility with all of them.

### 18.1 Git

Borrow:

- index-style stat caching;
- separation of durable object identity from worktree metadata;
- racy-clean handling;
- optional directory/untracked caching;
- optional FSMonitor as an acceleration layer rather than the baseline.

Do not require:

- putting large data bytes into Git objects;
- putting every dataset file into Git's own index.

### 18.2 Git LFS

Borrow:

- small Git-tracked metadata pointing at large content;
- content-addressed large objects;
- the principle that working files can remain ordinary files.

Do not initially require:

- clean/smudge filters;
- per-file Git pointer blobs;
- automatic materialization during checkout.

### 18.3 DVC-like systems

Borrow:

- dataset-level metadata tracked by Git;
- remote backends;
- reproducible materialization.

Different priority here:

- avoid making a mandatory local duplicate cache the core model;
- preserve loose-file arbitrary item identity;
- support peer-to-peer publication as a first-class backend.

### 18.4 Xet-style systems

Borrow conceptually:

- content-addressed data separated from Git history;
- deduplicated remote storage;
- local acceleration/cache state;
- efficient reuse across versions.

Avoid making the durable format depend on a single hosted service or proprietary server implementation.

## 19. Safety and trust boundaries

### 19.1 Paths

Portable manifests must reject absolute paths and path traversal such as `..` escaping the snapshot root.

Materialization must never write outside the explicitly allowed root unless the user opts into that behavior.

### 19.2 Symlinks

Symlink policy must be explicit before schema v1 is frozen.

Possible policy:

- represent a symlink by its link text as a distinct entry type;
- never follow a manifest symlink to write outside the root during materialization;
- reject special device files and sockets by default.

### 19.3 Remote hints are untrusted

Peer hints, URLs, trackers, and mirrors can be stale or malicious. Content verification must happen independently of the locator.

### 19.4 Resource exhaustion

A manifest may describe more data than expected. Pull commands should support dry-run/plan output with total expected bytes and file counts before materialization.

### 19.5 Secrets

No credentials in committed manifests or sidecars.

## 20. Garbage collection and retention

Content-addressed storage creates a lifecycle problem: when may an object be deleted?

The initial implementation should be conservative.

### 20.1 Local working files

Never delete working-tree data merely because it is no longer referenced by the current Git commit.

Deletion should be explicit.

### 20.2 Central remotes

Remote garbage collection should require an explicit reachability policy, for example:

- preserve all published snapshots;
- preserve snapshots reachable from selected Git refs/tags;
- preserve named releases;
- expire explicitly marked temporary snapshots.

A dry-run reachability report should precede deletion.

### 20.3 IPFS

Pinning/retention is backend-specific publication state. Dataset identity must remain meaningful after a pin disappears.

### 20.4 Torrents

A `.torrent`/infohash may outlive active seeds. Web seeds or central mirrors can provide durability even when peer availability is low.

## 21. Performance goals

The architecture should be evaluated with explicit performance properties rather than intuition alone.

### 21.1 Normal status

For a metadata-clean tree:

- read no file payload bytes;
- perform approximately one stat-family operation per tracked file unless a later optional monitor reduces this;
- avoid recursive directory enumeration when directory cache entries are trusted and unchanged;
- keep memory bounded for multi-million-file datasets.

### 21.2 Candidate changes

For N candidate-changed files:

- hash approximately those N files, not the entire dataset;
- refresh their local stat records when content is unchanged;
- report path-level results.

### 21.3 Strong verify

Strong verification is allowed to be O(total bytes). It should be explicit and should report progress.

### 21.4 Snapshot creation

Creating the first transport-neutral snapshot requires reading every file at least once to establish durable content identities, unless a trusted compatible hash source is imported.

Subsequent snapshots should reuse cached hashes for metadata-clean files.

### 21.5 Publication

Backends should reuse existing content where possible:

- IPFS reuses identical blocks/DAG nodes;
- central CAS remotes skip existing object hashes;
- torrent generation may reuse cached piece/Merkle hashes;
- unchanged files should not need to be uploaded again to a content-addressed remote.

## 22. Observability

Commands should explain what they trusted and what they actually verified.

Useful status output includes:

```text
snapshot: sha256:...
root: /data/example

metadata-clean:      1,482,991 files
candidate-changed:   3 files
missing:             0 files
new/untracked:       1 file
rehash bytes:        42.7 MiB
last full verify:    2026-09-20 ...
```

For a specific changed path, diagnostics should distinguish:

```text
stat changed, content unchanged
content changed
path added
path deleted
path type changed
racy/ambiguous -> content checked
```

Publication commands should report deduplication/reuse rather than only transferred bytes.

## 23. Testing strategy

The implementation needs more than happy-path unit tests because many bugs live at filesystem and transport boundaries.

### 23.1 Manifest tests

- deterministic snapshot identity;
- path ordering;
- Unicode/path edge cases;
- invalid traversal rejection;
- schema forward/backward compatibility;
- hash algorithm tagging;
- empty files;
- duplicate/colliding paths.

### 23.2 Local-index tests

- metadata-clean avoids payload reads;
- modified size;
- modified same-size content;
- touch with identical content;
- deletion;
- addition;
- rename;
- type change;
- inode replacement;
- timestamp resolution/racy-clean simulation;
- unreliable directory-mtime fallback;
- index deletion/rebuild;
- transaction interruption/corruption recovery;
- multiple linked Git worktrees.

Tests should instrument reads so "did not hash clean files" is an asserted property rather than inferred from timing.

### 23.3 Scale benchmarks

Maintain synthetic benchmarks for at least:

- 100k files;
- 1M files;
- many shallow directories;
- deeply nested directories;
- warm and cold metadata caches;
- local SSD and, where available, network filesystem behavior.

Measure stat count, readdir count, bytes read, wall time, and peak memory.

### 23.4 Backend tests

Each backend should have contract tests for:

- publish;
- republish with most content unchanged;
- partial fetch;
- missing publication;
- corrupted bytes;
- stale hints;
- interrupted upload/download;
- verification.

External-daemon tests can be optional/marked integration tests, but the core state-machine logic should remain testable without network services.

## 24. Proposed implementation phases

The phases are intentionally incremental so the current IPFS experiment remains useful while the broader design is developed.

### Phase 0: document and stabilize the existing IPFS workflow

Goals:

- preserve current sidecar compatibility;
- keep peer hints advisory;
- avoid expanding backend-specific metadata unnecessarily;
- establish this planning document as the architecture target.

### Phase 1: Git-like local status index for existing IPFS sidecars

Implement a local SQLite index without introducing a new portable dataset format yet.

Goals:

- per-file stat records rather than aggregate quickstat;
- directory cache for additions/removals;
- nanosecond timestamps plus timestamp-granularity awareness;
- racy-clean fallback;
- path-level dirty reporting;
- local index stored outside committed sidecars;
- rebuildable state;
- no daemon/watcher.

At this stage, exact IPFS verification may still use the existing root CID recomputation when necessary.

This phase delivers immediate value and measures whether metadata traversal is fast enough on real datasets.

### Phase 2: transport-neutral snapshot manifest

Introduce the durable logical dataset representation.

Goals:

- per-file durable content digests;
- deterministic snapshot identity;
- snapshot creation reuses the Phase 1 local index;
- `status` and `verify` no longer require IPFS;
- existing `.ipfs` files can reference a snapshot ID while remaining backward compatible.

Do not freeze schema v1 until path/symlink/canonicalization behavior is explicitly specified and tested.

### Phase 3: first centralized CAS remote

Implement the simplest backend that validates the abstraction without requiring a specialized service.

Good candidates are filesystem/SSH or S3-compatible object storage.

Goals:

- push only missing objects;
- manifest-last publication;
- partial pull by path;
- no mandatory local duplicate cache;
- remote aliases with secrets outside Git.

### Phase 4: BitTorrent v2 publication

Goals:

- generate a torrent from the same loose snapshot tree;
- seed from the existing files;
- trackers/DHT metadata;
- web-seed support;
- selective materialization;
- cache expensive torrent hashing state where useful;
- measure v2 and hybrid-client interoperability.

### Phase 5: normalize IPFS as a backend of the common model

Refactor the current IPFS code behind the same publication interface.

Goals:

- root CID recorded as publication identity;
- path/CID mapping where useful;
- peer hints and provider information remain advisory;
- explore lower-duplication publication modes;
- allow IPFS to be disabled or removed without invalidating dataset snapshots.

### Phase 6: optional advanced acceleration / packaging

Only after measurement:

- optional FSMonitor/watcher acceleration;
- pack/shard publication layouts;
- lazy/on-demand materialization;
- Git clean/smudge integration;
- remote inventory/Bloom filters for massive CAS stores;
- distributed publication health checks;
- richer provenance/lineage.

## 25. Initial decisions

These are the current architectural decisions this document intends to preserve unless later evidence changes them.

1. **Do not make IPFS the durable definition of a dataset snapshot.** IPFS is one publication backend.
2. **Do not require an archive.** Loose logical files are the default; packed layouts are derived optimizations.
3. **Do not require a duplicate local content-addressed cache.** The ordinary worktree may be the only local copy.
4. **Do not require a filesystem watcher.** Start with a Git-like stat/index design.
5. **Keep machine-local acceleration state out of committed sidecars/manifests.**
6. **Provide both cheap status and explicit strong verification.** The output must distinguish heuristic cleanliness from cryptographic verification.
7. **Keep each file independently content-identified.** Dataset-level identity is derived from the file tree rather than replacing item identity.
8. **Allow multiple simultaneous publications for one snapshot.** Peer-to-peer and centralized sources should coexist.
9. **Treat retrieval locations as replaceable hints/state, not content identity.**
10. **Preserve existing `*.ipfs` compatibility while the abstraction grows.**
11. **Incubate in `git-well`, but design the core so it can move to a standalone repository.**

## 26. Open questions before a portable format is frozen

### Snapshot semantics

- Is SHA-256 the only required v1 per-file digest, or merely the default?
- What exact canonical encoding defines the snapshot root identity?
- Are empty directories represented?
- Are executable bits identity-bearing?
- How are symlinks represented?
- Are hardlink relationships meaningful or treated as duplicate content entries?
- What path encoding and normalization rules are portable across Linux/macOS/Windows?
- How are case-colliding paths handled?

### Local index

- SQLite versus a simpler custom format?
- Exact racy-clean rule and timestamp-granularity probe?
- Which stat fields are trusted on each platform/filesystem?
- Should directory cache behavior be capability-tested like Git's untracked cache?
- How should multiple snapshots sharing one physical root reuse index rows?

### CLI / user model

- `git ipfs` plus new backend-specific commands, or a new `git well data` namespace?
- What should the portable manifest extension be?
- Should publication descriptors be one multi-backend file or backend-specific sidecars?
- Should `--full` become/alias `--verify`?
- What is the clearest terminology for metadata-clean versus verified?

### Publication

- First centralized backend: local/SSH, S3, or both behind a generic object API?
- Commit `.torrent` files directly, or commit a small torrent publication descriptor pointing to them?
- How should public web-seed layouts map snapshot paths to URLs?
- How should IPFS no-copy publication be supported without depending on experimental features?
- Should publication records include observed availability/health, or should that remain purely local/ephemeral?

### Project boundary

- At what implementation phase does a standalone repository become cleaner than continued incubation in `git-well`?
- If split, should Git integration remain the primary UX or merely one frontend?

## 27. Related specifications and references

These are design references, not dependencies of the proposed format.

- Git index/stat-cache behavior:
  https://git-scm.com/docs/git-update-index
- Git racy-clean discussion:
  https://git-scm.com/docs/racy-git
- Git FSMonitor daemon:
  https://git-scm.com/docs/git-fsmonitor--daemon
- Git attributes / clean-smudge-process filters:
  https://git-scm.com/docs/gitattributes
- Git LFS pointer and storage model:
  https://github.com/git-lfs/git-lfs/tree/main/docs
- BitTorrent v2 specification (BEP 52):
  https://www.bittorrent.org/beps/bep_0052.html
- BitTorrent web seeds (BEP 19):
  https://www.bittorrent.org/beps/bep_0019.html
- IPFS content addressing concepts:
  https://docs.ipfs.tech/concepts/content-addressing/
- Kubo experimental features / filestore documentation:
  https://github.com/ipfs/kubo/blob/master/docs/experimental-features.md
- Hugging Face Xet documentation, as a reference for Git-oriented content-addressed data workflows:
  https://huggingface.co/docs/hub/xet/index

## 28. Short version

The desired system is not "Git LFS implemented with IPFS." It is a small, durable data-versioning layer with Git-like local change detection and pluggable publication.

```text
                         Git history
                             |
                     snapshot manifests
                             |
                  transport-neutral hashes
                             |
              ordinary materialized file tree
                             |
       +---------------------+----------------------+
       |                     |                      |
     IPFS              BitTorrent v2          central remotes
  content DAG        peers + web seeds       CAS over S3/SSH/HTTP
```

The local performance model is:

```text
portable snapshot manifest
            |
            +---- durable content hashes
            |
local index (disposable)
            +---- file stat cache
            +---- directory cache
            +---- cached backend hashes

normal status:
    stat metadata -> hash only candidates

strong verify:
    read bytes -> verify durable hashes
```

The architecture is deliberately designed so that losing any one publication technology is inconvenient rather than catastrophic. The durable things are the logical snapshot definition, content hashes, and the ordinary files themselves.
