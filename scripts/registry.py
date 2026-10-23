#!/usr/bin/env python3
"""External multi-vault registry and cross-process locking for KnowledgeRouter."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import sys
import tempfile
import time
import uuid
from contextlib import AbstractContextManager, contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from secure_io import SecureParent, is_reparse

_SCHEMA_VERSION = 1
_NAME_RE = re.compile(r"[^a-zA-Z0-9._-]+")


def default_state_root() -> Path:
    """Return the machine-local production projection root.

    Priority:
    1. KNOWLEDGE_ROUTER_STATE_ROOT environment variable (if set).
    2. Primary standalone root: ~/.knowledge-router/vaults.
    3. Legacy fallback: ~/.ductor/vaults (if ~/.knowledge-router/vaults does not exist,
       but ~/.ductor/vaults has an existing registry.json).
    """
    override = os.environ.get("KNOWLEDGE_ROUTER_STATE_ROOT", "").strip()
    if override:
        return Path(override).expanduser().resolve()

    primary = (Path.home() / ".knowledge-router" / "vaults").resolve()
    if primary.exists():
        return primary

    legacy = (Path.home() / ".ductor" / "vaults").resolve()
    if (legacy / "registry.json").is_file():
        return legacy

    return primary


def _utc_now() -> str:
    return datetime.now(UTC).isoformat()


def _stable_vault_id(root: Path) -> str:
    canonical = os.path.normcase(str(root.resolve()))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _default_name(root: Path) -> str:
    value = _NAME_RE.sub("-", root.name.strip()).strip("-.").casefold()
    return value or f"vault-{_stable_vault_id(root)[:8]}"


def _ensure_directory_chain(base: Path, target: Path) -> None:
    base = base.expanduser().resolve(strict=True)
    target = target.expanduser().absolute()
    try:
        relative = target.relative_to(base)
    except ValueError:
        raise ValueError(f"State root must be below its trusted base: {target}") from None
    current = base
    for part in relative.parts:
        candidate = current / part
        try:
            information = candidate.lstat()
        except FileNotFoundError:
            with SecureParent(current, ".directory-anchor", allow_hidden=True):
                try:
                    candidate.mkdir()
                except FileExistsError:
                    pass
            information = candidate.lstat()
        if (
            not stat.S_ISDIR(information.st_mode)
            or stat.S_ISLNK(information.st_mode)
            or is_reparse(information)
        ):
            raise ValueError(f"State directories cannot be links or reparse points: {candidate}")
        current = candidate


def _atomic_replace(path: Path, data: bytes) -> None:
    """Write bytes through a same-directory temporary file and atomic replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor: int | None = None
    try:
        descriptor = os.open(temporary, flags, 0o600)
        information = os.fstat(descriptor)
        if (
            not stat.S_ISREG(information.st_mode)
            or is_reparse(information)
            or information.st_nlink > 1
        ):
            raise ValueError(f"Unsafe temporary state file: {temporary}")
        written = os.write(descriptor, data)
        if written != len(data):
            raise OSError(f"Short JSON write: wrote {written} of {len(data)} bytes")
        os.fsync(descriptor)
        os.close(descriptor)
        descriptor = None
        os.replace(temporary, path)
        if os.name != "nt":
            path.chmod(0o600)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        temporary.unlink(missing_ok=True)


