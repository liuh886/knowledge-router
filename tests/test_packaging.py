"""Test package metadata and entry points for pip installation."""

from __future__ import annotations

import shutil
import subprocess
import sys


def test_package_import_and_metadata():
    import knowledge_router

    assert hasattr(knowledge_router, "__version__")
    assert knowledge_router.__version__ == "0.2.0"
    assert callable(knowledge_router.main)


def test_isolated_package_import():
    cmd = [
        sys.executable,
        "-c",
        "import sys; before = list(sys.path); import knowledge_router; "
        "assert knowledge_router.__version__ == '0.2.0'; assert callable(knowledge_router.main); "
        "assert sys.path == before",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=False)
    assert result.returncode == 0


def test_module_execution():
    result = subprocess.run(
        [sys.executable, "-m", "knowledge_router", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "One-command" in result.stdout
    assert "scaffold" in result.stdout


def test_distribution_declares_actual_python_baseline():
    import tomllib
    from pathlib import Path

    data = tomllib.loads(
        (Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert data["project"]["requires-python"] == ">=3.11"


def test_installed_cli_executable():
    kr_bin = shutil.which("kr")
    if not kr_bin:
        # In isolated environments where PATH hasn't been updated, skip binary check
        return

    result = subprocess.run(
        [kr_bin, "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    assert "One-command" in result.stdout
