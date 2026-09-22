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
- [x] `tests/` suite: **274 tests**, ruff- and bytecode-clean.

## Correctness closeout (P0 of the roadmap review)

Four reproduced boundary defects were re-verified as still open and then fixed:

- [x] **Scope change no longer forges a resolution.** The review `basis` is semantic: it carries the
      Context revision, a `definitions_fingerprint` (scopes/filters/aliases/project ids/maintenance)
      and the `diagnostics_version`. Any change between runs refuses the comparison
      (`context_definitions_changed`, `diagnostics_version_changed`) and moves vanished ids to
      `resolved_suppressed`. Ledger entries predating the semantic basis report
      `previous_basis_not_semantic` instead of being misread as a definitions change.
- [x] **An incomplete review exits non-zero.** `contexts review` and `converge` return 2 when the
      basis is incomplete, consistent with `contexts tick` and `contexts coverage`.
- [x] **`../` note-relative links resolve.** Note-relative targets are normalised lexically, so
      `[[../Target]]` from `Area/Sub/` finds `Area/Target.md`, `[[./Target]]` is not demoted to a bare
      filename, and a path escaping the Vault stays unresolved. The packaged Vault used for
      acceptance contains no such links, so this is verified by synthetic tests only.
- [x] **Custom compiled-card outputs are tracked.** Each `--save` records the real output path, the
      definitions fingerprint and the exact `{source: checksum}` set, so review reports
      `compiled_card_stale` (including newly **added** sources), `compiled_card_scope_changed` or
      `compiled_card_missing` for custom `--out-root`/`--name` locations. Pre-registry cards keep the
      default-path mtime fallback and are not retro-adopted, because their dependencies are unknown.

## P1 — a usable Context Base: versioned knowledge pages