def atomic_json_write(path: Path, payload: object) -> None:
    """Write JSON through a same-directory temporary file and atomic replace."""
    _atomic_replace(
        path, (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    )


def atomic_text_write(path: Path, text: str) -> None:
    """Write UTF-8 text through a same-directory temporary file and atomic replace."""
    _atomic_replace(path, text.encode("utf-8"))


class FileLock(AbstractContextManager["FileLock"]):
    """Small cross-platform advisory lock backed by a state-directory file."""

    def __init__(
        self,
        path: Path,
        *,
        timeout: float = 15.0,
        pinned_parent: SecureParent | None = None,
    ) -> None:
        self._path = path
        self._timeout = timeout
        self._handle: Any = None
        self._pinned_parent = pinned_parent

    def __enter__(self) -> FileLock:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        guard = self._pinned_parent
        owns_guard = guard is None
        if guard is None:
            guard = SecureParent(self._path.parent, self._path.name, allow_hidden=True).__enter__()
        elif guard.parent != self._path.parent:
            raise ValueError("Pinned lock parent does not match the lock path")
        try:
            handle = self._path.open("a+b")
            path_info = self._path.lstat()
            handle_info = os.fstat(handle.fileno())
            if (
                stat.S_ISLNK(path_info.st_mode)
                or is_reparse(path_info)
                or path_info.st_nlink > 1
                or handle_info.st_nlink > 1
            ):
                raise ValueError(f"Lock files cannot be links or reparse points: {self._path}")
            if (path_info.st_dev, path_info.st_ino) != (
                handle_info.st_dev,
                handle_info.st_ino,
            ):
                raise RuntimeError(f"Lock file changed while opening: {self._path}")
        except Exception:
            if "handle" in locals():
                handle.close()
            if owns_guard:
                guard.__exit__(None, None, None)
            raise
        if owns_guard:
            guard.__exit__(None, None, None)
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        deadline = time.monotonic() + self._timeout
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                self._handle = handle
                return self
            except OSError:
                if time.monotonic() >= deadline:
                    handle.close()
                    raise TimeoutError(f"Timed out waiting for lock: {self._path}") from None
                time.sleep(0.05)

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        handle = self._handle
        if handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()
            self._handle = None


@dataclass(slots=True)
class VaultConfig:
    id: str
    name: str
    root: str
    write_policy: str = "off"
    capture_mode: str = "explicit"
    active_index: str = ""
    index_generation: str = ""
    project_root: str = ""
    project_catalog: str = ""
    project_generation: str = ""
    projects_updated_at: str = ""
    attached_at: str = ""
    updated_at: str = ""

    @property
    def root_path(self) -> Path:
        return Path(self.root).resolve()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> VaultConfig:
        return cls(
            id=str(raw["id"]),
            name=str(raw["name"]),
            root=str(raw["root"]),
            write_policy=str(raw.get("write_policy", "off")),
            capture_mode=str(raw.get("capture_mode", "explicit")),
            active_index=str(raw.get("active_index", "")),
            index_generation=str(raw.get("index_generation", "")),
            project_root=str(raw.get("project_root", "")),
            project_catalog=str(raw.get("project_catalog", "")),
            project_generation=str(raw.get("project_generation", "")),
            projects_updated_at=str(raw.get("projects_updated_at", "")),
            attached_at=str(raw.get("attached_at", "")),
            updated_at=str(raw.get("updated_at", "")),
        )


class VaultRegistry:
    """Persist attached Vault identities without writing inside any Vault."""

    def __init__(self, state_root: Path | None = None) -> None:
        if state_root is None:
            candidate = default_state_root()
            try:
                candidate.relative_to(Path.home().resolve())
                trusted_base = Path.home().resolve()
            except ValueError:
                trusted_base = (
                    Path(candidate.anchor).resolve()
                    if candidate.is_absolute()
                    else Path.home().resolve()
                )
        else:
            candidate = state_root.expanduser().resolve()
            temporary_root = Path(tempfile.gettempdir()).resolve()
            if candidate != default_state_root():
                try:
                    candidate.relative_to(temporary_root)
                    trusted_base = temporary_root
                except ValueError:
                    raise ValueError(
                        "Custom state roots are restricted to the operating-system temporary "
                        "directory; production state is fixed (defaults to ~/.knowledge-router/vaults "
                        "or configured via KNOWLEDGE_ROUTER_STATE_ROOT)"
                    ) from None
            else:
                try:
                    candidate.relative_to(Path.home().resolve())
                    trusted_base = Path.home().resolve()
                except ValueError:
                    trusted_base = (
                        Path(candidate.anchor).resolve()
                        if candidate.is_absolute()
                        else Path.home().resolve()
                    )
        for ancestor in (candidate, *candidate.parents):
            if (ancestor / ".obsidian").is_dir():
                raise ValueError("External state root must not be inside an Obsidian Vault")
        self._trusted_state_base = trusted_base
        self.state_root = candidate
        self.registry_path = self.state_root / "registry.json"
        self.lock_path = self.state_root / ".registry.lock"

    def vault_state_dir(self, config_or_id: VaultConfig | str) -> Path:
        vault_id = config_or_id.id if isinstance(config_or_id, VaultConfig) else config_or_id
        return self.state_root / vault_id

    @contextmanager
    def guard_vault_state(self, config: VaultConfig) -> Any:
        state = self.vault_state_dir(config)
        if not state.is_dir():
            raise FileNotFoundError(f"Vault state directory is missing: {state}")
        with SecureParent(
            self.state_root,
            f"{config.id}/.state-anchor",
            allow_hidden=True,
        ) as guard:
            yield guard

    def _load_unlocked(self) -> list[VaultConfig]:
        if not self.registry_path.is_file():
            return []
        try:
            raw = json.loads(self.registry_path.read_text(encoding="utf-8"))
            if int(raw.get("schema_version", 0)) != _SCHEMA_VERSION:
                raise ValueError("unsupported registry schema")
            return [VaultConfig.from_dict(item) for item in raw.get("vaults", [])]
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"Invalid KnowledgeRouter registry: {self.registry_path}: {exc}"
            ) from exc

    def _save_unlocked(self, configs: list[VaultConfig]) -> None:
        atomic_json_write(
            self.registry_path,
            {
                "schema_version": _SCHEMA_VERSION,
                "updated_at": _utc_now(),
                "vaults": [item.to_dict() for item in sorted(configs, key=lambda x: x.name)],
            },
        )

    def list(self) -> list[VaultConfig]:
        if not self.state_root.exists():
            return []
        with FileLock(self.lock_path):
            return self._load_unlocked()

    def resolve(self, selector: str | None = None) -> VaultConfig:
        configs = self.list()
        if selector:
            selector_folded = selector.casefold()
            selected_root = ""
            candidate = Path(selector).expanduser()
            if candidate.is_absolute():
                selected_root = os.path.normcase(str(candidate.resolve(strict=False)))
            matches = [
                item
                for item in configs
                if item.name.casefold() == selector_folded
                or item.id == selector
                or (
                    selected_root
                    and os.path.normcase(str(Path(item.root).expanduser().resolve(strict=False)))
                    == selected_root
                )
            ]
            if len(matches) == 1:
                return matches[0]
            raise KeyError(f"Unknown Vault: {selector}")
        if len(configs) == 1:
            return configs[0]
        if not configs:
            raise KeyError("No Vault is attached. Run `knowledge_router.py attach <path>`. ")
        raise KeyError("Multiple Vaults are attached; specify --vault <name-or-id-or-root-path>.")

    def attach(
        self,
        root: Path,
        *,
        name: str | None = None,
        write_policy: str = "off",
        capture_mode: str = "explicit",
    ) -> VaultConfig:
        root = root.expanduser().resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"Vault directory does not exist: {root}")
        try:
            self.state_root.relative_to(root)
        except ValueError:
            pass
        else:
            raise ValueError("External state root must not be inside the Vault")
        try:
            root.relative_to(self.state_root)
        except ValueError:
            pass
        else:
            raise ValueError("Vault must not be inside the external state root")
        if write_policy not in {"off", "append-create", "reviewed-edit"}:
            raise ValueError("write_policy must be 'off', 'append-create' or 'reviewed-edit'")
        if capture_mode not in {"explicit", "auto"}:
            raise ValueError("capture_mode must be 'explicit' or 'auto'")
        _ensure_directory_chain(self._trusted_state_base, self.state_root)
        vault_id = _stable_vault_id(root)
        chosen_name = (name or _default_name(root)).strip()
        if not chosen_name:
            raise ValueError("Vault name cannot be empty")
        now = _utc_now()
        with FileLock(self.lock_path):
            configs = self._load_unlocked()
            by_id = next((item for item in configs if item.id == vault_id), None)
            name_conflict = next(
                (
                    item
                    for item in configs
                    if item.name.casefold() == chosen_name.casefold() and item.id != vault_id
                ),
                None,
            )
            if name_conflict:
                raise ValueError(
                    f"Vault name {chosen_name!r} is already used by {name_conflict.root}"
                )
            if by_id:
                by_id.name = chosen_name
                by_id.root = str(root)
                by_id.write_policy = write_policy
                by_id.capture_mode = capture_mode
                by_id.updated_at = now
                config = by_id
            else:
                config = VaultConfig(
                    id=vault_id,
                    name=chosen_name,
                    root=str(root),
                    write_policy=write_policy,
                    capture_mode=capture_mode,
                    attached_at=now,
                    updated_at=now,
                )
                configs.append(config)
            state_dir = self.vault_state_dir(config)
            with SecureParent(self.state_root, ".state-root-anchor", allow_hidden=True):
                try:
                    state_dir.mkdir()
                except FileExistsError:
                    pass
            with self.guard_vault_state(config):
                pass
            self._save_unlocked(configs)
            return config

    def update_index(self, vault_id: str, *, filename: str, generation: str) -> VaultConfig:
        with FileLock(self.lock_path):
            configs = self._load_unlocked()
            config = next((item for item in configs if item.id == vault_id), None)
            if config is None:
                raise KeyError(f"Unknown Vault id: {vault_id}")
            config.active_index = filename
            config.index_generation = generation
            config.updated_at = _utc_now()
            self._save_unlocked(configs)
            return config

    def attach_projects(self, vault_id: str, project_root: Path) -> VaultConfig:
        """Bind one read-only project-library root to an attached Vault."""
        root = project_root.expanduser().resolve()
        if not root.is_dir():
            raise FileNotFoundError(f"Project library does not exist: {root}")
        information = root.lstat()
        if stat.S_ISLNK(information.st_mode) or is_reparse(information):
            raise ValueError(f"Project library root cannot be a link: {root}")
        try:
            self.state_root.relative_to(root)
        except ValueError:
            pass
        else:
            raise ValueError("External state root must not be inside the project library")
        try:
            root.relative_to(self.state_root)
        except ValueError:
            pass
        else:
            raise ValueError("Project library must not be inside external state")
        with FileLock(self.lock_path):
            configs = self._load_unlocked()
            config = next((item for item in configs if item.id == vault_id), None)
            if config is None:
                raise KeyError(f"Unknown Vault id: {vault_id}")
            config.project_root = str(root)
            config.project_catalog = ""
            config.project_generation = ""
            config.projects_updated_at = _utc_now()
            config.updated_at = config.projects_updated_at
            self._save_unlocked(configs)
            return config

    def update_projects(self, vault_id: str, *, filename: str, generation: str) -> VaultConfig:
        """Activate one immutable external project catalog generation."""
        with FileLock(self.lock_path):
            configs = self._load_unlocked()
            config = next((item for item in configs if item.id == vault_id), None)
            if config is None:
                raise KeyError(f"Unknown Vault id: {vault_id}")
            config.project_catalog = filename
            config.project_generation = generation
            config.projects_updated_at = _utc_now()
            config.updated_at = config.projects_updated_at
            self._save_unlocked(configs)
            return config


def running_on_supported_python() -> bool:
    return sys.version_info >= (3, 11)
