---
name: knowledge-router
description: Semantic routing and RAG retrieval across local knowledge vaults, documentation,
  and notes.
---

# KnowledgeRouter

Use `scripts/knowledge_router.py` as the stable entry point. All registry, taxonomy, index,
audit, and idempotency data stays outside Vaults under `~/.ductor/vaults` by default.

## Start

Attach and index a Vault without changing any path or byte inside it:

```bash
python scripts/knowledge_router.py attach "/path/to/vault" --name personal
```

Attach the Vault and its Git project library in one command when both are in scope:

```bash
python scripts/knowledge_router.py attach "/path/to/vault" --name personal --projects-root "/path/to/GitHub"
```

Enable capture only when the user wants it:

```bash
python scripts/knowledge_router.py attach "/path/to/vault" --name personal --write-policy append-create --capture-mode auto
```

Production state is fixed at `~/.ductor/vaults`. The compatibility `--state-root` option accepts
only the operating-system temporary directory tree for isolated tests; it cannot redirect
production state into an arbitrary location or another Vault.

## Operate

- Scheduler/monitor callers use `contexts tick --vault <name> --json` for one combined
  maintenance and due-follow-up poll. It includes Context ids from applied events as well
  as registered definitions. Incomplete reads/limits return exit 2, never a false clear.
  `fingerprint` excludes check time; compare it only after a complete scan. Output truncation
  and scan incompleteness are distinct. A poll is not a notification delivery receipt.

- Capture a follow-up with the usual explicit target/Context arguments plus
  `--followup-id <stable-id> --followup-title <title> --due-at <ISO-timestamp-with-offset>`.
  `--state-context <linked-id>` selects the owning Context; default is primary.
  Use `--followup-status done|cancelled|open` and `--replaces <previous-event-id>` for
  completion, cancellation, reopening or rescheduling. These are append-only event updates.
- `contexts followups <context-id> --due-only --json` lists due items and unresolved
  conflicts; omit `--due-only` for all recorded statuses. Capture accepts supported Chinese
  dates such as `--due-at 明天上午9点 --timezone Asia/Shanghai` and displays `due_resolution`.
  Date-only, missing timezone and ambiguous DST times fail; do not guess missing details.
  Reuse the frozen event id for commit/retry rather than re-resolving a relative date later. Listing is not
  scheduling: no notification or timer is created by these commands.

- `contexts deliver --sink <absolute-path> --vault <name-or-id> --json` is the explicit local
  delivery boundary for due follow-ups. It writes a durable pending receipt before appending
  one JSONL notification, then records delivered/failed outcomes. Re-running is idempotent by
  notification id; `--retry-failed` opts into another attempt. Closed or replaced follow-ups
  cancel undelivered intents. The sink must be outside both the Vault and external state. This
  is a verified local sink only; Telegram/Matrix/Ductor transport delivery remains a separate
  adapter and is never implied by `contexts tick`.

- `contexts due --vault <name> --json` returns an opt-in maintenance review queue.
  Registered definitions may include `maintenance` with `enabled` (boolean),
  `review_interval_days` (1-3650), and `check_source_changes` (boolean). Missing policy
  means no periodic maintenance assumption. This command neither repairs nor sends alerts.
  A verified `reviewed_at` state declaration must carry a timezone-aware ISO timestamp;
  file modification time does not count as review. Conflicts and invalid dates require review.

- Read explicitly recorded state with `contexts state <context-id> --vault <name> --json`.
  State declarations can accompany `contexts capture` using `--state-field status
  --state-value <value> --state-context <linked-id>`. For corrections, repeat
  `--replaces <previous-event-id>` for every conflicting declaration being superseded.
  Use `--state-field lifecycle --state-value retired` (or `active`) for logical retirement
  (or reopening). These remain preview-first broker captures, never physical deletion.
  Unresolved declarations yield conflicts; unavailable source evidence yields no current value.

- Read a named Context with `contexts brief <name-or-id> --query <question> --vault <name>`.
  Omit `--query` for a bounded overview; add `--json` for structured citations. This reads
  live scoped sources plus linked event sources, verifies hashes and abstains on a gap.
  It returns extractive evidence, not a reconciled current-state judgment. Name ambiguity
  requires the exact Context id; do not silently choose a same-name person or project.

- For functional Context questions or maintenance planning, start with
  `python scripts/knowledge_router.py contexts discover --vault <name> --json`.
- Preview all named Context impacts (Daily, Project, Contact and custom types) with
  `python scripts/knowledge_router.py contexts plan "<text>" --vault <name> --json`.
  Use `--definitions <json-path>` for explicit purposes, aliases and multiple source scopes.
  Inferred Contexts are candidates, not write authority. M1 previews do not commit events
  or schedule detected follow-ups. Read `references/context_protocol.md` for these limits.
  Continue to use source search for relevant historical answers; scope samples are not briefs.
