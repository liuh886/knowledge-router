"""End-to-end regressions for the unified memory workflow boundary."""

import ast
import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))
from capture_broker import commit_capture_plan
from memory_service import (
    append_contact_log,
    append_inbox_stream,
    drain_quick_capture_buffer,
    triage_todos,
)
from registry import VaultRegistry
from runtime_backup import create_runtime_backup
from route_engine import RoutingResult, execute_capture
from triage_engine import ContactResolver, get_contact_radar
from vault_service import search_vault


@pytest.fixture
def memory(tmp_path):
    vault = tmp_path / "Vault"
    (vault / "000_Inbox").mkdir(parents=True)
    people = vault / "300_Resources" / "305_Contact"
    people.mkdir(parents=True)
    (people / "Alice.md").write_text(
        "---\naliases: [SharedName]\n---\n# Alice\n\n## Log\n- old\n\n## Notes\nkept\n",
        encoding="utf-8",
    )
    (vault / "000_Inbox" / "todo.2026.md").write_bytes(
        b"\xef\xbb\xbf# Todo\r\n\r\n- [ ] model history\r\n"
    )
    registry = VaultRegistry(tmp_path / "State")
    config = registry.attach(vault, name="test", write_policy="append-create")
    return registry, config


def snapshot(vault):
    return {
        p.relative_to(vault).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in vault.rglob("*")
        if p.is_file()
    }


@pytest.mark.parametrize(
    "arguments",
    [
        ["inbox", "append", "memo", "read only test"],
        ["contact", "append", "Alice", "read only test"],
        ["triage", "todo", "--year", "2026", "--commit"],
    ],
)
def test_cli_mutations_refuse_read_only_vault_without_touching_sources(memory, arguments):
    registry, config = memory
    registry.attach(config.root_path, name="test", write_policy="off")
    before = snapshot(config.root_path)
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "knowledge_router.py"),
            "--state-root",
            str(registry.state_root),
            *arguments,
            "--vault",
            "test",
            "--json",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert snapshot(config.root_path) == before


def test_capture_preserves_prefix_and_triages_only_new_entry_and_refreshes_index(memory):
    registry, config = memory
    path = config.root_path / "000_Inbox" / "todo.2026.md"
    before = path.read_bytes()
    result = append_inbox_stream(registry, config, "todo", "model instantrecall", year=2026)
    assert result["status"] == "success"
    after = path.read_bytes()
    assert after.startswith(before)
    assert b"model history\r\n" in after
    assert after.count(b"#research") == 1
    assert after.count(b"\n") == after.count(b"\r\n")
    hits = search_vault(registry, registry.resolve(config.id), "instantrecall", limit=5)
    assert hits and hits[0]["path"] == "000_Inbox/todo.2026.md"
    assert not hits[0]["stale"]


def test_contact_appends_without_changing_earlier_sections_and_cas_preserves_human_edit(memory):
    registry, config = memory
    path = config.root_path / "300_Resources" / "305_Contact" / "Alice.md"
    before = path.read_bytes()
    result = append_contact_log(registry, config, "Alice", "firstrecall")
    assert path.read_bytes().startswith(before)
    assert result["mutation"]["action"] == "append_existing"
    preview = append_contact_log(registry, config, "Alice", "pendingedit", preview=True)
    with path.open("ab") as f:
        f.write(b"human edits after preview\n")
    changed = path.read_bytes()
    with pytest.raises(RuntimeError, match="Compare-and-swap"):
        commit_capture_plan(config.root_path, registry.vault_state_dir(config), preview["plan_id"])
    assert path.read_bytes() == changed


