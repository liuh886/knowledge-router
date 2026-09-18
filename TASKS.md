# KnowledgeRouter — status and remaining work

This is the portable status for the packaged release. It distinguishes proven local behavior
from host-specific activation gates.

## Done (verified by the test suite)

- [x] External multi-Vault registry, immutable FTS index and source citations.
- [x] Existing-folder routing and frozen append/create capture with recovery.
- [x] Evidence briefs, quality signals and audited maintenance CLI hook.
- [x] Read-only Git project catalog and federated evidence retrieval.
- [x] Generic Context definitions: identity, type, purpose, scopes, aliases, origin.
- [x] Candidate discovery from live readable sources; arbitrary types, multiple scopes.
- [x] Daily, Project and Contact recognized in one input without exclusive routing.
- [x] `contexts discover | plan` CLI, no source mutation; duplicate-name/missing-scope signals.
- [x] Versioned external definitions with revision conflicts and retained history; discover/plan
      reuse the saved version; `contexts check` reports drift without repairing.
- [x] One canonical event bound to multiple Contexts; retry identity; frozen broker payload.
- [x] `contexts brief` with scoped evidence, linked events and explicit evidence gaps.
- [x] Explicit state declarations, corrections, conflicts and logical retirement; no rewriting of
      human originals.
- [x] Opt-in per-Context review intervals and source-change checks; `contexts due`, `coverage`.
- [x] Structured follow-ups with timezone validation, due filtering, completion/cancellation/
      reopening and replacement history.
- [x] Conservative Chinese relative-date resolution (`明天上午9点`, `下周二14:30`) with ambiguity
      refusal and DST-gap refusal.
- [x] Explicit local JSONL delivery with durable receipts, stable-id deduplication and retry opt-in.
- [x] Unified `contexts tick` poll with completeness/errors and a stable fingerprint.
- [x] Maintenance cycle with durable receipts, staged success baseline and same-Vault serialization;
      incomplete scans and stale audits stay non-clear.
- [x] Recovery: `checkpoint`, `backup`, isolated `restore`; allowlists, SHA-256 manifests + sidecars.
- [x] Context-aware bounded quality queue; no opaque score, no automatic repair.
- [x] Reviewed Context → project-catalog bindings (`project_ids`); unknown ids refused, stale sources
      surfaced, no same-name joins.
- [x] Representative effect/performance acceptance (Recall@K, MRR, abstention precision, citation
      coverage all 1.0 in the isolated harness) plus process-inclusive timing tooling.
- [x] Cross-Vault identity safety: same-named Contexts stay partitioned by stable Vault id.
- [x] Unified `status --json` combining attachment/index facts, coverage, receipts, baseline,
      failed runs and the delivery boundary.
- [x] `tests/` suite: **153 tests**, ruff- and bytecode-clean.

## Open

- [ ] **Optional Ductor conversation integration activation.** The workspace bridge
      (`integrations/ductor/knowledge_router.py`) is implemented and tested. A host runtime's
      read-only conversation hook is opt-in and defaults to disabled. Activation needs a verified
      supervisor/restart owner and a rollback rehearsal; see below. This is operational, not a
      missing code requirement.
- [ ] **Remote transport delivery** (Telegram/Matrix/other) on top of the local JSONL sink. This is
      an adapter-specific gate and is never implied by `contexts tick`.
- [ ] **Automatic state extraction from legacy prose** with evidence-backed review. Explicit
      declarations are the supported mechanism today.
- [ ] **Optional semantic retrieval sidecar** — only if a broader benchmark shows a measured recall
      gap. Current lexical/bigram/alias retrieval meets every acceptance gate.

## Activation / rollback runbook (Ductor conversation hook)

Preparation only; do not run while an unverified supervisor owns the process.

1. **Preflight.** Confirm the source hook exists and the host records a live process with a known
   parent/supervisor. If the recorded parent is gone and no scheduled task/launcher exists, stop:
   a restart marker cannot be issued safely.
2. **Configure disabled.** Add the opt-in block with `enabled: false` first and confirm
   `status --json` still reports service enablement as unknown/unset.
3. **Verify the hook in memory.** Invoke the real hook against a read-only Vault and confirm cited
   evidence, staleness fields and bounded timeout. Never let a failure fall back to another
   retrieval backend.
4. **Enable and reload.** Flip `enabled: true` with an explicit `vault` and absolute `entrypoint`.
   The validated config watcher hot-reloads the nested settings; no restart is required once the
   loaded code includes the hook. Turning it off does not retroactively remove evidence already in
   an in-flight request.
5. **Accept.** Run one real conversation query and confirm citations are attached.
6. **Rollback.** Set `enabled: false` (or remove the block). The previous valid configuration is
   retained; in-flight lookups keep their original snapshot and deadline.

## Scope discipline

Context is a purpose with a lifecycle, not a synonym for every folder. Inferred names do not prove
active status or authorize writes. A preview is not a committed event, a detected follow-up is not a
scheduled reminder, and a maintenance hook is not a running daemon. Existing source-protection and
broker guarantees remain in force.