The roadmap review's P1 asks for one research topic and one project to have a knowledge page, an
evidence manifest, and a budgeted context pack. Implemented as an **external derived artifact** (the
roadmap's option (c)), so no new write primitive is needed and the Vault stays untouched.

- [x] `contexts page publish <selector> --content-file <draft.md>` validates a draft against the
      Vault and publishes a **versioned page** into `<vault>_knowledge/`. Every publish records the
      page version, the Context revision, the definitions fingerprint and the scoped source snapshot.
- [x] **Sourced vs inference is enforced, not suggested.** A sourced claim must cite at least one
      real note (recorded with its checksum); an inference must be labelled and cites nothing; a
      `[conflict]` claim needs at least two sources. A citation to a note that does not exist, or to
      lines beyond the end of the note, is **refused** — an unresolvable citation is indistinguishable
      from a fabricated one. Undecorated items are refused rather than guessed at.
- [x] `contexts get <selector> --budget-tokens N` returns a **budgeted pack**: purpose, scope, claims
      marked `sourced`/`conflict`/`inference`, open questions, coverage, and its own estimated token
      cost. Claims that do not fit are omitted and counted, and the total is always reported, so a
      caller is never handed a silent truncation.
- [x] `contexts expand <page_id> <claim_id>` returns the claim next to its sources **as they are now**,
      each marked `current` / `changed` / `missing`. Drift compares the Context's current scoped set,
      so a newly added in-scope note makes an existing page stale.
- [x] `contexts page list` / `contexts page show` for inspection, with per-page staleness.
- [x] Reference: [`references/knowledge_pages.md`](references/knowledge_pages.md) documents the draft
      syntax and its refusal rules.

Acceptance run on the packaged Vault: `research:ccs` (research topic) and `topic:new_business`
(project) each published with real citations, `contexts get` served both as `current`, and
`contexts expand` returned genuine excerpts. One bug was found this way and fixed: staleness compared
the scoped snapshot against *all* Vault notes, so every page looked stale immediately.

Stated limits: no conflict claim was published on the real Vault because no verified disagreement
between two sources was found, so that path is covered by tests only; the token budget is an estimate,
not a tokenizer; re-scoping marks a page `scope_changed` but never rewrites it or decides which claims
survive.

## Tightening the derived-artifact contracts

A second review of the same batch reproduced six defects in these mechanisms. All six are fixed, each
with a regression test.

- [x] **A card was vouched for by the wrong source state.** `write_compiled_card` recorded the scoped
      checksums *after* writing the prose, so a draft authored against OLD was registered against NEW
      and review said `clear`. `compile` now returns a `source_snapshot_sha256`, and
      `--save --expected-source-snapshot <hash>` verifies it before writing; a mismatch is refused.
      Omitting it records `basis: unverified`, surfaced as `compiled_card_unverified_basis` — such a
      card never gets the meaning "sources unchanged". The recorded `card_sha256` is checked on read,
      so an external edit becomes `compiled_card_edited_externally`.
- [x] **A damaged registry made stale items look resolved.** Reading now returns
      `absent / valid / unreadable / invalid` with a reason; the state is in the review basis and in
      the agenda as `card_registry_unreadable`, makes the basis incomplete, blocks `resolved` and exits
      2. The write path refuses to rebuild a damaged registry as empty, and the registry is checked
      *before* any file is written so an unregistered artifact cannot be left behind. Same for pages.
- [x] **Scope was decided from a truncated display sample.** Scope is now decided over the full
      occurrence stream before aggregation; per-scope counts and `counts_by_hint` come from in-scope
      occurrences only, with the vault-wide total reported separately as `vault_target_occurrences`.
- [x] **An unrelated Context marked a card re-scoped.** Cards and pages record and compare a
      per-Context `context_fingerprint`; a record predating it is reported as needing re-verification.
- [x] **No definitions looked like a complete empty result.** `converge` reports `configured: false`,
      marks the result incomplete and exits 2; an unknown `--context` is an error.
- [x] **A successful create is not a fixed link.** `stage_missing_note` re-resolves the planned path
      with the shared resolver, lists the occurrences it would fix, and refuses a create that would
      resolve nothing.
- [x] `status` no longer masks an incomplete basis when items exist: incompleteness is a top-level
      `incomplete` field and the warning is printed whenever it is set.
- [x] An unregistered card is not a trusted card. The legacy fallback emitted an item only when a
      source was newer than the card file, so a card that was merely *older* than its sources
      passed with no item at all. Such cards now report `compiled_card_unregistered`; on the
      reference Vault this surfaced a second card that had been silently trusted, taking the
      agenda from 2,734 to 2,735 items.

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

## Roadmap delivery queue

The delivery queue, its acceptance conditions and the resume procedure now live in
[`docs/development-plan.zh.md`](docs/development-plan.zh.md). That document is authoritative for the
plan; this file stays the item-by-item implementation record.

Summary as of 2026-09-22: A (contract fixes) and B (versioned knowledge pages) are done; C (one
Context update loop), D (one shared read snapshot; `review` done, cross-command still queued),
E (learning from work) and F (profiling-driven optimisation) remain. Non-goals: a vector database,
an always-on service, a separate memory system, and bulk placeholder creation. The orphan signal
helps find content; it is not a to-do to drive to zero.

## 2026-09-10 — Approved OpenCode execution batch

User approved all ten tasks and requested OpenCode as implementation subagent, with
Codex supervision and independent acceptance. Detailed acceptance and boundaries are
in references/opencode-local-delivery.md; current decisions in DESIGN.md, results in
EVALUATE.md. This batch refines the open mainline above; distribution stays deferred.

- [x] OC-01 P0: Complete maintenance receipts, durable baseline/deltas and failure recovery.
  - [x] Staged baseline promotion preserves the last successful baseline when a completion
    receipt fails; a distinct whole-cycle lock serializes same-Vault callers.
  - [x] Incomplete scans, stale audit-only indexes and failed runs remain non-clear and
    expose durable receipts; quality increases carry bounded reason/delta findings.
- [x] OC-02 P0: Verified initial Context maintenance policies and visible coverage.
  - [x] `contexts coverage` reports registered/enabled/disabled-or-unconfigured/retired
    Contexts and fails closed when Context state output is truncated.
- [ ] OC-03 P0: Actual Ductor conversation integration, activation preflight and rollback.
- [x] OC-04 P0: Full multi-Context preview/authorized capture/query lifecycle acceptance.
  - [x] Dedicated CLI acceptance covers Daily + Contact + Project in one frozen event,
    preview/no-write, append-create commit, idempotent retry, cited brief/history/state,
    follow-up completion, logical retirement, and unchanged Contact/Project originals.
  - OpenCode's separate test-only session was transport-blocked; Codex added and reviewed
    the acceptance test without changing production lifecycle logic.
- [x] OC-05 P0: Local source checkpoints and durable runtime backup/isolated restore.
  - [x] Allowlisted source/runtime archives carry an integrity manifest and SHA-256
    sidecar; rebuildable indexes/locks are explicit exclusions.
  - [x] Restore is verification-first, isolated-target-only and exclusive-create;
    failed verification/restore preserves live state and baselines.
- [x] OC-06 P1: Follow-up delivery acknowledgments, retry/deduplication and completion.
  - [x] Local JSONL sink delivery records pending/delivered/failed/cancelled receipts,
    retries failed intents only when requested, deduplicates stable notification ids and
    reconciles closed/replaced follow-ups. Remote transport delivery is a separate gate.
- [x] OC-07 P1: Evidence-backed actionable quality queue and orphan-growth diagnosis.
  - [x] `quality` now exposes a bounded Context-aware queue. Maintained-scope findings are
    prioritized with context ids, reason/evidence/suggestion remain attached, and unscoped
    additions plus archive/backup historical hints stay visible rather than being filtered.
    Existing maintenance baselines provide the separate orphan-growth delta; no repair runs.
- [x] OC-08 P1: Representative effect tests and repeated search/brief latency budgets.
  - [x] Isolated acceptance covers Daily/Context, Contact/entity, Project, old-state archive
    and explicit abstention cases. Recall@K, MRR, source citation coverage and abstention
    precision are all asserted at 1.0; warm search repeats and process-inclusive search/
    Context-brief timings are measured without turning one machine's timing into an SLA.
- [x] OC-09 P1: Explicit persistent Context/project binding and progress capture preview.
  - [x] Context definitions accept reviewed `project_ids`; registration rejects unknown ids,
    briefs reuse exact bindings with explicit origin metadata, stale repository evidence remains
    visible, and the existing broker still requires a preview before progress capture.
- [x] OC-10 P1: Unified factual status and current operating documentation.
  - [x] `status --json` now combines attachment/index facts, maintenance coverage, durable
    receipts, last successful baseline, failed-run count, explicit delivery-not-configured
    boundary and next action. README/SKILL/context/runtime references describe current behavior
    and preserve historical evidence.

Dispatch status: all ten tasks assigned to OpenCode. Two implementation attempts
lost their prompt transport with fetch failed; no implementation files changed and
no OC task had passed acceptance at the initial dispatch checkpoint. Session
receipts and pending operations are in references/opencode-job.json and EVALUATE.md;
the later OC-01/OC-02 acceptance is recorded below. Resolve bridge long-run/failed-job
permission and cancellation handling before resuming any failed session.

Acceptance update (2026-09-11): OpenCode completed the OC-01/OC-02 implementation
slice in an isolated copy. Codex independently reviewed and integrated only the
validated files. The local Router suite now passes 134 tests; live Vaults and the
distribution repository remain untouched. A subsequent OpenCode hardening request
was blocked twice by certificate verification, so the remaining OC-03 through OC-10
tasks are still open and will be dispatched in bounded slices.

Second acceptance update (2026-09-11): OC-05 recovery tools are now integrated locally;
the full suite is **138 passed**. OC-04 remains open only for a dedicated Contact +
Project scenario; OC-03 and OC-06 through OC-10 remain open. Distribution and production
activation are still deliberately deferred.

Third acceptance update (2026-09-11): OC-04's dedicated Contact + Project scenario and the
OC-08 representative effect/performance acceptance are now green. The full local suite is
**140 passed**; static checks and bytecode compilation pass. OC-03 and OC-06, OC-07, OC-09,
and OC-10 remain open, with distribution still deferred.

Fourth acceptance update (2026-09-11): OC-07's bounded Context-aware quality queue is now
implemented and tested through both the pure analyzer and the `quality --json` CLI path. The
full suite count will be refreshed after the next regression run; production Vaults and the
distribution repository remain untouched.

Fifth acceptance update (2026-09-11): OC-09's reviewed Context-to-project binding is now
implemented and covered by the project catalog integration tests. Unknown ids fail at
registration, exact ids are reused by Context briefs, and no inferred same-name relationship
is created. OC-03 and OC-06 remain open; OC-10 is documentation/status consolidation.

Sixth acceptance update (2026-09-11): The integrated local regression is **144 passed** with
ruff and bytecode compilation clean. OC-04, OC-07, OC-08 and OC-09 are accepted locally;
remaining work is the non-claimable Ductor live activation/rollback and delivery gate (OC-03),
follow-up acknowledgments (OC-06), and unified current-status documentation (OC-10).

Seventh acceptance update (2026-09-11): OC-10's unified `status --json` view is implemented
and covered by receipt/baseline and CLI tests. The next full regression will refresh the count;
only the Ductor live activation/rollback gate (OC-03) and follow-up delivery acknowledgments
(OC-06) remain open in the ten-task batch; M4's broad live-scan latency budget and cross-Vault
identity evaluation remain open before considering portable packaging.

Eighth acceptance update (2026-09-11): Final local regression after the status view is
**145 passed in 72.91 seconds**; ruff and bytecode compilation are clean. The only warning is
the intentional duplicate ZIP entry in the tamper-refusal fixture. Production Vault, live
Ductor activation, the M4 broad-scan/cross-Vault gates and the distribution repository remain
untouched.

Ninth acceptance update (2026-09-11): OC-06's explicit local delivery boundary is now green.
`contexts deliver --sink ABSOLUTE.jsonl` writes pending then delivered/failed/cancelled
receipts outside the Vault, deduplicates by stable notification id, supports opt-in failed
retries and reconciles closed/replaced follow-ups. The maintenance status view reports the
ledger when present and preserves the not-configured boundary otherwise. A repeatable
process-inclusive Context latency tool and same-named cross-Vault identity test are also
green. The remaining non-claimable gate is Ductor live activation/rollback and real remote
transport delivery; distribution remains deferred.

Tenth acceptance update (2026-09-19): M4 distribution packaging is complete. The stable local
behavior was packaged into the published repository (scripts, tests, the Ductor
bridge, SKILL.md, portable references, and rewritten README/DESIGN/TASKS/EVALUATE plus
pyproject/LICENSE/.gitignore). The packaged copy passes 151 tests and ruff in isolation, and a
standalone CLI attach/search smoke test returned a cited result. The bridge gained an explicit
absolute `KNOWLEDGE_ROUTER_ENTRYPOINT` override (relative paths rejected), raising the local
suite to **153 passed**. The real-Vault retrieval fixture and the opencode operation log were
not distributed; `runtime_layout.md` was sanitized. OC-03 remains an operational gate: no live
Ductor process was found, so activation/rollback stays unexecuted.
