"""OC-01 durable maintain-then-tick cycle plus OC-02 coverage slice.

Success paths use real primitives (sync, doctor, audit, tick, coverage) in
temporary Vaults; only the failure injection uses a patched sync. No success
is hardcoded through helpers.
"""

# ruff: noqa: E402
import json
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from context_events import commit_event, preview_event
from context_store import register_contexts
from maintenance_cycle import (
    maintenance_coverage,
    maintenance_status,
    run_maintenance_cycle,
)
from registry import VaultRegistry


def make_vault(tmp_path, name="test"):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "note.md").write_text("Original", encoding="utf-8")
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name=name, write_policy="append-create")
    return registry, config


def register_policy(registry, config, contexts):
    register_contexts(
        registry, config, {"schema_version": 1, "contexts": contexts}
    )


def test_cycle_unchanged_success_stable_fingerprint(tmp_path):
    registry, config = make_vault(tmp_path)
    register_policy(
        registry,
        config,
        [
            {
                "id": "project:alpha",
                "type": "project",
                "name": "Alpha",
                "purpose": "test",
                "scopes": ["."],
                "maintenance": {"review_interval_days": 30},
            }
        ],
    )
    first = run_maintenance_cycle(registry, config)
    assert first["status"] in {"clear", "attention"}
    assert first["validated"] is True
    assert first["baseline"]["updated_this_run"] is True
    second = run_maintenance_cycle(registry, config)
    assert second["status"] == first["status"]
    assert second["fingerprint"] == first["fingerprint"]
    assert second["changed_since_baseline"] is False
    assert second["delta"]["changed"] is False
    # Receipts are durable outside the Vault.
    state = registry.vault_state_dir(config)
    assert (state / "maintenance_cycle.jsonl").is_file()
    assert (state / "maintenance_baseline.json").is_file()
    assert (state / "maintain_audit.jsonl").is_file()


def test_maintenance_status_exposes_receipts_baseline_and_delivery_boundary(tmp_path):
    registry, config = make_vault(tmp_path)
    register_policy(
        registry,
        config,
        [
            {
                "id": "project:alpha",
                "type": "project",
                "name": "Alpha",
                "purpose": "test",
                "scopes": ["."],
            }
        ],
    )
    run = run_maintenance_cycle(registry, config)
    status = maintenance_status(registry, registry.resolve(config.id), limit=5)

    assert status["status"] == "current"
    assert status["last_cycle"]["run_id"] == run["run_id"]
    assert status["successful_baseline"]["run_id"] == run["run_id"]
    assert status["failed_run_count"] == 0
    assert status["delivery"] == {
        "configured": False,
        "pending": 0,
        "last_acknowledged": None,
        "meaning": "Router emits poll results; notification delivery is not configured here",
    }
    cli = Path(__file__).resolve().parent.parent / "scripts" / "knowledge_router.py"
    completed = subprocess.run(
        [
            sys.executable,
            str(cli),
            "--state-root",
            str(registry.state_root),
            "status",
            "--vault",
            config.id,
            "--json",
        ],
        capture_output=True,
        encoding="utf-8",
        check=True,
        timeout=30,
    )
    payload = json.loads(completed.stdout)
    assert payload["runtime"]["loaded"] is True
    assert payload["runtime"]["successful_baseline"]["run_id"] == run["run_id"]
    assert payload["maintenance_coverage"]["status"] == "current"


def test_cycle_altered_counts_reports_deltas(tmp_path):
    registry, config = make_vault(tmp_path)
    register_policy(
        registry,
        config,
        [
            {
                "id": "project:alpha",
                "type": "project",
                "name": "Alpha",
                "purpose": "test",
                "scopes": ["."],
                "maintenance": {"review_interval_days": 30},
            }
        ],
    )
    first = run_maintenance_cycle(registry, config)
    (config.root_path / "extra.md").write_text("New note", encoding="utf-8")
    second = run_maintenance_cycle(registry, config)
    assert second["validated"] is True
    assert second["changed_since_baseline"] is True
    assert second["delta"]["changed"] is True
    assert second["fingerprint"] != first["fingerprint"]
    assert second["snapshot"]["notes_scanned"] == first["snapshot"]["notes_scanned"] + 1