def test_alias_collisions_and_duplicate_basenames_require_path(memory):
    registry, config = memory
    people = config.root_path / "300_Resources" / "305_Contact"
    (people / "Bob.md").write_text("---\naliases: [sharedname]\n---\n# Bob\n", encoding="utf-8")
    (people / "_Template.md").write_text("# Template Person\n", encoding="utf-8")
    resolver = ContactResolver(config.root_path)
    entity, candidates = resolver.match_contact("SHAREDNAME")
    assert entity is None and candidates == ["Alice", "Bob"]
    linked, _ = resolver.link_entities("SharedName has work")
    assert linked == "SharedName has work"
    assert "_Template" not in resolver.entities
    before = snapshot(config.root_path)
    with pytest.raises(ValueError, match="ambiguous"):
        append_contact_log(registry, config, "SharedName", "must refuse")
    assert snapshot(config.root_path) == before
    tasks = config.root_path / "000_Inbox/todo.2026.md"
    tasks.write_text("- [ ] SharedName\n- [ ] [[Alice]]\n", encoding="utf-8")
    counts = {
        item["canonical"]: item["pending_tasks_count"]
        for item in get_contact_radar(config.root_path, year=2026)["contacts"]
    }
    assert counts == {"Alice": 1, "Bob": 0}
    shown = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "knowledge_router.py"),
            "--state-root",
            str(registry.state_root),
            "contact",
            "show",
            "SharedName",
            "--vault",
            "test",
            "--json",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert shown.returncode == 1
    assert json.loads(shown.stdout)["status"] == "ambiguous"
    other = people / "Team"
    other.mkdir()
    (other / "Alice.md").write_text("# Alice\n", encoding="utf-8")
    resolver = ContactResolver(config.root_path)
    assert resolver.match_contact("Alice")[0] is None
    exact = "300_Resources/305_Contact/Team/Alice.md"
    assert resolver.match_contact(exact)[0].relative_path == exact
    assert append_contact_log(registry, config, exact, "exactperson")["status"] == "success"


def test_failed_index_refresh_is_visible_and_intent_retry_writes_once(memory):
    registry, config = memory
    with patch(
        "mutation_service.vault_service.sync_vault", side_effect=RuntimeError("index unavailable")
    ):
        pending = append_inbox_stream(
            registry, config, "memo", "recoverablemarker", idempotency_key="request-1"
        )
    assert pending["status"] == "applied_pending_index" and pending["committed"]
    path = config.root_path / pending["file"]
    before = path.read_bytes()
    result = append_inbox_stream(
        registry, config, "memo", "recoverablemarker", idempotency_key="request-1"
    )
    assert result["status"] == "success"
    assert result["mutation"]["status"] == "idempotent_replay"
    assert path.read_bytes() == before
    assert search_vault(registry, registry.resolve(config.id), "recoverablemarker", limit=5)
    with pytest.raises(ValueError, match="different workflow"):
        append_inbox_stream(
            registry, config, "memo", "different contents", idempotency_key="request-1"
        )


def test_buffer_is_explicit_nonrecursive_repeatable_and_preserves_completion(memory):
    registry, config = memory
    buffer = config.root_path / "000_Inbox" / "Quick_Capture.md"
    done_entry = "- [x] ⏳ 2025-09-02 10:00 bufferdonemarker #todo/next"
    original = f"# Buffer\n\n- [ ] bufferopenmarker\n{done_entry}\n- 💡 bufferideamarker\n"
    buffer.write_text(original, encoding="utf-8")
    append_inbox_stream(registry, config, "todo", "regularcapture", year=2026)
    assert "bufferopenmarker" not in (config.root_path / "000_Inbox/todo.2026.md").read_text(
        encoding="utf-8"
    )
    first = drain_quick_capture_buffer(registry, config, year=2026)
    before = snapshot(config.root_path)
    second = drain_quick_capture_buffer(registry, config, year=2026)
    assert first["routed_count"] == second["routed_count"] == 3
    assert all(item["replayed"] for item in second["routed"])
    assert snapshot(config.root_path) == before
    assert buffer.read_text(encoding="utf-8") == original
    todo = (config.root_path / "000_Inbox/todo.2026.md").read_text(encoding="utf-8-sig")
    assert todo.count("bufferopenmarker") == todo.count("bufferdonemarker") == 1
    assert done_entry in todo
    assert any(
        line.startswith("- [x]") and "bufferdonemarker" in line for line in todo.splitlines()
    )


