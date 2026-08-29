# Durable public state

ASRO stores its operational SQLite state as immutable, compressed assets on the public
`asro-state` GitHub Release in this repository. The repository contains only
`data/state/current.json`, a small pointer with three independently verifiable versions.
Release assets are public: anyone can download the complete database and inspect all public
evidence it contains. Secrets and private reviewer inputs must never be written to this state.

## Why

The migration baseline was 62,341,120 bytes (15,220 4 KiB pages). `documents` occupied
43,728,896 bytes; `candidate_event` occupied 6,164,480 bytes. The freelist was empty, so an
ordinary `VACUUM` could not reclaim deleted pages. `dbstat` reported 3,906,519 bytes of internal
unused space, making the optimistic rebuild floor about 58.4 MB—still above GitHub's 50 MB
recommended blob size. Routine collection grew the database by roughly 0.18–0.36 MB/day;
reviewed full-document batches caused much larger step changes. Compaction alone therefore does
not solve repository growth.

## Write protocol

1. The daily workflow downloads the pointer's newest asset and manifest.
2. It verifies manifest SHA-256, compressed SHA-256 and size, decompresses to a temporary file,
   verifies database SHA-256 and size, runs `integrity_check` and `foreign_key_check`, then
   atomically replaces `data/monitor.db`.
3. Repository startup applies forward migrations.
4. Collection, review and site generation run normally.
5. A deterministic gzip asset and canonical manifest are produced under a content-addressed,
   versioned filename. The candidate pointer retains the newest three versions.
6. Release validation binds the site snapshot, pointer and exact database SHA-256.
7. The workflow creates/uploads the versioned assets. Existing names must have identical bytes;
   they are never overwritten.
8. Only after successful upload does the workflow commit the pointer and site. `monitor.db` is
   explicitly removed from Git tracking.

The workflow concurrency group serializes writers. An interrupted upload or pointer update leaves
the prior pointer valid. The Release retains all immutable assets; the pointer retains two prior
versions for automatic rollback.

## Recovery

For normal bootstrap:

```text
pip install -e .
asro state-restore --pointer data/state/current.json --database data/monitor.db
asro build-site
```

`state-restore` tries the current version first, then the two prior versions. It fails if none pass
all hashes, sizes, SQLite integrity, foreign-key and migration-ledger checks. After restore,
`SqliteRepository.connect()` applies pending forward migrations.

For disaster recovery, download `data/state/current.json` from a known Git commit and run the same
command. If GitHub Release delivery is unavailable, manually download the manifest and gzip asset
named in any retained pointer entry, verify all recorded hashes and sizes, and provide those bytes
to the restore command through a temporary local mirror. Never edit a manifest or reuse an asset
name. To roll back operationally, create a reviewed pointer commit that makes a previously retained
valid entry current; do not delete later assets or rewrite Git history.

## Alternatives considered

- `VACUUM`: safe as maintenance after a copied backup, but estimated savings are only about 3.9 MB.
- Git LFS: removes large blobs from ordinary Git, but adds bandwidth/storage accounting, pointer
  checkout requirements and recovery dependence on LFS service state.
- Repository sharding: durable in GitHub but creates cross-repository permissions, atomicity and
  recovery coordination.
- External databases/object storage: operationally scalable but introduces a new provider,
  credentials, billing/security configuration and backup responsibility.
- GitHub Release assets: selected because state is already public, assets are same-repository,
  immutable by naming policy, downloadable without secrets, and recoverable from a small pointer.

No history rewrite is required. The pre-migration SQLite blobs remain recoverable from existing Git
history.