def test_cycle_incomplete_scan_never_clear_and_baseline_retained(tmp_path):
    registry, config = make_vault(tmp_path)
    register_policy(
        registry,
        config,
        [
            {
                "id": f"project:{name}",
                "type": "project",
                "name": name,
                "purpose": "test",
                "scopes": ["."],
            }
            for name in ("alpha", "beta")
        ],
    )
    first = run_maintenance_cycle(registry, config)
    assert first["validated"] is True
    baseline_fingerprint = first["fingerprint"]
    incomplete = run_maintenance_cycle(registry, config, max_contexts=1)
    assert incomplete["status"] == "incomplete"
    assert incomplete["validated"] is False
    assert incomplete["tick"]["scan_incomplete"] is True
    # Incomplete run must not erase the last successful baseline.
    second_baseline = json.loads(
        (registry.vault_state_dir(config) / "maintenance_baseline.json").read_text(
            encoding="utf-8"
        )
    )
    assert second_baseline["fingerprint"] == baseline_fingerprint


def test_cycle_failure_retains_successful_baseline(tmp_path, monkeypatch):
    registry, config = make_vault(tmp_path)
    register_policy(
        registry,
        config,
        [
            {
                "id": "project:alpha",
                "type": "project",
                "name": "Alpha",
                "purpose": "test",
                "scopes": ["."],
            }
        ],
    )
    first = run_maintenance_cycle(registry, config)
    assert first["validated"] is True

    import maintenance_cycle as cycle_module

    def _boom(*args, **kwargs):
        raise RuntimeError("simulated sync failure")

    monkeypatch.setattr(cycle_module, "sync_vault_with_scan", _boom)
    failed = run_maintenance_cycle(registry, config)
    assert failed["status"] == "failed"
    assert failed["validated"] is False
    assert "simulated sync failure" in failed["error"]
    baseline = json.loads(
        (registry.vault_state_dir(config) / "maintenance_baseline.json").read_text(
            encoding="utf-8"
        )
    )
    assert baseline["fingerprint"] == first["fingerprint"]
    assert baseline["run_id"] == first["run_id"]


def test_coverage_missing_policy_disabled_and_retired(tmp_path):
    registry, config = make_vault(tmp_path)
    register_policy(
        registry,
        config,
        [
            {
                "id": "project:enabled",
                "type": "project",
                "name": "Enabled",
                "purpose": "test",
                "scopes": ["."],
                "maintenance": {"review_interval_days": 30},
            },
            {
                "id": "project:missing",
                "type": "project",
                "name": "Missing",
                "purpose": "test",
                "scopes": ["."],
            },
            {
                "id": "project:off",
                "type": "project",
                "name": "Off",
                "purpose": "test",
                "scopes": ["."],
                "maintenance": {"enabled": False},
            },
            {
                "id": "project:retiring",
                "type": "project",
                "name": "Retiring",
                "purpose": "test",
                "scopes": ["."],
                "maintenance": {"review_interval_days": 30},
            },
        ],
    )
    event = preview_event(
        registry,
        config,
        "Retire",
        target="note.md",
        primary_context="project:retiring",
        context_ids=[],
        idempotency_key="retire-coverage",
        state_change={
            "context_id": "project:retiring",
            "field": "lifecycle",
            "value": "retired",
        },
    )
    commit_event(registry, config, event["event_id"])
    coverage = maintenance_coverage(registry, config)
    assert coverage["status"] == "current"
    assert coverage["registered_context_count"] == 4
    assert coverage["enabled_context_count"] == 1
    assert coverage["disabled_context_count"] == 2
    assert coverage["retired_context_count"] == 1
    by_id = {item["context_id"]: item for item in coverage["items"]}
    assert by_id["project:missing"]["reason"] == "missing_policy"
    assert by_id["project:off"]["reason"] == "policy_disabled"
    assert by_id["project:retiring"]["category"] == "retired"


