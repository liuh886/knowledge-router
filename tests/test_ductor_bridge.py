"""Ductor bridge argument integrity, installation detection and timeout behavior."""

import importlib.util
import subprocess
from pathlib import Path
from unittest.mock import patch


def load_bridge():
    path = (
        Path(__file__).resolve().parent.parent
        / "integrations"
        / "ductor"
        / "knowledge_router.py"
    )
    spec = importlib.util.spec_from_file_location("router_ductor_bridge", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_bridge_forwards_arguments_without_shell(tmp_path, monkeypatch):
    module = load_bridge()
    monkeypatch.delenv("KNOWLEDGE_ROUTER_ENTRYPOINT", raising=False)
    entry = tmp_path / ".codex/skills/knowledge-router/scripts/knowledge_router.py"
    entry.parent.mkdir(parents=True)
    entry.write_text("", encoding="utf-8")
    with (
        patch.object(module.Path, "home", return_value=tmp_path),
        patch.object(module.subprocess, "run") as run,
    ):
        run.return_value.returncode = 17
        query = "literal; $(not a command)"
        assert module.main(["search", query, "--json"]) == 17
        assert run.call_args.args[0] == [
            module.sys.executable,
            str(entry),
            "search",
            query,
            "--json",
        ]
        assert "shell" not in run.call_args.kwargs
        assert run.call_args.kwargs["cwd"] == entry.parent


def test_missing_router_and_check_never_run_legacy(tmp_path, capsys, monkeypatch):
    module = load_bridge()
    monkeypatch.delenv("KNOWLEDGE_ROUTER_ENTRYPOINT", raising=False)
    with (
        patch.object(module.Path, "home", return_value=tmp_path),
        patch.object(module.subprocess, "run") as run,
    ):
        assert module.main(["--bridge-check"]) == 2
        assert module.main(["search", "anything"]) == 2
        run.assert_not_called()
    assert "not installed" in capsys.readouterr().err


def test_explicit_entrypoint_override_is_honored(tmp_path, monkeypatch):
    module = load_bridge()
    entry = tmp_path / "custom" / "knowledge_router.py"
    entry.parent.mkdir(parents=True)
    entry.write_text("", encoding="utf-8")
    monkeypatch.setenv("KNOWLEDGE_ROUTER_ENTRYPOINT", str(entry))
    with patch.object(module.subprocess, "run") as run:
        run.return_value.returncode = 0
        assert module.main(["status", "--vault", "personal"]) == 0
        assert run.call_args.args[0][1] == str(entry)


def test_relative_override_is_rejected(tmp_path, monkeypatch):
    module = load_bridge()
    monkeypatch.setenv("KNOWLEDGE_ROUTER_ENTRYPOINT", "relative/knowledge_router.py")
    with patch.object(module.subprocess, "run") as run:
        assert module.main(["search", "anything"]) == 2
        run.assert_not_called()


def test_timeout_is_failure_not_empty_evidence(tmp_path, monkeypatch):
    module = load_bridge()
    monkeypatch.delenv("KNOWLEDGE_ROUTER_ENTRYPOINT", raising=False)
    entry = tmp_path / ".codex/skills/knowledge-router/scripts/knowledge_router.py"
    entry.parent.mkdir(parents=True)
    entry.write_text("", encoding="utf-8")
    with (
        patch.object(module.Path, "home", return_value=tmp_path),
        patch.object(
            module.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired("router", 120),
        ),
    ):
        assert module.main(["contexts", "commit", "event-id"]) == 2
