#!/usr/bin/env python3
"""Read-only Git repository adapter backed by an immutable external catalog."""

from __future__ import annotations

import bisect
import hashlib
import json
import math
import os
import re
import sqlite3
import stat
import subprocess
import uuid
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evidence import EvidenceRecord
from fts_indexer import extract_query_terms
from registry import FileLock, VaultConfig, VaultRegistry
from secure_io import is_reparse

_CORE_DOCUMENTS = {"agents.md", "design.md", "tasks.md"}
_MAX_DOCUMENT_BYTES = 256 * 1024
_MAX_DOCUMENTS_PER_REPOSITORY = 20
_IGNORED_DOC_DIRECTORIES = {
    ".git",
    ".venv",
    "build",
    "dist",
    "node_modules",
    "vendor",
}


@dataclass(frozen=True, slots=True)
class ProjectDocument:
    repo_id: str
    project_id: str
    title: str
    path: str
    checksum: str
    content: str
    revision: str


@dataclass(slots=True)
class ProjectRecord:
    repo_id: str
    project_id: str
    root: str
    remote: str
    branch: str
    head: str
    last_commit_at: str
    dirty: bool
    core_documents: list[str] = field(default_factory=list)
    skipped_documents: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "repo_id": self.repo_id,
            "project_id": self.project_id,
            "root": self.root,
            "remote": self.remote,
            "branch": self.branch,
            "head": self.head,
            "last_commit_at": self.last_commit_at,
            "dirty": self.dirty,
            "core_documents": list(self.core_documents),
            "skipped_documents": list(self.skipped_documents),
        }


@dataclass(frozen=True, slots=True)
class ProjectScan:
    root: Path
    projects: tuple[ProjectRecord, ...]
    documents: tuple[ProjectDocument, ...]
    skipped_entries: tuple[str, ...]


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _repo_id(root: Path) -> str:
    canonical = os.path.normcase(str(root.resolve()))
    return _sha256(canonical)[:16]


def _project_id(name: str) -> str:
    value = re.sub(r"[^a-zA-Z0-9._-]+", "-", name.strip()).strip("-.").casefold()
    return value or "project"


def _git(root: Path, *arguments: str, check: bool = False) -> str:
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
            "GCM_INTERACTIVE": "Never",
        }
    )
    completed = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(root), *arguments],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=15,
        env=environment,
        check=False,
    )
    if check and completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(f"Git metadata read failed for {root}: {detail}")
    return completed.stdout.strip() if completed.returncode == 0 else ""


def _is_repository(root: Path) -> bool:
    reported = _git(root, "rev-parse", "--show-toplevel")
    if not reported:
        return False
    try:
        return Path(reported).resolve() == root.resolve()
    except OSError:
        return False


def _repository_dirty(root: Path) -> bool:
    return bool(
        _git(
            root,
            "status",
            "--porcelain=v1",
            "--untracked-files=normal",
            "--ignore-submodules=dirty",
        )
    )


def _candidate_roots(project_root: Path) -> Iterable[Path]:
    if (project_root / ".git").exists():
        yield project_root
        return
    with os.scandir(project_root) as entries:
        for entry in sorted(entries, key=lambda item: item.name.casefold()):
            try:
                information = entry.stat(follow_symlinks=False)
            except OSError:
                continue
            if (
                entry.name.startswith(".")
                or not stat.S_ISDIR(information.st_mode)
                or stat.S_ISLNK(information.st_mode)
                or is_reparse(information)
            ):
                continue
            candidate = Path(entry.path)
            if (candidate / ".git").exists():
                yield candidate


