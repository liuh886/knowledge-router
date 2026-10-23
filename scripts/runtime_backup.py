#!/usr/bin/env python3
"""OC-05: small safe source checkpoint and durable runtime backup/isolated restore.

Router-owned state only; the Vault itself is never traversed. Frozen edit plans
can contain complete original source bytes for recovery. Backups live under an
explicit caller-provided backup root. Archives carry a per-file integrity
manifest plus a sidecar SHA-256. Only allowlisted relative entries are stored;
traversal and reparse/symlink objects are rejected. Archives are created
atomically. Restore targets only a non-existing isolated directory or an
explicitly allowed empty directory, with exclusive creation so an existing
baseline is never overwritten on failure.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any

from secure_io import is_reparse, validate_relative_path

_SCHEMA_VERSION = 1
_MANIFEST_NAME = "MANIFEST.json"

_LABEL_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
_PLAN_RE = re.compile(r"plan\.[A-Za-z0-9._-]{1,128}\.json")
_EVENT_RE = re.compile(r"context-event\.[a-f0-9]{32}\.(plan|applied|completed)\.json")
_WORKFLOW_RE = re.compile(r"workflow\.[a-f0-9]{64}\.json")

_SOURCE_ALLOWED_DIRS = {"scripts", "tests", "integrations", "references", "docs"}
_SOURCE_ALLOWED_SUFFIXES = {".py", ".md", ".json"}
_SOURCE_ALLOWED_ROOT_FILES = {
    "SKILL.md",
    "README.md",
    "DESIGN.md",
    "TASKS.md",
    "EVALUATE.md",
    "pyproject.toml",
    "LICENSE",
}
# Rebuildable projections and transient files are identified, never archived.
_CACHE_NAMES = {"taxonomy.json"}
_CACHE_PREFIXES = ("index.", "projects.")
_TRANSIENT_SUFFIXES = (".lock", ".tmp", ".staging.json")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _digest_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _temp_base() -> Path:
    return Path(tempfile.gettempdir()).resolve()


def _ensure_dir_chain_under_base(base: Path, target: Path) -> Path:
    """Create target dirs under base, rejecting links/reparse points."""
    base = base.resolve()
    resolved_target = target.expanduser().absolute()
    try:
        relative = resolved_target.relative_to(base)
    except ValueError:
        raise ValueError(f"Path must be below trusted base {base}: {target}") from None
    current = base
    for part in relative.parts:
        if part in {"", ".", ".."}:
            raise ValueError(f"Unsafe path component: {part}")
        candidate = current / part
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            candidate.mkdir()
            info = candidate.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or is_reparse(info):
            raise ValueError(f"State directories cannot be links or reparse points: {candidate}")
        current = candidate
    return current.resolve()


def _require_backup_root(backup_root: Path) -> Path:
    if not isinstance(backup_root, Path):
        raise ValueError("backup_root must be a Path")
    candidate = backup_root.expanduser().absolute()
    # User-controlled backups may live in the private home projection (for
    # example ~/.codex/knowledge-router-local-backups) or in OS temp for a
    # disposable rehearsal. Never create an arbitrary absolute root.
    resolved: Path | None = None
    for base in (Path.home().resolve(), _temp_base()):
        try:
            candidate.relative_to(base)
        except ValueError:
            continue
        resolved = _ensure_dir_chain_under_base(base, candidate)
        break
    if resolved is None:
        raise ValueError(
            f"Backup root must be below the user home or OS temp directory: {candidate}"
        )
    if (resolved / ".obsidian").is_dir():
        raise ValueError("Backup root must not be inside an Obsidian Vault")
    return resolved


def _safe_read_file(path: Path) -> bytes:
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or is_reparse(info):
        raise ValueError(f"Refusing link/reparse/non-regular file: {path}")
    if info.st_nlink > 1:
        raise ValueError(f"Hard-linked targets are not allowed: {path}")
    with open(path, "rb") as handle:
        return handle.read()


def _sanitize_label(label: str | None) -> str:
    if label is None:
        return uuid.uuid4().hex[:8]
    if not isinstance(label, str) or not _LABEL_RE.fullmatch(label):
        raise ValueError(f"Unsafe label: {label!r}")
    return label


def _is_transient(name: str) -> bool:
    return name.endswith(_TRANSIENT_SUFFIXES) or name.startswith(".")


def _is_cache(name: str) -> bool:
    if name in _CACHE_NAMES:
        return True
    return any(name.startswith(prefix) for prefix in _CACHE_PREFIXES)


def _is_allowed_runtime_entry(relative: str, vault_id: str) -> bool:
    if relative == "registry.json":
        return True
    prefix = f"{vault_id}/"
    if not relative.startswith(prefix):
        return False
    name = relative[len(prefix) :]
    if "/" in name or not name:
        return False
    if name in {
        "context_definitions.json",
        "maintenance_baseline.json",
        "maintenance_cycle.jsonl",
        "maintain_audit.jsonl",
        "mutations.sqlite3",
        "compiled_cards.json",
        "knowledge_pages.json",
        "review_ledger.jsonl",
        "context_delivery.jsonl",
    }:
        return True
    return bool(
        _PLAN_RE.fullmatch(name) or _EVENT_RE.fullmatch(name) or _WORKFLOW_RE.fullmatch(name)
    )


def _is_allowed_source_entry(relative: str) -> bool:
    try:
        validated = validate_relative_path(relative)
    except ValueError:
        return False
    parts = validated.parts
    if any(part.startswith(".") for part in parts):
        return False
    if "__pycache__" in parts:
        return False
    if len(parts) == 1:
        if parts[0] in _SOURCE_ALLOWED_ROOT_FILES:
            return True
        return False
    if parts[0] not in _SOURCE_ALLOWED_DIRS:
        return False
    suffix = PurePosixPath(parts[-1]).suffix
    return suffix in _SOURCE_ALLOWED_SUFFIXES


def _zip_atomic(
    backup_root: Path, final_name: str, files: list[tuple[str, bytes]], manifest: dict[str, Any]
) -> Path:
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode("utf-8")
    tmp_name = f".{final_name}.{uuid.uuid4().hex}.tmp"
    tmp_path = backup_root / tmp_name
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    fd: int | None = None
    try:
        fd = os.open(tmp_path, flags, 0o600)
        os.close(fd)
        fd = None
    except Exception:
        if fd is not None:
            os.close(fd)
        raise
    try:
        with zipfile.ZipFile(tmp_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr(_MANIFEST_NAME, manifest_bytes)
            for arcname, data in files:
                archive.writestr(arcname, data)
        with open(tmp_path, "rb") as handle:
            digest = hashlib.sha256(handle.read()).hexdigest()
        # fsync archive before atomic publish
        with open(tmp_path, "r+b") as handle:
            os.fsync(handle.fileno())
        final_path = backup_root / final_name
        if final_path.exists():
            raise FileExistsError(f"Backup archive already exists: {final_path}")
        os.replace(tmp_path, final_path)
    finally:
        try:
            tmp_path.unlink()
        except OSError:
            pass
    # Sidecar checksum written atomically next to the archive.
    sidecar = final_path.with_name(final_path.name + ".sha256")
    sidecar_tmp = backup_root / f".{sidecar.name}.{uuid.uuid4().hex}.tmp"
    payload = f"{digest}  {final_path.name}\n".encode("utf-8")
    sfd = os.open(sidecar_tmp, flags, 0o600)
    try:
        if os.write(sfd, payload) != len(payload):
            raise OSError("Short sidecar write")
        os.fsync(sfd)
    finally:
        os.close(sfd)
    try:
        if sidecar.exists():
            sidecar_tmp.unlink()
        else:
            os.replace(sidecar_tmp, sidecar)
    finally:
        try:
            sidecar_tmp.unlink()
        except OSError:
            pass
    return final_path


def create_source_checkpoint(
    source_root: Path, backup_root: Path, *, label: str | None = None
) -> dict[str, Any]:
    """Archive allowlisted Router source files; never Vault content."""
    src = source_root.expanduser().resolve()
    if not src.is_dir():
        raise FileNotFoundError(f"Source root does not exist: {src}")
    if (src / ".obsidian").is_dir():
        raise ValueError("Source checkpoint must not run inside an Obsidian Vault")
    root = _require_backup_root(backup_root)
    try:
        src.relative_to(root)
    except ValueError:
        pass
    else:
        raise ValueError("Source root must not be inside the backup root")
    try:
        root.relative_to(src)
    except ValueError:
        pass
    else:
        raise ValueError("Backup root must not be inside the source root")
    safe_label = _sanitize_label(label)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    files: list[tuple[str, bytes]] = []
    excluded: list[str] = []
    for candidate in sorted(src.rglob("*")):
        try:
            rel = candidate.relative_to(src).as_posix()
        except ValueError:
            continue
        if candidate.is_dir():
            continue
        if not _is_allowed_source_entry(rel):
            excluded.append(rel)
            continue
        data = _safe_read_file(candidate)
        validate_relative_path(rel)
        files.append((rel, data))
    # Deterministic order for reproducible manifests.
    files.sort(key=lambda item: item[0])
    entries = [
        {"path": name, "sha256": _digest_bytes(data), "size": len(data)} for name, data in files
    ]
    manifest = {
        "schema_version": _SCHEMA_VERSION,
        "kind": "source-checkpoint",
        "created_at": _utc_now(),
        "source_root": str(src),
        "entries": entries,
        "excluded_count": len(excluded),
        "excluded_sample": sorted(excluded)[:20],
    }
    final_name = f"source-checkpoint-{stamp}-{safe_label}.zip"
    final_path = _zip_atomic(root, final_name, files, manifest)
    with open(final_path, "rb") as handle:
        archive_sha256 = hashlib.sha256(handle.read()).hexdigest()
    return {
        "schema_version": _SCHEMA_VERSION,
        "kind": "source-checkpoint",
        "archive": str(final_path),
        "archive_sha256": archive_sha256,
        "entry_count": len(entries),
        "excluded_count": len(excluded),
        "manifest": manifest["entries"],
    }


def create_runtime_backup(
    registry: Any, config: Any, backup_root: Path, *, label: str | None = None
) -> dict[str, Any]:
    """Archive durable Router-owned runtime files for one Vault."""
    from pathlib import Path as _Path  # local import guard for typing only

    root = _require_backup_root(backup_root)
    state_root = _Path(registry.state_root).expanduser().resolve()
    vault_state = _Path(registry.vault_state_dir(config)).expanduser().resolve()
    if not vault_state.is_dir():
        raise FileNotFoundError(f"Vault state directory is missing: {vault_state}")
    # Never back up human Vault content: backup root and state must stay
    # outside the Vault, and the Vault must stay outside the backup root.
    vault_root = _Path(config.root_path).expanduser().resolve(strict=False)
    for probe, name in ((root, "backup root"), (state_root, "state root")):
        try:
            probe.relative_to(vault_root)
        except ValueError:
            pass
        else:
            raise ValueError(f"{name} must not be inside the Vault")
        try:
            vault_root.relative_to(probe)
        except ValueError:
            pass
        else:
            raise ValueError(f"Vault must not be inside {name}")
    try:
        root.relative_to(state_root)
    except ValueError:
        pass
    else:
        raise ValueError("Backup root must not be inside external state")
    safe_label = _sanitize_label(label)
    stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
    files: list[tuple[str, bytes]] = []
    excluded_caches: list[str] = []
    # registry.json at the state root (durable attachment index).
    registry_file = state_root / "registry.json"
    if registry_file.is_file():
        data = _safe_read_file(registry_file)
        files.append(("registry.json", data))
    # Durable per-Vault files.
    for candidate in sorted(vault_state.iterdir()):
        if candidate.is_dir():
            excluded_caches.append(f"{config.id}/{candidate.name}/")
            continue
        name = candidate.name
        rel = f"{config.id}/{name}"
        if _is_transient(name) or _is_cache(name):
            excluded_caches.append(rel)
            continue
        if not _is_allowed_runtime_entry(rel, config.id):
            excluded_caches.append(rel)
            continue
        data = _safe_read_file(candidate)
        validate_relative_path(rel)
        files.append((rel, data))
    files.sort(key=lambda item: item[0])
    entries = [
        {"path": name, "sha256": _digest_bytes(data), "size": len(data)} for name, data in files
    ]
    manifest = {
        "schema_version": _SCHEMA_VERSION,
        "kind": "runtime-backup",
        "created_at": _utc_now(),
        "vault_id": config.id,
        "entries": entries,
        "excluded_caches": sorted(excluded_caches),
        "rebuild_note": "Excluded caches (taxonomy/index/project catalogs, locks, staging) rebuild via sync; only allowlisted durable files restore.",
    }
    final_name = f"runtime-backup-{config.id[:8]}-{stamp}-{safe_label}.zip"
    final_path = _zip_atomic(root, final_name, files, manifest)
    with open(final_path, "rb") as handle:
        archive_sha256 = hashlib.sha256(handle.read()).hexdigest()
    return {
        "schema_version": _SCHEMA_VERSION,
        "kind": "runtime-backup",
        "vault_id": config.id,
        "archive": str(final_path),
        "archive_sha256": archive_sha256,
        "entry_count": len(entries),
        "excluded_caches": sorted(excluded_caches),
    }


def verify_backup_archive(archive: Path) -> dict[str, Any]:
    """Integrity-check an archive without writing restored state."""
    path = archive.expanduser().absolute()
    info = path.lstat() if path.exists() else None
    if info is None:
        raise FileNotFoundError(f"Backup archive does not exist: {path}")
    if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or is_reparse(info):
        raise ValueError(f"Backup archive must be a regular file: {path}")
    if info.st_nlink > 1:
        raise ValueError(f"Hard-linked archives are not allowed: {path}")
    with open(path, "rb") as handle:
        archive_bytes = handle.read()
    archive_sha256 = hashlib.sha256(archive_bytes).hexdigest()
    sidecar = path.with_name(path.name + ".sha256")
    sidecar_status = "absent"
    if sidecar.is_file():
        try:
            text = sidecar.read_text(encoding="utf-8").strip().split()
            if text and text[0] != archive_sha256:
                raise ValueError("Archive SHA-256 sidecar mismatch")
            sidecar_status = "verified" if text else "absent"
        except OSError as exc:
            raise ValueError(f"Cannot read archive sidecar: {exc}") from exc
    try:
        with zipfile.ZipFile(path, mode="r") as zf:
            names = zf.namelist()
            if _MANIFEST_NAME not in names:
                raise ValueError("Backup archive is missing its integrity manifest")
            if len(set(names)) != len(names):
                raise ValueError("Backup archive contains duplicate entries")
            manifest = json.loads(zf.read(_MANIFEST_NAME).decode("utf-8"))
            if not isinstance(manifest, dict) or manifest.get("schema_version") != _SCHEMA_VERSION:
                raise ValueError("Unsupported backup manifest")
            entries = manifest.get("entries")
            if not isinstance(entries, list):
                raise ValueError("Corrupted backup manifest: entries")
            expected = {item["path"]: item for item in entries if isinstance(item, dict)}
            if set(expected) != set(n for n in names if n != _MANIFEST_NAME):
                raise ValueError("Backup manifest does not match archive entries")
            for item in entries:
                rel = item.get("path")
                want_sha = item.get("sha256")
                want_size = item.get("size")
                validate_relative_path(str(rel))
                if PurePosixPath(str(rel)).name == _MANIFEST_NAME:
                    raise ValueError("Manifest entry name is reserved")
                data = zf.read(str(rel))
                if len(data) != int(want_size) or _digest_bytes(data) != str(want_sha):
                    raise ValueError(f"Archive integrity failure for entry: {rel}")
                # Zip-slip guard: no backslashes, no drive letters.
                if "\\" in str(rel) or ":" in str(rel):
                    raise ValueError(f"Unsafe archive entry: {rel}")
    except zipfile.BadZipFile as exc:
        raise ValueError(f"Invalid backup archive: {exc}") from exc
    return {
        "schema_version": _SCHEMA_VERSION,
        "status": "verified",
        "archive": str(path),
        "archive_sha256": archive_sha256,
        "sidecar": sidecar_status,
        "entry_count": len(expected),
        "kind": manifest.get("kind"),
    }


def restore_backup_archive(
    archive: Path, target: Path, *, allow_empty_existing: bool = False
) -> dict[str, Any]:
    """Rehearse-restore an archive into an isolated directory only."""
    report = verify_backup_archive(archive)
    dest = target.expanduser().absolute()
    base = _temp_base()
    try:
        dest.relative_to(base)
    except ValueError:
        raise ValueError(f"Restore target must be below OS temp dir {base}: {dest}") from None
    if (dest / ".obsidian").exists():
        raise ValueError("Restore target must not be inside an Obsidian Vault")
    created_root = False
    if dest.exists():
        info = dest.lstat()
        if not stat.S_ISDIR(info.st_mode) or stat.S_ISLNK(info.st_mode) or is_reparse(info):
            raise ValueError(f"Restore target must be a directory: {dest}")
        existing = list(dest.iterdir())
        if existing:
            raise ValueError(f"Restore target is not empty: {dest}")
        if not allow_empty_existing:
            raise ValueError("Restore to an existing directory requires allow_empty_existing=True")
    else:
        _ensure_dir_chain_under_base(base, dest)
        created_root = True
    # All integrity checks already passed; extract exclusively so no existing
    # baseline file is ever overwritten. Track writes for cleanup on failure.
    written: list[Path] = []
    try:
        with zipfile.ZipFile(archive.expanduser().absolute(), mode="r") as zf:
            manifest = json.loads(zf.read(_MANIFEST_NAME).decode("utf-8"))
            for item in manifest["entries"]:
                rel = str(item["path"])
                validate_relative_path(rel)
                final = (dest / PurePosixPath(rel)).absolute()
                try:
                    final.relative_to(dest)
                except ValueError:
                    raise ValueError(f"Unsafe restore entry: {rel}") from None
                # Create parent chain safely.
                parent = final.parent
                parent.relative_to(dest)
                under = dest
                for part in parent.relative_to(dest).parts:
                    under = under / part
                    if under.exists():
                        pinfo = under.lstat()
                        if (
                            not stat.S_ISDIR(pinfo.st_mode)
                            or stat.S_ISLNK(pinfo.st_mode)
                            or is_reparse(pinfo)
                        ):
                            raise ValueError(f"Unsafe restore parent: {under}")
                    else:
                        under.mkdir()
                data = zf.read(rel)
                if len(data) != int(item["size"]) or _digest_bytes(data) != str(item["sha256"]):
                    raise ValueError(f"Restore integrity failure for entry: {rel}")
                flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
                flags |= getattr(os, "O_NOFOLLOW", 0)
                fd = os.open(final, flags, 0o600)
                try:
                    if os.write(fd, data) != len(data):
                        raise OSError(f"Short restore write: {rel}")
                    os.fsync(fd)
                finally:
                    os.close(fd)
                written.append(final)
    except Exception:
        # Preserve baseline semantics: remove only files this restore created.
        for created in written:
            try:
                created.unlink()
            except OSError:
                pass
        if created_root:
            try:
                dest.rmdir()
            except OSError:
                pass
        raise
    return {
        "schema_version": _SCHEMA_VERSION,
        "status": "restored",
        "archive": str(archive.expanduser().absolute()),
        "target": str(dest),
        "entry_count": len(written),
        "archive_sha256": report["archive_sha256"],
    }
