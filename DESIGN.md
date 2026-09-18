# KnowledgeRouter: System Design

This is the design for the current stable behavior. It supersedes the earlier prototype
(which described a fixed `100_Project/200_Area/300_Resources` router and a single `bootstrap.py`
entrypoint). The authoritative layer/module detail lives in
[`references/architecture.md`](references/architecture.md).

## 1. Product boundary

The Vault is the human-owned source of truth. KnowledgeRouter adds an **external control plane**
and **disposable read projections**. It does not impose a folder system and does not require
Obsidian to be running. The only permitted Vault mutations are reviewed exclusive creation and
checked append through the capture broker.

## 2. Layers

| Layer | Modules | Responsibility |
|---|---|---|
| Interface | `knowledge_router.py`, `bootstrap.py` | Stable CLI, bounded output, compatibility entrypoint |
| Application | `vault_service.py` | Shared attach/sync/status/search orchestration |
| Context domain | `context_manager.py` | Generic candidate discovery, declared functional scopes, multi-context impact preview; no mutation |
| Control records | `context_store.py` | External versioned definitions, revision conflicts, baseline drift checks |
| Event execution | `context_events.py` | Bind one frozen broker capture to multiple Contexts, durable receipts, resume indexing |
| State projection | `context_state.py` | Source-verified state declarations and replacement chains; conflicting heads preserved |
| Maintenance | `context_maintenance.py`, `context_tick.py` | Opt-in review queue and one combined poll; no scheduler, no auto-repair |
| Follow-ups | `context_followups.py`, `due_time.py` | Typed state events with timezone validation; no task store, no notification |
| Delivery | `context_delivery.py` | Explicit local JSONL sink with pending/delivered/failed/cancelled receipts |
| Evidence seam | `evidence.py` | Canonical records, quality signals, deterministic federated merge |
| Read domain | `fts_indexer.py`, `taxonomy_scanner.py`, `vault_doctor.py`, `context_assembler.py`, `quality_analyzer.py` | Verified snapshot, immutable index, topology, diagnostics, briefs |
| Project adapter | `project_adapter.py` | Read-only Git metadata/docs, immutable catalog |
| Write domain | `route_engine.py`, `capture_broker.py` | Route preview, frozen plan, compare-and-swap mutation, recovery |
| Infrastructure | `registry.py`, `secure_io.py` | External registry, locks, pinned paths, safe file handles |
| Recovery | `runtime_backup.py` | Allowlisted checkpoints and isolated, verification-first restore |

The interface layer must not reimplement projection or mutation mechanics. Compatibility
wrappers call the same application service as the primary CLI.

## 3. Read flow

1. Attach: register the canonical Vault root under the external state root; pin the per-Vault
   state directory and acquire its index lock.
2. Scan every eligible Markdown source into one verified `ScanReport`; reuse that snapshot for
   taxonomy induction and index generation.
3. Build a content-addressed immutable SQLite generation; re-scan the manifest before activation
   so a concurrent source change fails closed; retain two generations for rollback.
4. Search validates index/Vault binding and generation before querying; every hit carries Vault id,
   relative path, line range, source SHA-256, generation and staleness.
5. Briefs use only index-backed sources and expose an evidence gap rather than synthesizing claims.
6. Vault and repository retrieval meet at `EvidenceRecord`; federated merge projects both adapters
   into canonical records before global ranking.

## 4. Write flow

1. Induce the existing taxonomy and resolve a target inside an existing directory.
2. Persist an external, expiring, checksummed plan with the exact action and payload.
3. On commit, reload and validate the frozen plan and record `PREPARED` externally.
4. Pin every target parent; on Windows deny competing writers and perform exclusive create or one
   checked append. Record before touching the Vault, and reconcile interrupted retries instead of
   writing twice.
5. Fsync, verify prefix/content invariants, mark the ledger committed, then refresh projections.

No interface may bypass the capture broker for Vault writes.

## 5. Context model

A Context is a **purpose with a lifecycle**, not a synonym for a folder. Definitions are versioned
external records (`context_definitions.json`) with revision conflicts and retained history.
Inferred candidates are never automatically promoted to write authority. A preview is not a
committed event; a detected follow-up is not a scheduled reminder; a maintenance hook is not a
running daemon.

One canonical event can serve several Contexts without duplicating its content into every human
note: the event Markdown is the durable record, and external receipts bind it to each Context.

## 6. Interface policy

- A Vault selector accepts its registered name, stable id, or canonical root path.
- Human output is concise by default; machine JSON is bounded by default when data can grow with
  Vault size. `--details` opts into unbounded forensic payloads.
- Exit codes: `0` success, `1` findings (e.g. doctor issues), `2` operational or safety failure.
  A completed maintenance run may report findings without being an operational failure.
- Project catalog operations are nested under `projects`; federated retrieval is opt-in.

## 7. Portability boundary

The shareable repository keeps this layer split and the behavior tests. Non-Windows append remains
fail-closed until an equivalent kernel-level writer-exclusion broker exists; packaging must not
weaken that invariant. An optional embedding sidecar or packaged executable may be added later
without changing the layer boundaries.