def _eligible_document_paths(root: Path) -> tuple[list[Path], list[str]]:
    candidates: dict[str, Path] = {}
    skipped: list[str] = []
    try:
        root_entries = list(root.iterdir())
    except OSError as exc:
        return [], [f".: {type(exc).__name__}: {exc}"]
    for path in root_entries:
        folded = path.name.casefold()
        if path.is_file() and (folded in _CORE_DOCUMENTS or folded.startswith("readme.")):
            candidates[path.relative_to(root).as_posix()] = path
    docs_root = next(
        (item for item in root_entries if item.name.casefold() == "docs" and item.is_dir()),
        None,
    )
    if docs_root is not None:
        try:
            direct_docs = sorted(docs_root.iterdir(), key=lambda item: item.name.casefold())
        except OSError as exc:
            skipped.append(f"docs/: {type(exc).__name__}: {exc}")
            direct_docs = []
        for path in direct_docs:
            try:
                information = path.lstat()
            except OSError:
                continue
            if (
                path.suffix.casefold() == ".md"
                and not path.name.startswith(".")
                and stat.S_ISREG(information.st_mode)
                and not stat.S_ISLNK(information.st_mode)
                and not is_reparse(information)
            ):
                candidates[path.relative_to(root).as_posix()] = path
    ordered = [candidates[key] for key in sorted(candidates, key=str.casefold)]
    if len(ordered) > _MAX_DOCUMENTS_PER_REPOSITORY:
        skipped.append(f"document limit: kept {_MAX_DOCUMENTS_PER_REPOSITORY} of {len(ordered)}")
        ordered = ordered[:_MAX_DOCUMENTS_PER_REPOSITORY]
    return ordered, skipped


def _title(content: str, fallback: str) -> str:
    for line in content.splitlines()[:40]:
        if match := re.match(r"^#\s+(.+?)\s*$", line):
            return match.group(1).strip()
    return fallback


def _read_project_document(root: Path, relative: str) -> bytes:
    """Read one already-discovered document while rejecting replacement links."""
    path = root / relative
    resolved = path.resolve(strict=True)
    resolved.relative_to(root.resolve())
    information = path.lstat()
    if (
        not stat.S_ISREG(information.st_mode)
        or stat.S_ISLNK(information.st_mode)
        or is_reparse(information)
    ):
        raise ValueError("project document is not a safe regular file")
    return path.read_bytes()


def _scan_repository(repository: Path) -> tuple[ProjectRecord, list[ProjectDocument]]:
    if not _is_repository(repository):
        raise RuntimeError(f"Git worktree does not resolve to its candidate root: {repository}")
    identifier = _repo_id(repository)
    project_id = _project_id(repository.name)
    remote = _git(repository, "config", "--get", "remote.origin.url")
    branch = _git(repository, "symbolic-ref", "--short", "-q", "HEAD")
    head = _git(repository, "rev-parse", "HEAD")
    last_commit_at = _git(repository, "show", "-s", "--format=%cI", "HEAD")
    dirty = _repository_dirty(repository)
    revision = f"{head or 'unborn'}+working-tree" if dirty else head or "unborn"
    record = ProjectRecord(
        repo_id=identifier,
        project_id=project_id,
        root=str(repository),
        remote=remote,
        branch=branch,
        head=head,
        last_commit_at=last_commit_at,
        dirty=dirty,
    )
    documents: list[ProjectDocument] = []
    paths, path_skips = _eligible_document_paths(repository)
    record.skipped_documents.extend(path_skips)
    for path in paths:
        relative = path.relative_to(repository).as_posix()
        try:
            information = path.lstat()
            if (
                not stat.S_ISREG(information.st_mode)
                or stat.S_ISLNK(information.st_mode)
                or is_reparse(information)
                or information.st_size > _MAX_DOCUMENT_BYTES
            ):
                raise ValueError("not a safe regular file or exceeds 256 KiB")
            payload = _read_project_document(repository, relative)
            content = payload.decode("utf-8-sig")
        except (OSError, UnicodeError, ValueError) as exc:
            record.skipped_documents.append(f"{relative}: {type(exc).__name__}: {exc}")
            continue
        documents.append(
            ProjectDocument(
                repo_id=identifier,
                project_id=project_id,
                title=_title(content, path.stem),
                path=relative,
                checksum=hashlib.sha256(payload).hexdigest(),
                content=content,
                revision=revision,
            )
        )
        if path.parent == repository:
            record.core_documents.append(relative)
    return record, documents


