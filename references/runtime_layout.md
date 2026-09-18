# Runtime layout and source ownership

This document describes where rules, executable code and durable state live. It is a
portable description; substitute your own Vault and state-root paths.

## Knowledge Router

| Role | Location | Treatment |
|---|---|---|
| Agent operating instructions | `SKILL.md` | How to invoke commands and interpret results |
| Architecture/safety/routing contracts | `references/architecture.md`, `safety_contract.md`, `routing_protocol.md`, `context_protocol.md` | Hard mutation restrictions are enforced by code, not just prose |
| Executable behavior | `scripts/` | Discovery/matching in `context_manager.py`; storage in `context_store.py`; events in `context_events.py`; writes only through `capture_broker.py` / `secure_io.py` |
| Read/evidence domain | `scripts/fts_indexer.py`, `taxonomy_scanner.py`, `vault_doctor.py`, `context_assembler.py`, `quality_analyzer.py` | Read-only projections and diagnostics |
| Project catalog | `scripts/project_adapter.py` | Read-only Git metadata/docs; no project code is executed |
| Recovery | `scripts/runtime_backup.py` | Allowlisted source/runtime checkpoints and isolated restore |
| Original knowledge | your Vault root | Human-owned source of truth; never restructured by the tool |
| Read-only project sources | your Git project library | Only Git metadata and selected docs are indexed |

## External state root

Production state defaults to `~/.ductor/vaults` and is deliberately kept outside the Vault.
It contains:

- `registry.json`: Vault roots, write policy, active index/catalog pointers.
- `<vault-id>/context_definitions.json`: active full definition set plus retained versions
  and source baselines.
- `taxonomy.json`, `index.<generation>.db`, `projects.<generation>.db`: rebuildable
  projections. The registry chooses the active generation; newest filename is not authority.
- `maintain_audit.jsonl`: maintenance run receipts, not a running scheduler.
- `plan.<id>.json`, `mutations.sqlite3`: frozen capture payloads and the broker recovery journal.
- `context-event.<id>.plan.json|.applied.json|.completed.json`: one broker plan bound to
  multiple Contexts, its application and index completion.
- `context_delivery.jsonl`: optional local-sink delivery ledger created only by an explicit
  `contexts deliver --sink ...`.

Indexes, caches, locks and staging files are rebuildable and are never treated as
authoritative backups. Durable definitions, plans, receipts and the mutation journal are.

## Boundary between configuration and loaded behavior

A readable skill directory does not mean a runtime has loaded the code. `status --json`
reports `installed` and `loaded` facts it can verify and leaves service enablement
explicit rather than inferring it from files on disk. Notification delivery remains
`configured=false` until an explicit local sink run records a ledger.

## Ductor integration (optional)

The optional Ductor adapter is the workspace bridge in `integrations/ductor/knowledge_router.py`.
It forwards explicit CLI arguments to the installed skill with the same Python interpreter,
without shell interpolation, and never falls back to an unrelated retrieval backend. A host
runtime that embeds the Router may additionally provide a read-only conversation hook; that
hook is opt-in, bounded, and must fail closed (disclose retrieval unavailable rather than
return empty evidence). See `TASKS.md` for the activation/rollback procedure.

## Rule handling

Explicit current-state declarations are stored in canonical event Markdown and the matching
external event plan. `context_state.py` rebuilds the view on demand; there is no second
mutable state database. Corrections reference replaced event ids. Lifecycle retirement is a
declaration, never a physical deletion. Legacy note prose is not automatically converted
into explicit declarations.

Vault `*.workflow.md` documents are evidence/descriptions, not executable automation.
Their path claims may be stale.
