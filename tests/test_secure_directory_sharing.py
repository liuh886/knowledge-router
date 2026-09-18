"""Windows reader compatibility without relaxing directory rename protection."""

# ruff: noqa: E402
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import secure_io
from secure_io import SecureParent, secure_read_bytes

pytestmark = pytest.mark.skipif(
    os.name != "nt", reason="Windows kernel sharing contract"
)


def test_reader_handle_does_not_block_secure_read_or_allow_rename(
    tmp_path, monkeypatch
):
    root = tmp_path / "vault"
    directory = root / "project"
    directory.mkdir(parents=True)
    note = directory / "note.md"
    note.write_bytes(b"human original\n")
    original_open = secure_io._windows_open_raw

    # A normal directory reader commonly does not grant FILE_SHARE_DELETE.
    handle = original_open(
        directory,
        access=secure_io._GENERIC_READ,
        share=secure_io._FILE_SHARE_READ | secure_io._FILE_SHARE_WRITE,
        disposition=secure_io._OPEN_EXISTING,
        directory=True,
    )

    def no_retry(path, **kwargs):
        # Regression fails immediately, not after the old 30-second retry.
        kwargs["retry_seconds"] = 0
        return original_open(path, **kwargs)

    monkeypatch.setattr(secure_io, "_windows_open_raw", no_retry)
    try:
        with pytest.raises(OSError):
            original_open(
                directory,
                access=secure_io._FILE_READ_ATTRIBUTES | secure_io._DELETE,
                share=secure_io._FILE_SHARE_READ | secure_io._FILE_SHARE_WRITE,
                disposition=secure_io._OPEN_EXISTING,
                directory=True,
            )  # Previous implementation conflicts with the same normal reader.
        assert secure_read_bytes(root, "project/note.md") == b"human original\n"
    finally:
        secure_io._close_handle(handle)

    # Now our own guard alone must prevent rename/move of each pinned ancestor.
    with SecureParent(root, "project/note.md"):
        assert secure_read_bytes(root, "project/note.md") == b"human original\n"
        with pytest.raises(OSError):
            directory.rename(root / "moved")
        with pytest.raises(OSError):
            root.rename(tmp_path / "moved-vault")
    directory.rename(root / "moved")  # Protection ends when the guard closes.
    assert (root / "moved/note.md").read_bytes() == b"human original\n"


def test_existing_delete_capability_is_still_rejected(tmp_path, monkeypatch):
    directory = tmp_path / "project"
    directory.mkdir()
    (directory / "note.md").write_bytes(b"human original")
    original_open = secure_io._windows_open_raw
    # An existing handle with DELETE access must conflict with our deny-delete pin.
    handle = original_open(
        directory,
        access=secure_io._FILE_READ_ATTRIBUTES | secure_io._DELETE,
        share=secure_io._FILE_SHARE_READ | secure_io._FILE_SHARE_WRITE | 4,
        disposition=secure_io._OPEN_EXISTING,
        directory=True,
    )

    def no_retry(path, **kwargs):
        kwargs["retry_seconds"] = 0
        return original_open(path, **kwargs)

    monkeypatch.setattr(secure_io, "_windows_open_raw", no_retry)
    try:
        with pytest.raises(OSError):
            secure_read_bytes(tmp_path, "project/note.md")
    finally:
        secure_io._close_handle(handle)
    assert secure_read_bytes(tmp_path, "project/note.md") == b"human original"
