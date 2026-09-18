"""Product acceptance: arbitrary functional types share the real CLI lifecycle."""

import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

CLI = Path(__file__).resolve().parent.parent / "scripts" / "knowledge_router.py"


@pytest.mark.parametrize(
    "kind,name,alias,purpose",
    [
        ("research", "潮汐研究", "TidalStudy", "维护假设、实验结果和复现安排"),
        ("trip", "天津行程", "TianjinTrip", "维护行程确认、变更与待办"),
        ("job", "岗位申请", "RoleApplication", "维护申请进展、反馈和面试跟进"),
        ("equipment", "仪器维护", "SensorCare", "用户自定义：维护设备校准与维修记录"),
    ],
)
def test_cli_lifecycle_for_arbitrary_type(tmp_path, kind, name, alias, purpose):
    root = tmp_path / "existing vault"
    paths = ["随手记/原有日志.md", "旧资料/主题甲.md", "杂项/另一份依据.md"]
    for relative in paths:
        source = root / relative
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(f"# 手写原文\r\n{relative}\r\n".encode())
    originals = {p: (root / p).read_bytes() for p in paths}
    directories = {p.relative_to(root) for p in root.rglob("*") if p.is_dir()}
    state_root = tmp_path / "external state"

    def call(*args, expected=0):
        completed = subprocess.run(
            [
                sys.executable,
                str(CLI),
                "--state-root",
                str(state_root),
                *args,
                "--json",
            ],
            capture_output=True,
            encoding="utf-8",
            timeout=30,
        )
        assert completed.returncode == expected, completed.stderr
        return json.loads(completed.stdout) if expected == 0 else completed.stderr

    call("attach", str(root), "--name", "acceptance", "--write-policy", "append-create")
    definitions = tmp_path / "definitions.json"
    definitions.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "contexts": [
                    {
                        "id": "journal",
                        "type": "daily",
                        "kind": "collection",
                        "name": "随手记",
                        "purpose": "延续原有记录习惯",
                        "scopes": ["随手记"],
                    },
                    {
                        "id": "subject",
                        "type": kind,
                        "kind": "instance",
                        "name": name,
                        "purpose": purpose,
                        "aliases": [alias],
                        "scopes": paths[1:],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    call("contexts", "register", "--definitions", str(definitions))
    plan = call("contexts", "plan", f"今天推进 {alias}，记录进展")
    assert {"journal", "subject"} <= {i["context_id"] for i in plan["impacts"]}
    assert (
        next(i for i in plan["impacts"] if i["context_id"] == "subject")["type"] == kind
    )

    def capture(key, *fields):
        before = (root / paths[0]).read_bytes()
        event = call(
            "contexts",
            "capture",
            f"{alias}：Cobalt milestone {key}",
            "--target",
            paths[0],
            "--primary-context",
            "journal",
            "--context-id",
            "subject",
            "--idempotency-key",
            key,
            "--state-context",
            "subject",
            *fields,
        )
        assert (root / paths[0]).read_bytes() == before  # Preview does not write.
        assert call("contexts", "commit", event["event_id"])["status"] == "completed"
        after = (root / paths[0]).read_bytes()
        assert after.startswith(before)
        call("contexts", "commit", event["event_id"])
        assert (root / paths[0]).read_bytes() == after  # Retry is idempotent.
        return event["event_id"]

    initial = capture("progress", "--state-field", "status", "--state-value", "待确认")
    capture(
        "confirmed",
        "--state-field",
        "status",
        "--state-value",
        "已确认",
        "--replaces",
        initial,
    )
    state = call("contexts", "state", "subject")
    assert state["fields"][0]["value"] == "已确认"
    assert state["fields"][0]["status"] == "current"
    brief = call("contexts", "brief", alias, "--query", "Cobalt milestone")
    assert not brief["evidence_gap"]
    assert {e["path"] for e in brief["evidence"]} == {paths[0]}
    for evidence in brief["evidence"]:
        assert (
            evidence["source_sha256"]
            == hashlib.sha256((root / paths[0]).read_bytes()).hexdigest()
        )
        assert evidence["line_start"] > 0 and not evidence["stale"]
    assert call("contexts", "history", "subject")["event_count"] == 2

    followup_fields = [
        "--followup-id",
        "check",
        "--followup-title",
        "核实下一步",
        "--due-at",
        "2000-01-01T09:00:00+08:00",
    ]
    opened = capture("followup-open", *followup_fields)
    tick = call("contexts", "tick")
    assert not tick["scan_incomplete"]
    assert any(
        i["context_id"] == "subject" and i["kind"] == "followup" for i in tick["items"]
    )
    assert tick["notifications_sent"] == 0
    capture(
        "followup-done",
        *followup_fields,
        "--followup-status",
        "done",
        "--replaces",
        opened,
    )
    assert call("contexts", "followups", "subject", "--due-only")["item_count"] == 0
    capture("retire", "--state-field", "lifecycle", "--state-value", "retired")
    fields = {f["field"]: f for f in call("contexts", "state", "subject")["fields"]}
    assert fields["lifecycle"]["value"] == "retired"
    assert call("contexts", "tick")["status"] == "clear"

    assert {p.relative_to(root).as_posix() for p in root.rglob("*.md")} == set(paths)
    assert {p.relative_to(root) for p in root.rglob("*") if p.is_dir()} == directories
    assert all((root / p).read_bytes() == originals[p] for p in paths[1:])
    assert (root / paths[0]).read_bytes().startswith(originals[paths[0]])


def test_cli_daily_contact_project_multi_context_lifecycle(tmp_path):
    """Daily, Contact and Project can share one frozen event safely."""
    root = tmp_path / "multi-context-vault"
    paths = {
        "daily": "100_Logs/daily/2026-09-11.md",
        "contact": "300_Resources/305_Contact/张三.md",
        "project": "100_Project/星河/README.md",
    }
    for relative, label in paths.items():
        target = root / label
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(f"# {relative}\nOriginal evidence\n", encoding="utf-8")
    originals = {key: (root / value).read_bytes() for key, value in paths.items()}
    state_root = tmp_path / "external-state"

    def call(*args, expected=0):
        completed = subprocess.run(
            [sys.executable, str(CLI), "--state-root", str(state_root), *args, "--json"],
            capture_output=True,
            encoding="utf-8",
            timeout=30,
        )
        assert completed.returncode == expected, completed.stderr
        return json.loads(completed.stdout) if expected == 0 else completed.stderr

    call("attach", str(root), "--name", "multi", "--write-policy", "append-create")
    definitions = tmp_path / "definitions.json"
    definitions.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "contexts": [
                    {
                        "id": "daily:main",
                        "type": "daily",
                        "kind": "collection",
                        "name": "Daily",
                        "purpose": "daily progress",
                        "scopes": ["100_Logs/daily"],
                    },
                    {
                        "id": "contact:zhang",
                        "type": "contact",
                        "kind": "instance",
                        "name": "张三",
                        "purpose": "communication history",
                        "aliases": ["Zhang San"],
                        "scopes": ["300_Resources/305_Contact/张三.md"],
                    },
                    {
                        "id": "project:xinghe",
                        "type": "project",
                        "kind": "instance",
                        "name": "星河",
                        "purpose": "project progress",
                        "scopes": ["100_Project/星河"],
                    },
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    call("contexts", "register", "--definitions", str(definitions))
    plan = call("contexts", "plan", "和张三推进星河项目，记录今天进展")
    assert {"daily:main", "contact:zhang", "project:xinghe"} <= {
        item["context_id"] for item in plan["impacts"]
    }

    before = {key: (root / value).read_bytes() for key, value in paths.items()}
    event = call(
        "contexts",
        "capture",
        "和张三确认星河项目下一步",
        "--target",
        paths["daily"],
        "--primary-context",
        "daily:main",
        "--context-id",
        "contact:zhang",
        "--context-id",
        "project:xinghe",
        "--idempotency-key",
        "multi-event-1",
        "--state-field",
        "status",
        "--state-value",
        "待确认",
        "--state-context",
        "project:xinghe",
    )
    assert {key: (root / value).read_bytes() for key, value in paths.items()} == before
    assert call("contexts", "commit", event["event_id"])["status"] == "completed"
    after_commit = {key: (root / value).read_bytes() for key, value in paths.items()}
    assert all(after_commit[key] == before[key] for key in ("contact", "project"))
    assert after_commit["daily"].startswith(before["daily"])
    assert call("contexts", "commit", event["event_id"])["status"] == "completed"
    assert {key: (root / value).read_bytes() for key, value in paths.items()} == after_commit

    state = call("contexts", "state", "project:xinghe")
    assert next(item for item in state["fields"] if item["field"] == "status")["value"] == "待确认"
    brief = call("contexts", "brief", "contact:zhang", "--query", "星河")
    assert not brief["evidence_gap"]
    assert any(item["path"] == paths["daily"] for item in brief["evidence"])
    assert call("contexts", "history", "contact:zhang")["event_count"] == 1

    followup = call(
        "contexts",
        "capture",
        "确认星河项目下一步",
        "--target",
        paths["daily"],
        "--primary-context",
        "daily:main",
        "--context-id",
        "project:xinghe",
        "--state-context",
        "project:xinghe",
        "--idempotency-key",
        "multi-followup-open",
        "--followup-id",
        "xinghe_next",
        "--followup-title",
        "确认下一步",
        "--due-at",
        "2000-01-01T09:00:00+08:00",
    )
    call("contexts", "commit", followup["event_id"])
    due = call("contexts", "followups", "project:xinghe", "--due-only")
    assert due["item_count"] == 1
    done = call(
        "contexts",
        "capture",
        "已确认星河项目下一步",
        "--target",
        paths["daily"],
        "--primary-context",
        "daily:main",
        "--context-id",
        "project:xinghe",
        "--state-context",
        "project:xinghe",
        "--idempotency-key",
        "multi-followup-done",
        "--followup-id",
        "xinghe_next",
        "--followup-title",
        "确认下一步",
        "--due-at",
        "2000-01-01T09:00:00+08:00",
        "--followup-status",
        "done",
        "--replaces",
        followup["event_id"],
    )
    call("contexts", "commit", done["event_id"])
    assert call("contexts", "followups", "project:xinghe", "--due-only")["item_count"] == 0

    retired = call(
        "contexts",
        "capture",
        "星河项目结束归档",
        "--target",
        paths["daily"],
        "--primary-context",
        "daily:main",
        "--context-id",
        "project:xinghe",
        "--idempotency-key",
        "multi-retire",
        "--state-field",
        "lifecycle",
        "--state-value",
        "retired",
        "--state-context",
        "project:xinghe",
    )
    call("contexts", "commit", retired["event_id"])
    project_state = call("contexts", "state", "project:xinghe")
    assert next(item for item in project_state["fields"] if item["field"] == "lifecycle")["value"] == "retired"
    assert (root / paths["contact"]).read_bytes() == originals["contact"]
    assert (root / paths["project"]).read_bytes() == originals["project"]
    assert (root / paths["daily"]).read_bytes().startswith(originals["daily"])
