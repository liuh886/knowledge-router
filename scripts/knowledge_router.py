#!/usr/bin/env python3
"""One-command, source-cited Vault interface with explicit mutation policies."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Ensure package directory is on sys.path for sibling imports when loaded via console script
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from capture_broker import create_capture_plan, load_capture_plan
from context_assembler import assemble_decision_context
from context_brief import brief_context, format_context_brief
from context_compile import (
    compile_context,
    format_compile_bundle,
    stage_compiled_card,
    write_compiled_card,
)
from context_delivery import deliver_followups
from context_events import commit_event, context_history, inspect_event, preview_event
from context_followups import followup_change, list_followups
from context_maintenance import maintenance_queue
from context_review import format_review_agenda, review_agenda
from context_state import project_state
from context_store import (
    check_contexts,
    compact_context_store,
    load_context_version,
    register_contexts,
)
from context_tick import poll_contexts
from maintenance_cycle import (
    maintenance_coverage,
    maintenance_status,
    run_maintenance_cycle,
)
from due_time import resolve_due_time
from evidence import EvidenceRecord, merge_evidence
from fts_indexer import audit_index, scan_notes
from link_convergence import converge_targets, format_convergence, stage_missing_note
from knowledge_page import (
    context_pack,
    expand_claim,
    find_page,
    format_expand,
    format_page_pack,
    load_pages,
    page_status,
    publish_page,
)
from link_graph import context_links, format_context_links
from project_adapter import (
    list_projects as list_catalog_projects,
)
from project_adapter import (
    project_status,
    search_projects,
    sync_project_catalog,
)
from quality_analyzer import (
    context_quality_queue,
    project_quality_signals,
    quality_report,
    vault_quality_signals,
)
from registry import FileLock, VaultConfig, VaultRegistry
from route_engine import resolve_route
from runtime_backup import (
    create_runtime_backup,
    create_source_checkpoint,
    restore_backup_archive,
)
from secure_io import secure_read_bytes
from scaffold import scaffold_symbiotic_vault
from taxonomy_scanner import scan_taxonomy
from text_budget import budget_brief
from memory_commands import add_memory_commands, run_memory_command
from memory_service import plan_summary
from mutation_service import commit_and_refresh
from vault_doctor import (
    diagnose_vault,
    diagnostic_counts,
    format_diagnostics_summary,
    summarize_diagnostics,
)
from vault_service import (
    active_index_path,
    inspect_contexts,
    search_vault,
    sync_vault,
    sync_vault_with_scan,
    vault_status,
)


__version__ = "0.2.0"


def _emit(value: object, *, json_output: bool, message: str | None = None) -> None:
    if json_output:
        print(json.dumps(value, ensure_ascii=False, indent=2))
    elif message is not None:
        print(message)
    elif isinstance(value, str):
        print(value)
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2))


def _record_maintenance(
    registry: VaultRegistry,
    config: VaultConfig,
    *,
    run_id: str,
    status: str,
    result: dict[str, Any] | None = None,
    error: str | None = None,
    duration_ms: float | None = None,
) -> None:
    state = registry.vault_state_dir(config)
    event: dict[str, Any] = {
        "schema_version": 1,
        "run_id": run_id,
        "recorded_at": datetime.now(UTC).isoformat(),
        "vault_id": config.id,
        "status": status,
    }
    if duration_ms is not None:
        event["duration_ms"] = round(duration_ms, 3)
    if result is not None:
        sync_result = result.get("sync")
        counts = diagnostic_counts(result["doctor"])
        event.update(
            {
                "index_generation": (
                    sync_result.get("generation", config.index_generation)
                    if sync_result
                    else config.index_generation
                ),
                "doctor_status": result["doctor"]["status"],
                "notes_scanned": result["doctor"]["notes_scanned"],
                "broken_links": counts["broken_link_occurrences"],
                "broken_targets": counts["broken_targets"],
                "broken_by_hint": result["doctor"].get("broken_by_hint", {}),
                "ambiguous_links": counts["ambiguous_link_occurrences"],
                "ambiguous_targets": counts["ambiguous_targets"],
                "orphans": counts["orphans"],
                "scan_complete": result["doctor"].get("scan_complete", True),
            }
        )
    if error:
        event["error"] = error
    payload = (json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
    with registry.guard_vault_state(config) as state_guard:
        with FileLock(state / ".maintain-audit.lock", pinned_parent=state_guard):
            try:
                descriptor = state_guard.open_existing(
                    writable=True,
                    exclusive_writes=False,
                    name="maintain_audit.jsonl",
                )
            except FileNotFoundError:
                descriptor = state_guard.create_new(name="maintain_audit.jsonl")
            try:
                os.lseek(descriptor, 0, os.SEEK_END)
                written = os.write(descriptor, payload)
                if written != len(payload):
                    raise OSError("Short maintenance audit write")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)


def _maintenance_message(result: dict[str, Any] | list[dict[str, Any]]) -> str:
    entries = result if isinstance(result, list) else [result]
    lines: list[str] = []
    for entry in entries:
        if entry["status"] == "failed":
            lines.append(f"{entry['vault']}: failed: {entry['error']}")
            continue
        lines.append(
            f"{entry['vault']}: completed; "
            f"index {entry['index_audit']['status']}; "
            f"{format_diagnostics_summary(entry['doctor'])}"
        )
    return "\n".join(lines)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-root", type=Path, help="Override external derived-data root")
    sub = parser.add_subparsers(dest="command", required=True)

    attach = sub.add_parser("attach", help="Attach and index a Vault without changing it")
    attach.add_argument("path", type=Path)
    attach.add_argument(
        "--projects-root",
        type=Path,
        help="Optionally bind a read-only Git project library",
    )
    attach.add_argument("--name")
    attach.add_argument(
        "--write-policy", choices=("off", "append-create", "reviewed-edit"), default="off"
    )
    attach.add_argument("--capture-mode", choices=("explicit", "auto"), default="explicit")
    attach.add_argument("--json", action="store_true")

    scaffold = sub.add_parser(
        "scaffold",
        help="Scaffold a Vault with the 6 symbiotic memory slices (Inbox stream, Focus, Contact, WYQD)",
    )
    scaffold.add_argument("path", type=Path, help="Target Vault directory path")
    scaffold.add_argument("--name", help="Custom name for the vault in registry")
    scaffold.add_argument(
        "--no-attach", action="store_true", help="Do not attach after scaffolding"
    )
    scaffold.add_argument(
        "--write-policy",
        choices=("off", "append-create", "reviewed-edit"),
        default="off",
        help="Write policy after initialization (default: off)",
    )
    scaffold.add_argument("--json", action="store_true")

    listing = sub.add_parser("list", help="List attached Vaults")
    listing.add_argument("--json", action="store_true")

    status = sub.add_parser("status", help="Show attachment and index status")
    status.add_argument("--vault")
    status.add_argument("--audit", action="store_true")
    status.add_argument("--json", action="store_true")

    sync = sub.add_parser("sync", help="Build and atomically activate a fresh external index")
    sync.add_argument("--vault")
    sync.add_argument("--json", action="store_true")
    sync.add_argument("--full", action="store_true", help="Build a compact fresh index instead of inheriting a predecessor")

    search = sub.add_parser("search", help="Search with path, lines, checksum, and staleness")
    search.add_argument("query")
    search.add_argument("--vault")
    search.add_argument("--all", action="store_true")
    search.add_argument(
        "--projects", action="store_true", help="Include the attached project catalog"
    )
    search.add_argument("--project", help="Limit repository evidence to one project id")
    search.add_argument("--limit", type=int, default=5)
    search.add_argument("--json", action="store_true")

    route = sub.add_parser("route", help="Preview a capture route")
    route.add_argument("text")
    route.add_argument("--vault")
    route.add_argument("--json", action="store_true")

    capture = sub.add_parser(
        "capture", help="Preview by default; commit only when explicitly asked"
    )
    capture.add_argument("text", nargs="?")
    capture.add_argument("--vault")
    capture.add_argument("--title")
    capture.add_argument("--commit", action="store_true")
    capture.add_argument("--plan-id")
    capture.add_argument("--expected-sha256")
    capture.add_argument("--idempotency-key")
    capture.add_argument("--json", action="store_true")

    brief = sub.add_parser("brief", help="Build a bounded source-cited reference brief")
    brief.add_argument("topic")
    brief.add_argument("--vault")
    brief.add_argument("--projects", action="store_true", help="Include repository documentation")
    brief.add_argument("--project", help="Limit repository evidence to one project id")
    brief.add_argument("--json", action="store_true")
    brief.add_argument(
        "--budget-tokens",
        type=int,
        help="Budget the complete output using a disclosed token estimate",
    )

    projects = sub.add_parser(
        "projects", help="Attach, sync, and inspect a read-only Git project catalog"
    )
    project_sub = projects.add_subparsers(dest="project_command", required=True)
    projects_attach = project_sub.add_parser(
        "attach", help="Bind and catalog a Git project-library root"
    )
    projects_attach.add_argument("path", type=Path)
    projects_attach.add_argument("--vault")
    projects_attach.add_argument("--json", action="store_true")
    projects_sync = project_sub.add_parser("sync", help="Refresh the project catalog")
    projects_sync.add_argument("--vault")
    projects_sync.add_argument("--json", action="store_true")
    projects_status = project_sub.add_parser("status", help="Show project catalog status")
    projects_status.add_argument("--vault")
    projects_status.add_argument("--json", action="store_true")
    projects_list = project_sub.add_parser("list", help="List cataloged Git repositories")
    projects_list.add_argument("--vault")
    projects_list.add_argument("--json", action="store_true")

    contexts = sub.add_parser(
        "contexts", help="Discover functional Contexts and preview maintenance"
    )
    context_sub = contexts.add_subparsers(dest="context_command", required=True)
    for operation in ("discover", "plan", "register", "check"):
        context_parser = context_sub.add_parser(operation)
        if operation == "plan":
            context_parser.add_argument("text")
        elif operation == "discover":
            context_parser.add_argument("--details", action="store_true")
        if operation == "register":
            context_parser.add_argument("--expected-revision", type=int, default=0)
        context_parser.add_argument("--vault")
        context_parser.add_argument(
            "--definitions",
            type=Path,
            required=operation == "register",
            help="Explicit Context definition JSON",
        )
        context_parser.add_argument("--limit", type=int, default=20)
        context_parser.add_argument("--json", action="store_true")

    context_brief = context_sub.add_parser(
        "brief", help="Read a scoped, source-verified Context brief"
    )
    context_brief.add_argument("selector")
    context_brief.add_argument("--query", default="")
    context_brief.add_argument("--limit", type=int, default=8)
    context_brief.add_argument("--project", help="Explicit project id; requires --query")
    context_brief.add_argument("--vault")
    context_brief.add_argument("--json", action="store_true")
    tick = context_sub.add_parser(
        "tick", help="One scheduler poll for maintenance and due follow-ups"
    )
    tick.add_argument("--vault")
    tick.add_argument("--limit", type=int, default=20)
    tick.add_argument("--max-contexts", type=int, default=100)
    tick.add_argument("--json", action="store_true")
    due = context_sub.add_parser("due", help="Read per-Context maintenance review queue")
    due.add_argument("--vault")
    due.add_argument("--limit", type=int, default=20)
    due.add_argument("--json", action="store_true")
    coverage = context_sub.add_parser(
        "coverage", help="Show registered/enabled/disabled/retired maintenance coverage"
    )
    coverage.add_argument("--vault")
    coverage.add_argument("--limit", type=int, default=20)
    coverage.add_argument("--json", action="store_true")
    links = context_sub.add_parser(
        "links", help="Show one Context's read-only wikilink relationship graph"
    )
    links.add_argument("selector")
    links.add_argument("--limit", type=int, default=12)
    links.add_argument(
        "--rebuild", action="store_true", help="Rebuild the external link graph first"
    )
    links.add_argument("--vault")
    links.add_argument("--json", action="store_true")
    compile_parser = context_sub.add_parser(
        "compile", help="Assemble a compiled Context card bundle or stage a draft"
    )
    compile_parser.add_argument("selector")
    compile_parser.add_argument("--stage", action="store_true")
    compile_parser.add_argument("--save", action="store_true")
    compile_parser.add_argument("--content-file", type=Path)
    compile_parser.add_argument(
        "--expected-source-snapshot",
        help=(
            "The bundle's source_snapshot_sha256 the draft was written against. "
            "Verified before writing; without it the card is recorded as unverified basis."
        ),
    )
    compile_parser.add_argument("--target")
    compile_parser.add_argument("--out-root")
    compile_parser.add_argument("--name")
    compile_parser.add_argument("--overwrite", action="store_true")
    compile_parser.add_argument("--title")
    compile_parser.add_argument("--idempotency-key")
    compile_parser.add_argument("--limit", type=int, default=8)
    compile_parser.add_argument(
        "--query", help="Topic terms; defaults to the Context name and aliases"
    )
    compile_parser.add_argument("--vault")
    compile_parser.add_argument("--json", action="store_true")
    review = context_sub.add_parser(
        "review", help="Bounded review agenda plus an external deposition ledger entry"
    )
    review.add_argument("--context")
    review.add_argument("--limit", type=int, default=20)
    review.add_argument(
        "--code",
        help="Show only one finding code (display filter; the ledger still records all)",
    )
    review.add_argument("--no-record", action="store_true")
    review.add_argument("--vault")
    review.add_argument("--json", action="store_true")
    converge = context_sub.add_parser(
        "converge",
        help="Collapse repeated unresolved link targets into one decision each",
    )
    converge.add_argument("--context")
    converge.add_argument("--limit", type=int, default=20)
    converge.add_argument("--min-occurrences", type=int, default=1)
    converge.add_argument(
        "--include-noisy",
        action="store_true",
        help="Also converge machine-generated link noise (Zotero/template)",
    )
    converge.add_argument(
        "--create",
        help="Stage a plan creating this unresolved target (requires --target-path)",
    )
    converge.add_argument("--target-path", help="Vault-relative .md path for --create")
    converge.add_argument("--content-file", type=Path)
    converge.add_argument("--title")
    converge.add_argument("--idempotency-key")
    converge.add_argument("--vault")
    converge.add_argument("--json", action="store_true")
    compact = context_sub.add_parser(
        "compact",
        help="Drop redundant per-revision binding snapshots from external state",
    )
    compact.add_argument("--vault")
    compact.add_argument("--json", action="store_true")
    get_parser = context_sub.add_parser(
        "get", help="Assemble a budgeted, source-grounded context pack"
    )
    get_parser.add_argument("selector")
    get_parser.add_argument("--budget-tokens", type=int, default=1200)
    get_parser.add_argument("--vault")
    get_parser.add_argument("--json", action="store_true")
    expand_parser = context_sub.add_parser(
        "expand", help="Expand one published claim to its cited sources"
    )
    expand_parser.add_argument("page_id")
    expand_parser.add_argument("claim_id")
    expand_parser.add_argument("--depth", choices=("brief", "full"), default="brief")
    expand_parser.add_argument("--vault")
    expand_parser.add_argument("--json", action="store_true")
    page_parser = context_sub.add_parser(
        "page", help="Publish, list or inspect versioned knowledge pages"
    )
    page_sub = page_parser.add_subparsers(dest="page_command", required=True)
    page_publish = page_sub.add_parser("publish")
    page_publish.add_argument("selector")
    page_publish.add_argument("--content-file", type=Path, required=True)
    page_publish.add_argument("--name")
    page_publish.add_argument("--out-root")
    page_publish.add_argument("--overwrite", action="store_true")
    page_publish.add_argument("--title")
    page_publish.add_argument("--page-type")
    page_publish.add_argument("--vault")
    page_publish.add_argument("--json", action="store_true")
    page_list = page_sub.add_parser("list")
    page_list.add_argument("--vault")
    page_list.add_argument("--json", action="store_true")
    page_show = page_sub.add_parser("show")
    page_show.add_argument("page_id")
    page_show.add_argument("--vault")
    page_show.add_argument("--json", action="store_true")
    history = context_sub.add_parser(
        "history", help="List applied event references for one Context"
    )
    history.add_argument("context_id")
    history.add_argument("--vault")
    history.add_argument("--limit", type=int, default=20)
    history.add_argument("--json", action="store_true")
    event_capture = context_sub.add_parser("capture", help="Freeze one event and its Context links")
    event_capture.add_argument("text")
    event_capture.add_argument("--target", required=True)
    event_capture.add_argument("--primary-context", required=True)
    event_capture.add_argument("--context-id", action="append", default=[])
    event_capture.add_argument("--idempotency-key", required=True)
    event_capture.add_argument("--state-field")
    event_capture.add_argument("--state-value")
    event_capture.add_argument("--state-context")
    event_capture.add_argument("--replaces", action="append", default=[])
    event_capture.add_argument("--followup-id")
    event_capture.add_argument("--followup-title")
    event_capture.add_argument("--due-at")
    event_capture.add_argument("--timezone", help="IANA timezone for relative or local due times")
    event_capture.add_argument("--followup-status", choices=("open", "done", "cancelled"))
    event_capture.add_argument("--vault")
    event_capture.add_argument("--json", action="store_true")
    followups = context_sub.add_parser("followups", help="List source-backed follow-ups")
    followups.add_argument("context_id")
    followups.add_argument("--due-only", action="store_true")
    followups.add_argument("--limit", type=int, default=20)
    followups.add_argument("--vault")
    followups.add_argument("--json", action="store_true")
    delivery = context_sub.add_parser(
        "deliver", help="Deliver due follow-ups to an explicit local JSONL sink"
    )
    delivery.add_argument(
        "--sink", type=Path, required=True, help="Absolute JSONL sink path outside the Vault"
    )
    delivery.add_argument("--limit", type=int, default=20)
    delivery.add_argument("--retry-failed", action="store_true")
    delivery.add_argument("--vault")
    delivery.add_argument("--json", action="store_true")
    state_parser = context_sub.add_parser(
        "state", help="Rebuild explicit source-backed Context state"
    )
    state_parser.add_argument("context_id")
    state_parser.add_argument("--vault")
    state_parser.add_argument("--limit", type=int, default=20)
    state_parser.add_argument("--json", action="store_true")
    for operation in ("commit", "event"):
        event_command = context_sub.add_parser(operation)
        event_command.add_argument("event_id")
        event_command.add_argument("--vault")
        event_command.add_argument("--json", action="store_true")

    quality = sub.add_parser(
        "quality", help="Read-only explainable Vault and project quality signals"
    )
    quality.add_argument("--vault")
    quality.add_argument(
        "--vault-only", action="store_true", help="Ignore an attached project catalog"
    )
    quality.add_argument("--details", action="store_true")
    quality.add_argument("--detail-limit", type=int, default=20)
    quality.add_argument("--json", action="store_true")

    doctor = sub.add_parser("doctor", help="Read-only broken-link and orphan report")
    doctor.add_argument("--vault")
    doctor.add_argument("--include-daily-orphans", action="store_true")
    doctor.add_argument("--details", action="store_true", help="Return every issue occurrence")
    doctor.add_argument("--detail-limit", type=int, default=10)
    doctor.add_argument("--json", action="store_true")

    maintain = sub.add_parser("maintain", help="Refresh index then run read-only diagnostics")
    maintain.add_argument("--vault")
    maintain.add_argument("--all", action="store_true")
    maintain.add_argument("--no-sync", action="store_true")
    maintain.add_argument("--audit-only", action="store_true")
    maintain.add_argument(
        "--with-tick",
        action="store_true",
        help="Run maintain primitives to completion, then the tick poll sequentially",
    )
    maintain.add_argument("--tick-limit", type=int, default=20)
    maintain.add_argument("--tick-max-contexts", type=int, default=100)
    maintain.add_argument(
        "--details", action="store_true", help="Return every doctor issue occurrence"
    )
    maintain.add_argument("--detail-limit", type=int, default=10)
    maintain.add_argument("--json", action="store_true")

    backup = sub.add_parser(
        "backup", help="Archive durable Router-owned runtime state (never Vault notes)"
    )
    backup.add_argument("--vault")
    backup.add_argument("--backup-root", type=Path, required=True)
    backup.add_argument("--label")
    backup.add_argument("--json", action="store_true")

    restore = sub.add_parser(
        "restore", help="Verify and rehearse-restore a backup into an isolated target"
    )
    restore.add_argument("--archive", type=Path, required=True)
    restore.add_argument("--target", type=Path, required=True)
    restore.add_argument("--allow-empty-existing", action="store_true")
    restore.add_argument("--json", action="store_true")

    checkpoint = sub.add_parser(
        "checkpoint", help="Archive allowlisted Router source files (never Vault notes)"
    )
    checkpoint.add_argument("--source-root", type=Path, required=True)
    checkpoint.add_argument("--backup-root", type=Path, required=True)
    checkpoint.add_argument("--label")
    checkpoint.add_argument("--json", action="store_true")

    add_memory_commands(sub)
    return parser


def main() -> int:
    args = _parser().parse_args()
    try:
        registry = VaultRegistry(args.state_root)
        if args.command == "scaffold":
            res = scaffold_symbiotic_vault(
                args.path,
                name=args.name,
                attach=not args.no_attach,
                write_policy=args.write_policy,
                state_root=args.state_root,
            )
            msg = (
                f"✓ 成功生产共生记忆架构于: {res['vault_root']}\n"
                f"  新建目录: {res['created_directories_count']} 个，新建模板文件: {res['created_files_count']} 个"
            )
            if res.get("attach"):
                msg += f"\n  已自动挂载并索引: [{res['attach']['vault_name']}] ({res['attach']['indexed_notes']} 篇笔记)"
            _emit(res, json_output=args.json, message=msg)
            return 0

        if args.command == "attach":
            config = registry.attach(
                args.path,
                name=args.name,
                write_policy=args.write_policy,
                capture_mode=args.capture_mode,
            )
            sync_result = sync_vault(registry, config)
            result = {
                "attachment": registry.resolve(config.id).to_dict(),
                "sync": sync_result,
            }
            if args.projects_root:
                attached = registry.attach_projects(config.id, args.projects_root)
                result["projects"] = sync_project_catalog(registry, attached)
            _emit(
                result,
                json_output=args.json,
                message=(
                    f"Attached {config.name}: {config.root}\n"
                    f"Indexed {result['sync']['indexed_notes']} notes outside the Vault."
                    + (
                        f" Cataloged {result['projects']['project_count']} repositories."
                        if "projects" in result
                        else ""
                    )
                ),
            )
            return 0

        if args.command == "list":
            configs = [item.to_dict() for item in registry.list()]
            _emit(configs, json_output=args.json)
            return 0

        if args.command == "checkpoint":
            result = create_source_checkpoint(args.source_root, args.backup_root, label=args.label)
            _emit(
                result,
                json_output=args.json,
                message=f"Checkpoint {result['archive']} ({result['entry_count']} entries)",
            )
            return 0
        if args.command == "restore":
            result = restore_backup_archive(
                args.archive,
                args.target,
                allow_empty_existing=args.allow_empty_existing,
            )
            _emit(
                result,
                json_output=args.json,
                message=f"Restored {result['entry_count']} entries to {result['target']}",
            )
            return 0
        if args.command in {"search", "maintain"} and args.all:
            configs = registry.list()
            if not configs:
                raise KeyError("No Vault is attached. Run `knowledge_router.py attach <path>`.")
            config = configs[0]
        else:
            config = registry.resolve(getattr(args, "vault", None))
        if args.command in {"triage", "contact", "inbox", "streams"}:
            return run_memory_command(args, registry, config, _emit)
        if args.command == "backup":
            result = create_runtime_backup(registry, config, args.backup_root, label=args.label)
            _emit(
                result,
                json_output=args.json,
                message=f"Backup {result['archive']} ({result['entry_count']} entries)",
            )
            return 0
        if args.command == "status":
            result = vault_status(registry, config, audit=args.audit)
            try:
                coverage = maintenance_coverage(registry, config, limit=100)
                runtime = maintenance_status(registry, config, limit=20)
            except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
                coverage = {
                    "status": "incomplete",
                    "scan_incomplete": True,
                    "error": str(exc),
                }
                runtime = {
                    "status": "incomplete",
                    "errors": [str(exc)],
                    "error_count": 1,
                }
            result["maintenance_coverage"] = coverage
            result["runtime"] = {
                "installed": True,
                "loaded": True,
                "enabled": None,
                "enabled_reason": "Router is opt-in per invocation; Ductor activation is separate",
                **runtime,
            }
            _emit(result, json_output=args.json)
        elif args.command == "sync":
            result = sync_vault(registry, config, force_rebuild=args.full)
            _emit(
                result,
                json_output=args.json,
                message=f"Indexed {result['indexed_notes']} notes for {config.name}.",
            )
        elif args.command == "search":
            if args.limit < 1 or args.limit > 50:
                raise ValueError("--limit must be between 1 and 50")
            if args.project and not args.projects:
                raise ValueError("--project requires --projects")
            configs = registry.list() if args.all else [config]
            results: list[dict[str, Any]] = []
            for item in configs:
                results.extend(search_vault(registry, item, args.query, limit=args.limit))
            if args.projects:
                evidence_groups: list[list[EvidenceRecord]] = [
                    [EvidenceRecord.from_vault_hit(item) for item in results]
                ]
                catalog_count = 0
                for item in configs:
                    if not item.project_catalog:
                        continue
                    catalog_count += 1
                    evidence_groups.append(
                        search_projects(
                            registry,
                            item,
                            args.query,
                            limit=args.limit,
                            project_id=args.project,
                        )
                    )
                if not catalog_count:
                    raise FileNotFoundError(
                        "No selected Vault has a project catalog; run projects attach <path>"
                    )
                merged = merge_evidence(evidence_groups, limit=args.limit)
                canonical = [
                    item.to_dict(compatibility=item.source_type == "vault") for item in merged
                ]
                if args.json:
                    _emit(canonical, json_output=True)
                elif not canonical:
                    print("No sufficiently relevant Vault or repository evidence found.")
                else:
                    for item in canonical:
                        stale = " STALE" if item["stale"] else ""
                        if item["source_type"] == "repository":
                            source = (
                                f"{item.get('project_id', item['source_id'])}/{item['path']}"
                                f":{item['line_start']}-{item['line_end']}"
                            )
                        else:
                            source = f"{item['path']}:{item['line_start']}-{item['line_end']}"
                        print(
                            f"## {item['title']}{stale}\n{item['excerpt']}\n"
                            f"Source: {source} sha256 {item['content_hash']} "
                            f"revision {item['revision']}\n"
                        )
                return 0
            results.sort(
                key=lambda item: (
                    -float(item.get("score", 0.0)),
                    str(item["vault_id"]),
                    str(item["path"]),
                )
            )
            results = results[: args.limit]
            if args.json:
                _emit(results, json_output=True)
            elif not results:
                print("No sufficiently relevant Vault evidence found.")
            else:
                for item in results:
                    stale = " STALE" if item["stale"] else ""
                    print(
                        f"## {item['title']}{stale}\n{item['excerpt']}\n"
                        f"Source: {item['path']}:{item['line_start']}-{item['line_end']} "
                        f"sha256 {item['source_sha256']}\n"
                    )
        elif args.command == "contexts":
            if args.context_command == "tick":
                result = poll_contexts(
                    registry, config, limit=args.limit, max_contexts=args.max_contexts
                )
                _emit(result, json_output=args.json)
                return 2 if result["scan_incomplete"] else 0
            elif args.context_command == "followups":
                result = list_followups(
                    registry,
                    config,
                    args.context_id,
                    due_only=args.due_only,
                    limit=args.limit,
                )
            elif args.context_command == "deliver":
                result = deliver_followups(
                    registry,
                    config,
                    args.sink,
                    limit=args.limit,
                    retry_failed=args.retry_failed,
                )
            elif args.context_command == "due":
                result = maintenance_queue(registry, config, limit=args.limit)
                _emit(result, json_output=args.json)
                return 0
            elif args.context_command == "coverage":
                result = maintenance_coverage(registry, config, limit=args.limit)
                _emit(result, json_output=args.json)
                return 2 if result["scan_incomplete"] else 0
            elif args.context_command == "links":
                result = context_links(
                    registry,
                    config,
                    args.selector,
                    limit=args.limit,
                    rebuild=args.rebuild,
                )
                _emit(
                    result,
                    json_output=args.json,
                    message=format_context_links(result),
                )
                return 0
            elif args.context_command == "compile":
                if args.save:
                    if not args.content_file:
                        raise ValueError("--save requires --content-file")
                    path = args.content_file.expanduser().absolute()
                    content = secure_read_bytes(path.parent, path.name).decode("utf-8-sig")
                    written = write_compiled_card(
                        registry,
                        config,
                        args.selector,
                        content,
                        name=args.name,
                        out_root=args.out_root,
                        overwrite=args.overwrite,
                        title=args.title,
                        expected_source_snapshot=args.expected_source_snapshot,
                    )
                    _emit(
                        written,
                        json_output=args.json,
                        message=(
                            f"已写入外部编译区（未碰 Vault）：{written['path']}\n"
                            f"编译区：{written['compiled_area']}\n"
                            f"依据：{written['basis']}"
                            + (
                                ""
                                if written["basis"] == "verified"
                                else "（未提供编译包来源快照，登记的是保存时刻来源）"
                            )
                        ),
                    )
                    return 0
                if args.stage:
                    if not args.content_file:
                        raise ValueError("--stage requires --content-file")
                    path = args.content_file.expanduser().absolute()
                    content = secure_read_bytes(path.parent, path.name).decode("utf-8-sig")
                    staged = stage_compiled_card(
                        registry,
                        config,
                        args.selector,
                        content,
                        target=args.target,
                        title=args.title,
                        idempotency_key=args.idempotency_key,
                    )
                    _emit(
                        staged,
                        json_output=args.json,
                        message=(
                            f"Staged only (no Vault write): {staged['action']} -> "
                            f"{staged['relative_path']}\nPlan: {staged['plan_id']}\n"
                            f"Commit: {staged['commit_command']}"
                        ),
                    )
                    return 0
                bundle = compile_context(
                    registry,
                    config,
                    args.selector,
                    limit=args.limit,
                    target=args.target,
                    query=getattr(args, "query", None),
                )
                _emit(
                    bundle,
                    json_output=args.json,
                    message=format_compile_bundle(bundle),
                )
                return 0
            elif args.context_command == "review":
                agenda = review_agenda(
                    registry,
                    config,
                    context_id=args.context,
                    limit=args.limit,
                    code=args.code,
                    record=not args.no_record,
                )
                _emit(
                    agenda,
                    json_output=args.json,
                    message=format_review_agenda(agenda),
                )
                return 2 if agenda["scan_incomplete"] else 0
            elif args.context_command == "converge":
                if args.create:
                    if not args.target_path:
                        raise ValueError("--create requires --target-path")
                    content = (
                        args.content_file.read_text(encoding="utf-8") if args.content_file else None
                    )
                    staged = stage_missing_note(
                        registry,
                        config,
                        args.create,
                        args.target_path,
                        content=content,
                        title=args.title,
                        idempotency_key=args.idempotency_key,
                    )
                    _emit(
                        staged,
                        json_output=args.json,
                        message=(
                            f"已冻结计划（未写入）：{staged['action']} -> "
                            f"{staged['relative_path']}\n"
                            f"计划：{staged['plan_id']}\n"
                            f"提交：{staged['next_step']}"
                        ),
                    )
                    return 0
                version = load_context_version(registry, config)
                definitions = version["definitions"]["contexts"] if version else []
                scan = scan_notes(config.root_path)
                report = converge_targets(
                    config,
                    definitions,
                    scan,
                    context_id=args.context,
                    min_occurrences=args.min_occurrences,
                    include_noisy=args.include_noisy,
                )
                summary = report.to_dict()
                scan_incomplete = (
                    bool(scan.skipped_decode or scan.skipped_io) or not report.configured
                )
                result = {
                    "schema_version": 1,
                    "vault_id": config.id,
                    "scope": report.scope,
                    **summary,
                    "scan_incomplete": scan_incomplete,
                    "items": [item.to_dict() for item in report.items[: args.limit]],
                    "items_truncated": len(report.items) > args.limit,
                    "writes_performed": 0,
                    "vault_writes": 0,
                    "meaning": (
                        "One decision per repeated target name, with the evidence behind each "
                        "suggestion. Suggestions are not verdicts and nothing is written."
                    ),
                }
                _emit(
                    result,
                    json_output=args.json,
                    message=format_convergence(report),
                )
                return 2 if scan_incomplete else 0
            elif args.context_command == "compact":
                result = compact_context_store(registry, config)
                _emit(
                    result,
                    json_output=args.json,
                    message=(
                        f"状态 {result['status']} · 压缩 {result['revisions_compacted']} 个历史版本 · "
                        f"{result['bytes_before']:,} -> {result['bytes_after']:,} 字节"
                        f"（省 {result.get('bytes_saved', 0):,}）"
                    ),
                )
                return 0
            elif args.context_command == "get":
                pack = context_pack(
                    registry,
                    config,
                    args.selector,
                    budget_tokens=args.budget_tokens,
                )
                _emit(
                    pack,
                    json_output=args.json,
                    message=format_page_pack(pack),
                )
                return 0
            elif args.context_command == "expand":
                expanded = expand_claim(
                    registry, config, args.page_id, args.claim_id, depth=args.depth
                )
                _emit(
                    expanded,
                    json_output=args.json,
                    message=format_expand(expanded),
                )
                return 0
            elif args.context_command == "page":
                if args.page_command == "publish":
                    draft_path = args.content_file.expanduser().absolute()
                    draft = secure_read_bytes(draft_path.parent, draft_path.name).decode(
                        "utf-8-sig"
                    )
                    published = publish_page(
                        registry,
                        config,
                        args.selector,
                        draft,
                        name=args.name,
                        out_root=args.out_root,
                        overwrite=args.overwrite,
                        title=args.title,
                        page_type=args.page_type,
                    )
                    _emit(
                        published,
                        json_output=args.json,
                        message=(
                            f"已发布知识页 v{published['version']}：{published['path']}\n"
                            f"主张 {published['claim_count']} 条 "
                            f"（{published['claims_by_kind']}）· 引用 "
                            f"{published['citation_count']} 处 · 未碰 Vault"
                        ),
                    )
                    return 0
                if args.page_command == "list":
                    pages = load_pages(registry, config)
                    rows = []
                    for page in pages:
                        status = page_status(registry, config, page)
                        rows.append(
                            {
                                "page_id": page.get("page_id"),
                                "version": page.get("version"),
                                "path": page.get("path"),
                                "claim_count": len(page.get("claims", [])),
                                "stale": status["stale"],
                            }
                        )
                    _emit(
                        {"page_count": len(rows), "pages": rows},
                        json_output=args.json,
                        message="\n".join(
                            f"{row['page_id']} v{row['version']} stale={row['stale']} {row['path']}"
                            for row in rows
                        )
                        or "尚未发布任何知识页。",
                    )
                    return 0
                page = find_page(registry, config, args.page_id)
                if page is None:
                    raise FileNotFoundError(f"No published page matches: {args.page_id}")
                status = page_status(registry, config, page)
                _emit(
                    {"page": page, "status": status},
                    json_output=args.json,
                    message=(
                        f"{page['page_id']} v{page['version']} · stale={status['stale']} · "
                        f"来源 {page['source_count']} 条 · 主张 {len(page['claims'])} 条\n"
                        f"{page['path']}"
                    ),
                )
                return 0
            elif args.context_command == "state":
                result = project_state(registry, config, args.context_id, limit=args.limit)
            elif args.context_command == "brief":
                result = brief_context(
                    registry,
                    config,
                    args.selector,
                    query=args.query,
                    limit=args.limit,
                    project_id=args.project,
                )
                _emit(result, json_output=args.json, message=format_context_brief(result))
                return 0
            elif args.context_command == "history":
                result = context_history(registry, config, args.context_id, limit=args.limit)
            elif args.context_command == "capture":
                change = None
                due_resolution = None
                if args.timezone and not args.due_at:
                    raise ValueError("--timezone requires --due-at")
                if args.followup_id or args.followup_title or args.due_at or args.followup_status:
                    if args.state_field or args.state_value:
                        raise ValueError("Use follow-up fields or generic state fields, not both")
                    if not args.followup_id or not args.followup_title or not args.due_at:
                        raise ValueError(
                            "Follow-up requires --followup-id, --followup-title and --due-at"
                        )
                    due_resolution = resolve_due_time(args.due_at, timezone=args.timezone)
                    change = followup_change(
                        args.state_context or args.primary_context,
                        followup_id=args.followup_id,
                        title=args.followup_title,
                        due_at=due_resolution["due_at"],
                        status=args.followup_status or "open",
                        replaces=args.replaces,
                    )
                elif args.state_field or args.state_value or args.state_context or args.replaces:
                    if not args.state_field or not args.state_value:
                        raise ValueError("State capture requires --state-field and --state-value")
                    change = {
                        "context_id": args.state_context or args.primary_context,
                        "field": args.state_field,
                        "value": args.state_value,
                        "replaces": args.replaces,
                    }
                result = preview_event(
                    registry,
                    config,
                    args.text,
                    target=args.target,
                    primary_context=args.primary_context,
                    context_ids=args.context_id,
                    idempotency_key=args.idempotency_key,
                    state_change=change,
                )
                if due_resolution:
                    result["due_resolution"] = due_resolution
            elif args.context_command == "commit":
                result = commit_event(registry, config, args.event_id)
            elif args.context_command == "event":
                result = inspect_event(registry, config, args.event_id)
            elif args.context_command == "register":
                path = args.definitions.expanduser().absolute()
                definitions = json.loads(
                    secure_read_bytes(path.parent, path.name).decode("utf-8-sig")
                )
                result = register_contexts(
                    registry,
                    config,
                    definitions,
                    expected_revision=args.expected_revision,
                )
            elif args.context_command == "check":
                if args.definitions:
                    raise ValueError(
                        "contexts check uses registered definitions; omit --definitions"
                    )
                result = check_contexts(registry, config, limit=args.limit)
            else:
                result = inspect_contexts(
                    config,
                    registry=registry,
                    text=getattr(args, "text", None),
                    definitions_path=args.definitions,
                    details=getattr(args, "details", False),
                    limit=args.limit,
                )
            _emit(result, json_output=args.json)
            if result.get("status") == "applied_pending_index":
                return 2
        elif args.command == "route":
            route = resolve_route(
                args.text, scan_taxonomy(config.root_path), vault_root=config.root_path
            )
            _emit(route.to_dict(), json_output=args.json)
        elif args.command == "capture":
            if args.plan_id and args.text:
                raise ValueError("Use either capture text or --plan-id, not both")
            if args.plan_id and not args.commit:
                raise ValueError("--plan-id requires --commit")
            if not args.plan_id and not args.text:
                raise ValueError("Capture text is required when --plan-id is absent")
            state_dir = registry.vault_state_dir(config)
            if args.plan_id:
                plan = load_capture_plan(state_dir, args.plan_id)
                route_data = None
            else:
                route = resolve_route(
                    args.text,
                    scan_taxonomy(config.root_path),
                    vault_root=config.root_path,
                )
                plan = create_capture_plan(
                    config.root_path,
                    state_dir,
                    args.text,
                    route,
                    title=args.title,
                    expected_sha256=args.expected_sha256,
                    idempotency_key=args.idempotency_key,
                )
                route_data = route.to_dict()
            summary = plan_summary(plan)
            if not args.commit:
                _emit(
                    {
                        "status": "preview",
                        "write_policy": config.write_policy,
                        "route": route_data,
                        "plan": summary,
                    },
                    json_output=args.json,
                    message=(
                        f"Preview only: {plan.action} -> {plan.relative_path}\n"
                        f"Plan: {plan.plan_id}\n"
                        "Commit with --commit --plan-id <id>."
                    ),
                )
            else:
                result = {"plan": summary, **commit_and_refresh(registry, config, plan)}
                _emit(
                    result,
                    json_output=args.json,
                    message=result.get("message", f"Committed {plan.action}: {plan.relative_path}"),
                )
                if result["status"] == "applied_pending_index":
                    return 2
        elif args.command == "brief":
            if args.project and not args.projects:
                raise ValueError("--project requires --projects")
            repository_evidence = (
                search_projects(
                    registry,
                    config,
                    args.topic,
                    limit=5,
                    project_id=args.project,
                )
                if args.projects
                else []
            )
            result = assemble_decision_context(
                config.root_path,
                active_index_path(registry, config),
                args.topic,
                vault_id=config.id,
                generation=config.index_generation,
                repository_evidence=repository_evidence,
            )
            if args.budget_tokens is not None:
                result = budget_brief(result, args.budget_tokens)
            _emit(result, json_output=args.json, message=result["markdown"])
        elif args.command == "projects":
            if args.project_command == "attach":
                attached = registry.attach_projects(config.id, args.path)
                result = sync_project_catalog(registry, attached)
                _emit(
                    result,
                    json_output=args.json,
                    message=(
                        f"Cataloged {result['project_count']} repositories and "
                        f"{result['document_count']} documents outside the project library."
                    ),
                )
            elif args.project_command == "sync":
                result = sync_project_catalog(registry, config)
                _emit(
                    result,
                    json_output=args.json,
                    message=(
                        f"Cataloged {result['project_count']} repositories and "
                        f"{result['document_count']} documents."
                    ),
                )
            elif args.project_command == "status":
                result = project_status(registry, config)
                _emit(result, json_output=args.json)
            else:
                result = list_catalog_projects(registry, config)
                _emit(result, json_output=args.json)
        elif args.command == "quality":
            source_scan = scan_notes(config.root_path)
            doctor_report = diagnose_vault(config.root_path, source_scan=source_scan)
            signals = vault_quality_signals(config.id, doctor_report, source_scan)
            if config.project_catalog and not args.vault_only:
                signals.extend(project_quality_signals(list_catalog_projects(registry, config)))
            result = quality_report(signals, detail_limit=args.detail_limit, details=args.details)
            version = load_context_version(registry, config)
            result["context_queue"] = context_quality_queue(
                version["definitions"]["contexts"] if version else [],
                signals,
                limit=args.detail_limit,
                source_scan=source_scan,
                doctor_report=doctor_report,
            )
            _emit(
                result,
                json_output=args.json,
                message=(
                    f"{result['status']}: {result['total_signals']} explainable signals; "
                    f"{result['counts_by_severity']}"
                ),
            )
            if result["status"] == "findings":
                return 1
        elif args.command == "doctor":
            full_report = diagnose_vault(
                config.root_path, include_daily_orphans=args.include_daily_orphans
            )
            result = (
                full_report
                if args.details
                else summarize_diagnostics(full_report, detail_limit=args.detail_limit)
            )
            _emit(
                result,
                json_output=args.json,
                message=format_diagnostics_summary(result),
            )
            if full_report["status"] == "issues_found":
                return 1
        elif args.command == "maintain":
            audit_only = args.no_sync or args.audit_only
            if args.detail_limit < 0 or args.detail_limit > 100:
                raise ValueError("--detail-limit must be between 0 and 100")
            if args.with_tick:
                if args.tick_limit < 1 or args.tick_limit > 100:
                    raise ValueError("--tick-limit must be between 1 and 100")
                if args.tick_max_contexts < 1 or args.tick_max_contexts > 1000:
                    raise ValueError("--tick-max-contexts must be between 1 and 1000")
                configs = (
                    sorted(registry.list(), key=lambda item: item.id) if args.all else [config]
                )
                cycle_run = uuid.uuid4().hex
                cycles: list[dict[str, Any]] = []
                incomplete_or_failed = False
                for item in configs:
                    cycle = run_maintenance_cycle(
                        registry,
                        registry.resolve(item.id),
                        with_sync=not audit_only,
                        detail_limit=args.detail_limit,
                        limit=args.tick_limit,
                        max_contexts=args.tick_max_contexts,
                        run_id=cycle_run,
                    )
                    cycles.append(cycle)
                    if cycle.get("status") in {"failed", "incomplete"}:
                        incomplete_or_failed = True
                result = cycles if args.all else cycles[0]
                _emit(result, json_output=args.json)
                return 2 if incomplete_or_failed else 0
            configs = sorted(registry.list(), key=lambda item: item.id) if args.all else [config]
            maintained: list[dict[str, Any]] = []
            failures = False
            run_id = uuid.uuid4().hex
            for item in configs:
                started = time.perf_counter()
                _record_maintenance(registry, item, run_id=run_id, status="started")
                try:
                    if audit_only:
                        sync_result = None
                        source_scan = scan_notes(item.root_path)
                    else:
                        sync_result, source_scan = sync_vault_with_scan(registry, item)
                    full_doctor_result = diagnose_vault(item.root_path, source_scan=source_scan)
                    doctor_result = (
                        full_doctor_result
                        if args.details
                        else summarize_diagnostics(
                            full_doctor_result, detail_limit=args.detail_limit
                        )
                    )
                    entry = {
                        "schema_version": 1,
                        "vault": item.name,
                        "vault_id": item.id,
                        "run_id": run_id,
                        "status": "completed",
                        "sync": sync_result,
                        "index_audit": audit_index(
                            item.root_path,
                            active_index_path(registry, registry.resolve(item.id)),
                            source_scan=source_scan,
                        ),
                        "doctor": doctor_result,
                    }
                    duration_ms = (time.perf_counter() - started) * 1000
                    entry["duration_ms"] = round(duration_ms, 3)
                    _record_maintenance(
                        registry,
                        item,
                        run_id=run_id,
                        status="completed",
                        result=entry,
                        duration_ms=duration_ms,
                    )
                except Exception as exc:
                    failures = True
                    duration_ms = (time.perf_counter() - started) * 1000
                    _record_maintenance(
                        registry,
                        item,
                        run_id=run_id,
                        status="failed",
                        error=f"{type(exc).__name__}: {exc}",
                        duration_ms=duration_ms,
                    )
                    entry = {
                        "schema_version": 1,
                        "vault": item.name,
                        "vault_id": item.id,
                        "run_id": run_id,
                        "status": "failed",
                        "duration_ms": round(duration_ms, 3),
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                    if not args.all:
                        raise
                maintained.append(entry)
            result = maintained if args.all else maintained[0]
            _emit(
                result,
                json_output=args.json,
                message=_maintenance_message(result),
            )
            if failures:
                return 2
        return 0
    except (
        OSError,
        KeyError,
        RuntimeError,
        ValueError,
    ) as exc:
        print(f"KnowledgeRouter error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
