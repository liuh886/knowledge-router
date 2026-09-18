#!/usr/bin/env python3
# ruff: noqa: E402
"""Read-only project catalog, federated evidence, and quality-signal tests."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

from bootstrap import bootstrap_vault
from context_assembler import assemble_decision_context
from context_brief import brief_context, format_context_brief
from context_events import preview_event
from context_store import register_contexts
from fts_indexer import scan_notes
from project_adapter import list_projects, search_projects, sync_project_catalog
from quality_analyzer import (
    project_quality_signals,
    quality_report,
    vault_quality_signals,
)
from registry import VaultRegistry
from vault_doctor import diagnose_vault


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="")


def _snapshot(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


class TestProjectIntegration(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="knowledge-router-project-test-"))
        self.vault = self.temp / "Vault"
        self.state = self.temp / "state"
        self.projects = self.temp / "GitHub"
        self.repo = self.projects / "alpha"
        self.repo.mkdir(parents=True)
        _write(
            self.vault / "notes.md",
            "# Project memory\nOrbital telemetry uses the historical amber protocol.\n",
        )
        _write(
            self.repo / "README.md",
            "# Alpha Router\nOrbital telemetry uses the current cobalt protocol.\n",
        )
        _write(
            self.repo / "TASKS.md",
            "# Tasks\n- [ ] Validate the cobalt protocol adapter.\n",
        )
        _write(
            self.repo / "docs" / "operations.md",
            "# Operations\nCobalt deployments require a signed manifest.\n",
        )
        subprocess.run(["git", "init", "-q", str(self.repo)], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "config", "user.email", "test@example.com"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.repo), "config", "user.name", "Knowledge Test"],
            check=True,
        )
        subprocess.run(["git", "-C", str(self.repo), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(self.repo), "commit", "-qm", "initial"], check=True
        )
        bootstrap_vault(self.vault, state_root=self.state, name="personal")
        self.registry = VaultRegistry(self.state)
        self.config = self.registry.resolve("personal")

    def tearDown(self) -> None:
        shutil.rmtree(self.temp, ignore_errors=True)

    def _attach_projects(self):
        config = self.registry.attach_projects(self.config.id, self.projects)
        result = sync_project_catalog(self.registry, config)
        return result, self.registry.resolve(self.config.id)

    def test_named_context_explicit_project_cli_is_read_only_and_scoped(self) -> None:
        other = self.projects / "unrelated"
        _write(
            other / "README.md", "# Orbital telemetry\nUnrelated private scenario.\n"
        )
        subprocess.run(["git", "init", "-q", str(other)], check=True)
        _, config = self._attach_projects()
        register_contexts(
            self.registry,
            config,
            {
                "schema_version": 1,
                "contexts": [
                    {
                        "id": "subject",
                        "type": "research",
                        "name": "Telemetry",
                        "purpose": "study",
                        "scopes": ["notes.md"],
                    },
                ],
            },
        )
        identity = next(
            p["project_id"]
            for p in list_projects(self.registry, config)
            if Path(p["root"]) == self.repo
        )
        before = (_snapshot(self.vault), _snapshot(self.projects))
        plain = brief_context(
            self.registry, config, "subject", query="Orbital telemetry"
        )
        self.assertNotIn("project_evidence", plain)
        command = [
            sys.executable,
            str(SCRIPT_DIR / "knowledge_router.py"),
            "--state-root",
            str(self.state),
            "contexts",
            "brief",
            "Telemetry",
            "--query",
            "Orbital telemetry",
            "--project",
            identity,
            "--limit",
            "1",
            "--json",
        ]
        completed = subprocess.run(
            command, capture_output=True, encoding="utf-8", timeout=30
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        result = json.loads(completed.stdout)
        self.assertEqual(result["evidence"][0]["path"], "notes.md")
        self.assertEqual(len(result["project_evidence"]), 1)
        item = result["project_evidence"][0]
        self.assertEqual(item["project_id"], identity)
        self.assertEqual(Path(item["metadata"]["repo_root"]), self.repo)
        self.assertNotIn("Unrelated private", item["excerpt"])
        self.assertEqual(
            item["content_hash"],
            hashlib.sha256((self.repo / item["path"]).read_bytes()).hexdigest(),
        )
        self.assertTrue(item["revision"])
        self.assertFalse(item["stale"])
        self.assertFalse(result["project_selection"]["persisted_relationship"])
        self.assertFalse(result["combined_evidence_gap"])
        self.assertEqual(before, (_snapshot(self.vault), _snapshot(self.projects)))

    def test_context_project_gaps_unknown_ids_and_staleness_are_explicit(self) -> None:
        _, config = self._attach_projects()
        register_contexts(
            self.registry,
            config,
            {
                "schema_version": 1,
                "contexts": [
                    {
                        "id": "subject",
                        "type": "project",
                        "name": "Alpha",
                        "purpose": "test",
                        "scopes": ["notes.md"],
                    },
                ],
            },
        )
        identity = list_projects(self.registry, config)[0]["project_id"]
        with self.assertRaisesRegex(ValueError, "explicit project id and query"):
            brief_context(self.registry, config, "subject", project_id=identity)
        with self.assertRaisesRegex(ValueError, "Unknown project id"):
            brief_context(
                self.registry, config, "subject", query="cobalt", project_id="unknown"
            )
        result = brief_context(
            self.registry, config, "subject", query="cobalt", project_id=identity
        )
        self.assertTrue(
            result["evidence_gap"]
        )  # Repository evidence cannot fill Vault state.
        self.assertFalse(result["combined_evidence_gap"])
        _write(self.repo / "README.md", "# Changed\nManual edit after indexing\n")
        result = brief_context(
            self.registry,
            config,
            "subject",
            query="Orbital telemetry",
            project_id=identity,
        )
        self.assertTrue(result["project_evidence"][0]["stale"])
        self.assertIn("来源已变化或不可读取", format_context_brief(result))
        self.assertIn("revision=", format_context_brief(result))
        gap = brief_context(
            self.registry, config, "subject", query="unobtainium", project_id=identity
        )
        self.assertTrue(gap["project_evidence_gap"])
        self.assertTrue(gap["combined_evidence_gap"])

    def test_reviewed_context_project_binding_is_versioned_and_reused(self) -> None:
        _result, config = self._attach_projects()
        identity = list_projects(self.registry, config)[0]["project_id"]
        register_contexts(
            self.registry,
            config,
            {
                "schema_version": 1,
                "contexts": [
                    {
                        "id": "subject",
                        "type": "project",
                        "name": "Telemetry",
                        "purpose": "project progress",
                        "scopes": ["notes.md"],
                        "project_ids": [identity],
                    },
                ],
            },
        )
        brief = brief_context(
            self.registry, config, "subject", query="cobalt protocol"
        )
        self.assertEqual(
            brief["project_selection"],
            {
                "project_ids": [identity],
                "project_id": identity,
                "origin": "reviewed_binding",
                "persisted_relationship": True,
            },
        )
        self.assertEqual(brief["project_evidence"][0]["project_id"], identity)
        self.assertFalse(brief["project_evidence"][0]["stale"])

        preview = preview_event(
            self.registry,
            config,
            "Router progress from the bound project",
            target="notes.md",
            primary_context="subject",
            context_ids=[],
            idempotency_key="bound-project-preview",
        )
        self.assertIn(preview["status"], {"preview", "preview_or_interrupted"})
        self.assertEqual([item["id"] for item in preview["context_links"]], ["subject"])

        with self.assertRaisesRegex(ValueError, "Unknown reviewed project id"):
            register_contexts(
                self.registry,
                config,
                {
                    "schema_version": 1,
                    "contexts": [
                        {
                            "id": "bad",
                            "type": "project",
                            "name": "Bad",
                            "purpose": "test",
                            "scopes": ["notes.md"],
                            "project_ids": ["does-not-exist"],
                        },
                    ],
                },
                expected_revision=1,
            )

    def test_project_catalog_is_external_content_addressed_and_read_only(self) -> None:
        before = _snapshot(self.projects)
        result, config = self._attach_projects()
        self.assertEqual(before, _snapshot(self.projects))
        self.assertEqual(result["project_count"], 1)
        self.assertEqual(result["document_count"], 3)
        self.assertEqual(len(result["generation"]), 64)
        self.assertNotIn(str(self.projects), str(self.registry.vault_state_dir(config)))
        repeated = sync_project_catalog(self.registry, config)
        self.assertTrue(repeated["reused"])
        self.assertEqual(before, _snapshot(self.projects))

    def test_one_line_attach_can_bind_the_project_library(self) -> None:
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
                "personal",
                "--projects-root",
                str(self.projects),
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        payload = json.loads(completed.stdout)
        self.assertEqual(payload["projects"]["project_count"], 1)
        self.assertEqual(
            self.registry.resolve("personal").project_root, str(self.projects.resolve())
        )

    def test_repository_search_returns_resolvable_evidence_and_staleness(self) -> None:
        _result, config = self._attach_projects()
        hit = search_projects(self.registry, config, "cobalt protocol adapter")[0]
        self.assertEqual(hit.source_type, "repository")
        self.assertEqual(hit.project_id, "alpha")
        self.assertEqual(hit.path, "TASKS.md")
        self.assertEqual(len(hit.content_hash), 64)
        self.assertFalse(hit.stale)
        _write(
            self.repo / "TASKS.md",
            "# Tasks\n- [x] Validate the cobalt protocol adapter.\n",
        )
        stale = search_projects(self.registry, config, "cobalt protocol adapter")[0]
        self.assertTrue(stale.stale)

    def test_federated_cli_and_brief_use_one_evidence_shape(self) -> None:
        _result, config = self._attach_projects()
        cli = SCRIPT_DIR / "knowledge_router.py"
        completed = subprocess.run(
            [
                sys.executable,
                str(cli),
                "--state-root",
                str(self.state),
                "search",
                "orbital telemetry protocol",
                "--vault",
                "personal",
                "--projects",
                "--json",
            ],
            check=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
        )
        evidence = json.loads(completed.stdout)
        self.assertEqual(
            {item["source_type"] for item in evidence}, {"vault", "repository"}
        )
        for item in evidence:
            self.assertTrue(
                {
                    "source_type",
                    "source_id",
                    "path",
                    "line_start",
                    "line_end",
                    "content_hash",
                    "revision",
                    "stale",
                }.issubset(item)
            )
        repository = search_projects(
            self.registry, config, "orbital telemetry protocol", limit=3
        )
        brief = assemble_decision_context(
            self.vault,
            self.registry.vault_state_dir(config) / config.active_index,
            "orbital telemetry protocol",
            vault_id=config.id,
            generation=config.index_generation,
            repository_evidence=repository,
        )
        self.assertIn("Repository context", brief["markdown"])
        self.assertTrue(
            any(item["source_type"] == "repository" for item in brief["sources"])
        )

    def test_quality_signals_are_explainable_and_bounded(self) -> None:
        _write(
            self.vault / "duplicate.md", (self.vault / "notes.md").read_text("utf-8")
        )
        bootstrap_vault(self.vault, state_root=self.state, name="personal")
        _result, config = self._attach_projects()
        scan = scan_notes(self.vault)
        doctor = diagnose_vault(self.vault, source_scan=scan)
        signals = vault_quality_signals(config.id, doctor, scan)
        signals.extend(project_quality_signals(list_projects(self.registry, config)))
        report = quality_report(signals, detail_limit=2)
        self.assertIsNone(report["aggregate_score"])
        self.assertIn("duplicate_content", report["counts_by_kind"])
        self.assertIn("project_metadata_gap", report["counts_by_kind"])
        self.assertLessEqual(len(report["signals"]), 2)
        self.assertGreater(report["truncated"], 0)

    def test_catalog_tampering_fails_closed(self) -> None:
        _result, config = self._attach_projects()
        catalog = self.registry.vault_state_dir(config) / config.project_catalog
        with sqlite3.connect(catalog) as connection:
            connection.execute("UPDATE documents SET checksum=?", ("0" * 64,))
        with self.assertRaisesRegex(RuntimeError, "do not match"):
            search_projects(self.registry, config, "cobalt protocol")


if __name__ == "__main__":
    unittest.main()
