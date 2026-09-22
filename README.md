# KnowledgeRouter

**Turn any existing Obsidian Vault into an agent-ready knowledge base — without moving a single note.**

KnowledgeRouter is a zero-dependency control plane that sits beside a human-owned Markdown
Vault. It indexes what is already there, resolves the *purposes* behind folders (Daily, Project,
Contact, Research, Trip, or your own types), answers questions with cited evidence, previews and
performs reviewed captures, and keeps maintenance, state and follow-ups in external durable
records. It never imposes a folder system and never rewrites human prose.

> 在不改变任何笔记习惯、不重构目录的前提下，把现有 Obsidian 库变成可被 Agent 精确检索、
> 引用、安全写入并持续维护的知识底座。人类 Markdown 是唯一真理源，索引与状态全部外部化、可重建。

---

## Why it exists

An agent dropped into a raw Markdown folder usually fails for three reasons: it cannot tell what
parts of the Vault *mean*; it cannot cite where an answer came from; and it cannot write without
risking damage. KnowledgeRouter closes that gap with four properties:

- **Purpose, not folders.** Generic Context definitions describe identity, type, purpose, scopes and
  aliases. The same engine handles Daily journals, project folders, contact notes and custom types;
  adding a type needs no new routing branch.
- **Evidence with provenance.** Every result carries `path:line_start-line_end`, source `sha256`,
  index generation and a `stale` flag. Retrieval abstains instead of inventing an answer.
- **Preview-first writes.** The only permitted Vault mutations are reviewed exclusive file creation
  or one checked byte append, executed by a broker that pins parent directories and records a
  durable recovery journal. Conflicts and interrupted runs are reconciled, never double-written.
- **Durable external state.** Registry, definitions, frozen plans, receipts and delivery ledgers live
  outside the Vault and are rebuildable where they are derived. The Vault stays authoritative.

---

## What is implemented

| Capability | Command surface |
|---|---|
| Attach + multi-Vault registry | `attach` |
| Immutable full-text index, cited search | `search`, `brief` |
| Context discovery / impact preview / registration | `contexts discover \| plan \| register \| check` |
| Evidence briefs per Context | `contexts brief`, `contexts state`, `contexts history` |
| Multi-Context event capture with recovery | `contexts capture \| commit \| event` |
| Follow-ups with timezone-safe due dates | `contexts capture --followup-*`, `contexts followups` |
| Explicit local notification sink | `contexts deliver --sink ABSOLUTE.jsonl` |
| Maintenance policy + coverage | `contexts due \| coverage`, `maintain` |
| Combined maintenance/poll cycle | `maintain --with-tick`, `contexts tick` |
| Read-only Git project catalog + federated retrieval | `projects *`, `search --projects`, `brief --projects` |
| Explainable quality signals + Context-aware queue | `quality` |
| Read-only Context relationship graph | `contexts links` |
| Compiled Context card (bundle + external save) | `contexts compile` |
| Review agenda + honest deposition ledger | `contexts review` |
| Recovery: checkpoints, backup, isolated restore | `checkpoint`, `backup`, `restore` |
| Unified operating status | `status --json` |

All 33 modules are pure Python standard library. The full suite is **224 tests**. See
[`EVALUATE.md`](EVALUATE.md) for the measured gates and [`SKILL.md`](SKILL.md) for the exact
agent-facing command contract.

Two properties are worth knowing before reading any diagnostic number:

- **One resolver, documented rules.** `link_resolver.py` resolves `[[wikilinks]]` for both the
  doctor and the relationship graph, so the two can never report different broken/ambiguous counts.
  A target that carries a path is resolved as a path (relative to the note, then from the vault
  root); a bare target by unique filename, then unique alias. Non-Markdown targets must carry their
  extension. Verdicts record the deciding level, ambiguity stays ambiguity, and two undocumented
  cases — a proximity-collapsible ambiguity and an unresolved path whose basename is unique
  elsewhere — are counted and reported rather than silently decided.
- **Hints are not verdicts.** Broken-link triage hints (`zotero_generated`, `template_placeholder`,
  `missing_asset_reference`, `needs_review`) always carry `confidence: hint`, the original totals
  are retained, and no reference is dismissed because of where it lives.

---

## Quick start

