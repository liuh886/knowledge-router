"""Durable maintain-then-tick cycle and explicit maintenance coverage.

OC-01: one bounded interface runs the existing maintain primitives to
completion and only then runs the existing tick poll in the same process.
Started/completed/failed cycle receipts are durable JSONL outside the Vault.
The last SUCCESSFUL validated baseline is retained across failures and
interrupted runs; incomplete or failed runs never report a false clear and
never erase that baseline.

OC-02 (coverage slice): explicit registered/enabled/disabled-unconfigured
and retired accounting from registered definitions. Missing policy means no
periodic maintenance assumption; no policy is invented here.

Existing primitives are reused (sync_vault_with_scan, diagnose_vault,
audit_index, poll_contexts, project_state, load_context_version). No parallel
sync implementation and no timeout flag that only checks elapsed time after
completion.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import UTC, datetime
from typing import Any

from context_state import project_state
from context_delivery import delivery_status
from context_store import load_context_version
from context_tick import poll_contexts
from fts_indexer import audit_index, scan_notes
from registry import FileLock, VaultConfig, VaultRegistry, atomic_json_write
from vault_doctor import diagnostic_counts, diagnose_vault, summarize_diagnostics
from vault_service import active_index_path, sync_vault_with_scan

_SCHEMA_VERSION = 1
_CYCLE_JOURNAL = "maintenance_cycle.jsonl"
_BASELINE_FILENAME = "maintenance_baseline.json"
_STAGING_FILENAME = "maintenance_baseline.staging.json"
_MAINTAIN_JOURNAL = "maintain_audit.jsonl"
_CYCLE_LOCK = ".maintenance-cycle.lock"
_MAINTAIN_LOCK = ".maintain-audit.lock"
_RUN_LOCK = ".maintenance-run.lock"
# Snapshot fields where an increase over the previous successful baseline is a
# new quality finding that must never be reported as clear.
_DOCTOR_INCREASE_FIELDS: dict[str, tuple[str, str]] = {
    "broken_link_occurrences": ("broken_links", "broken_link_increase"),
    "broken_targets": ("broken_links", "broken_target_increase"),
    "ambiguous_link_occurrences": ("ambiguous_links", "ambiguous_link_increase"),
    "ambiguous_targets": ("ambiguous_links", "ambiguous_target_increase"),
    "orphans": ("orphans", "orphan_increase"),
}


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def validate_context_id(value: Any) -> str:
    """Validate a user- or registry-provided Context id without traversal."""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Context id must be a nonempty string")
    if value != value.strip():
        raise ValueError(f"Unsafe Context id: {value!r}")
    if len(value) > 256:
        raise ValueError(f"Unsafe Context id: {value!r}")
    if "/" in value or "\\" in value:
        raise ValueError(f"Unsafe Context id: {value!r}")
    if ".." in value or value in {".", ".."}:
        raise ValueError(f"Unsafe Context id: {value!r}")
    for part in value.split(":"):
        if part in {"", ".", ".."} or "/" in part or "\\" in part:
            raise ValueError(f"Unsafe Context id: {value!r}")
    return value


def _check_limit(limit: int, *, name: str, low: int, high: int) -> None:
    if type(limit) is not int or not low <= limit <= high:
        raise ValueError(f"{name} must be an integer between {low} and {high}")


def _now_utc(now: datetime | None) -> datetime:
    value = now or datetime.now(UTC)
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("Cycle time must include a timezone")
    return value


def _append_jsonl(
    registry: VaultRegistry,
    config: VaultConfig,
    *,
    filename: str,
    lock_name: str,
    payload: dict[str, Any],
) -> None:
    state = registry.vault_state_dir(config)
    data = (json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n").encode(
        "utf-8"
    )
    with registry.guard_vault_state(config) as guard:
        with FileLock(state / lock_name, pinned_parent=guard):
            try:
                descriptor = guard.open_existing(
                    writable=True, exclusive_writes=False, name=filename
                )
            except FileNotFoundError:
                descriptor = guard.create_new(name=filename)
            try:
                os.lseek(descriptor, 0, os.SEEK_END)
                written = os.write(descriptor, data)
                if written != len(data):
                    raise OSError(f"Short audit write: {filename}")
                os.fsync(descriptor)
            finally:
                os.close(descriptor)


def _read_baseline(
    registry: VaultRegistry, config: VaultConfig
) -> dict[str, Any] | None:
    """Read the last successful baseline or fail closed on corruption."""
    with registry.guard_vault_state(config) as guard:
        try:
            descriptor = guard.open_existing(
                writable=False, name=_BASELINE_FILENAME
            )
        except FileNotFoundError:
            return None
        try:
            chunks: list[bytes] = []
            while chunk := os.read(descriptor, 1024 * 1024):
                chunks.append(chunk)
        finally:
            os.close(descriptor)
    try:
        data = json.loads(b"".join(chunks).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Corrupted maintenance baseline: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("Corrupted maintenance baseline: not an object")
    checksum = data.pop("checksum", None)
    if (
        checksum != _digest(data)
        or data.get("schema_version") != _SCHEMA_VERSION
        or data.get("vault_id") != config.id
    ):
        raise ValueError("Corrupted maintenance baseline: checksum or binding mismatch")
    snapshot = data.get("snapshot")
    fingerprint = data.get("fingerprint")
    if not isinstance(snapshot, dict) or not isinstance(fingerprint, str):
        raise ValueError("Corrupted maintenance baseline: missing snapshot")
    return {**data, "checksum": checksum}


def _build_baseline_payload(
    *,
    config: VaultConfig,
    run_id: str,
    snapshot: dict[str, Any],
    fingerprint: str,
    recorded_at: str,
) -> dict[str, Any]:
    body = {
        "schema_version": _SCHEMA_VERSION,
        "vault_id": config.id,
        "recorded_at": recorded_at,
        "run_id": run_id,
        "fingerprint": fingerprint,
        "snapshot": snapshot,
    }
    return {**body, "checksum": _digest(body)}


def _write_baseline(
    registry: VaultRegistry,
    config: VaultConfig,
    *,
    run_id: str,
    snapshot: dict[str, Any],
    fingerprint: str,
    recorded_at: str,
) -> dict[str, Any]:
    state = registry.vault_state_dir(config)
    payload = _build_baseline_payload(
        config=config,
        run_id=run_id,
        snapshot=snapshot,
        fingerprint=fingerprint,
        recorded_at=recorded_at,
    )
    with registry.guard_vault_state(config) as guard:
        with FileLock(state / _CYCLE_LOCK, pinned_parent=guard):
            atomic_json_write(state / _BASELINE_FILENAME, payload)
    return payload


def _write_staging(
    registry: VaultRegistry,
    config: VaultConfig,
    *,
    payload: dict[str, Any],
) -> None:
    """Stage a validated baseline without publishing it as the success baseline."""
    state = registry.vault_state_dir(config)
    with registry.guard_vault_state(config) as guard:
        with FileLock(state / _CYCLE_LOCK, pinned_parent=guard):
            atomic_json_write(state / _STAGING_FILENAME, payload)


def _promote_staging(
    registry: VaultRegistry,
    config: VaultConfig,
    *,
    expected_fingerprint: str,
) -> dict[str, Any]:
    """Publish the staged baseline only after completion receipts succeeded.

    The previously successful baseline is left untouched until this promotion.
    The staging file remains on failure so an interrupted commit stays
    recoverable by inspection instead of silently becoming the success baseline.
    """
    state = registry.vault_state_dir(config)
    with registry.guard_vault_state(config) as guard:
        with FileLock(state / _CYCLE_LOCK, pinned_parent=guard):
            staging_path = state / _STAGING_FILENAME
            try:
                raw = staging_path.read_text(encoding="utf-8")
            except OSError as exc:
                raise OSError(f"Missing staged maintenance baseline: {exc}") from exc
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Corrupted staged maintenance baseline: {exc}") from exc
            if not isinstance(payload, dict):
                raise ValueError("Corrupted staged maintenance baseline: not an object")
            checksum = payload.get("checksum")
            body = {k: v for k, v in payload.items() if k != "checksum"}
            if (
                checksum != _digest(body)
                or payload.get("schema_version") != _SCHEMA_VERSION
                or payload.get("vault_id") != config.id
                or payload.get("fingerprint") != expected_fingerprint
            ):
                raise ValueError(
                    "Corrupted staged maintenance baseline: checksum or binding mismatch"
                )
            atomic_json_write(state / _BASELINE_FILENAME, payload)
            try:
                staging_path.unlink()
            except OSError:
                pass
            return payload


def _quality_new_findings(
    previous_snapshot: dict[str, Any] | None,
    current_snapshot: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compare doctor totals against the last successful baseline.

    A first run establishes the baseline and reports no new findings so routine
    historical totals do not alert. Any later increase is returned with its
    category, reason and delta; totals themselves stay in the snapshot.
    """
    if previous_snapshot is None:
        return []
    findings: list[dict[str, Any]] = []
    for field, (category, reason) in _DOCTOR_INCREASE_FIELDS.items():
        try:
            before = int(previous_snapshot.get(field, 0))
            after = int(current_snapshot.get(field, 0))
        except (TypeError, ValueError):
            continue
        if after > before:
            findings.append(
                {
                    "category": category,
                    "reason": reason,
                    "field": field,
                    "previous": before,
                    "current": after,
                    "delta": after - before,
                }
            )
    findings.sort(key=lambda item: str(item["field"]))
    return findings


