"""Full-coverage scan equivalence and bounded parent-handle reuse."""

# ruff: noqa: E402
import hashlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import fts_indexer
from secure_io import SecureParent, secure_read_bytes


def test_scan_reuses_each_parent_and_preserves_all_source_bytes(tmp_path, monkeypatch):
    sources = {
        "a.md": b"# A\n",
        "B.md": b"# B\r\n",
        "d/x.md": b"# X\n",
        "d/Y.md": b"\xef\xbb\xbf# Y\r",
        "d/sub/z.md": "# 汉字\n".encode(),
    }
    for relative, raw in {
        **sources,
        "d/invalid.md": b"\xff\xfe",
        ".hidden/no.md": b"skip",
    }.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    expected_paths = [
        p.relative_to(tmp_path).as_posix()
        for p in sorted(tmp_path.rglob("*.md"))
        if p.relative_to(tmp_path).as_posix() in sources
    ]
    entered = []
    original = SecureParent.__enter__

    def count(parent):
        entered.append(parent.parent)
        return original(parent)

    monkeypatch.setattr(SecureParent, "__enter__", count)
    scan = fts_indexer.scan_notes(tmp_path)
    assert [r.path for r in scan.records] == expected_paths
    assert sorted(entered) == sorted({tmp_path, tmp_path / "d", tmp_path / "d/sub"})
    assert scan.skipped_decode == ("d/invalid.md",)
    assert not scan.skipped_io
    for record in scan.records:
        assert record.content == sources[record.path].decode("utf-8-sig")
        assert record.checksum == hashlib.sha256(sources[record.path]).hexdigest()
        assert record.size == len(sources[record.path])
    assert all((tmp_path / path).read_bytes() == raw for path, raw in sources.items())


def test_one_read_failure_does_not_hide_sibling_or_other_directory(
    tmp_path, monkeypatch
):
    for relative in ("a.md", "b.md", "other/c.md"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Original\n", encoding="utf-8")
    original = SecureParent.read_bytes

    def fail_one(parent, *, name=None):
        if name == "a.md":
            raise PermissionError("simulated source busy")
        return original(parent, name=name)

    monkeypatch.setattr(SecureParent, "read_bytes", fail_one)
    scan = fts_indexer.scan_notes(tmp_path)
    assert scan.skipped_io == ("a.md",)
    assert {r.path for r in scan.records} == {"b.md", "other/c.md"}


def test_sibling_reads_require_live_guard_and_reject_escape(tmp_path):
    (tmp_path / "a.md").write_bytes(b"original")
    parent = SecureParent(tmp_path, "a.md")
    with pytest.raises(RuntimeError, match="active directory guard"):
        parent.read_bytes()
    with parent:
        with pytest.raises(ValueError, match="Unsafe sibling"):
            parent.read_bytes(name="../a.md")
        assert parent.read_bytes(name="a.md") == b"original"
    with pytest.raises(RuntimeError, match="active directory guard"):
        parent.read_bytes()
    assert secure_read_bytes(tmp_path, "a.md") == b"original"
