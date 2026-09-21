# Windows-local architecture

This document is the authoritative architecture for the local delivery. The repository at
`D:\Documents\GitHub\knowledge-router` remains a future portability target and may still describe
the earlier prototype.

## Product boundary

The Vault is the human-owned source of truth. KnowledgeRouter adds an external control plane and
disposable read projections; it does not impose a folder system or require Obsidian to be running.
The only permitted Vault mutations are reviewed exclusive creation and checked append through the
capture broker.

## Layers and ownership

| Layer | Modules | Responsibility |
|---|---|---|
| Interface | `knowledge_router.py`, `bootstrap.py` | Stable CLI, bounded output, compatibility entrypoint |
| Application | `vault_service.py` | Shared attach/sync/status/search orchestration |
| Context domain | `context_manager.py` | Generic candidate discovery, declared functional scopes, multi-context impact preview; no mutation capability |
| Context control records | `context_store.py` | External versioned definitions, revision conflicts, source-baseline drift checks; no Vault mutations |
| Relationship graph | `link_graph.py` | Read-only external wikilink projection, MOC anchors and per-Context neighbourhoods (anchors, related Contexts, hubs, bridges); rebuildable, no Vault mutations |
| Link resolution | `link_resolver.py` | Single read-only resolution of `[[wikilinks]]` (relative path → vault path → unique basename → unique alias) shared by diagnostics and the graph so the two can never disagree; reports the deciding level, keeps ambiguities ambiguous and records a proximity candidate without acting on it |
| Compiled cards | `context_compile.py` | Read-only compiled-card bundle (scaffold plus cited evidence) and external broker-plan staging; no Vault mutation outside the capture broker |
| Deposition loop | `context_review.py` | Bounded, de-noised review agenda plus a durable external ledger with new/resolved deltas; Vault is read only |
| Context event execution | `context_events.py` | Bind one frozen broker capture to multiple Contexts, publish durable receipts, resume indexing and expose historical event references |
| Explicit state projection | `context_state.py` | Rebuild source-verified state declarations and replacement chains; preserve conflicting heads and logical lifecycle history |
| Maintenance policy | `context_maintenance.py` | Opt-in review queue from registered policies, source baselines and verified review/lifecycle state; no scheduler or automatic repair |
| Follow-ups | `context_followups.py` | Typed state-event declarations with timezone validation and due/status views; no independent task store or notification delivery |
| Due-time interpretation | `due_time.py` | Conservative local/Chinese relative date resolution into an explicit instant; missing details and DST ambiguity fail before capture |
| Evidence seam | `evidence.py` | Canonical records, quality signals, deterministic federated merge |
| Read domain | `fts_indexer.py`, `taxonomy_scanner.py`, `vault_doctor.py`, `context_assembler.py`, `quality_analyzer.py` | Verified source snapshot, immutable index, topology, diagnostics (with per-occurrence triage hints), quality, briefs |
| Project adapter | `project_adapter.py` | Read-only Git metadata/docs, immutable catalog, repository evidence |
| Write domain | `route_engine.py`, `capture_broker.py` | Route preview, frozen plan, compare-and-swap mutation, recovery |
| Infrastructure | `registry.py`, `secure_io.py` | External registry, locks, pinned paths, safe file handles |

The interface layer must not reimplement projection or mutation mechanics. Compatibility wrappers
call the same application service as the primary CLI.

## Read flow

### Context management mainline

The active local backlog is `../TASKS.md`; `context_protocol.md` defines the current
local interface. The Context layer discovers purposes and scopes from live source scans
or explicit definitions, reports inference uncertainty, and previews multiple impacts
for one input. It does not replace the broker, execute workflow notes or implement a
scheduler. Registered definitions are versioned external control records, automatically
reused by discovery/planning and checked against their source baseline. Explicit state
declarations, corrections, retirement and tracked follow-ups share the same canonical
event pipeline and source verification. Legacy prose extraction and complete agent
lifecycle automation remain open. Research, Trip, Job and custom equipment Contexts
have passed the same CLI lifecycle acceptance without per-type storage branches.
See `runtime_layout.md` for the main Ductor workspace
bridge, provider invocation rules, retained legacy entrypoint and remaining hook work.