```bash
# 1. Attach a Vault (read-only; registers an external index)
python scripts/knowledge_router.py attach "/path/to/vault" --name personal

# 2. Ask a question with citations
python scripts/knowledge_router.py search "Qatar seismic spec" --vault personal --json

# 3. See which purposes exist, then bake them into versioned definitions
python scripts/knowledge_router.py contexts discover --vault personal --json
python scripts/knowledge_router.py contexts register --definitions examples/contexts.json --vault personal

# 4. Inspect a Context's existing wikilink neighbourhood (anchors, related Contexts, hubs)
python scripts/knowledge_router.py contexts links project:main --vault personal

# 5. Assemble a cited "current state" card scaffold for one Context (read-only)
python scripts/knowledge_router.py contexts compile project:main --vault personal

# 5b. Write the reviewed card to a parallel area outside the Vault (no write policy needed)
python scripts/knowledge_router.py contexts compile project:main --save --content-file card.md --vault personal

# 6. Run a review agenda; each run is recorded in an external ledger
python scripts/knowledge_router.py contexts review --vault personal

# 7. Preview a capture, then commit only the reviewed payload
python scripts/knowledge_router.py capture "Decided to defer the offline compression work" --vault personal
python scripts/knowledge_router.py capture --vault personal --commit --plan-id <id>
```

Capture requires an explicit write policy (`--write-policy append-create`) and either an explicit
user instruction or a saved `capture_mode=auto`. `write_policy=off` is the default.

`contexts review --limit N` truncates the printed page only: the ledger entry, the fingerprint and
the new/resolved deltas always use the full item set, so a smaller limit cannot look like items were
resolved. A disappearance is reported as resolved only when both the previous and the current run
had a complete basis (otherwise `comparable: false` with a named reason and `resolved_suppressed`),
and an empty agenda over an incomplete basis (no registered Context, unreadable source, truncated
poll) returns `incomplete` rather than `clear`.

`examples/contexts.json` is a starting template: edit its `scopes` to match directories that
actually contain Markdown in your Vault. Registration fails closed if a scope has no readable
evidence, so the template never silently creates folders.

---

## Architecture

The implementation is split into strict layers; the CLI never reimplements projection or mutation
mechanics.

```
Interface        knowledge_router.py, bootstrap.py      stable CLI, bounded output
Application      vault_service.py                       attach/sync/status/search orchestration
Context domain   context_manager.py                     discovery, scopes, impact preview (no writes)
Control records  context_store.py                       versioned definitions, revision conflicts
Event execution  context_events.py, context_state.py    one frozen broker plan -> many Contexts
Maintenance      context_maintenance.py, context_tick.py
Follow-ups       context_followups.py, due_time.py
Read domain      fts_indexer, taxonomy_scanner, vault_doctor, context_assembler, quality_analyzer
Project adapter  project_adapter.py                     read-only Git catalog
Write domain     route_engine.py, capture_broker.py     frozen plan, compare-and-swap, recovery
Infrastructure   registry.py, secure_io.py              external locks, pinned paths, safe handles
```

The Vault and the Git project catalog meet at one `EvidenceRecord` interface, so callers never
learn either index implementation. See [`DESIGN.md`](DESIGN.md) and
[`references/architecture.md`](references/architecture.md).

---

## Safety contract (summary)

1. Human Markdown is the only source of truth; the tool never replaces, truncates, moves, renames
   or deletes a user note.
2. Writes are exclusive-create or one checked append, behind a frozen, checksummed plan.
3. Search, doctor, quality and project catalog are read-only.
4. On this local release, Windows append uses parent-directory pinning plus an OS handle that
   denies competing writers. Other platforms fail closed for append until an equivalent kernel
   broker exists; exclusive new-file capture remains portable.
5. Index activation fails if any Markdown source is unreadable; search verifies the database's
   `(path, checksum)` manifest, canonical root and generation before answering.

Full details in [`references/safety_contract.md`](references/safety_contract.md).

---

## Layout

```text
knowledge-router/
├── SKILL.md                 # agent-facing command contract
├── README.md  DESIGN.md  TASKS.md  EVALUATE.md
├── pyproject.toml           # pytest + ruff configuration
├── scripts/                 # 33 stdlib-only modules
├── examples/contexts.json   # Context definition template
├── integrations/ductor/     # optional workspace bridge
├── references/              # architecture, safety, routing, context, operations
├── docs/                    # background research
└── tests/                   # 224 tests
```

## Deferred (explicitly)

- Optional local embedding sidecar — only if a broader benchmark shows a measured recall gap;
  lexical/bigram/alias retrieval currently meets all acceptance gates.
- Automatic extraction of state from legacy prose — explicit declarations are supported today.
- A remote notification transport adapter — the local JSONL sink is the verified boundary.

## License

MIT. See [`LICENSE`](LICENSE).