- Save explicit functional definitions with `contexts register --definitions <json-path>
  --vault <name> --json`. Initial registration expects revision 0. Revisions replace the
  active definition set but preserve prior versions; use `--expected-revision N` for updates.
  Discovery and planning reuse saved definitions automatically. A per-call `--definitions`
  remains a temporary override and does not change the registered version.
- Refine a Context's membership with optional `include`, `exclude` and `tags` in its
  definition. `include`/`exclude` are Vault-relative path prefixes (a note must match an
  `include` when present and must not match any `exclude`); `tags` requires the note's
  frontmatter `tags`/`tag` to contain one of the listed values (nested tags match their
  parent, e.g. `Contact/Person` satisfies `Contact`). Filters apply consistently to
  discovery, briefs, drift checks and `contexts links`; they never rewrite a note.
- A Context brief boosts passages that a topic anchor links to (graph affinity) when a
  stored `link_graph.json` exists. It reports `graph_affinity_used` and
  `graph_affinity_source_count`; without a stored graph, ranking is unchanged.
- Use `contexts check --vault <name> --json` to compare current source paths/content against
  the registered baseline. A changed source may be an ordinary edit; it is not automatically
  a quality defect. Checks do not repair notes, guess moved paths or reset the baseline.
- Read one Context's resolved wikilink neighbourhood with
  `contexts links NAME_OR_ID --vault <name> [--rebuild] [--json]`. It reports the topic
  anchors (MOC/index notes), related Contexts ranked by crossing links, hub notes and
  cross-scope bridges. The graph is a rebuildable external projection (`link_graph.json`)
  over links the Vault already states; `--rebuild` refreshes it from a live scan instead of
  reusing the stored generation. Attachment embeds are counted separately and never become
  note edges. No relationship is inferred from co-occurrence, and no Vault source is written.

- Search before answering Vault-specific questions:
  `python scripts/knowledge_router.py search "<query>" --vault <name> --json`
- Cite `path:line_start-line_end`, `source_sha256`, and disclose `stale: true`.
- Build project or decision context with:
  `python scripts/knowledge_router.py brief "<topic>" --vault <name>`
- Attach a Git project library as a read-only catalog for one Vault:
  `python scripts/knowledge_router.py projects attach "/path/to/GitHub" --vault <name>`
- Refresh or inspect that catalog with `projects sync`, `projects status`, and `projects list`.
- A reviewed Context may persist exact catalog identities with `project_ids` in its definition.
  Registration rejects unknown ids; later `contexts brief ... --query ...` reuses those bindings
  and marks the selection `origin: reviewed_binding`. Repository moves, missing files and edits
  remain explicit stale evidence; no same-name join is inferred.
- Search Vault notes and repository documentation through one evidence shape with:
  `python scripts/knowledge_router.py search "<query>" --vault <name> --projects --json`
  Use `--project <project-id>` to restrict repository evidence.
- Add repository evidence to a brief with `brief "<topic>" --projects`. Repository citations carry
  project/repository identity, path, lines, content hash, Git revision, dirty state, and staleness.
- Inspect explainable Vault and project quality signals with:
  `python scripts/knowledge_router.py quality --vault <name> --json`
  Output is bounded by default and deliberately has no opaque aggregate score. Use `--details` only
  for the complete forensic list, or `--vault-only` to omit the project catalog. The JSON also
  includes a bounded `context_queue` that prioritizes findings inside reviewed scopes while
  retaining unscoped and historical-hint candidates; it never repairs or deletes sources.
- Preview classification with:
  `python scripts/knowledge_router.py capture "<text>" --vault <name> --json`
- Preview persists a frozen external plan. Commit exactly that reviewed target and payload with:
  `python scripts/knowledge_router.py capture --vault <name> --commit --plan-id <id> --json`
- The compatibility form `capture "<text>" --commit` freezes and commits within one command;
  use it only for a user-authorized automatic capture.
- Commit only if the Vault has `append-create` policy and either the user explicitly requested
  this write or its saved `capture_mode` is `auto` for ordinary captures.
- Use `--expected-sha256 <hash>` when appending to a file previously read by the agent.
  Reuse `--idempotency-key <stable-key>` across retries.
- Refresh and inspect with:
  `python scripts/knowledge_router.py maintain --vault <name> --json`
- Run the complete sequential maintenance cycle with:
  `python scripts/knowledge_router.py maintain --vault <name> --with-tick --json`
  It refreshes/audits the external index, diagnoses link/orphan quality, then polls Contexts.
  It writes durable started/completed/failed receipts and updates the successful baseline only
  after a validated complete cycle; same-Vault callers are serialized.
- Inspect explicit maintenance coverage with:
  `python scripts/knowledge_router.py contexts coverage --vault <name> --json`
  Missing policy is shown as disabled/unconfigured, not silently treated as active coverage;
  truncated Context state is incomplete.