### Attach and sync

1. Register the canonical Vault root under the fixed external state root.
2. Pin the per-Vault state directory and acquire its index lock.
3. Read every eligible Markdown source into one verified `ScanReport`.
4. Reuse that snapshot for taxonomy induction and index generation.
5. Build or verify a content-addressed immutable SQLite generation.
6. Scan the Vault manifest again before activation so a concurrent source change fails closed.
7. Atomically update the external registry, then retain two index generations for rollback.

### Search and brief

Search validates the index/Vault binding and content generation before querying. Every hit carries
Vault id, relative path, line range, source SHA-256, index generation, and staleness. Briefs use only
those index-backed sources and expose an evidence gap instead of synthesizing unsupported claims.

Vault and repository retrieval meet at the `EvidenceRecord` Interface. The Vault adapter retains
its existing output for compatibility; federated search projects both adapters into canonical
records before global ranking. This is a real Seam with two adapters, so search and briefs gain
Leverage without teaching callers either index Implementation. The resulting Locality keeps Git
rules inside `project_adapter.py` and Vault rules inside `fts_indexer.py`.

### Project catalog

One attached Vault may bind one project-library root. The project adapter enumerates immediate Git
repositories, reads metadata with optional Git locks disabled, and indexes only root
README/DESIGN/TASKS/AGENTS Markdown plus direct `docs/*.md`. It executes no project code. Each immutable
catalog generation records repository path, origin, branch, HEAD, dirty state, last commit, core
document coverage, document checksum, and working-tree-aware revision. Search verifies the catalog
root/generation/content manifest and re-reads a returned document to disclose staleness.

The catalog lives in the Vault's external state directory. Attaching a project root resets only
its external catalog binding; it never adds files, configuration, hooks, or metadata to a repo.

### Quality signals

`quality_analyzer.py` converts existing Vault diagnostics, exact duplicate hashes, and project
catalog gaps into explainable `QualitySignal` records. Defaults are bounded and no aggregate score
is calculated. Signals suggest human review but cannot call the capture broker or any Git mutation.

### Doctor and maintenance

One source scan is shared by index audit and graph diagnostics. Default CLI output is a bounded
schema-versioned summary with counts and small samples; `--details` explicitly requests every issue
occurrence. This prevents scheduled maintenance from flooding an agent context while preserving a
complete forensic mode. Maintenance writes only an external started/completed/failed audit event.

## Write flow

1. Induce the existing Vault taxonomy and resolve a target in an existing directory.
2. Persist an external, expiring, checksummed plan containing the exact action and payload.
3. On commit, reload and validate the frozen plan and record `PREPARED` externally.
4. Pin every target parent. On Windows, deny competing writers and perform exclusive create or one
   checked append.
5. Fsync, verify prefix/content invariants, mark the ledger committed, then refresh projections.

No interface may bypass the capture broker for Vault writes.

## Interface policy

- A Vault selector accepts its registered name, stable id, or canonical root path.
- Human output is concise by default. Machine JSON is bounded by default when data can grow with
  Vault size; explicit detail flags opt into unbounded forensic payloads.
- Exit codes distinguish successful operation (`0`), findings such as doctor issues (`1`), and
  operational or safety failure (`2`). A completed maintenance run may report findings without
  being an operational failure.
- Project catalog operations are nested under `projects`. Existing commands remain compatible;
  federated retrieval is opt-in through `search --projects` and `brief --projects`.

## Portability boundary

The later shareable repository should copy this layer split and behavior tests. It may add a
packaged executable, scheduler integration, and an optional embedding sidecar. Non-Windows append
remains fail-closed until an equivalent kernel-level writer exclusion broker exists; portable
packaging must not weaken that invariant.
