"""Document operations preserve the old append boundary and make edits explicit."""

import base64
import hashlib
import json
import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
import capture_broker as broker
from registry import VaultRegistry


@pytest.fixture
def documents(tmp_path):
    vault = tmp_path / "Vault"
    vault.mkdir()
    registry = VaultRegistry(tmp_path / "State")
    config = registry.attach(vault, name="test", write_policy="append-create")
    return registry, config, registry.vault_state_dir(config)


def plan_for(documents, before, after):
    registry, config, state = documents
    path = config.root_path / "note.md"
    if before is not None:
        path.write_bytes(before)
    plan = broker.create_document_plan(
        config.root_path,
        state,
        "note.md",
        after,
        expected_sha256=hashlib.sha256(before).hexdigest() if before is not None else None,
    )
    return path, plan


def test_document_append_and_create_replay_exact_bytes(documents):
    registry, config, state = documents
    path, plan = plan_for(documents, None, b"\xef\xbb\xbf# Note\r\n")
    assert plan.action == "create_new"
    broker.commit_capture_plan(config.root_path, state, plan)
    before = path.read_bytes()
    append = broker.create_document_plan(
        config.root_path,
        state,
        "note.md",
        before + b"next\r\n",
        expected_sha256=hashlib.sha256(before).hexdigest(),
    )
    assert append.action == "append_existing"
    broker.commit_capture_plan(config.root_path, state, append)
    broker.commit_capture_plan(config.root_path, state, append.plan_id)
    assert path.read_bytes() == before + b"next\r\n"


def test_document_requires_registration_policy_and_exact_basis(documents):
    registry, config, state = documents
    path, plan = plan_for(documents, b"original\n", b"original\nnew\n")
    registry.attach(config.root_path, name=config.name, write_policy="off")
    with pytest.raises(PermissionError):
        broker.commit_capture_plan(config.root_path, state, plan)
    assert path.read_bytes() == b"original\n"
    with pytest.raises(RuntimeError, match="Compare-and-swap"):
        broker.create_document_plan(
            config.root_path, state, "note.md", b"new", expected_sha256=None
        )


def test_full_edit_is_not_authorized_by_append_create(documents):
    registry, config, state = documents
    path, plan = plan_for(documents, b"old long value\n", b"new\n")
    assert plan.action == "replace_existing"
    assert base64.b64decode(plan.original_base64) == path.read_bytes()
    with pytest.raises(PermissionError, match="reviewed-edit"):
        broker.commit_capture_plan(config.root_path, state, plan)
    assert path.read_bytes() == b"old long value\n"


@pytest.mark.skipif(os.name != "nt", reason="Windows exclusive editing")
def test_edit_cas_and_writer_exclusion(documents):
    registry, config, state = documents
    registry.attach(config.root_path, name=config.name, write_policy="reviewed-edit")
    path, plan = plan_for(documents, b"old long value\n", b"new\n")
    path.write_bytes(b"human changed the note\n")
    with pytest.raises(RuntimeError, match="Compare-and-swap"):
        broker.commit_capture_plan(config.root_path, state, plan)
    assert path.read_bytes() == b"human changed the note\n"
    after = b"\xef\xbb\xbfnew\r\n"
    edit = broker.create_document_plan(
        config.root_path,
        state,
        "note.md",
        after,
        expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    with path.open("a+b"):
        with pytest.raises(PermissionError):
            broker.commit_capture_plan(config.root_path, state, edit)
    # The refused attempt is journalled for review, not silently reapplied.
    fresh = broker.create_document_plan(
        config.root_path,
        state,
        "note.md",
        after,
        expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
    )
    broker.commit_capture_plan(config.root_path, state, fresh)
    assert path.read_bytes() == after


@pytest.mark.skipif(os.name != "nt", reason="Windows exclusive editing")
def test_edit_short_write_keeps_recovery_original_and_refuses_retry(documents):
    registry, config, state = documents
    registry.attach(config.root_path, name=config.name, write_policy="reviewed-edit")
    before, after = b"original contents\r\n", b"replacement contents\r\n"
    path, plan = plan_for(documents, before, after)
    real_write = os.write

    def short_write(fd, data):
        return real_write(fd, data[:3]) if data == after else real_write(fd, data)

    with patch.object(broker.os, "write", side_effect=short_write):
        with pytest.raises(broker.ShortWriteError):
            broker.commit_capture_plan(config.root_path, state, plan)
    damaged = path.read_bytes()
    assert damaged != after
    assert base64.b64decode(broker.load_capture_plan(state, plan.plan_id).original_base64) == before
    with pytest.raises(RuntimeError, match="NEEDS_RECONCILIATION"):
        broker.commit_capture_plan(config.root_path, state, plan)
    assert path.read_bytes() == damaged


@pytest.mark.skipif(os.name != "nt", reason="Windows exclusive editing")
def test_interruption_after_edit_reconciles_without_second_write(documents):
    registry, config, state = documents
    registry.attach(config.root_path, name=config.name, write_policy="reviewed-edit")
    path, plan = plan_for(documents, b"old\n", b"new\n")
    real_mark = broker._mark_status

    def interrupted(*args, **kwargs):
        if kwargs.get("status") == "COMMITTED":
            raise RuntimeError("simulated process interruption")
        return real_mark(*args, **kwargs)

    with patch.object(broker, "_mark_status", side_effect=interrupted):
        with pytest.raises(RuntimeError, match="interruption"):
            broker.commit_capture_plan(config.root_path, state, plan)
    assert path.read_bytes() == b"new\n"
    result = broker.commit_capture_plan(config.root_path, state, plan)
    assert result.status == "idempotent_replay"


def test_historical_schema_two_plan_retains_checksum_and_replays(documents):
    registry, config, state = documents
    path, plan = plan_for(documents, None, b"original v2\n")
    raw = plan.to_dict()
    raw["schema_version"] = 2
    raw.pop("original_base64")
    raw.pop("plan_sha256")
    raw["plan_sha256"] = hashlib.sha256(
        json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    (state / f"plan.{plan.plan_id}.json").write_text(json.dumps(raw), encoding="utf-8")
    old = broker.load_capture_plan(state, plan.plan_id)
    assert old.schema_version == 2
    broker.commit_capture_plan(config.root_path, state, old)
    assert path.read_bytes() == b"original v2\n"


@pytest.mark.skipif(os.name != "nt", reason="Windows exclusive editing")
def test_policy_revoked_while_waiting_for_broker_lock_is_rechecked(documents):
    registry, config, state = documents
    path, plan = plan_for(documents, b"original\n", b"original\nnext\n")
    real_lock = broker.FileLock

    class RevokeBeforeLock:
        def __init__(self, *args, **kwargs):
            self.lock = real_lock(*args, **kwargs)

        def __enter__(self):
            registry.attach(config.root_path, name=config.name, write_policy="off")
            return self.lock.__enter__()

        def __exit__(self, *args):
            return self.lock.__exit__(*args)

    with patch.object(broker, "FileLock", RevokeBeforeLock):
        with pytest.raises(PermissionError, match="changed before commit"):
            broker.commit_capture_plan(config.root_path, state, plan)
    assert path.read_bytes() == b"original\n"
