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
- [x] Read-only Context relationship graph: `contexts links` promotes the wikilink graph to a
      durable external projection (`link_graph.json`), resolving Markdown and attachment targets,
      detecting MOC/index anchors, and reporting related Contexts, hub notes and cross-scope
      bridges. No relationship is inferred from co-occurrence; no Vault source is written.
- [x] Context `include`/`exclude`/`tags` filters, validated at registration and applied
      consistently to discovery, briefs, drift checks and `contexts links`, plus graph-affinity
      brief ranking when a stored `link_graph.json` exists. Filters never rewrite a note.
- [x] Compiled Context cards: `contexts compile` returns a read-only bundle (recorded state,
      open follow-ups, topic anchors, bounded cited evidence, Markdown scaffold; query defaults
      to the Context name and aliases) and writes the reviewed card with `--save` to an external
      compiled area beside the Vault (`<vault>_compiled/`, `--out-root` override) with no write
      policy involved. Writing inside the Vault remains available via `--stage` plus
      `capture --commit` under `append-create`.
- [x] Deposition loop: `contexts review` produces a prioritized agenda (maintenance-due Contexts,
      due follow-ups, stale compiled cards, aggregated maintained-scope doctor findings) and
      appends a durable external ledger entry; re-runs report new and resolved items. The Vault is
      read-only and unscoped findings remain available through `quality`.
      `--limit` truncates the printed page only: the ledger, the fingerprint and the deltas always
      use the full item set, so a smaller limit cannot look like a resolution. Each entry stores a
      `basis` block and a disappearance counts as resolved only when both runs had a complete
      basis, otherwise `comparable: false` with a named reason and `resolved_suppressed`. An empty
      agenda over an incomplete basis (no registered Context, unreadable source, truncated poll)
      returns `incomplete`, never `clear`.
- [x] Single link resolver: `link_resolver.py` owns `[[wikilink]]` resolution for both
      `vault_doctor` and `link_graph`, so diagnostics and the relationship graph cannot disagree.
      The model follows Obsidian's official help: a target carrying a path is resolved as a path
      (relative to the note, then from the vault root); a bare target by unique filename, then
      unique alias; non-Markdown targets must carry their extension. An earlier
      "relative path first for everything" ladder was corrected because it let a bare-name match
      override an explicit path. Verdicts record the deciding level, ambiguity stays ambiguity, and
      two undocumented cases (`ambiguous_proximity_unique`,
      `unresolved_with_unique_basename_fallback`) are reported rather than silently decided.
- [x] Conservative link triage: each broken occurrence carries `triage_hint` + `triage_reason`
      with `confidence: hint` and the original totals are retained (`broken_link_occurrences`,
      `broken_by_hint`). A hint is never a verdict; no reference is dismissed because of its
      source, and no target is called actionable from its name alone.
- [x] `_stale_card_items` checks the external compiled area as well as any in-Vault card.
- [x] Orphans are first-class agenda items: an in-scope note nobody links to and that links
      nowhere is reported by `contexts review` as `orphan_note`, with `counts_by_code` exposing
      the composition and `--code` narrowing the display (never the ledger). Broken links see
      "points at nothing"; orphans are the other half, "nothing points here".
- [x] Link convergence: `contexts converge` collapses repeated unresolved targets into one
      decision each. Grouping is case-insensitive (`CCS` and `ccs` are one missing note) and
      carries the evidence (occurrences, sample sources, triage hint). Suggestions are
      `check_naming_variant` (a unique same-name variant exists, e.g. `Digital Carbon` vs
      `Digital_Carbon`), `create_or_relink`, or `needs_judgement` for names too generic to guess.
      Machine-generated noise is excluded by default but counted (`excluded_noisy_*`), and a
      suggestion is never a verdict.
- [x] A real, bounded write path for convergence: `contexts converge --create X --target-path
      A/B.md` freezes an ordinary capture plan (preview, nothing written) and
      `capture --plan-id <id> --commit` lands it under `append-create`. Staging refuses a path
      that already exists, because the broker would otherwise pick a unique sibling name and the
      link would silently stay broken. Alias edits are *not* expressible under `append-create`;
      that is stated rather than faked.
- [x] State bloat removed, with the reason recorded: per-revision `bindings` snapshots are kept
      only on the latest revision (the only one ever read) via `contexts compact`, and
      `register_contexts` compacts on every append. Historical `definitions` are preserved.
- [x] External index schema 5 drops the `notes_trigram` table, which was created and populated
      but never queried (~51 MB of write-only weight per generation on a real Vault). Chinese
      recall is served by `note_bigrams` plus a LIKE fallback, English by `notes_fts`. The schema
      version is part of the generation hash, so changing the layout rebuilds instead of
      silently reusing a stale index.
- [x] `tests/` suite: **224 tests**, ruff- and bytecode-clean.

## Open

- [ ] **Optional Ductor conversation integration activation.** The workspace bridge
      (`integrations/ductor/knowledge_router.py`) is implemented and tested. A host runtime's
      read-only conversation hook is opt-in and defaults to disabled. Activation needs a verified
      supervisor/restart owner and a rollback rehearsal; see below. This is operational, not a
      missing code requirement.
      Progress: on the reference host the hook was switched on and the live process logged the
      hot-reload, and the real hook function returns cited evidence for a new session and nothing
      for a continuing one. The invocation path was also corrected from `python3` to `python` in
      both the live rule files and the deploy templates. A live conversational end-to-end pass is
      still unobserved and `search` leaves no audit trace, so that gate stays open. Note that the
      host's `python3` placeholder problem also affects other tool-invocation lines in the host
      rule files; it does not affect the automatic hook, which uses `sys.executable`.
- [ ] **Remote transport delivery** (Telegram/Matrix/other) on top of the local JSONL sink. This is
      an adapter-specific gate and is never implied by `contexts tick`.
- [ ] **Automatic state extraction from legacy prose** with evidence-backed review. Explicit
      declarations are the supported mechanism today.
- [ ] **Broader graph-aware ranking.** Context filters and MOC-anchor affinity are in place;
      ranking that uses general link distance between arbitrary notes (not only anchors)
      remains open.
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