def test_triage_preview_and_reviewed_edit_preserve_bom_crlf_and_completed_tasks(memory):
    registry, config = memory
    path = config.root_path / "000_Inbox/todo.2026.md"
    before = path.read_bytes() + b"- [x] model completed\r\n"
    path.write_bytes(before)
    preview = triage_todos(registry, config, year=2026)
    assert preview["status"] == "preview" and not preview["committed"]
    assert "original_base64" not in preview["plan"]
    assert path.read_bytes() == before
    with pytest.raises(PermissionError, match="reviewed-edit"):
        triage_todos(registry, config, year=2026, commit=True)
    config = registry.attach(config.root_path, name="test", write_policy="reviewed-edit")
    applied = triage_todos(registry, config, year=2026, commit=True)
    assert applied["status"] == "success"
    after = path.read_bytes()
    assert after.startswith(b"\xef\xbb\xbf")
    assert after.count(b"\r\n") == after.count(b"\n")
    assert b"- [x] model completed\r\n" in after
    assert not triage_todos(registry, config, year=2026, commit=True)["committed"]


def test_workflows_do_not_create_directories(memory):
    registry, config = memory
    (config.root_path / "000_Inbox/todo.2026.md").unlink()
    (config.root_path / "000_Inbox").rmdir()
    with pytest.raises(FileNotFoundError):
        append_inbox_stream(registry, config, "memo", "no hidden initialization")
    assert not (config.root_path / "000_Inbox").exists()


def test_linked_contacts_cannot_redirect_writes(memory, tmp_path):
    registry, config = memory
    outside = tmp_path / "outside.md"
    outside.write_text("# Redirected Person\n", encoding="utf-8")
    link = config.root_path / "300_Resources/305_Contact/Redirected.md"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation unavailable")
    with pytest.raises(ValueError, match="not found"):
        append_contact_log(registry, config, "Redirected", "must refuse")
    assert outside.read_text(encoding="utf-8") == "# Redirected Person\n"


def test_backup_retains_workflow_intents_frozen_originals_and_journal(memory, tmp_path):
    registry, config = memory
    append_contact_log(registry, config, "Alice", "backedupintent", idempotency_key="backup-test")
    preview = triage_todos(registry, config, year=2026)
    result = create_runtime_backup(registry, registry.resolve(config.id), tmp_path / "Backups")
    with zipfile.ZipFile(result["archive"]) as archive:
        names = archive.namelist()
        assert any("/workflow." in name and name.endswith(".json") for name in names)
        assert any(name.endswith("mutations.sqlite3") for name in names)
        plan_path = next(name for name in names if name.endswith(f"plan.{preview['plan_id']}.json"))
        assert b'"original_base64"' in archive.read(plan_path)


def test_legacy_relocation_entrypoint_cannot_bypass_broker(memory):
    registry, config = memory
    before = snapshot(config.root_path)
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "migrate_daily_logs.py"),
            "--vault",
            str(config.root_path),
            "--commit",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "retired" in result.stderr
    assert snapshot(config.root_path) == before


def test_profile_and_cli_cannot_reintroduce_mutation_bypasses():
    for name in ("triage_engine.py", "memory_commands.py"):
        tree = ast.parse((SCRIPTS / name).read_text(encoding="utf-8"))
        assert not any(
            isinstance(n, ast.Attribute)
            and n.attr in {"write_text", "write_bytes", "mkdir", "replace", "rename", "unlink"}
            and isinstance(n.value, ast.Name)
            and n.value.id in {"path", "target", "file_path"}
            for n in ast.walk(tree)
        )
    tree = ast.parse((SCRIPTS / "triage_engine.py").read_text(encoding="utf-8"))
    assert not any(
        isinstance(n, ast.ImportFrom)
        and n.module in {"registry", "memory_service", "capture_broker", "mutation_service"}
        for n in ast.walk(tree)
    )


def test_compatibility_capture_refreshes_the_same_projection(memory):
    registry, config = memory
    route = RoutingResult(
        "memo",
        "create_new",
        "000_Inbox",
        "compat.md",
        "000_Inbox/compat.md",
        1.0,
        "compatibility acceptance",
    )
    mutation = execute_capture(
        config.root_path, registry.vault_state_dir(config), "compatibilityrecallmarker", route
    )
    assert mutation.relative_path == "000_Inbox/compat.md"
    hits = search_vault(registry, registry.resolve(config.id), "compatibilityrecallmarker", limit=5)
    assert hits and not hits[0]["stale"]