def maintenance_coverage(
    registry: VaultRegistry,
    config: VaultConfig,
    *,
    limit: int = 20,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Report registered/enabled/disabled-unconfigured/retired Contexts.

    No maintenance policy is invented: a missing ``maintenance`` block or an
    explicit ``enabled: false`` both count as disabled/unconfigured, never as
    active coverage. Retired lifecycle state is resolved through the same
    source-verified projection used by the review queue.
    """
    _check_limit(limit, name="Coverage limit", low=1, high=100)
    moment = _now_utc(now)
    version = load_context_version(registry, config)
    if version is None:
        return {
            "schema_version": _SCHEMA_VERSION,
            "vault_id": config.id,
            "status": "unregistered",
            "definition_revision": 0,
            "registered_context_count": 0,
            "enabled_context_count": 0,
            "disabled_context_count": 0,
            "retired_context_count": 0,
            "item_count": 0,
            "items": [],
            "errors": [],
            "error_count": 0,
            "scan_incomplete": False,
            "output_truncated": False,
            "checked_at": moment.isoformat(),
            "writes_performed": 0,
            "notifications_sent": 0,
            "meaning": "No registered Context definitions; missing policy is not coverage",
        }
    items: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []
    for context in version["definitions"]["contexts"]:
        try:
            identity = validate_context_id(context.get("id"))
            policy = context.get("maintenance")
            has_policy = isinstance(policy, dict)
            enabled_setting = bool(has_policy and policy.get("enabled", True))
            try:
                state = project_state(registry, config, identity)
                fields = {item["field"]: item for item in state["fields"]}
                lifecycle = fields.get("lifecycle", {})
                retired = (
                    lifecycle.get("status") == "current"
                    and lifecycle.get("value") == "retired"
                )
                state_status = state["status"]
                state_truncated = bool(state["truncated"])
            except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
                errors.append(
                    {"stage": "state", "context_id": identity, "error": str(exc)}
                )
                retired = False
                state_status = "error"
                state_truncated = True
            if retired:
                category = "retired"
            elif enabled_setting:
                category = "enabled"
            else:
                category = "disabled_or_unconfigured"
            reason = (
                "lifecycle_retired"
                if retired
                else "policy_enabled"
                if enabled_setting
                else "policy_disabled"
                if has_policy
                else "missing_policy"
            )
            items.append(
                {
                    "context_id": identity,
                    "type": context.get("type"),
                    "name": context.get("name"),
                    "scopes": list(context.get("scopes", [])),
                    "maintenance": policy if has_policy else None,
                    "enabled": enabled_setting and not retired,
                    "retired": retired,
                    "category": category,
                    "reason": reason,
                    "state_status": state_status,
                    "state_truncated": state_truncated,
                }
            )
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            errors.append({"stage": "definition", "error": str(exc)})
    items.sort(key=lambda item: str(item["context_id"]))
    enabled = sum(1 for item in items if item["category"] == "enabled")
    disabled = sum(
        1 for item in items if item["category"] == "disabled_or_unconfigured"
    )
    retired_count = sum(1 for item in items if item["category"] == "retired")
    # A returned project_state(truncated=true) proves the per-Context scan did
    # not fit the inspection bound even when no exception was raised.
    state_truncated_any = any(bool(item.get("state_truncated")) for item in items)
    incomplete = bool(errors) or state_truncated_any
    return {
        "schema_version": _SCHEMA_VERSION,
        "vault_id": config.id,
        "status": "incomplete" if incomplete else "current",
        "definition_revision": version["revision"],
        "registered_context_count": len(items),
        "enabled_context_count": enabled,
        "disabled_context_count": disabled,
        "retired_context_count": retired_count,
        "retired_contexts": sorted(
            item["context_id"] for item in items if item["retired"]
        )[:limit],
        "item_count": len(items),
        "items": items[:limit],
        "errors": errors[:limit],
        "error_count": len(errors),
        "scan_incomplete": incomplete,
        "output_truncated": len(items) > limit or len(errors) > limit,
        "checked_at": moment.isoformat(),
        "writes_performed": 0,
        "notifications_sent": 0,
        "meaning": "Explicit coverage only; missing policy is not active maintenance",
    }


def _read_jsonl_records(
    registry: VaultRegistry, config: VaultConfig, filename: str
) -> tuple[list[dict[str, Any]], list[str]]:
    """Read Router-owned receipts without treating malformed lines as success."""
    with registry.guard_vault_state(config) as guard:
        try:
            descriptor = guard.open_existing(writable=False, name=filename)
        except FileNotFoundError:
            return [], []
        try:
            chunks: list[bytes] = []
            while chunk := os.read(descriptor, 1024 * 1024):
                chunks.append(chunk)
        finally:
            os.close(descriptor)
    records: list[dict[str, Any]] = []
    errors: list[str] = []
    for line_number, line in enumerate(b"".join(chunks).splitlines(), 1):
        if not line.strip():
            continue
        try:
            value = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            errors.append(f"{filename}:{line_number}: {exc}")
            continue
        if not isinstance(value, dict):
            errors.append(f"{filename}:{line_number}: receipt is not an object")
            continue
        records.append(value)
    return records, errors


def maintenance_status(
    registry: VaultRegistry,
    config: VaultConfig,
    *,
    limit: int = 20,
) -> dict[str, Any]:
    """Return one factual, bounded view of receipts, baseline and delivery boundary."""
    _check_limit(limit, name="Status limit", low=1, high=100)
    cycle, cycle_errors = _read_jsonl_records(registry, config, _CYCLE_JOURNAL)
    audit, audit_errors = _read_jsonl_records(registry, config, _MAINTAIN_JOURNAL)
    errors = cycle_errors + audit_errors
    try:
        baseline = _read_baseline(registry, config)
    except (OSError, ValueError, RuntimeError) as exc:
        baseline = None
        errors.append(f"baseline: {exc}")
    last_cycle = cycle[-1] if cycle else None
    last_audit = audit[-1] if audit else None
    successful_run_id = str(baseline.get("run_id", "")) if baseline else ""
    successful = next(
        (item for item in reversed(cycle) if item.get("run_id") == successful_run_id),
        None,
    )
    failures = [item for item in cycle if item.get("status") == "failed"]
    try:
        delivery = delivery_status(registry, config, limit=limit)
    except (OSError, ValueError, RuntimeError, TypeError, KeyError) as exc:
        delivery = {
            "configured": False,
            "pending": 0,
            "last_acknowledged": None,
            "meaning": "Delivery ledger unavailable; no notification acknowledgement is assumed",
            "errors": [f"delivery: {exc}"],
        }
        errors.append(f"delivery: {exc}")
    return {
        "schema_version": _SCHEMA_VERSION,
        "vault_id": config.id,
        "status": "incomplete" if errors else "current",
        "last_cycle": last_cycle,
        "last_maintenance": last_audit,
        "last_successful_cycle": successful,
        "successful_baseline": (
            {
                "run_id": baseline.get("run_id"),
                "recorded_at": baseline.get("recorded_at"),
                "fingerprint": baseline.get("fingerprint"),
            }
            if baseline
            else None
        ),
        "failed_runs": failures[-limit:],
        "failed_run_count": len(failures),
        "errors": errors[:limit],
        "error_count": len(errors),
        "output_truncated": len(errors) > limit or len(failures) > limit,
        "delivery": delivery,
        "next_action": (
            "Review failed or incomplete receipts before relying on a clear result"
            if errors or failures or (last_cycle and last_cycle.get("status") != "completed")
            else "Run maintain --with-tick when a fresh Context and Vault check is needed"
        ),
    }


def _content_snapshot(
    *,
    sync_result: dict[str, Any] | None,
    index_audit: dict[str, Any],
    doctor_counts: dict[str, int],
    notes_scanned: int,
    tick: dict[str, Any],
    coverage: dict[str, Any],
) -> dict[str, Any]:
    """Content-based snapshot excluding timing, run ids and wall-clock time."""
    return {
        "index_generation": (sync_result or {}).get("generation", ""),
        "indexed_notes": int((sync_result or {}).get("indexed_notes", notes_scanned)),
        "notes_scanned": int(notes_scanned),
        "index_audit_status": str(index_audit.get("status", "")),
        "broken_link_occurrences": int(doctor_counts["broken_link_occurrences"]),
        "broken_targets": int(doctor_counts["broken_targets"]),
        "ambiguous_link_occurrences": int(
            doctor_counts["ambiguous_link_occurrences"]
        ),
        "ambiguous_targets": int(doctor_counts["ambiguous_targets"]),
        "orphans": int(doctor_counts["orphans"]),
        "skipped_decode_count": len(index_audit.get("skipped_decode", [])),
        "skipped_io_count": len(index_audit.get("skipped_io", [])),
        "tick_status": str(tick.get("status", "")),
        "tick_fingerprint": str(tick.get("fingerprint", "")),
        "tick_context_count": int(tick.get("context_count", 0)),
        "tick_contexts_checked": int(tick.get("contexts_checked", 0)),
        "tick_item_count": int(tick.get("item_count", 0)),
        "tick_error_count": int(tick.get("error_count", 0)),
        "coverage_status": str(coverage.get("status", "")),
        "coverage_revision": int(coverage.get("definition_revision", 0)),
        "coverage_registered": int(coverage.get("registered_context_count", 0)),
        "coverage_enabled": int(coverage.get("enabled_context_count", 0)),
        "coverage_disabled": int(coverage.get("disabled_context_count", 0)),
        "coverage_retired": int(coverage.get("retired_context_count", 0)),
    }


def _delta(
    previous: dict[str, Any] | None, current: dict[str, Any]
) -> dict[str, Any]:
    if previous is None:
        return {
            "first_run": True,
            "changed": True,
            "changed_fields": sorted(current),
            "fields": {
                key: {"previous": None, "current": value, "changed": True}
                for key, value in sorted(current.items())
            },
        }
    fields: dict[str, Any] = {}
    changed_fields: list[str] = []
    for key in sorted(set(previous) | set(current)):
        before = previous.get(key)
        after = current.get(key)
        changed = before != after
        if changed:
            changed_fields.append(key)
        fields[key] = {"previous": before, "current": after, "changed": changed}
    return {
        "first_run": False,
        "changed": bool(changed_fields),
        "changed_fields": changed_fields,
        "fields": fields,
    }


def _run_maintenance_cycle(
    registry: VaultRegistry,
    config: VaultConfig,
    *,
    with_sync: bool = True,
    detail_limit: int = 10,
    limit: int = 20,
    max_contexts: int = 100,
    now: datetime | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Run maintain primitives to completion, then the tick poll, sequentially.

    The same ``run_id`` links the maintain audit events, the tick phase and the
    durable cycle receipt so a late completion cannot be confused with a newer
    run. Only a validated success refreshes the durable baseline.
    """
    _check_limit(detail_limit, name="Detail limit", low=0, high=100)
    _check_limit(limit, name="Tick limit", low=1, high=100)
    _check_limit(max_contexts, name="Max Contexts", low=1, high=1000)
    moment = _now_utc(now)
    cycle_run = run_id or uuid.uuid4().hex
    started_at = time.perf_counter()
    _append_jsonl(
        registry,
        config,
        filename=_CYCLE_JOURNAL,
        lock_name=_CYCLE_LOCK,
        payload={
            "schema_version": _SCHEMA_VERSION,
            "run_id": cycle_run,
            "recorded_at": moment.isoformat(),
            "vault_id": config.id,
            "status": "started",
        },
    )
    # Maintain audit started event shares the cycle run identity.
    _append_jsonl(
        registry,
        config,
        filename=_MAINTAIN_JOURNAL,
        lock_name=_MAINTAIN_LOCK,
        payload={
            "schema_version": _SCHEMA_VERSION,
            "run_id": cycle_run,
            "recorded_at": moment.isoformat(),
            "vault_id": config.id,
            "status": "started",
        },
    )
    try:
        if with_sync:
            sync_result, source_scan = sync_vault_with_scan(registry, config)
        else:
            sync_result = None
            source_scan = scan_notes(config.root_path)
        full_doctor = diagnose_vault(config.root_path, source_scan=source_scan)
        counts = diagnostic_counts(full_doctor)
        doctor_view = (
            full_doctor
            if detail_limit >= 100
            and len(full_doctor.get("broken_targets", [])) <= detail_limit
            and len(full_doctor.get("ambiguous_targets", [])) <= detail_limit
            and len(full_doctor.get("orphans", [])) <= detail_limit
            else summarize_diagnostics(full_doctor, detail_limit=detail_limit)
        )
        refreshed = registry.resolve(config.id)
        index_audit = audit_index(
            config.root_path,
            active_index_path(registry, refreshed),
            source_scan=source_scan,
        )
        maintain_entry: dict[str, Any] = {
            "schema_version": _SCHEMA_VERSION,
            "vault": config.name,
            "vault_id": config.id,
            "run_id": cycle_run,
            "status": "completed",
            "sync": sync_result,
            "index_audit": index_audit,
            "doctor": doctor_view,
            # Complete counts are always preserved separately so bounded
            # samples cannot be mistaken for totals.
            "doctor_counts": counts,
            "notes_scanned": full_doctor["notes_scanned"],
        }
        # Tick runs only after maintain primitives completed in this process.
        tick = poll_contexts(
            registry, refreshed, limit=limit, max_contexts=max_contexts, now=moment
        )
        coverage = maintenance_coverage(registry, refreshed, limit=limit, now=moment)
        skipped = bool(source_scan.skipped_decode or source_scan.skipped_io)
        # Audit-only mode still needs a current immutable index. Skipping the
        # rebuild is allowed; skipping freshness validation is not.
        audit_ok = index_audit.get("status") == "current"
        tick_complete = not tick.get("scan_incomplete", True) and (
            tick.get("error_count", 1) == 0
        )
        coverage_complete = coverage.get("status") != "incomplete" and not coverage.get(
            "scan_incomplete", False
        )
        validated = (
            not skipped and audit_ok and tick_complete and coverage_complete
        )
        snapshot = _content_snapshot(
            sync_result=sync_result,
            index_audit=index_audit,
            doctor_counts=counts,
            notes_scanned=full_doctor["notes_scanned"],
            tick=tick,
            coverage=coverage,
        )
        fingerprint = _digest(snapshot)
        try:
            previous = _read_baseline(registry, refreshed)
        except ValueError as exc:
            # Corrupted baseline fails closed: never clear, never overwrite.
            duration_ms = (time.perf_counter() - started_at) * 1000
            failed = {
                "schema_version": _SCHEMA_VERSION,
                "vault_id": config.id,
                "run_id": cycle_run,
                "status": "failed",
                "validated": False,
                "duration_ms": round(duration_ms, 3),
                "error": f"ValueError: {exc}",
                "maintain": maintain_entry,
                "tick": tick,
                "coverage": coverage,
                "baseline": None,
                "baseline_error": str(exc),
                "changed_since_baseline": False,
                "meaning": "Baseline is corrupted; resolve it before trusting a clear result",
            }
            _append_jsonl(
                registry,
                config,
                filename=_CYCLE_JOURNAL,
                lock_name=_CYCLE_LOCK,
                payload={
                    "schema_version": _SCHEMA_VERSION,
                    "run_id": cycle_run,
                    "recorded_at": datetime.now(UTC).isoformat(),
                    "vault_id": config.id,
                    "status": "failed",
                    "error": failed["error"],
                },
            )
            return failed
        deltas = _delta(
            previous["snapshot"] if previous else None, snapshot
        )
        baseline_changed = (
            previous is None or previous.get("fingerprint") != fingerprint
        )
        quality_new_findings = _quality_new_findings(
            previous["snapshot"] if previous else None, snapshot
        )
        quality_regressed = bool(quality_new_findings)
        if not validated:
            status = "incomplete"
        else:
            # Tick already distinguishes clear/attention; never upgrade an
            # incomplete poll to clear here. New doctor findings (for example
            # an orphan increase over the last successful baseline) must never
            # be reported as clear even when the tick poll itself is clear.
            # A first run establishes the baseline and keeps tick semantics so
            # historical totals do not alert without a prior comparison.
            status = str(tick.get("status", "incomplete"))
            if status not in {"clear", "attention"}:
                status = "incomplete"
            if quality_regressed and status == "clear":
                status = "attention"
        # Commit ordering: stage the validated baseline, publish all required
        # completion receipts, then promote the staging to the success
        # baseline. A receipt failure therefore leaves the previous successful
        # baseline unchanged with recoverable staging instead of promoting a
        # run whose completion was never durably recorded.
        staged_payload: dict[str, Any] | None = None
        if validated:
            staged_payload = _build_baseline_payload(
                config=refreshed,
                run_id=cycle_run,
                snapshot=snapshot,
                fingerprint=fingerprint,
                recorded_at=moment.isoformat(),
            )
            _write_staging(registry, refreshed, payload=staged_payload)
        baseline: dict[str, Any] | None = previous
        duration_ms = (time.perf_counter() - started_at) * 1000
        pending_baseline = staged_payload if staged_payload is not None else baseline
        result: dict[str, Any] = {
            "schema_version": _SCHEMA_VERSION,
            "vault_id": config.id,
            "vault": config.name,
            "run_id": cycle_run,
            "status": status,
            "validated": validated,
            "duration_ms": round(duration_ms, 3),
            "maintain": maintain_entry,
            "tick": tick,
            "coverage": coverage,
            "snapshot": snapshot,
            "fingerprint": fingerprint,
            "delta": deltas,
            "changed_since_baseline": baseline_changed,
            "quality": {
                "regressed": quality_regressed,
                "new_findings": quality_new_findings,
                "finding_count": len(quality_new_findings),
                "first_run": previous is None,
            },
            "baseline": (
                {
                    "fingerprint": pending_baseline.get("fingerprint"),
                    "recorded_at": pending_baseline.get("recorded_at"),
                    "run_id": pending_baseline.get("run_id"),
                    "updated_this_run": validated,
                }
                if pending_baseline
                else {
                    "fingerprint": None,
                    "recorded_at": None,
                    "run_id": None,
                    "updated_this_run": validated,
                }
            ),
            # Complete counts vs truncated samples stay explicit for monitors
            # so a bounded sample cannot trigger a duplicate false alert.
            "counts": {
                "tick_item_count": tick.get("item_count", 0),
                "tick_error_count": tick.get("error_count", 0),
                "tick_context_count": tick.get("context_count", 0),
                "tick_output_truncated": tick.get("output_truncated", False),
                "tick_scan_incomplete": tick.get("scan_incomplete", False),
                "coverage_item_count": coverage.get("item_count", 0),
                "coverage_output_truncated": coverage.get("output_truncated", False),
            },
            "monitor": {
                "deduplication_key": fingerprint,
                "alertable": status in {"attention", "incomplete", "failed"}
                and baseline_changed,
            },
            "meaning": "Combined maintain-then-tick result; compare fingerprints only after a complete scan",
        }
        _append_jsonl(
            registry,
            config,
            filename=_MAINTAIN_JOURNAL,
            lock_name=_MAINTAIN_LOCK,
            payload={
                "schema_version": _SCHEMA_VERSION,
                "run_id": cycle_run,
                "recorded_at": datetime.now(UTC).isoformat(),
                "vault_id": config.id,
                "status": "completed",
                "duration_ms": round(duration_ms, 3),
                "index_generation": (
                    (sync_result or {}).get(
                        "generation", config.index_generation
                    )
                ),
                "doctor_status": full_doctor["status"],
                "notes_scanned": full_doctor["notes_scanned"],
                "broken_links": counts["broken_link_occurrences"],
                "broken_targets": counts["broken_targets"],
                "ambiguous_links": counts["ambiguous_link_occurrences"],
                "ambiguous_targets": counts["ambiguous_targets"],
                "orphans": counts["orphans"],
            },
        )
        _append_jsonl(
            registry,
            config,
            filename=_CYCLE_JOURNAL,
            lock_name=_CYCLE_LOCK,
            payload={
                "schema_version": _SCHEMA_VERSION,
                "run_id": cycle_run,
                "recorded_at": datetime.now(UTC).isoformat(),
                "vault_id": config.id,
                "status": "completed" if validated else "incomplete",
                "cycle_status": status,
                "fingerprint": fingerprint,
                "changed_since_baseline": baseline_changed,
                "duration_ms": round(duration_ms, 3),
            },
        )
        if staged_payload is not None:
            # Publish the new success baseline only after both completion
            # receipts are durable. Staging was written before the receipts;
            # promotion happens strictly after them.
            baseline = _promote_staging(
                registry, refreshed, expected_fingerprint=fingerprint
            )
            result["baseline"] = {
                "fingerprint": baseline.get("fingerprint"),
                "recorded_at": baseline.get("recorded_at"),
                "run_id": baseline.get("run_id"),
                "updated_this_run": True,
            }
        return result
    except Exception as exc:
        duration_ms = (time.perf_counter() - started_at) * 1000
        error = f"{type(exc).__name__}: {exc}"
        try:
            previous = _read_baseline(registry, config)
        except ValueError:
            previous = None
        try:
            _append_jsonl(
                registry,
                config,
                filename=_MAINTAIN_JOURNAL,
                lock_name=_MAINTAIN_LOCK,
                payload={
                    "schema_version": _SCHEMA_VERSION,
                    "run_id": cycle_run,
                    "recorded_at": datetime.now(UTC).isoformat(),
                    "vault_id": config.id,
                    "status": "failed",
                    "error": error,
                    "duration_ms": round(duration_ms, 3),
                },
            )
        except (OSError, ValueError, RuntimeError):
            pass
        try:
            _append_jsonl(
                registry,
                config,
                filename=_CYCLE_JOURNAL,
                lock_name=_CYCLE_LOCK,
                payload={
                    "schema_version": _SCHEMA_VERSION,
                    "run_id": cycle_run,
                    "recorded_at": datetime.now(UTC).isoformat(),
                    "vault_id": config.id,
                    "status": "failed",
                    "error": error,
                    "duration_ms": round(duration_ms, 3),
                },
            )
        except (OSError, ValueError, RuntimeError):
            pass
        # Failed runs retain the previous successful baseline and never clear.
        return {
            "schema_version": _SCHEMA_VERSION,
            "vault_id": config.id,
            "vault": config.name,
            "run_id": cycle_run,
            "status": "failed",
            "validated": False,
            "duration_ms": round(duration_ms, 3),
            "error": error,
            "baseline": (
                {
                    "fingerprint": previous.get("fingerprint"),
                    "recorded_at": previous.get("recorded_at"),
                    "run_id": previous.get("run_id"),
                    "updated_this_run": False,
                }
                if previous
                else None
            ),
            "changed_since_baseline": False,
            "meaning": "Cycle failed before validation; previous successful baseline is retained",
        }


def run_maintenance_cycle(
    registry: VaultRegistry,
    config: VaultConfig,
    *,
    with_sync: bool = True,
    detail_limit: int = 10,
    limit: int = 20,
    max_contexts: int = 100,
    now: datetime | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Serialize one complete maintain-then-tick cycle per Vault.

    Receipt locks protect individual append operations. This separate lock
    protects the whole read/diagnose/tick/stage/promote transaction so two
    callers cannot race their successful baselines or interleave receipts.
    """
    state = registry.vault_state_dir(config)
    with registry.guard_vault_state(config) as guard:
        with FileLock(state / _RUN_LOCK, pinned_parent=guard):
            return _run_maintenance_cycle(
                registry,
                config,
                with_sync=with_sync,
                detail_limit=detail_limit,
                limit=limit,
                max_contexts=max_contexts,
                now=now,
                run_id=run_id,
            )