def scan_projects(project_root: Path) -> ProjectScan:
    """Scan repository metadata and documentation without executing or changing a repo."""
    root = project_root.expanduser().resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Project library does not exist: {root}")
    root_information = root.lstat()
    if stat.S_ISLNK(root_information.st_mode) or is_reparse(root_information):
        raise ValueError(f"Project library root cannot be a link: {root}")
    projects: list[ProjectRecord] = []
    documents: list[ProjectDocument] = []
    skipped_entries: list[str] = []
    used_project_ids: set[str] = set()
    repositories = list(_candidate_roots(root))
    worker_count = min(8, max(1, len(repositories)))
    with ThreadPoolExecutor(max_workers=worker_count) as executor:
        scanned = list(executor.map(_scan_repository, repositories))
    for record, repo_documents in scanned:
        project_id = record.project_id
        if project_id in used_project_ids:
            project_id = f"{project_id}-{record.repo_id[:6]}"
            record.project_id = project_id
            repo_documents = [
                ProjectDocument(
                    repo_id=item.repo_id,
                    project_id=project_id,
                    title=item.title,
                    path=item.path,
                    checksum=item.checksum,
                    content=item.content,
                    revision=item.revision,
                )
                for item in repo_documents
            ]
        used_project_ids.add(project_id)
        projects.append(record)
        documents.extend(repo_documents)
    return ProjectScan(
        root=root,
        projects=tuple(projects),
        documents=tuple(documents),
        skipped_entries=tuple(skipped_entries),
    )


def _manifest_payload(
    projects: Iterable[ProjectRecord], documents: Iterable[ProjectDocument]
) -> dict[str, Any]:
    return {
        "projects": [
            {
                "repo_id": item.repo_id,
                "project_id": item.project_id,
                "root": item.root,
                "remote": item.remote,
                "branch": item.branch,
                "head": item.head,
                "last_commit_at": item.last_commit_at,
                "dirty": item.dirty,
                "core_documents": sorted(item.core_documents),
                "skipped_documents": sorted(item.skipped_documents),
            }
            for item in sorted(projects, key=lambda value: value.repo_id)
        ],
        "documents": [
            {
                "repo_id": item.repo_id,
                "project_id": item.project_id,
                "path": item.path,
                "checksum": item.checksum,
                "indexed_content_hash": _sha256(item.content),
                "revision": item.revision,
            }
            for item in sorted(documents, key=lambda value: (value.repo_id, value.path))
        ],
    }


