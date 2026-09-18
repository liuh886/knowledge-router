# Safety and mutation contract

The Vault is authoritative. Derived indexes and taxonomy caches are disposable projections;
capture plans, mutation reconciliation state, and maintenance audit events are durable external
control records. Attaching, syncing, searching, briefing, and diagnosing must not write inside
the Vault. Production state is fixed at `~/.ductor/vaults`.

Git project libraries are also human-owned, read-only sources. Project attachment and sync may
write only immutable catalogs inside the external state directory associated with an attached
Vault. They must disable optional Git locks, execute no repository code, reject linked project
roots/documents, and never invoke Git or hosting mutations such as add, commit, checkout, reset,
push, issue creation, or pull-request creation. Dirty working-tree documents remain admissible
evidence only when cited by exact content hash and a working-tree-aware revision.

Capture is a narrow mutation broker:

1. Default to preview. Commit requires `write_policy=append-create` plus explicit user intent or
   the user-selected `capture_mode=auto`.
2. Open and hold every parent-directory handle while resolving the target. On Windows request
   `FILE_LIST_DIRECTORY | FILE_READ_ATTRIBUTES` without delete sharing so an ancestor cannot
   be renamed or replaced during the operation. Do not use attribute-only opens: their sharing
   restrictions do not reliably pin rename. Requesting DELETE access is unnecessary and causes
   conflicts with ordinary readers. An existing DELETE-capable handle must still conflict with
   our deny-delete sharing. Native tests verify these conditions and release behavior.
   Reject hidden targets, reparse points, links, and missing directories.
3. Preview persists a checksummed, expiring plan containing the exact target, action, expected
   source hash, encoding/newline choice, and payload. Commit never reroutes or regenerates it.
4. Record `PREPARED` in the external SQLite journal before touching the Vault. A retry reconciles
   source bytes with the prepared/committed states. Same key plus a different request fails.
5. Existing files may only receive one checked append write. On Windows, open one verified file
   handle while denying other writers; if an editor already has write access, fail before any
   mutation. Check the exact source hash and size, detect short writes, fsync, and prove the old
   bytes remain an exact prefix. Non-Windows append fails closed until an equivalent kernel
   exclusion mechanism is implemented.
6. New files use exclusive creation. Never truncate or replace a collision; freeze a suffix in
   the plan. A short or interrupted write becomes `NEEDS_RECONCILIATION`, never a blind retry.
7. Decode existing content before planning. Preserve UTF-8 BOM and LF/CRLF/CR style. Reject
   UTF-16 and unknown encodings before mutation.
8. Do not implement delete, move, rename, rewrite, or recursive directory creation operations.

The generated note may add frontmatter only to the new file. Existing frontmatter and prose are
never parsed and rewritten. External indexes are content-addressed and activated only after
SQLite integrity, database-manifest regeneration, canonical Vault binding, row-count, zero
skipped Markdown sources, and a second source-manifest scan succeed.
