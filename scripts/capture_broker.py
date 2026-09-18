#!/usr/bin/env python3
"""Frozen capture plans and crash-reconcilable append/create-only mutations."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Protocol

from registry import FileLock, VaultConfig, VaultRegistry
from secure_io import SecureParent, secure_read_bytes

_PLAN_SCHEMA = 2
_PLAN_ID = re.compile(r"^[a-f0-9]{32}$")
_IDEMPOTENCY_KEY = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")


class RoutingLike(Protocol):
    action: str
    relative_target_path: str
    target_filename: str
    suggested_frontmatter: dict[str, Any]
    suggested_wikilinks: list[str]


@dataclass(frozen=True, slots=True)
class CapturePlan:
    schema_version: int
    plan_id: str
    vault_id: str
    action: str
    relative_path: str
    payload_base64: str
    payload_sha256: str
    content_sha256: str
    expected_sha256: str | None
    before_size: int
    encoding: str
    bom: str
    newline: str
    idempotency_key: str
    request_fingerprint: str
    local_timestamp: str
    timezone_offset: str
    created_at: str
    expires_at: str
    plan_sha256: str

    @property
    def payload(self) -> bytes:
        return base64.b64decode(self.payload_base64, validate=True)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> CapturePlan:
        plan = cls(
            schema_version=int(raw["schema_version"]),
            plan_id=str(raw["plan_id"]),
            vault_id=str(raw["vault_id"]),
            action=str(raw["action"]),
            relative_path=str(raw["relative_path"]),
            payload_base64=str(raw["payload_base64"]),
            payload_sha256=str(raw["payload_sha256"]),
            content_sha256=str(raw["content_sha256"]),
            expected_sha256=(
                str(raw["expected_sha256"])
                if raw.get("expected_sha256") is not None
                else None
            ),
            before_size=int(raw["before_size"]),
            encoding=str(raw["encoding"]),
            bom=str(raw["bom"]),
            newline=str(raw["newline"]),
            idempotency_key=str(raw["idempotency_key"]),
            request_fingerprint=str(raw["request_fingerprint"]),
            local_timestamp=str(raw["local_timestamp"]),
            timezone_offset=str(raw["timezone_offset"]),
            created_at=str(raw["created_at"]),
            expires_at=str(raw["expires_at"]),
            plan_sha256=str(raw["plan_sha256"]),
        )
        _validate_plan(plan, check_expiry=False)
        return plan


@dataclass(frozen=True, slots=True)
class MutationResult:
    status: str
    plan_id: str
    action: str
    relative_path: str
    before_sha256: str | None
    after_sha256: str
    idempotency_key: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class ShortWriteError(RuntimeError):
    pass


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _canonical_hash(payload: dict[str, Any]) -> str:
    return _sha256(
        json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    )


def _plan_hash(plan: CapturePlan) -> str:
    raw = plan.to_dict()
    raw.pop("plan_sha256", None)
    return _canonical_hash(raw)


def _validate_plan(plan: CapturePlan, *, check_expiry: bool = True) -> None:
    if plan.schema_version != _PLAN_SCHEMA:
        raise RuntimeError("Unsupported capture plan schema")
    if not _PLAN_ID.fullmatch(plan.plan_id):
        raise RuntimeError("Invalid capture plan id")
    if plan.action not in {"append_existing", "create_new"}:
        raise RuntimeError("Invalid capture plan action")
    try:
        payload = plan.payload
    except (ValueError, TypeError) as exc:
        raise RuntimeError("Invalid capture plan payload") from exc
    if _sha256(payload) != plan.payload_sha256:
        raise RuntimeError("Capture plan payload checksum mismatch")
    request = {
        "vault_id": plan.vault_id,
        "action": plan.action,
        "relative_path": plan.relative_path,
        "payload_sha256": plan.payload_sha256,
        "expected_sha256": plan.expected_sha256,
    }
    if _canonical_hash(request) != plan.request_fingerprint:
        raise RuntimeError(
            "Capture plan was modified after preview (request fingerprint mismatch)"
        )
    if _plan_hash(plan) != plan.plan_sha256:
        raise RuntimeError("Capture plan was modified after preview")
    try:
        expires = datetime.fromisoformat(plan.expires_at)
    except ValueError as exc:
        raise RuntimeError("Invalid capture plan expiry") from exc
    if expires.tzinfo is None:
        raise RuntimeError("Capture plan expiry must include a timezone")
    if check_expiry and datetime.now(UTC) > expires.astimezone(UTC):
        raise RuntimeError("Capture plan has expired; create a new preview")


def _registered_context(
    vault_root: Path, state_dir: Path
) -> tuple[VaultRegistry, VaultConfig, Path, Path]:
    root = vault_root.expanduser().resolve()
    state = state_dir.expanduser().resolve()
    try:
        state.relative_to(root)
    except ValueError:
        pass
    else:
        raise ValueError("Capture state must stay outside the Vault")
    registry = VaultRegistry(state.parent)
    config = registry.resolve(state.name)
    expected_state = registry.vault_state_dir(config).resolve()
    if expected_state != state or config.root_path != root or config.id != state.name:
        raise PermissionError(
            "Capture state does not match the registered Vault identity"
        )
    return registry, config, root, state


def _safe_destination(vault_root: Path, relative_path: str) -> Path:
    with SecureParent(vault_root, relative_path) as parent:
        return parent.path


def _read_descriptor(descriptor: int) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks: list[bytes] = []
    while True:
        chunk = os.read(descriptor, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _read_existing(root: Path, relative_path: str) -> bytes:
    return secure_read_bytes(root, relative_path)


def _newline_style(existing: bytes) -> tuple[str, str]:
    if existing.startswith(b"\xff\xfe") or existing.startswith(b"\xfe\xff"):
        raise UnicodeError("UTF-16 notes are not appendable; convert explicitly first")
    bom = "utf-8" if existing.startswith(b"\xef\xbb\xbf") else "none"
    existing.decode("utf-8-sig")
    without_crlf = existing.replace(b"\r\n", b"")
    counts = {
        "\r\n": existing.count(b"\r\n"),
        "\n": without_crlf.count(b"\n"),
        "\r": without_crlf.count(b"\r"),
    }
    maximum = max(counts.values())
    if maximum == 0:
        return "\n", bom
    winners = {key for key, count in counts.items() if count == maximum}
    if len(winners) == 1:
        return next(iter(winners)), bom
    match = re.search(rb"\r\n|\r|\n", existing)
    first = match.group().decode("ascii") if match else "\n"
    return (first if first in winners else sorted(winners)[0]), bom


def _yaml_scalar(value: object) -> str:
    if isinstance(value, list):
        return (
            "["
            + ", ".join(json.dumps(str(item), ensure_ascii=False) for item in value)
            + "]"
        )
    return json.dumps(str(value), ensure_ascii=False)


def _new_note(content: str, routing: RoutingLike, title: str | None) -> bytes:
    metadata = ["---"]
    metadata.extend(
        f"{key}: {_yaml_scalar(value)}"
        for key, value in routing.suggested_frontmatter.items()
    )
    metadata.append("---")
    heading = (
        (title or routing.target_filename.removesuffix(".md"))
        .replace("\r", " ")
        .replace("\n", " ")
    )
    body = [*metadata, "", f"# {heading}", ""]
    if routing.suggested_wikilinks:
        body.extend([f"Related: {' '.join(routing.suggested_wikilinks)}", ""])
    body.extend([content.strip(), ""])
    return "\n".join(body).encode("utf-8")


def _append_payload(
    existing: bytes, content: str, *, newline: str, timestamp: datetime
) -> bytes:
    encoded_newline = newline.encode("ascii")
    if existing.endswith(encoded_newline * 2):
        prefix = ""
    elif existing.endswith(encoded_newline):
        prefix = newline
    else:
        prefix = newline * 2
    rendered = timestamp.strftime("%Y-%m-%d %H:%M:%S %z")
    return f"{prefix}## [{rendered}] Update{newline}{newline}{content.strip()}{newline}".encode(
        "utf-8"
    )


def _unique_destination(destination: Path) -> Path:
    if not destination.exists():
        return destination
    for number in range(2, 10_000):
        candidate = destination.with_name(
            f"{destination.stem}-{number}{destination.suffix}"
        )
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"Unable to choose a collision-free name for {destination}")


def create_capture_plan(
    vault_root: Path,
    state_dir: Path,
    content: str,
    routing: RoutingLike,
    *,
    title: str | None = None,
    expected_sha256: str | None = None,
    idempotency_key: str | None = None,
    ttl_seconds: int = 86_400,
) -> CapturePlan:
    """Persist exactly the payload and target shown by preview."""
    if not content.strip():
        raise ValueError("Capture content cannot be empty")
    if ttl_seconds < 1:
        raise ValueError("Capture plan TTL must be positive")
    registry, config, root, state = _registered_context(vault_root, state_dir)
    local_now = datetime.now().astimezone()
    planned = _safe_destination(root, routing.relative_target_path)
    try:
        before: bytes | None = _read_existing(root, routing.relative_target_path)
    except FileNotFoundError:
        before = None
    before_hash = _sha256(before) if before is not None else None
    if expected_sha256 is not None and expected_sha256 != before_hash:
        raise RuntimeError(
            f"Compare-and-swap failed for {routing.relative_target_path}: "
            f"expected {expected_sha256}, found {before_hash or '[missing]'}"
        )
    if routing.action == "append_existing" and before is not None:
        destination = planned
        newline, bom = _newline_style(before)
        payload = _append_payload(before, content, newline=newline, timestamp=local_now)
        action = "append_existing"
    else:
        destination = _unique_destination(planned)
        destination = _safe_destination(root, destination.relative_to(root).as_posix())
        newline, bom = "\n", "none"
        payload = _new_note(content, routing, title)
        action = "create_new"
        before_hash = None
        before = None
    relative_path = destination.relative_to(root).as_posix()
    plan_id = uuid.uuid4().hex
    key = idempotency_key or plan_id
    if not _IDEMPOTENCY_KEY.fullmatch(key):
        raise ValueError(
            "Idempotency key must use 1-128 letters, digits, dots, underscores, colons, or hyphens"
        )
    created = datetime.now(UTC)
    request = {
        "vault_id": config.id,
        "action": action,
        "relative_path": relative_path,
        "payload_sha256": _sha256(payload),
        "expected_sha256": before_hash,
    }
    plan = CapturePlan(
        schema_version=_PLAN_SCHEMA,
        plan_id=plan_id,
        vault_id=config.id,
        action=action,
        relative_path=relative_path,
        payload_base64=base64.b64encode(payload).decode("ascii"),
        payload_sha256=_sha256(payload),
        content_sha256=_sha256(content.encode("utf-8")),
        expected_sha256=before_hash,
        before_size=len(before or b""),
        encoding="utf-8",
        bom=bom,
        newline={"\n": "LF", "\r\n": "CRLF", "\r": "CR"}[newline],
        idempotency_key=key,
        request_fingerprint=_canonical_hash(request),
        local_timestamp=local_now.isoformat(),
        timezone_offset=local_now.strftime("%z"),
        created_at=created.isoformat(),
        expires_at=(created + timedelta(seconds=ttl_seconds)).isoformat(),
        plan_sha256="",
    )
    plan = replace(plan, plan_sha256=_plan_hash(plan))
    payload_bytes = (
        json.dumps(plan.to_dict(), ensure_ascii=False, indent=2) + "\n"
    ).encode("utf-8")
    with registry.guard_vault_state(config) as state_guard:
        descriptor = state_guard.create_new(name=f"plan.{plan.plan_id}.json")
        try:
            written = os.write(descriptor, payload_bytes)
            if written != len(payload_bytes):
                raise ShortWriteError(
                    f"Short plan write: wrote {written} of {len(payload_bytes)} bytes"
                )
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    return plan


def _load_capture_plan_from_guard(
    state_guard: SecureParent, plan_id: str
) -> CapturePlan:
    descriptor = state_guard.open_existing(writable=False, name=f"plan.{plan_id}.json")
    try:
        raw = json.loads(_read_descriptor(descriptor).decode("utf-8"))
        return CapturePlan.from_dict(raw)
    finally:
        os.close(descriptor)


def load_capture_plan(state_dir: Path, plan_id: str) -> CapturePlan:
    if not _PLAN_ID.fullmatch(plan_id):
        raise ValueError("Invalid capture plan id")
    state = state_dir.expanduser().resolve()
    registry = VaultRegistry(state.parent)
    config = registry.resolve(state.name)
    try:
        with registry.guard_vault_state(config) as state_guard:
            return _load_capture_plan_from_guard(state_guard, plan_id)
    except FileNotFoundError:
        raise FileNotFoundError(f"Capture plan does not exist: {plan_id}") from None
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid capture plan: {plan_id}: {exc}") from exc


@contextmanager
def _ledger(state: Path, state_guard: SecureParent) -> Iterator[sqlite3.Connection]:
    path = state / "mutations.sqlite3"
    try:
        pin = state_guard.open_existing(writable=False, name=path.name)
        created = False
    except FileNotFoundError:
        created_descriptor = state_guard.create_new(name=path.name)
        os.close(created_descriptor)
        pin = state_guard.open_existing(writable=False, name=path.name)
        created = True
    journal_pin: int | None = None
    connection: sqlite3.Connection | None = None
    try:
        journal_name = "mutations.sqlite3-journal"
        try:
            journal_pin = state_guard.open_existing(writable=False, name=journal_name)
        except FileNotFoundError:
            journal_descriptor = state_guard.create_new(name=journal_name)
            os.close(journal_descriptor)
            journal_pin = state_guard.open_existing(writable=False, name=journal_name)
        connection = sqlite3.connect(path, timeout=30)
        if created and os.name != "nt":
            path.chmod(0o600)
        connection.row_factory = sqlite3.Row
        connection.executescript(
            """
            PRAGMA journal_mode=PERSIST;
            PRAGMA synchronous=FULL;
            PRAGMA temp_store=MEMORY;
            CREATE TABLE IF NOT EXISTS mutations (
                idempotency_key TEXT PRIMARY KEY,
                request_fingerprint TEXT NOT NULL,
                plan_id TEXT NOT NULL,
                status TEXT NOT NULL,
                action TEXT NOT NULL,
                relative_path TEXT NOT NULL,
                before_sha256 TEXT,
                after_sha256 TEXT,
                write_offset INTEGER NOT NULL,
                write_length INTEGER NOT NULL,
                prepared_at TEXT NOT NULL,
                committed_at TEXT,
                error TEXT
            );
            """
        )
        try:
            yield connection
        except Exception:
            connection.rollback()
            raise
        else:
            connection.commit()
    finally:
        if connection is not None:
            connection.close()
        if journal_pin is not None:
            os.close(journal_pin)
        os.close(pin)


def _result_from_row(row: sqlite3.Row, *, status: str) -> MutationResult:
    return MutationResult(
        status=status,
        plan_id=str(row["plan_id"]),
        action=str(row["action"]),
        relative_path=str(row["relative_path"]),
        before_sha256=(
            str(row["before_sha256"]) if row["before_sha256"] is not None else None
        ),
        after_sha256=str(row["after_sha256"]),
        idempotency_key=str(row["idempotency_key"]),
    )


def _mark_status(
    state: Path,
    state_guard: SecureParent,
    key: str,
    *,
    status: str,
    after_sha256: str | None = None,
    error: str | None = None,
) -> sqlite3.Row:
    with _ledger(state, state_guard) as connection:
        connection.execute(
            "UPDATE mutations SET status=?, after_sha256=COALESCE(?, after_sha256), "
            "committed_at=CASE WHEN ?='COMMITTED' THEN ? ELSE committed_at END, error=? "
            "WHERE idempotency_key=?",
            (status, after_sha256, status, datetime.now(UTC).isoformat(), error, key),
        )
        row = connection.execute(
            "SELECT * FROM mutations WHERE idempotency_key=?", (key,)
        ).fetchone()
    if row is None:
        raise RuntimeError("Mutation ledger row disappeared")
    return row


def _reconcile_prepared(
    state: Path,
    state_guard: SecureParent,
    root: Path,
    plan: CapturePlan,
    destination: Path,
) -> MutationResult | None:
    payload = plan.payload
    if plan.action == "create_new":
        if not destination.exists():
            return None
        current = _read_existing(root, plan.relative_path)
        if _sha256(current) == plan.payload_sha256:
            row = _mark_status(
                state,
                state_guard,
                plan.idempotency_key,
                status="COMMITTED",
                after_sha256=_sha256(current),
            )
            return _result_from_row(row, status="idempotent_replay")
    else:
        if not destination.is_file():
            raise RuntimeError("Prepared append target disappeared")
        current = _read_existing(root, plan.relative_path)
        if (
            _sha256(current) == plan.expected_sha256
            and len(current) == plan.before_size
        ):
            return None
        end = plan.before_size + len(payload)
        if (
            len(current) >= end
            and _sha256(current[: plan.before_size]) == plan.expected_sha256
            and current[plan.before_size : end] == payload
        ):
            row = _mark_status(
                state,
                state_guard,
                plan.idempotency_key,
                status="COMMITTED",
                after_sha256=_sha256(current),
            )
            return _result_from_row(row, status="idempotent_replay")
    _mark_status(
        state,
        state_guard,
        plan.idempotency_key,
        status="NEEDS_RECONCILIATION",
        error=f"Source differs from both prepared and committed states: {destination.relative_to(root)}",
    )
    raise RuntimeError(
        "Prepared mutation needs manual reconciliation; no retry was written"
    )


def _apply_plan(
    root: Path, plan: CapturePlan, destination: Path
) -> tuple[str | None, str]:
    payload = plan.payload
    if plan.action == "append_existing":
        with SecureParent(root, plan.relative_path) as parent:
            descriptor = parent.open_existing(writable=True)
            try:
                before = _read_descriptor(descriptor)
                before_hash = _sha256(before)
                if (
                    before_hash != plan.expected_sha256
                    or len(before) != plan.before_size
                ):
                    raise RuntimeError(
                        f"Compare-and-swap failed for {plan.relative_path}: "
                        "source changed after preview"
                    )
                if os.lseek(descriptor, 0, os.SEEK_END) != plan.before_size:
                    raise RuntimeError(
                        "Append target size changed immediately before write"
                    )
                written = os.write(descriptor, payload)
                os.fsync(descriptor)
                if written != len(payload):
                    raise ShortWriteError(
                        f"Short append: wrote {written} of {len(payload)} bytes"
                    )
                after = _read_descriptor(descriptor)
                if (
                    after[: plan.before_size] != before
                    or after[plan.before_size : plan.before_size + len(payload)]
                    != payload
                ):
                    raise RuntimeError("Append verification failed after fsync")
                return before_hash, _sha256(after)
            finally:
                os.close(descriptor)

    if destination.exists():
        raise RuntimeError(
            f"Create target appeared after preview: {plan.relative_path}"
        )
    with SecureParent(root, plan.relative_path) as parent:
        descriptor = parent.create_new()
        try:
            written = os.write(descriptor, payload)
            os.fsync(descriptor)
            if written != len(payload):
                raise ShortWriteError(
                    f"Short create: wrote {written} of {len(payload)} bytes"
                )
            after = _read_descriptor(descriptor)
            if after != payload:
                raise RuntimeError("Create verification failed after fsync")
            return None, _sha256(after)
        finally:
            os.close(descriptor)


def commit_capture_plan(
    vault_root: Path, state_dir: Path, plan_or_id: CapturePlan | str
) -> MutationResult:
    """Commit one frozen plan with PREPARED/COMMITTED crash reconciliation."""
    registry, config, root, state = _registered_context(vault_root, state_dir)
    if config.write_policy != "append-create":
        raise PermissionError(
            f"Vault {config.name!r} is read-only; attach with --write-policy append-create"
        )
    with registry.guard_vault_state(config) as state_guard:
        if isinstance(plan_or_id, CapturePlan):
            plan = _load_capture_plan_from_guard(state_guard, plan_or_id.plan_id)
            if plan != plan_or_id:
                raise RuntimeError(
                    "Capture plan object does not match its persisted preview"
                )
        else:
            plan = _load_capture_plan_from_guard(state_guard, plan_or_id)
        _validate_plan(plan, check_expiry=False)
        if plan.vault_id != config.id:
            raise PermissionError("Capture plan belongs to a different Vault")
        destination = _safe_destination(root, plan.relative_path)
        with FileLock(state / ".capture.lock", timeout=30, pinned_parent=state_guard):
            with _ledger(state, state_guard) as connection:
                row = connection.execute(
                    "SELECT * FROM mutations WHERE idempotency_key=?",
                    (plan.idempotency_key,),
                ).fetchone()
                if row is not None:
                    if str(row["request_fingerprint"]) != plan.request_fingerprint:
                        raise RuntimeError(
                            "Idempotency key is already bound to a different capture request"
                        )
                    status = str(row["status"])
                    if status == "COMMITTED":
                        return _result_from_row(row, status="idempotent_replay")
                    if status == "NEEDS_RECONCILIATION":
                        raise RuntimeError("Mutation is marked NEEDS_RECONCILIATION")
                else:
                    _validate_plan(plan, check_expiry=True)
                    connection.execute(
                        """
                        INSERT INTO mutations(
                            idempotency_key, request_fingerprint, plan_id, status, action,
                            relative_path, before_sha256, after_sha256, write_offset,
                            write_length, prepared_at, committed_at, error
                        ) VALUES (?, ?, ?, 'PREPARED', ?, ?, ?, NULL, ?, ?, ?, NULL, NULL)
                        """,
                        (
                            plan.idempotency_key,
                            plan.request_fingerprint,
                            plan.plan_id,
                            plan.action,
                            plan.relative_path,
                            plan.expected_sha256,
                            plan.before_size,
                            len(plan.payload),
                            datetime.now(UTC).isoformat(),
                        ),
                    )
            if row is not None:
                replay = _reconcile_prepared(
                    state, state_guard, root, plan, destination
                )
                if replay is not None:
                    return replay
            try:
                before_hash, after_hash = _apply_plan(root, plan, destination)
            except Exception as exc:
                _mark_status(
                    state,
                    state_guard,
                    plan.idempotency_key,
                    status="NEEDS_RECONCILIATION",
                    error=f"{type(exc).__name__}: {exc}",
                )
                raise
            committed = _mark_status(
                state,
                state_guard,
                plan.idempotency_key,
                status="COMMITTED",
                after_sha256=after_hash,
            )
            result = _result_from_row(committed, status="committed")
            if result.before_sha256 != before_hash:
                raise RuntimeError("Mutation ledger before-hash verification failed")
            return result
