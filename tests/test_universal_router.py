#!/usr/bin/env python3
# ruff: noqa: E402
"""End-to-end safety and evidence tests for KnowledgeRouter."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from bootstrap import bootstrap_vault
from capture_broker import (
    ShortWriteError,
    commit_capture_plan,
    create_capture_plan,
    load_capture_plan,
)
from context_assembler import assemble_decision_context
from fts_indexer import (
    audit_index,
    build_generation,
    build_or_sync_index,
    prune_generations,
    search_index,
)
from registry import VaultRegistry
from route_engine import execute_capture, resolve_route
from secure_io import SecureParent
from taxonomy_scanner import scan_taxonomy
from vault_doctor import diagnose_vault


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="")


def _snapshot(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        result[f"dir:{relative}" if path.is_dir() else f"file:{relative}"] = (
            "" if path.is_dir() else hashlib.sha256(path.read_bytes()).hexdigest()
        )
    return result


class TestKnowledgeRouter(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="knowledge-router-test-"))
        self.vault = self.temp / "Vault"
        self.state = self.temp / "state"
        (self.vault / "000_Inbox").mkdir(parents=True)
        (self.vault / "100_Logs" / "daily").mkdir(parents=True)
        (self.vault / "100_Project" / "2603_AI_Agent").mkdir(parents=True)
        (self.vault / "200_Area" / "234_Qatar").mkdir(parents=True)
        (self.vault / "300_Resources").mkdir(parents=True)
        _write(
            self.vault / "100_Project" / "2603_AI_Agent" / "TASKS.md",
            "# Tasks\r\n- [ ] Build knowledge router\r\n",
        )
        _write(
            self.vault / "100_Project" / "2603_AI_Agent" / "PROGRESS.md",
            "# Progress\nInitial design accepted.\n",
        )
        _write(
            self.vault / "200_Area" / "234_Qatar" / "Seismic Spec.md",
            "# Qatar Seismic Specification\nOffshore 3D seismic acquisition requires traceability.\n",
        )

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def _attach(self, *, name: str = "test", write_policy: str = "off"):
        report = bootstrap_vault(
            self.vault,
            state_root=self.state,
            name=name,
            write_policy=write_policy,
        )
        config = VaultRegistry(self.state).resolve(name)
        return report, config, Path(report["index_db_path"])

    def test_attach_is_external_and_byte_preserving(self) -> None:
        before = _snapshot(self.vault)
        report, config, index = self._attach()
        self.assertEqual(before, _snapshot(self.vault))
        self.assertFalse((self.vault / ".knowledge_router").exists())
        self.assertFalse((self.vault / "AGENTS.md").exists())
        self.assertTrue(index.is_file())
        self.assertEqual(report["indexed_notes"], 3)
        self.assertEqual(config.write_policy, "off")
        self.assertNotIn(str(self.vault), str(index.parent))

    def test_state_root_inside_vault_is_rejected(self) -> None:
        before = _snapshot(self.vault)
        with self.assertRaisesRegex(ValueError, "must not be inside"):
            VaultRegistry(self.vault / ".derived").attach(self.vault)
        self.assertEqual(before, _snapshot(self.vault))
        with self.assertRaisesRegex(ValueError, "production state is fixed"):
            VaultRegistry(Path.home() / "knowledge-router-arbitrary-state")
        with self.assertRaisesRegex(ValueError, "indexes must stay outside"):
            build_or_sync_index(self.vault, self.vault / "derived.db")

    def test_multi_vault_registry_requires_selector(self) -> None:
        self._attach(name="first")
        second = self.temp / "Second"
        second.mkdir()
        _write(second / "note.md", "# Second\nIndependent vault.\n")
        VaultRegistry(self.state).attach(second, name="second")
        registry = VaultRegistry(self.state)
        with self.assertRaisesRegex(KeyError, "Multiple Vaults"):
            registry.resolve()
        self.assertEqual(registry.resolve("second").root_path, second.resolve())

    def test_registered_vault_can_be_selected_by_canonical_root_path(self) -> None:
        _report, config, _index = self._attach(name="path-selectable")
        resolved = VaultRegistry(self.state).resolve(str(self.vault))
        self.assertEqual(resolved.id, config.id)

    def test_supported_vault_archetypes_are_induced_without_scaffolding(self) -> None:
        para = self.temp / "PARA"
        for folder in ("Projects", "Areas", "Resources", "Archives"):
            (para / folder).mkdir(parents=True)
        _write(para / "Projects" / "Plan.md", "# Plan\n")

        daily = self.temp / "Chronological"
        _write(daily / "Daily" / "2026-08-29.md", "# Today\n")

        zettel = self.temp / "Zettelkasten"
        for folder in ("Permanent", "Literature", "Fleeting"):
            (zettel / folder).mkdir(parents=True)
        _write(zettel / "Permanent" / "Idea.md", "# Idea\n")

        flat = self.temp / "Flat"
        _write(flat / "One.md", "# One\n")

        self.assertEqual(scan_taxonomy(para).archetype, "PARA")
        self.assertEqual(scan_taxonomy(daily).archetype, "DailyChronological")
        self.assertEqual(scan_taxonomy(zettel).archetype, "Zettelkasten")
        self.assertEqual(scan_taxonomy(flat).archetype, "Flat")
        self.assertFalse((flat / "000_Inbox").exists())

    def test_taxonomy_does_not_follow_external_directory_links(self) -> None:
        external = self.temp / "ExternalTaxonomy"
        _write(external / "secret.md", "# Outside\n#should-not-be-scanned\n")
        linked = self.vault / "100_Project" / "linked-external"
        try:
            linked.symlink_to(external, target_is_directory=True)
        except OSError:
            self.skipTest("Directory symlink creation is unavailable")
        profile = scan_taxonomy(self.vault)
        self.assertNotIn("linked-external", profile.active_projects)
        self.assertEqual(profile.total_notes, 3)
        self.assertNotIn("should-not-be-scanned", profile.detected_tags)

    def test_external_generation_pruning_keeps_two_latest(self) -> None:
        state_dir = self.temp / "projection"
        state_dir.mkdir()
        for generation in range(1, 5):
            (state_dir / f"index.{generation}.db").write_bytes(str(generation).encode())
        removed = prune_generations(state_dir, active_filename="index.4.db", keep=2)
        self.assertEqual(removed, ["index.1.db", "index.2.db"])
        self.assertEqual(
            sorted(path.name for path in state_dir.glob("index.*.db")),
            ["index.3.db", "index.4.db"],
        )

    def test_search_returns_resolvable_evidence_and_staleness(self) -> None:
        _report, config, index = self._attach()
        results = search_index(
            index,
            "Qatar offshore seismic acquisition",
            vault_root=self.vault,
            vault_id=config.id,
            generation=config.index_generation,
        )
        self.assertTrue(results)
        hit = results[0]
        self.assertEqual(hit["path"], "200_Area/234_Qatar/Seismic Spec.md")
        self.assertGreaterEqual(hit["line_start"], 1)
        self.assertEqual(len(hit["source_sha256"]), 64)
        self.assertFalse(hit["stale"])
        target = self.vault / hit["path"]
        target.write_text(
            target.read_text(encoding="utf-8") + "Changed.\n", encoding="utf-8"
        )
        stale = search_index(index, "Qatar offshore seismic", vault_root=self.vault)[0]
        self.assertTrue(stale["stale"])
        self.assertEqual(audit_index(self.vault, index)["status"], "stale")
        self.assertEqual(search_index(index, "xyznonexistentquery123"), [])
        with self.assertRaisesRegex(RuntimeError, "generation mismatch"):
            search_index(index, "Qatar", generation="forged-generation")

    def test_content_addressed_generation_is_reused_when_sources_do_not_change(
        self,
    ) -> None:
        _report, config, _index = self._attach()
        state_dir = VaultRegistry(self.state).vault_state_dir(config)
        first = build_generation(self.vault, state_dir)
        second = build_generation(self.vault, state_dir)
        self.assertEqual(first["generation"], second["generation"])
        self.assertTrue(first["reused"])
        self.assertTrue(second["reused"])

    def test_logically_tampered_index_fails_generation_validation(self) -> None:
        _report, config, index = self._attach()
        with sqlite3.connect(index) as connection:
            connection.execute(
                "UPDATE notes SET checksum=? WHERE path=(SELECT path FROM notes ORDER BY path LIMIT 1)",
                ("0" * 64,),
            )
        with self.assertRaisesRegex(RuntimeError, "does not match its generation"):
            search_index(
                index,
                "",
                vault_root=self.vault,
                vault_id=config.id,
                generation=config.index_generation,
            )

    def test_unreadable_markdown_prevents_generation_activation(self) -> None:
        broken = self.vault / "000_Inbox" / "utf16.md"
        broken.write_bytes("# UTF16\n不能静默跳过\n".encode("utf-16"))
        projection = self.temp / "incomplete-projection"
        with self.assertRaisesRegex(RuntimeError, "activation refused"):
            build_generation(self.vault, projection)
        self.assertEqual(list(projection.glob("index.*.db")), [])

    def test_cross_vault_search_is_globally_ranked_and_fully_sourced(self) -> None:
        _write(self.vault / "000_Inbox" / "weak.md", "A mention of globalneedle.\n")
        self._attach(name="first")
        second = self.temp / "SecondSearch"
        second.mkdir()
        _write(second / "globalneedle.md", "# globalneedle\nExact title match.\n")
        bootstrap_vault(second, state_root=self.state, name="second")
        cli = SCRIPT_DIR / "knowledge_router.py"
        completed = subprocess.run(
            [
                sys.executable,
                str(cli),
                "--state-root",
                str(self.state),
                "search",
                "globalneedle",
                "--all",
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        results = json.loads(completed.stdout)
        second_id = VaultRegistry(self.state).resolve("second").id
        self.assertEqual(results[0]["vault_id"], second_id)
        for result in results:
            self.assertTrue(
                {
                    "vault_id",
                    "path",
                    "line_start",
                    "line_end",
                    "source_sha256",
                    "index_generation",
                    "stale",
                }.issubset(result)
            )

    def test_route_reuses_existing_structure_and_prioritizes_progress(self) -> None:
        profile = scan_taxonomy(self.vault)
        progress = resolve_route(
            "AI_Agent 本周进展：索引完成", profile, vault_root=self.vault
        )
        self.assertEqual(progress.intent, "project_progress")
        self.assertEqual(
            progress.relative_target_path, "100_Project/2603_AI_Agent/PROGRESS.md"
        )
        daily = resolve_route("今日工作总结", profile, vault_root=self.vault)
        self.assertTrue(daily.relative_target_path.startswith("100_Logs/daily/"))
        idea = resolve_route("突然有个灵感：做来源校验", profile, vault_root=self.vault)
        self.assertTrue(idea.relative_target_path.startswith("000_Inbox/"))

    def test_create_is_exclusive_collision_safe_and_idempotent(self) -> None:
        _report, config, _index = self._attach(write_policy="append-create")
        profile = scan_taxonomy(self.vault)
        route = resolve_route("灵感：来源哈希", profile, vault_root=self.vault)
        state_dir = VaultRegistry(self.state).vault_state_dir(config)
        plan = create_capture_plan(
            self.vault,
            state_dir,
            "保存每条证据的内容哈希。",
            route,
            idempotency_key="capture-1",
        )
        first = commit_capture_plan(self.vault, state_dir, plan)
        replay = commit_capture_plan(self.vault, state_dir, plan.plan_id)
        conflicting_plan = create_capture_plan(
            self.vault,
            state_dir,
            "不同请求不得复用同一个幂等键。",
            route,
            idempotency_key="capture-1",
        )
        with self.assertRaisesRegex(RuntimeError, "different capture request"):
            commit_capture_plan(self.vault, state_dir, conflicting_plan)
        collision = execute_capture(self.vault, state_dir, "另一条灵感。", route)
        self.assertEqual(replay.status, "idempotent_replay")
        self.assertEqual(replay.relative_path, first.relative_path)
        self.assertNotEqual(collision.relative_path, first.relative_path)
        self.assertEqual(len(list((self.vault / "000_Inbox").glob("*.md"))), 2)

    def test_frozen_plan_rejects_source_change_and_tampering(self) -> None:
        _report, config, _index = self._attach(write_policy="append-create")
        state_dir = VaultRegistry(self.state).vault_state_dir(config)
        route = resolve_route(
            "AI_Agent task: frozen plan",
            scan_taxonomy(self.vault),
            vault_root=self.vault,
        )
        plan = create_capture_plan(self.vault, state_dir, "- [ ] Frozen", route)
        target = self.vault / plan.relative_path
        target.write_bytes(target.read_bytes() + b"external change\n")
        external = target.read_bytes()
        with self.assertRaisesRegex(RuntimeError, "source changed after preview"):
            commit_capture_plan(self.vault, state_dir, plan)
        self.assertEqual(target.read_bytes(), external)

        second = create_capture_plan(self.vault, state_dir, "- [ ] New plan", route)
        plan_path = state_dir / f"plan.{second.plan_id}.json"
        raw = json.loads(plan_path.read_text(encoding="utf-8"))
        raw["relative_path"] = "000_Inbox/tampered.md"
        plan_path.write_text(json.dumps(raw), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "modified after preview"):
            load_capture_plan(state_dir, second.plan_id)

        third = create_capture_plan(
            self.vault, state_dir, "- [ ] Persisted only", route
        )
        forged_object = replace(third, relative_path="000_Inbox/forged.md")
        with self.assertRaisesRegex(RuntimeError, "persisted preview"):
            commit_capture_plan(self.vault, state_dir, forged_object)

    def test_expired_committed_plan_replays_without_duplicate_write(self) -> None:
        _report, config, _index = self._attach(write_policy="append-create")
        state_dir = VaultRegistry(self.state).vault_state_dir(config)
        route = resolve_route(
            "灵感：过期重放", scan_taxonomy(self.vault), vault_root=self.vault
        )
        plan = create_capture_plan(
            self.vault,
            state_dir,
            "expired replay payload",
            route,
            ttl_seconds=1,
        )
        first = commit_capture_plan(self.vault, state_dir, plan)
        target = self.vault / first.relative_path
        after = target.read_bytes()
        time.sleep(1.05)
        replay = commit_capture_plan(self.vault, state_dir, plan.plan_id)
        self.assertEqual(replay.status, "idempotent_replay")
        self.assertEqual(target.read_bytes(), after)

    def test_capture_rejects_unregistered_state_symlink_and_hardlink_targets(
        self,
    ) -> None:
        _report, config, _index = self._attach(write_policy="append-create")
        state_dir = VaultRegistry(self.state).vault_state_dir(config)
        route = resolve_route(
            "AI_Agent task: link safety",
            scan_taxonomy(self.vault),
            vault_root=self.vault,
        )
        with self.assertRaises((KeyError, PermissionError)):
            create_capture_plan(
                self.vault,
                self.temp / "unregistered" / config.id,
                "must fail",
                route,
            )

        target = self.vault / "100_Project" / "2603_AI_Agent" / "TASKS.md"
        hardlink = target.with_name("TASKS-hardlink.md")
        os.link(target, hardlink)
        try:
            with self.assertRaisesRegex(ValueError, "Hard-linked"):
                create_capture_plan(self.vault, state_dir, "must fail", route)
        finally:
            hardlink.unlink()

        plan = create_capture_plan(
            self.vault, state_dir, "- [ ] Safe before link", route
        )
        replacement = self.temp / "outside.md"
        replacement.write_text("outside", encoding="utf-8")
        original = target.read_bytes()
        target.unlink()
        try:
            target.symlink_to(replacement)
        except OSError:
            target.write_bytes(original)
            self.skipTest("Symlink creation is unavailable")
        with self.assertRaisesRegex(ValueError, "Symlink"):
            commit_capture_plan(self.vault, state_dir, plan)
        self.assertEqual(replacement.read_text(encoding="utf-8"), "outside")

    def test_state_subpaths_cannot_redirect_capture_artifacts(self) -> None:
        _report, config, _index = self._attach(write_policy="append-create")
        state_dir = VaultRegistry(self.state).vault_state_dir(config)
        route = resolve_route(
            "灵感：状态隔离", scan_taxonomy(self.vault), vault_root=self.vault
        )
        outside = self.temp / "outside-state"
        outside.mkdir()
        plan_for_link = create_capture_plan(
            self.vault, state_dir, "immutable plan file", route
        )
        plan_path = state_dir / f"plan.{plan_for_link.plan_id}.json"
        plan_path.unlink()
        outside_plan = outside / "outside-plan.json"
        outside_plan.write_bytes(b"plan-sentinel")
        os.link(outside_plan, plan_path)
        with self.assertRaisesRegex(ValueError, "Hard-linked"):
            load_capture_plan(state_dir, plan_for_link.plan_id)
        self.assertEqual(outside_plan.read_bytes(), b"plan-sentinel")
        plan_path.unlink()

        plan = create_capture_plan(self.vault, state_dir, "ledger hardlink", route)
        outside_lock = outside / "outside.lock"
        outside_lock.write_bytes(b"0")
        os.link(outside_lock, state_dir / ".capture.lock")
        with self.assertRaisesRegex(ValueError, "links or reparse"):
            commit_capture_plan(self.vault, state_dir, plan)
        self.assertEqual(outside_lock.read_bytes(), b"0")
        (state_dir / ".capture.lock").unlink()

        outside_journal = outside / "outside.journal"
        outside_journal.write_bytes(b"journal-sentinel")
        os.link(outside_journal, state_dir / "mutations.sqlite3-journal")
        with self.assertRaisesRegex(ValueError, "Hard-linked"):
            commit_capture_plan(self.vault, state_dir, plan)
        self.assertEqual(outside_journal.read_bytes(), b"journal-sentinel")
        (state_dir / "mutations.sqlite3-journal").unlink()
        (state_dir / "mutations.sqlite3").unlink(missing_ok=True)

        outside_ledger = outside / "outside.sqlite3"
        outside_ledger.write_bytes(b"not-a-database")
        os.link(outside_ledger, state_dir / "mutations.sqlite3")
        with self.assertRaisesRegex(ValueError, "Hard-linked"):
            commit_capture_plan(self.vault, state_dir, plan)
        self.assertEqual(outside_ledger.read_bytes(), b"not-a-database")

    def test_replaced_vault_state_directory_blocks_sync_before_writes(self) -> None:
        _report, config, _index = self._attach()
        registry = VaultRegistry(self.state)
        state_dir = registry.vault_state_dir(config)
        backup = state_dir.with_name(f"{state_dir.name}.backup")
        outside = self.temp / "redirected-state"
        outside.mkdir()
        before = _snapshot(self.vault)
        state_dir.rename(backup)
        try:
            try:
                state_dir.symlink_to(outside, target_is_directory=True)
            except OSError:
                self.skipTest("Directory symlink creation is unavailable")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "knowledge_router.py"),
                    "--state-root",
                    str(self.state),
                    "sync",
                    "--vault",
                    "test",
                    "--json",
                ],
                check=False,
                capture_output=True,
                text=True,
                encoding="utf-8",
            )
            self.assertEqual(completed.returncode, 2)
            self.assertIn("reparse", completed.stderr.casefold())
            self.assertEqual(list(outside.iterdir()), [])
            self.assertEqual(before, _snapshot(self.vault))
        finally:
            if state_dir.is_symlink():
                state_dir.unlink()
            if backup.exists():
                backup.rename(state_dir)

    @unittest.skipUnless(os.name == "nt", "Windows sharing-mode safety")
    def test_external_writer_blocks_append_before_any_mutation(self) -> None:
        _report, config, _index = self._attach(write_policy="append-create")
        state_dir = VaultRegistry(self.state).vault_state_dir(config)
        route = resolve_route(
            "AI_Agent task: external writer",
            scan_taxonomy(self.vault),
            vault_root=self.vault,
        )
        plan = create_capture_plan(self.vault, state_dir, "must not interleave", route)
        target = self.vault / plan.relative_path
        before = target.read_bytes()
        with target.open("a+b"):
            with self.assertRaises(PermissionError):
                commit_capture_plan(self.vault, state_dir, plan)
        self.assertEqual(target.read_bytes(), before)

    @unittest.skipUnless(os.name == "nt", "Windows directory pinning safety")
    def test_verified_parent_cannot_be_replaced_while_target_opens(self) -> None:
        parent_path = self.vault / "100_Project" / "2603_AI_Agent"
        moved = parent_path.with_name("2603_AI_Agent-moved")
        with SecureParent(self.vault, "100_Project/2603_AI_Agent/TASKS.md"):
            with self.assertRaises(PermissionError):
                parent_path.rename(moved)
        self.assertTrue(parent_path.is_dir())

    def test_short_write_is_detected_and_retry_never_duplicates(self) -> None:
        _report, config, _index = self._attach(write_policy="append-create")
        state_dir = VaultRegistry(self.state).vault_state_dir(config)
        route = resolve_route(
            "灵感：短写故障",
            scan_taxonomy(self.vault),
            vault_root=self.vault,
        )
        plan = create_capture_plan(self.vault, state_dir, "short-write-payload", route)
        real_write = __import__("os").write

        def short_write(descriptor: int, payload: bytes) -> int:
            return real_write(descriptor, payload[:-1])

        with patch("capture_broker.os.write", side_effect=short_write):
            with self.assertRaises(ShortWriteError):
                commit_capture_plan(self.vault, state_dir, plan)
        partial = (self.vault / plan.relative_path).read_bytes()
        with self.assertRaisesRegex(RuntimeError, "NEEDS_RECONCILIATION"):
            commit_capture_plan(self.vault, state_dir, plan)
        self.assertEqual((self.vault / plan.relative_path).read_bytes(), partial)

    def test_crash_after_fsync_is_reconciled_without_duplicate_write(self) -> None:
        _report, config, _index = self._attach(write_policy="append-create")
        state_dir = VaultRegistry(self.state).vault_state_dir(config)
        route = resolve_route(
            "AI_Agent task: crash recovery",
            scan_taxonomy(self.vault),
            vault_root=self.vault,
        )
        plan = create_capture_plan(
            self.vault, state_dir, "- [ ] Crash safe", route, ttl_seconds=1
        )
        with patch(
            "capture_broker._mark_status", side_effect=RuntimeError("simulated crash")
        ):
            with self.assertRaisesRegex(RuntimeError, "simulated crash"):
                commit_capture_plan(self.vault, state_dir, plan)
        target = self.vault / plan.relative_path
        after_crash = target.read_bytes()
        time.sleep(1.05)
        replay = commit_capture_plan(self.vault, state_dir, plan)
        self.assertEqual(replay.status, "idempotent_replay")
        self.assertEqual(target.read_bytes(), after_crash)

    def test_cr_only_bom_append_and_unrecognized_encoding_fail_closed(self) -> None:
        _report, config, _index = self._attach(write_policy="append-create")
        state_dir = VaultRegistry(self.state).vault_state_dir(config)
        target = self.vault / "100_Project" / "2603_AI_Agent" / "TASKS.md"
        target.write_bytes(b"\xef\xbb\xbf# Tasks\r- [ ] Existing\r")
        route = resolve_route(
            "AI_Agent task: CR line",
            scan_taxonomy(self.vault),
            vault_root=self.vault,
        )
        before = target.read_bytes()
        plan = create_capture_plan(self.vault, state_dir, "- [ ] Added", route)
        self.assertEqual(plan.newline, "CR")
        self.assertEqual(plan.bom, "utf-8")
        commit_capture_plan(self.vault, state_dir, plan)
        self.assertTrue(target.read_bytes().startswith(before))
        self.assertIn(b"\r## [", target.read_bytes())

        target.write_bytes(b"\xff\xfe#\x00 \x00T\x00a\x00s\x00k\x00s\x00")
        with self.assertRaisesRegex(UnicodeError, "UTF-16"):
            create_capture_plan(self.vault, state_dir, "must fail", route)

    def test_two_frozen_append_plans_allow_only_one_cas_commit(self) -> None:
        _report, config, _index = self._attach(write_policy="append-create")
        state_dir = VaultRegistry(self.state).vault_state_dir(config)
        route = resolve_route(
            "AI_Agent task: concurrency",
            scan_taxonomy(self.vault),
            vault_root=self.vault,
        )
        plans = [
            create_capture_plan(
                self.vault, state_dir, f"- [ ] Concurrent {index}", route
            )
            for index in range(2)
        ]

        def commit(plan):
            try:
                return commit_capture_plan(self.vault, state_dir, plan).status
            except RuntimeError:
                return "rejected"

        with ThreadPoolExecutor(max_workers=2) as pool:
            statuses = list(pool.map(commit, plans))
        self.assertEqual(statuses.count("committed"), 1)
        self.assertEqual(statuses.count("rejected"), 1)

    def test_two_processes_commit_only_one_frozen_append(self) -> None:
        _report, config, _index = self._attach(write_policy="append-create")
        state_dir = VaultRegistry(self.state).vault_state_dir(config)
        route = resolve_route(
            "AI_Agent task: process concurrency",
            scan_taxonomy(self.vault),
            vault_root=self.vault,
        )
        plans = [
            create_capture_plan(
                self.vault,
                state_dir,
                f"process-exclusive-{index}",
                route,
            )
            for index in range(2)
        ]
        processes = [
            subprocess.Popen(
                [
                    sys.executable,
                    str(SCRIPT_DIR / "knowledge_router.py"),
                    "--state-root",
                    str(self.state),
                    "capture",
                    "--vault",
                    "test",
                    "--commit",
                    "--plan-id",
                    plan.plan_id,
                    "--json",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
            )
            for plan in plans
        ]
        results = [process.communicate(timeout=30) for process in processes]
        returncodes = [process.returncode for process in processes]
        self.assertEqual(returncodes.count(0), 1, results)
        self.assertEqual(returncodes.count(2), 1, results)
        content = (self.vault / plans[0].relative_path).read_text(encoding="utf-8")
        occurrences = sum(
            content.count(f"process-exclusive-{index}") for index in range(2)
        )
        self.assertEqual(occurrences, 1)

    def test_append_preserves_original_prefix_and_enforces_cas(self) -> None:
        _report, config, _index = self._attach(write_policy="append-create")
        target = self.vault / "100_Project" / "2603_AI_Agent" / "TASKS.md"
        before = target.read_bytes()
        route = resolve_route(
            "AI_Agent task: add checksum test",
            scan_taxonomy(self.vault),
            vault_root=self.vault,
        )
        state_dir = VaultRegistry(self.state).vault_state_dir(config)
        result = execute_capture(
            self.vault,
            state_dir,
            "- [ ] Add checksum test",
            route,
            expected_sha256=hashlib.sha256(before).hexdigest(),
        )
        after = target.read_bytes()
        self.assertEqual(result.action, "append_existing")
        self.assertTrue(after.startswith(before))
        with self.assertRaisesRegex(RuntimeError, "Compare-and-swap failed"):
            execute_capture(
                self.vault,
                state_dir,
                "must not write",
                route,
                expected_sha256="0" * 64,
            )
        self.assertEqual(target.read_bytes(), after)

    def test_doctor_reports_broken_ambiguous_and_orphan_without_writes(self) -> None:
        _write(self.vault / "links" / "A.md", "[[B]] [[Missing]] [[Same]]\n")
        _write(self.vault / "links" / "B.md", "# B\n")
        _write(self.vault / "one" / "Same.md", "# Same one\n")
        _write(self.vault / "two" / "Same.md", "# Same two\n")
        _write(self.vault / "Orphan.md", "# Alone\n")
        before = _snapshot(self.vault)
        report = diagnose_vault(self.vault)
        self.assertEqual(before, _snapshot(self.vault))
        self.assertTrue(
            any(item["target"] == "Missing" for item in report["broken_links"])
        )
        self.assertTrue(
            any(item["target"] == "Same" for item in report["ambiguous_links"])
        )
        self.assertIn("Orphan.md", report["orphans"])

    def test_doctor_cli_defaults_to_bounded_summary_and_preserves_full_details(
        self,
    ) -> None:
        for number in range(40):
            _write(
                self.vault / "100_Logs" / "daily" / f"2026-08-{number:02d}.md",
                "# Daily\n[[Repeated Missing Target]]\n",
            )
        self._attach(name="doctor-summary")
        base = [
            sys.executable,
            str(SCRIPT_DIR / "knowledge_router.py"),
            "--state-root",
            str(self.state),
            "doctor",
            "--vault",
            str(self.vault),
            "--json",
        ]
        summary_run = subprocess.run(
            base,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(summary_run.returncode, 1)
        summary = json.loads(summary_run.stdout)
        self.assertGreaterEqual(summary["counts"]["broken_link_occurrences"], 40)
        self.assertNotIn("broken_links", summary)
        self.assertLess(len(summary_run.stdout.encode("utf-8")), 20_000)

        details_run = subprocess.run(
            [*base, "--details"],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(details_run.returncode, 1)
        details = json.loads(details_run.stdout)
        self.assertGreaterEqual(len(details["broken_links"]), 40)

    def test_brief_contains_hash_citations_and_evidence_gap(self) -> None:
        _report, config, index = self._attach()
        brief = assemble_decision_context(
            self.vault,
            index,
            "AI_Agent knowledge router tasks",
            vault_id=config.id,
            generation=config.index_generation,
        )
        self.assertEqual(brief["matched_project"], "2603_AI_Agent")
        self.assertFalse(brief["evidence_gap"])
        self.assertTrue(brief["sources"])
        self.assertIn("sha256", brief["markdown"])
        for source in brief["sources"]:
            self.assertTrue(
                {
                    "vault_id",
                    "path",
                    "line_start",
                    "line_end",
                    "source_sha256",
                    "index_generation",
                    "stale",
                }.issubset(source)
            )
        gap = assemble_decision_context(self.vault, index, "xyznonexistentquery123")
        self.assertTrue(gap["evidence_gap"])
        qatar = self.vault / "200_Area" / "234_Qatar" / "Seismic Spec.md"
        qatar.write_text(
            qatar.read_text(encoding="utf-8") + "stale\n", encoding="utf-8"
        )
        stale_only = assemble_decision_context(
            self.vault, index, "offshore 3D seismic acquisition traceability"
        )
        self.assertTrue(stale_only["evidence_gap"])

    def test_one_line_cli_attach_and_preview_are_non_mutating(self) -> None:
        before = _snapshot(self.vault)
        cli = SCRIPT_DIR / "knowledge_router.py"
        completed = subprocess.run(
            [
                sys.executable,
                str(cli),
                "--state-root",
                str(self.state),
                "attach",
                str(self.vault),
                "--name",
                "cli",
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["attachment"]["write_policy"], "off")
        preview = subprocess.run(
            [
                sys.executable,
                str(cli),
                "--state-root",
                str(self.state),
                "capture",
                "灵感：安全路由",
                "--vault",
                "cli",
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(json.loads(preview.stdout)["status"], "preview")
        self.assertEqual(before, _snapshot(self.vault))

        maintenance = subprocess.run(
            [
                sys.executable,
                str(cli),
                "--state-root",
                str(self.state),
                "maintain",
                "--vault",
                "cli",
                "--no-sync",
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertIn("doctor", json.loads(maintenance.stdout))
        self.assertEqual(before, _snapshot(self.vault))

    def test_cli_commits_exactly_the_frozen_plan(self) -> None:
        self._attach(name="writable", write_policy="append-create")
        cli = SCRIPT_DIR / "knowledge_router.py"
        preview = subprocess.run(
            [
                sys.executable,
                str(cli),
                "--state-root",
                str(self.state),
                "capture",
                "灵感：冻结计划 CLI",
                "--vault",
                "writable",
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        plan = json.loads(preview.stdout)["plan"]
        self.assertNotIn("payload_base64", plan)
        commit = subprocess.run(
            [
                sys.executable,
                str(cli),
                "--state-root",
                str(self.state),
                "capture",
                "--vault",
                "writable",
                "--commit",
                "--plan-id",
                plan["plan_id"],
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        result = json.loads(commit.stdout)
        self.assertEqual(result["mutation"]["plan_id"], plan["plan_id"])
        self.assertEqual(result["mutation"]["relative_path"], plan["relative_path"])
        self.assertTrue((self.vault / plan["relative_path"]).is_file())

    def test_maintain_all_is_stably_ordered_audited_and_read_only(self) -> None:
        self._attach(name="first")
        second = self.temp / "SecondMaintain"
        second.mkdir()
        _write(second / "note.md", "# Second\n")
        bootstrap_vault(second, state_root=self.state, name="second")
        before_first = _snapshot(self.vault)
        before_second = _snapshot(second)
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_DIR / "knowledge_router.py"),
                "--state-root",
                str(self.state),
                "maintain",
                "--all",
                "--audit-only",
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        result = json.loads(completed.stdout)
        self.assertEqual(
            [item["vault_id"] for item in result],
            sorted(item["vault_id"] for item in result),
        )
        for item in result:
            self.assertEqual(item["schema_version"], 1)
            self.assertIn("counts", item["doctor"])
            self.assertNotIn("broken_links", item["doctor"])
            self.assertGreaterEqual(item["duration_ms"], 0)
        self.assertEqual(len({item["run_id"] for item in result}), 1)
        registry = VaultRegistry(self.state)
        for config in registry.list():
            audit_path = registry.vault_state_dir(config) / "maintain_audit.jsonl"
            self.assertTrue(audit_path.is_file())
            events = [
                json.loads(line)
                for line in audit_path.read_text(encoding="utf-8").splitlines()
            ]
            self.assertEqual(
                [event["status"] for event in events[-2:]], ["started", "completed"]
            )
            self.assertEqual(len({event["run_id"] for event in events[-2:]}), 1)
            self.assertTrue(all(event["schema_version"] == 1 for event in events[-2:]))
        self.assertEqual(before_first, _snapshot(self.vault))
        self.assertEqual(before_second, _snapshot(second))

    def test_maintain_all_records_failure_and_continues_other_vaults(self) -> None:
        self._attach(name="healthy")
        missing = self.temp / "MissingMaintain"
        missing.mkdir()
        _write(missing / "note.md", "# Gone after attach\n")
        bootstrap_vault(missing, state_root=self.state, name="missing")
        shutil.rmtree(missing)
        before = _snapshot(self.vault)
        completed = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_DIR / "knowledge_router.py"),
                "--state-root",
                str(self.state),
                "maintain",
                "--all",
                "--audit-only",
                "--json",
            ],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(completed.returncode, 2)
        result = json.loads(completed.stdout)
        self.assertEqual(len(result), 2)
        self.assertEqual({item["status"] for item in result}, {"completed", "failed"})
        self.assertEqual(before, _snapshot(self.vault))
        registry = VaultRegistry(self.state)
        missing_config = registry.resolve("missing")
        audit = registry.vault_state_dir(missing_config) / "maintain_audit.jsonl"
        events = [
            json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(events[-1]["status"], "failed")

    def test_legacy_read_only_entrypoints_remain_compatible(self) -> None:
        report, _config, index = self._attach()
        bootstrap = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_DIR / "bootstrap.py"),
                "--vault",
                str(self.vault),
                "--state-root",
                str(self.state),
                "--name",
                "test",
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(json.loads(bootstrap.stdout)["status"], "ready")
        route = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_DIR / "route_engine.py"),
                "resolve",
                "今日总结",
                "--vault",
                str(self.vault),
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertEqual(json.loads(route.stdout)["intent"], "daily_log")
        context = subprocess.run(
            [
                sys.executable,
                str(SCRIPT_DIR / "context_assembler.py"),
                "assemble",
                "Qatar seismic",
                "--vault",
                str(self.vault),
                "--index",
                str(index),
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        self.assertFalse(json.loads(context.stdout)["evidence_gap"])
        self.assertEqual(report["agents_md_created"], False)


if __name__ == "__main__":
    unittest.main()