def test_coverage_unregistered_vault(tmp_path):
    registry, config = make_vault(tmp_path)
    coverage = maintenance_coverage(registry, config)
    assert coverage["status"] == "unregistered"
    assert coverage["registered_context_count"] == 0


def test_corrupted_baseline_fails_closed(tmp_path):
    registry, config = make_vault(tmp_path)
    register_policy(
        registry,
        config,
        [
            {
                "id": "project:alpha",
                "type": "project",
                "name": "Alpha",
                "purpose": "test",
                "scopes": ["."],
            }
        ],
    )
    first = run_maintenance_cycle(registry, config)
    assert first["validated"] is True
    baseline_path = registry.vault_state_dir(config) / "maintenance_baseline.json"
    baseline_path.write_text('{"schema_version": 1, "corrupted": true}', encoding="utf-8")
    result = run_maintenance_cycle(registry, config)
    assert result["status"] == "failed"
    assert result["validated"] is False
    with pytest.raises(ValueError):
        from maintenance_cycle import _read_baseline

        _read_baseline(registry, config)


def test_cycle_serializes_same_vault_callers(tmp_path, monkeypatch):
    """Two callers for one Vault never execute the cycle body concurrently."""
    registry, config = make_vault(tmp_path)
    import maintenance_cycle as cycle_module

    original = cycle_module._run_maintenance_cycle
    active = 0
    peak = 0
    state_lock = threading.Lock()

    def wrapped(*args, **kwargs):
        nonlocal active, peak
        with state_lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        try:
            return original(*args, **kwargs)
        finally:
            with state_lock:
                active -= 1

    monkeypatch.setattr(cycle_module, "_run_maintenance_cycle", wrapped)
    results = []

    def invoke():
        results.append(run_maintenance_cycle(registry, config))

    first = threading.Thread(target=invoke)
    second = threading.Thread(target=invoke)
    first.start()
    time.sleep(0.01)
    second.start()
    first.join()
    second.join()
    assert peak == 1
    assert len(results) == 2
    assert all(item["validated"] is True for item in results)


def test_cycle_no_sync_stale_index_is_incomplete(tmp_path):
    """Audit-only mode cannot establish a baseline from a stale index."""
    registry, config = make_vault(tmp_path)
    first = run_maintenance_cycle(registry, config)
    assert first["validated"] is True
    baseline_path = registry.vault_state_dir(config) / "maintenance_baseline.json"
    before = baseline_path.read_bytes()
    (config.root_path / "note.md").write_text("Changed", encoding="utf-8")
    result = run_maintenance_cycle(registry, config, with_sync=False)
    assert result["status"] == "incomplete"
    assert result["validated"] is False
    assert result["maintain"]["index_audit"]["status"] == "stale"
    assert baseline_path.read_bytes() == before


def test_cycle_completion_receipt_failure_retains_baseline(tmp_path, monkeypatch):
    """A validated run whose completion receipt fails must not publish."""
    registry, config = make_vault(tmp_path)
    register_policy(
        registry,
        config,
        [
            {
                "id": "project:alpha",
                "type": "project",
                "name": "Alpha",
                "purpose": "test",
                "scopes": ["."],
                "maintenance": {"review_interval_days": 30},
            }
        ],
    )
    first = run_maintenance_cycle(registry, config)
    assert first["validated"] is True
    state = registry.vault_state_dir(config)
    baseline_path = state / "maintenance_baseline.json"
    before_bytes = baseline_path.read_bytes()
    before = json.loads(before_bytes.decode("utf-8"))

    import maintenance_cycle as cycle_module

    original_append = cycle_module._append_jsonl

    def _fail_completion(registry_arg, config_arg, *, filename, lock_name, payload):
        if filename == cycle_module._CYCLE_JOURNAL and payload.get("status") in {
            "completed",
            "incomplete",
        }:
            raise OSError("simulated completion receipt failure")
        return original_append(
            registry_arg,
            config_arg,
            filename=filename,
            lock_name=lock_name,
            payload=payload,
        )

    monkeypatch.setattr(cycle_module, "_append_jsonl", _fail_completion)
    failed = run_maintenance_cycle(registry, config)
    assert failed["status"] == "failed"
    assert failed["validated"] is False
    assert "simulated completion receipt failure" in failed["error"]
    # The previously successful baseline must remain byte-identical.
    assert baseline_path.read_bytes() == before_bytes
    retained = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert retained["fingerprint"] == first["fingerprint"]
    assert retained["run_id"] == first["run_id"]
    assert retained["fingerprint"] == before["fingerprint"]
    # The failed commit stays recoverably staged instead of published.
    assert (state / "maintenance_baseline.staging.json").is_file()


