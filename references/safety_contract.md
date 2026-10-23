# Safety and mutation contract (0.2)

The Vault is authoritative original content. Context definitions, frozen plans, workflow intents,
mutation reconciliation and delivery/maintenance receipts are durable external control records.
They require backup; indexes/taxonomy/graphs can be rebuilt. State stays outside source Vaults.

## Policy and authorization

- `off`: all runtime Vault mutations refused.
- `append-create`: exclusive creation and a single checked append preserving the complete old prefix.
- `reviewed-edit`: adds a checked full-document edit, with original bytes frozen for recovery.

Generic capture and historical triage default to preview. Explicit append commands or `--commit`
express an operation request, never override policy. A frozen edit still needs reviewed-edit at commit.
Policy and Vault binding are rechecked after the broker lock is acquired. Read commands never repair.

## Broker invariants

1. No interface/profile bypasses the broker. Pin every target parent and reject hidden targets,
   links, hard links, reparse points, traversal and missing directories. Windows directory handles
   request LIST_DIRECTORY | READ_ATTRIBUTES and deny DELETE sharing, preventing ancestor replacement.
2. Persist a checksummed, expiring plan with exact action, path, byte payload, original hash/size,
   encoding/newline and idempotency key before touching a source. A retry never reroutes/regenerates.
3. Record PREPARED in the external SQLite journal. The same key cannot describe a different request.
4. Append uses one handle denying competing writers, checks hash/size, writes once, fsyncs, detects
   short writes and proves the old bytes are an exact prefix. A competing editor fails before mutation.
5. Create uses CREATE_NEW/O_EXCL. A collision is never truncated. Document plans require the exact
   path to be missing; generic capture may freeze a collision suffix during preview.
6. Full edit requires reviewed-edit and schema 3. Complete original bytes are saved inside the frozen
   plan before PREPARED. Windows opens one read/write handle with no read/write/delete sharing,
   checks the source basis, writes, truncates only to the frozen final length, fsyncs and verifies.
   This is not a crash-atomic swap: a process or power failure can leave a partial source, but its
   original is retained outside the Vault. Exact before/after states reconcile; partial/unknown states
   become NEEDS_RECONCILIATION. Refuse blind retries; preserve the source and the plan/journal for manual
   byte comparison and recovery. Do not delete/edit the recovery records or assume indexing succeeded.
7. Decode UTF-8/BOM strictly before planning. Reject UTF-16/unknown encodings. Ordinary appends retain
   every original byte and newline. Triage preserves BOM, unchanged lines and original line endings.
8. No source delete, move or rename operations. Recursive provisioning belongs only to explicitly
   invoked scaffold; it pins the root chain and exclusively creates files, skipping ordinary collisions.
9. Non-Windows append/edit fail closed until equivalent kernel-level exclusion exists.

Schema 2 plans remain supported without expanding their original mutation capabilities. Editing
source bytes after preview invalidates a plan; restore/review the actual source rather than guessing.

## Application completion and recovery

Generic capture, Context events and optional memory workflows use one commit/index service. Source
application is journalled before indexing. Only committed-and-indexed operations return success;
`applied_pending_index` carries a plan id and exits 2. Retry that plan, or the same explicit workflow
intent key, to finish indexing without another append. Context applied/completed receipts retain
those separate facts. No remote-delivery claim follows from a local commit or scheduler poll.

Workflow intent files bind request digests to frozen plans and must be retained with the mutation
journal. Buffer imports preserve the original buffer and completed markers; repeated imports replay
existing intents. Unsupported legacy rewriting/relocation is refused before execution.

## Read and external artifacts

Read generations activate only after integrity, canonical Vault binding, manifest, row count,
complete source decoding and a second source scan succeed. Returned evidence discloses staleness.
Knowledge pages and compiled artifacts live outside the Vault unless staged as ordinary broker plans.

Git project libraries are read-only: metadata and selected Markdown only, no repository code,
optional Git locks disabled, linked roots/documents rejected, no Git/hosting mutations. Dirty files
are evidence only with exact hashes and working-tree-aware revisions.