- Create a local source checkpoint or Router runtime backup outside the Vault with:
  `python scripts/knowledge_router.py checkpoint --source-root <skill-root> --backup-root <home-backup>`
  or `python scripts/knowledge_router.py backup --vault <name> --backup-root <home-backup> --json`.
  Verify/rehearse restore only into an isolated temporary target:
  `python scripts/knowledge_router.py restore --archive <zip> --target <temp-target> --json`.
  These commands archive only allowlisted Router files/state; indexes and locks are rebuildable
  exclusions, and restore never overwrites live state.
- A scheduler can safely call all attached Vaults in stable order with:
  `python scripts/knowledge_router.py maintain --all --json`
  Use `--audit-only` for diagnostics without rebuilding projections.
  Each run writes started/completed/failed JSONL events under the external Vault state; one
  failing Vault does not suppress later Vault results.
- Doctor and maintenance JSON are bounded summaries by default. Use `--details --json` only when
  every broken/ambiguous occurrence is needed; use `--detail-limit N` to resize summary samples.
- Measure broad live Context cost with `python scripts/context_latency.py --state-root <state>
  --vault <name> --selector <context-id> --query "..." --budget-ms <machine-budget>`.
  The report is process-inclusive and records p50/p95 plus failures; the supplied budget is
  machine/Vault-specific and is not a universal latency SLA.

With multiple Vaults, specify `--vault <name-or-id-or-root-path>`. Use `search --all` only when
the user wants cross-Vault retrieval.

## Hard boundaries

- Context event capture uses `contexts capture <text> --target <relative.md>
  --primary-context <id> --context-id <id> --idempotency-key <key> --vault <name> --json`.
  Review the exact payload and linked Contexts before `contexts commit <event-id>`.
  Ordinary capture permission rules still apply; code-development permission is not
  permission to write test events into a real Vault.
- `contexts event <event-id>` reports progress; retry the same commit after an interrupted
  run. `contexts history <context-id>` returns historical applied event references.
  A completed event is not a scheduled reminder or a synthesized current-state summary.
- See `references/runtime_layout.md` for rule/state/source locations and the optional
  Ductor workspace bridge (`integrations/ductor/knowledge_router.py`). Install that bridge
  as `tools/user_tools/knowledge_router.py` and prefer it when present. Sub-agent rollout and
  automatic lifecycle hooks are host-specific. Do not infer that an existing conversation has
  reloaded changed rules.

- Never create canonical folders merely because a Vault resembles PARA or Johnny Decimal.
- Never replace, truncate, move, rename, or delete a user note.
- Capture plans and the SQLite mutation journal are external. A commit records `PREPARED`
  before touching the Vault and reconciles interrupted retries instead of writing twice.
- On this Windows-local release, append commit obtains an OS handle that denies competing
  writers and pins every parent directory against replacement. Other platforms fail closed for
  append until their platform broker supplies equivalent guarantees; exclusive new-file capture
  remains portable.
- Capture may create a new Markdown file in an existing directory or append bytes to an
  existing UTF-8 Markdown file; same-name creation gets a collision-safe suffix. UTF-16 and
  unknown encodings fail before commit.
- Search and doctor are read-only. Indexes are immutable external projections.
- Project attachment, sync, search, brief, and quality checks are read-only against repositories.
  The adapter disables optional Git locks, executes no project code, and catalogs only root
  README/DESIGN/TASKS/AGENTS files plus direct Markdown under `docs/`. It never stages, commits, pushes,
  creates issues, or writes project files.
- `EvidenceRecord` is the shared retrieval Interface for Vault and repository adapters.
  `QualitySignal` reports a reason, severity, evidence, and suggestion; it never triggers an
  automatic note rewrite, merge, deletion, or repository action.
- Index activation fails if any Markdown source is unreadable. Search verifies the database's
  `(path, checksum)` manifest, canonical Vault root, Vault id, and content-addressed generation
  before answering—even for an empty query.
- If retrieval abstains or a brief reports `evidence_gap`, say that the Vault lacks sufficient
  evidence instead of inventing an answer.

Read [references/safety_contract.md](references/safety_contract.md) before changing capture
behavior. Read [references/routing_protocol.md](references/routing_protocol.md) when adjusting
classification rules.

Read [references/architecture.md](references/architecture.md) before changing module boundaries,
CLI output contracts, sync/maintenance data flow, or the later portable package.

See [TASKS.md](TASKS.md) for current delivery status, acceptance gates, and the explicitly
deferred optional embedding sidecar. See [EVALUATE.md](EVALUATE.md) for the quality gates and
measured results. See [references/runtime_layout.md](references/runtime_layout.md) for state
locations and the optional Ductor integration boundary.