def test_coverage_truncated_state_reports_incomplete(tmp_path, monkeypatch):
    """A returned project_state(truncated=true) is incomplete without exception."""
    registry, config = make_vault(tmp_path)
    register_policy(
        registry,
        config,
        [
            {
                "id": "project:alpha",
                "type": "project",
                "name": "Alpha",
                "purpose": "test",
                "scopes": ["."],
                "maintenance": {"review_interval_days": 30},
            }
        ],
    )
    import maintenance_cycle as cycle_module

    original_state = cycle_module.project_state

    def _truncated(registry_arg, config_arg, context_id, **kwargs):
        state = original_state(registry_arg, config_arg, context_id, **kwargs)
        return {**state, "truncated": True}

    monkeypatch.setattr(cycle_module, "project_state", _truncated)
    coverage = maintenance_coverage(registry, config)
    assert coverage["registered_context_count"] == 1
    assert coverage["status"] == "incomplete"
    assert coverage["scan_incomplete"] is True
    assert any(item.get("state_truncated") is True for item in coverage["items"])


def test_cycle_orphan_increase_never_clear(tmp_path):
    """New doctor findings must not be reported as clear; history must not re-alert."""
    root = tmp_path / "vault"
    root.mkdir()
    (root / "a.md").write_text("[[b]]", encoding="utf-8")
    (root / "b.md").write_text("[[a]]", encoding="utf-8")
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name="test", write_policy="append-create")
    register_policy(
        registry,
        config,
        [
            {
                "id": "project:alpha",
                "type": "project",
                "name": "Alpha",
                "purpose": "test",
                "scopes": ["."],
                "maintenance": {
                    "review_interval_days": 30,
                    "check_source_changes": False,
                },
            }
        ],
    )
    first = run_maintenance_cycle(registry, config)
    assert first["validated"] is True
    # First run establishes the baseline without a quality alert.
    assert first["status"] == "clear"
    assert first["quality"]["regressed"] is False
    assert first["quality"]["new_findings"] == []
    assert first["monitor"]["alertable"] is False

    (config.root_path / "lonely.md").write_text("Lonely", encoding="utf-8")
    second = run_maintenance_cycle(registry, config)
    assert second["validated"] is True
    assert second["snapshot"]["orphans"] == first["snapshot"]["orphans"] + 1
    assert second["status"] == "attention"
    assert second["status"] != "clear"
    assert second["quality"]["regressed"] is True
    orphan_findings = [
        item for item in second["quality"]["new_findings"] if item["field"] == "orphans"
    ]
    assert len(orphan_findings) == 1
    finding = orphan_findings[0]
    assert finding["category"] == "orphans"
    assert finding["reason"] == "orphan_increase"
    assert finding["delta"] == 1
    assert finding["previous"] == first["snapshot"]["orphans"]
    assert finding["current"] == second["snapshot"]["orphans"]
    assert second["monitor"]["alertable"] is True

    # Unchanged history after the new baseline must not re-alert.
    third = run_maintenance_cycle(registry, config)
    assert third["validated"] is True
    assert third["fingerprint"] == second["fingerprint"]
    assert third["quality"]["regressed"] is False
    assert third["status"] == "clear"
    assert third["monitor"]["alertable"] is False