def _generation(scan: ProjectScan) -> str:
    serialized = json.dumps(
        _manifest_payload(scan.projects, scan.documents),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256(serialized)


def _cjk_bigrams(value: str) -> str:
    grams: list[str] = []
    for run in re.findall(r"[\u3400-\u9fff]{2,}", value):
        grams.extend(run[index : index + 2] for index in range(len(run) - 1))
    return " ".join(dict.fromkeys(grams))


def _create_catalog(path: Path, scan: ProjectScan, generation: str) -> None:
    connection = sqlite3.connect(path)
    try:
        connection.executescript(
            """
            PRAGMA journal_mode=DELETE;
            CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE projects (
                repo_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                root TEXT NOT NULL,
                remote TEXT NOT NULL,
                branch TEXT NOT NULL,
                head TEXT NOT NULL,
                last_commit_at TEXT NOT NULL,
                dirty INTEGER NOT NULL,
                core_documents TEXT NOT NULL,
                skipped_documents TEXT NOT NULL
            );
            CREATE TABLE documents (
                doc_id INTEGER PRIMARY KEY,
                repo_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                title TEXT NOT NULL,
                path TEXT NOT NULL,
                checksum TEXT NOT NULL,
                content TEXT NOT NULL,
                revision TEXT NOT NULL,
                UNIQUE(repo_id, path)
            );
            CREATE VIRTUAL TABLE documents_fts USING fts5(
                title, path, content, search_tokens,
                tokenize='unicode61 remove_diacritics 2'
            );
            """
        )
        metadata = {
            "schema_version": "1",
            "project_root": str(scan.root),
            "generation": generation,
            "project_count": str(len(scan.projects)),
            "document_count": str(len(scan.documents)),
            "skipped_entries": json.dumps(scan.skipped_entries, ensure_ascii=False),
        }
        connection.executemany("INSERT INTO metadata(key, value) VALUES (?, ?)", metadata.items())
        connection.executemany(
            "INSERT INTO projects VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    item.repo_id,
                    item.project_id,
                    item.root,
                    item.remote,
                    item.branch,
                    item.head,
                    item.last_commit_at,
                    int(item.dirty),
                    json.dumps(sorted(item.core_documents), ensure_ascii=False),
                    json.dumps(sorted(item.skipped_documents), ensure_ascii=False),
                )
                for item in scan.projects
            ],
        )
        for item in scan.documents:
            cursor = connection.execute(
                """INSERT INTO documents(
                       repo_id, project_id, title, path, checksum, content, revision
                   ) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    item.repo_id,
                    item.project_id,
                    item.title,
                    item.path,
                    item.checksum,
                    item.content,
                    item.revision,
                ),
            )
            connection.execute(
                "INSERT INTO documents_fts(rowid, title, path, content, search_tokens) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    cursor.lastrowid,
                    item.title,
                    item.path,
                    item.content,
                    _cjk_bigrams(f"{item.title}\n{item.path}\n{item.content}"),
                ),
            )
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise RuntimeError(f"Project catalog integrity check failed: {integrity}")
    finally:
        connection.close()


def _read_metadata(connection: sqlite3.Connection) -> dict[str, str]:
    return {
        str(row[0]): str(row[1]) for row in connection.execute("SELECT key, value FROM metadata")
    }


def _database_generation(connection: sqlite3.Connection) -> str:
    projects = [
        ProjectRecord(
            repo_id=str(row["repo_id"]),
            project_id=str(row["project_id"]),
            root=str(row["root"]),
            remote=str(row["remote"]),
            branch=str(row["branch"]),
            head=str(row["head"]),
            last_commit_at=str(row["last_commit_at"]),
            dirty=bool(row["dirty"]),
            core_documents=json.loads(str(row["core_documents"])),
            skipped_documents=json.loads(str(row["skipped_documents"])),
        )
        for row in connection.execute("SELECT * FROM projects")
    ]
    documents = [
        ProjectDocument(
            repo_id=str(row["repo_id"]),
            project_id=str(row["project_id"]),
            title=str(row["title"]),
            path=str(row["path"]),
            checksum=str(row["checksum"]),
            content=str(row["content"]),
            revision=str(row["revision"]),
        )
        for row in connection.execute("SELECT * FROM documents")
    ]
    serialized = json.dumps(
        _manifest_payload(projects, documents),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return _sha256(serialized)


def _catalog_path(registry: VaultRegistry, config: VaultConfig) -> Path:
    if not config.project_root:
        raise FileNotFoundError(
            f"Vault {config.name!r} has no project library; run projects attach <path>"
        )
    if not config.project_catalog:
        raise FileNotFoundError(f"Project library for {config.name!r} has no catalog; run sync")
    path = registry.vault_state_dir(config) / config.project_catalog
    if not path.is_file():
        raise FileNotFoundError(f"Active project catalog is missing: {path}")
    return path


def _connect_catalog(
    registry: VaultRegistry, config: VaultConfig
) -> tuple[sqlite3.Connection, dict[str, str]]:
    path = _catalog_path(registry, config)
    connection = sqlite3.connect(f"file:{path.resolve().as_posix()}?mode=ro", uri=True)
    connection.row_factory = sqlite3.Row
    try:
        metadata = _read_metadata(connection)
        if metadata.get("schema_version") != "1":
            raise RuntimeError("Unsupported project catalog schema")
        if Path(metadata.get("project_root", "")).resolve() != Path(config.project_root).resolve():
            raise RuntimeError("Project catalog root binding mismatch")
        if metadata.get("generation") != config.project_generation:
            raise RuntimeError("Project catalog generation mismatch")
        if _database_generation(connection) != metadata.get("generation"):
            raise RuntimeError("Project catalog contents do not match its generation")
        return connection, metadata
    except Exception:
        connection.close()
        raise


def sync_project_catalog(registry: VaultRegistry, config: VaultConfig) -> dict[str, Any]:
    """Build and activate one content-addressed project catalog outside every repo."""
    if not config.project_root:
        raise FileNotFoundError(
            f"Vault {config.name!r} has no project library; run projects attach <path>"
        )
    state = registry.vault_state_dir(config)
    with registry.guard_vault_state(config) as state_guard:
        with FileLock(state / ".projects.lock", timeout=120, pinned_parent=state_guard):
            scan = scan_projects(Path(config.project_root))
            generation = _generation(scan)
            filename = f"projects.{generation}.db"
            destination = state / filename
            reused = destination.is_file()
            if not reused:
                temporary = state / f".{filename}.{uuid.uuid4().hex}.tmp"
                try:
                    _create_catalog(temporary, scan, generation)
                    temporary.replace(destination)
                finally:
                    temporary.unlink(missing_ok=True)
    registry.update_projects(config.id, filename=filename, generation=generation)
    active = registry.resolve(config.id)
    with registry.guard_vault_state(active) as state_guard:
        with FileLock(state / ".projects.lock", timeout=120, pinned_parent=state_guard):
            generations = sorted(
                state.glob("projects.*.db"), key=lambda item: item.stat().st_mtime_ns
            )
            removed: list[str] = []
            for old in generations[:-2]:
                if old.name != filename:
                    old.unlink()
                    removed.append(old.name)
    return {
        "schema_version": 1,
        "vault_id": config.id,
        "project_root": str(scan.root),
        "catalog": filename,
        "generation": generation,
        "reused": reused,
        "project_count": len(scan.projects),
        "document_count": len(scan.documents),
        "dirty_projects": sum(item.dirty for item in scan.projects),
        "skipped_documents": sum(len(item.skipped_documents) for item in scan.projects),
        "pruned_catalogs": removed,
    }


def list_projects(registry: VaultRegistry, config: VaultConfig) -> list[dict[str, Any]]:
    connection, _metadata = _connect_catalog(registry, config)
    try:
        rows = connection.execute("SELECT * FROM projects ORDER BY project_id").fetchall()
        return [
            {
                "repo_id": str(row["repo_id"]),
                "project_id": str(row["project_id"]),
                "root": str(row["root"]),
                "remote": str(row["remote"]),
                "branch": str(row["branch"]),
                "head": str(row["head"]),
                "last_commit_at": str(row["last_commit_at"]),
                "dirty": bool(row["dirty"]),
                "core_documents": json.loads(str(row["core_documents"])),
                "skipped_documents": json.loads(str(row["skipped_documents"])),
            }
            for row in rows
        ]
    finally:
        connection.close()


def project_status(registry: VaultRegistry, config: VaultConfig) -> dict[str, Any]:
    result: dict[str, Any] = {
        "vault_id": config.id,
        "project_root": config.project_root,
        "root_exists": bool(config.project_root and Path(config.project_root).is_dir()),
        "catalog": config.project_catalog,
        "generation": config.project_generation,
    }
    if not config.project_catalog:
        result.update({"catalog_exists": False, "project_count": 0, "document_count": 0})
        return result
    path = registry.vault_state_dir(config) / config.project_catalog
    result["catalog_exists"] = path.is_file()
    if path.is_file():
        connection, metadata = _connect_catalog(registry, config)
        connection.close()
        result.update(
            {
                "project_count": int(metadata["project_count"]),
                "document_count": int(metadata["document_count"]),
            }
        )
    return result


def _excerpt(content: str, terms: list[str], *, max_chars: int = 800) -> tuple[str, int, int]:
    folded = content.casefold()
    positions = [folded.find(term.casefold()) for term in terms if term]
    positions = [position for position in positions if position >= 0]
    position = min(positions) if positions else 0
    lines = content.splitlines() or [""]
    starts = [0]
    starts.extend(match.end() for match in re.finditer(r"\r\n|\r|\n", content))
    hit = max(0, bisect.bisect_right(starts, position) - 1)
    start = max(0, hit - 2)
    selected: list[str] = []
    end = start
    for index in range(start, len(lines)):
        candidate = "\n".join([*selected, lines[index]])
        if selected and len(candidate) > max_chars:
            break
        selected.append(lines[index])
        end = index
        if len(candidate) >= max_chars:
            break
    return "\n".join(selected)[:max_chars], start + 1, end + 1


def _fts_query(terms: list[str]) -> str:
    quoted: list[str] = []
    for term in terms:
        escaped = re.sub(r"[\"'*(){}:+]", "", term.strip()).replace("-", " ").strip()
        if escaped:
            quoted.append(f'"{escaped.replace(chr(34), chr(34) * 2)}"')
    return " OR ".join(quoted[:48])


def search_projects(
    registry: VaultRegistry,
    config: VaultConfig,
    query: str,
    *,
    limit: int = 5,
    project_id: str | None = None,
) -> list[EvidenceRecord]:
    """Search cataloged repository docs and return canonical evidence records."""
    if limit < 1 or limit > 50:
        raise ValueError("limit must be between 1 and 50")
    terms = extract_query_terms(query)
    match = _fts_query(terms)
    if not match:
        return []
    connection, metadata = _connect_catalog(registry, config)
    try:
        where = "documents_fts MATCH ?"
        parameters: list[Any] = [match]
        if project_id:
            where += " AND d.project_id = ?"
            parameters.append(project_id)
        parameters.append(max(40, limit * 10))
        rows = connection.execute(
            f"""
            SELECT d.*, p.root AS repo_root, p.remote, p.branch, p.dirty,
                   bm25(documents_fts, 0.0, 6.0, 3.0, 1.0) AS lexical_score
            FROM documents_fts
            JOIN documents d ON d.doc_id = documents_fts.rowid
            JOIN projects p ON p.repo_id = d.repo_id
            WHERE {where}
            ORDER BY lexical_score
            LIMIT ?
            """,
            parameters,
        ).fetchall()
        english = [term for term in terms if re.search(r"[a-zA-Z0-9]", term)]
        cjk = [term for term in terms if re.search(r"[\u3400-\u9fff]", term)][:16]
        ranked: list[tuple[float, sqlite3.Row]] = []
        for row in rows:
            searchable = re.sub(
                r"[_/\\-]+",
                " ",
                f"{row['title']}\n{row['path']}\n{row['content']}",
            ).casefold()
            matched_english = sum(term.casefold() in searchable for term in english)
            matched_cjk = sum(term.casefold() in searchable for term in cjk)
            english_required = max(1, math.ceil(len(english) * 0.6)) if english else 0
            cjk_required = max(2, math.ceil(len(cjk) * 0.1)) if cjk else 0
            if not (
                (english and matched_english >= english_required)
                or (cjk and matched_cjk >= cjk_required)
            ):
                continue
            title_path = f"{row['title']}\n{row['path']}".casefold()
            title_matches = sum(term.casefold() in title_path for term in [*english, *cjk])
            coverage = matched_english / max(1, len(english)) + matched_cjk / max(1, len(cjk))
            total = matched_english + matched_cjk
            score = title_matches * 1000 + coverage * 100 + total * 10 - float(row["lexical_score"])
            ranked.append((score, row))
        ranked.sort(key=lambda item: (-item[0], str(item[1]["project_id"])))
        results: list[EvidenceRecord] = []
        for score, row in ranked[:limit]:
            excerpt, line_start, line_end = _excerpt(str(row["content"]), terms)
            stale = False
            try:
                payload = _read_project_document(Path(str(row["repo_root"])), str(row["path"]))
                stale = hashlib.sha256(payload).hexdigest() != str(row["checksum"])
            except (OSError, ValueError):
                stale = True
            results.append(
                EvidenceRecord(
                    source_type="repository",
                    source_id=str(row["repo_id"]),
                    project_id=str(row["project_id"]),
                    title=str(row["title"]),
                    path=str(row["path"]),
                    line_start=line_start,
                    line_end=line_end,
                    content_hash=str(row["checksum"]),
                    revision=str(row["revision"]),
                    stale=stale,
                    excerpt=excerpt,
                    score=round(score, 6),
                    metadata={
                        "repo_root": str(row["repo_root"]),
                        "remote": str(row["remote"]),
                        "branch": str(row["branch"]),
                        "dirty": bool(row["dirty"]),
                        "catalog_generation": metadata["generation"],
                    },
                )
            )
        return results
    except sqlite3.Error as exc:
        raise RuntimeError(f"Project catalog query failed: {exc}") from exc
    finally:
        connection.close()
