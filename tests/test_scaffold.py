#!/usr/bin/env python3
"""Tests for the Symbiotic Memory (共生记忆) scaffold generator."""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
sys.path.insert(0, str(SCRIPTS_DIR))

from registry import VaultRegistry
from scaffold import scaffold_symbiotic_vault


class TestScaffold(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = Path(tempfile.mkdtemp(prefix="scaffold-test-"))
        self.vault = self.temp / "Vault"
        self.state = self.temp / "State"

    def tearDown(self) -> None:
        import shutil

        shutil.rmtree(self.temp, ignore_errors=True)

    def test_scaffold_creates_symbiotic_slices(self) -> None:
        year = datetime.now().strftime("%Y")
        res = scaffold_symbiotic_vault(self.vault, attach=False)

        self.assertEqual(res["status"], "scaffolded")
        self.assertTrue((self.vault / "000_Inbox" / "FOCUS.md").is_file())
        self.assertTrue((self.vault / "000_Inbox" / f"idea.{year}.md").is_file())
        self.assertTrue((self.vault / "000_Inbox" / f"memo.{year}.md").is_file())
        self.assertTrue((self.vault / "000_Inbox" / f"todo.{year}.md").is_file())
        self.assertTrue((self.vault / "000_Inbox" / "README.md").is_file())
        self.assertTrue((self.vault / "300_Resources" / "305_Contact" / "_Template.md").is_file())
        self.assertTrue((self.vault / "WYQD" / "Objects").is_dir())
        self.assertTrue((self.vault / "WYQD" / "Trips").is_dir())
        self.assertTrue((self.vault / "WYQD" / "Accounts").is_dir())

        # Check FOCUS.md content
        focus = (self.vault / "000_Inbox" / "FOCUS.md").read_text(encoding="utf-8")
        self.assertIn("Active Focus", focus)
        self.assertIn("North Star", focus)
        self.assertIn("Active Battlegrounds", focus)
        self.assertTrue(focus.startswith("# "))
        self.assertIn("\n## ", focus)
        idea = (self.vault / "000_Inbox" / f"idea.{year}.md").read_text(encoding="utf-8")
        self.assertTrue(idea.startswith("---\ntitle:"))
        self.assertIn("\n---\n\n# ", idea)

    def test_scaffold_non_destructive_on_existing_files(self) -> None:
        (self.vault / "000_Inbox").mkdir(parents=True)
        custom_focus = "# Custom Focus\nMy existing goals.\n"
        (self.vault / "000_Inbox" / "FOCUS.md").write_text(custom_focus, encoding="utf-8")

        res = scaffold_symbiotic_vault(self.vault, attach=False)
        self.assertIn(str(self.vault / "000_Inbox" / "FOCUS.md"), res["skipped_files"])

        # Check custom content was preserved
        actual = (self.vault / "000_Inbox" / "FOCUS.md").read_text(encoding="utf-8")
        self.assertEqual(actual, custom_focus)

    def test_scaffold_with_attach(self) -> None:
        res = scaffold_symbiotic_vault(
            self.vault,
            name="test-symbiotic",
            attach=True,
            state_root=self.state,
        )

        self.assertIn("attach", res)
        self.assertEqual(res["vault_name"], "test-symbiotic")
        self.assertTrue(Path(res["attach"]["index_db_path"]).is_file())

        registry = VaultRegistry(self.state)
        resolved = registry.resolve("test-symbiotic")
        self.assertEqual(resolved.root_path, self.vault.resolve())
        self.assertEqual(resolved.write_policy, "off")

    def test_scaffold_refuses_linked_directory(self) -> None:
        self.vault.mkdir()
        outside = self.temp / "outside"
        outside.mkdir()
        sentinel = outside / "sentinel.md"
        sentinel.write_bytes(b"keep")
        try:
            (self.vault / "000_Inbox").symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Symlink creation unavailable")
        with self.assertRaises(ValueError):
            scaffold_symbiotic_vault(self.vault, attach=False)
        self.assertEqual(list(outside.iterdir()), [sentinel])
        self.assertEqual(sentinel.read_bytes(), b"keep")

    def test_cli_scaffold(self) -> None:
        cli = SCRIPTS_DIR / "knowledge_router.py"
        target_vault = self.temp / "CliVault"

        cmd = [
            sys.executable,
            str(cli),
            "--state-root",
            str(self.state),
            "scaffold",
            str(target_vault),
            "--name",
            "cli-vault",
            "--json",
        ]

        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        self.assertEqual(proc.returncode, 0, f"Stderr: {proc.stderr}")

        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "scaffolded")
        self.assertEqual(data["vault_name"], "cli-vault")
        self.assertTrue((target_vault / "000_Inbox" / "FOCUS.md").is_file())


if __name__ == "__main__":
    unittest.main()
