#!/usr/bin/env python3
"""Immutable SQLite FTS5 projections and source-backed Vault retrieval."""

from __future__ import annotations

import argparse
import bisect
import hashlib
import itertools
import json
import math
import os
import re
import sqlite3
import stat
import uuid
from contextlib import ExitStack, closing
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from secure_io import SecureParent, is_reparse, secure_read_source_bytes as secure_read_bytes
from registry import atomic_json_write

_SKIP_DIR_NAMES = frozenset(
    {
        ".obsidian",
        ".git",
        ".trash",
        ".makemd",
        ".pandoc",
        ".infio_json_db",
        ".knowledge_router",
        "node_modules",
        "__pycache__",
    }
)
_MESSAGE_STOPWORDS = frozenset(
    {
        "about",
        "after",
        "also",
        "and",
        "before",
        "find",
        "from",
        "have",
        "help",
        "into",
        "more",
        "need",
        "notes",
        "please",
        "search",
        "show",
        "that",
        "the",
        "this",
        "what",
        "where",
        "which",
        "with",
        "would",
        "your",
    }
)
_CJK_PREFIXES = (
    "不存在的",
    "请帮我",
    "帮我",
    "查一下",
    "找一下",
    "查找",
    "搜索",
    "查询",
    "了解",
    "看看",
    "关于",
    "请",
)
_CJK_SUFFIXES = ("相关资料", "相关信息", "资料", "信息", "内容", "情况", "相关", "笔记")
_CONCEPT_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("海上地震项目质量控制", ("offshore seismic qc",)),
    ("求职简历", ("cv tailoring checklist",)),
    ("卫星激光测高", ("satellite laser altimetry", "snow depth")),
    ("机器学习", ("machine learning",)),
)
_VALIDATED_INDEXES: dict[str, tuple[int, int, int, int, str, str, str]] = {}
# Layout version of the external search index. Bump this whenever the table layout
# changes: it is part of the generation hash, so a bump rebuilds instead of reusing.
_SCHEMA_VERSION = "5"
_SCAN_WORKERS = 4


@dataclass(frozen=True, slots=True)
class NoteRecord:
    ulid: str
    title: str
    content: str
    path: str
    modified_at: str
    mtime_ns: int
    size: int
    checksum: str


@dataclass(frozen=True, slots=True)
class ScanReport:
    records: tuple[NoteRecord, ...]
    skipped_decode: tuple[str, ...]
    skipped_io: tuple[str, ...]


def _stable_id(relative_path: str) -> str:
    return hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:26].upper()


def _is_eligible(path: Path, root: Path, root_resolved: Path | None = None) -> bool:
    """Return whether *path* is a regular, non-link note inside *root*.

    ``root.resolve()`` is intentionally cached by ``scan_notes`` because this
    predicate runs once per candidate; resolving the same vault root thousands
    of times added measurable latency without strengthening the containment
    check.
    """
    try:
        relative = path.relative_to(root)
        if any(
            part.startswith(".") or part.casefold() in _SKIP_DIR_NAMES for part in relative.parts
        ):
            return False
        resolved = path.resolve(strict=True)
        resolved.relative_to(root_resolved or root.resolve())
        information = path.lstat()
    except (OSError, ValueError):
        return False
    return (
        stat.S_ISREG(information.st_mode)
        and not stat.S_ISLNK(information.st_mode)
        and not is_reparse(information)
        and not any(
            part.startswith(".") or part.casefold() in _SKIP_DIR_NAMES for part in relative.parts
        )
    )


def _extract_title(content: str, fallback: str) -> str:
    frontmatter = re.match(r"\A---\s*\r?\n(.*?)\r?\n---\s*(?:\r?\n|\Z)", content, re.DOTALL)
    if frontmatter:
        title_match = re.search(r"(?im)^title\s*:\s*[\"']?(.*?)[\"']?\s*$", frontmatter.group(1))
        if title_match and title_match.group(1).strip():
            return title_match.group(1).strip()
    heading = re.search(r"(?m)^#\s+(.+?)\s*$", content)
    return heading.group(1).strip() if heading else fallback


def _scan_group(vault_root: Path, siblings: list[Path]) -> ScanReport:
    """One independent directory guard; never share a native handle across workers."""
    records, skipped_decode, skipped_io = [], [], []
    siblings = [p for p in siblings if _is_eligible(p, vault_root, vault_root)]
    if not siblings:
        return ScanReport((), (), ())
    with ExitStack() as stack:
        try:
            parent = stack.enter_context(
                SecureParent(vault_root, siblings[0].relative_to(vault_root).as_posix())
            )
        except OSError:
            return ScanReport((), (), tuple(p.relative_to(vault_root).as_posix() for p in siblings))
        for path in siblings:
            relative = path.relative_to(vault_root).as_posix()
            try:
                raw = parent.read_bytes(name=path.name, allow_hardlinks=True)
                information = path.stat()
            except OSError:
                skipped_io.append(relative)
                continue
            try:
                content = raw.decode("utf-8-sig")
            except UnicodeDecodeError:
                skipped_decode.append(relative)
                continue
            records.append(NoteRecord(
                ulid=_stable_id(relative), title=_extract_title(content, path.stem),
                content=content, path=relative,
                modified_at=datetime.fromtimestamp(information.st_mtime, tz=UTC).isoformat(),
                mtime_ns=information.st_mtime_ns, size=information.st_size,
                checksum=hashlib.sha256(raw).hexdigest(),
            ))
    return ScanReport(tuple(records), tuple(skipped_decode), tuple(skipped_io))


def scan_notes(vault_root: Path, *, scopes: Iterable[str] | None = None) -> ScanReport:
    """Read eligible notes, optionally within explicit lexical scopes.

    Paths are still enumerated live, so new/moved/deleted notes need no cache
    invalidation. Scope filtering happens before source reads and expensive path
    checks. Every selected source uses the same pinned, verified read as a full scan.
    """
    vault_root = vault_root.expanduser().resolve()
    if not vault_root.is_dir():
        raise FileNotFoundError(f"Vault directory does not exist: {vault_root}")
    records: list[NoteRecord] = []
    skipped_decode: list[str] = []
    skipped_io: list[str] = []
    selected = None
    if scopes is not None:
        selected = []
        for scope in scopes:
            if not isinstance(scope, str) or not scope:
                raise ValueError("Scan scopes must be nonempty relative paths")
            parsed = PurePosixPath(scope)
            if (parsed.is_absolute() or ":" in scope or "\\" in scope
                    or any(part == ".." or part.startswith(".") for part in parsed.parts)):
                raise ValueError(f"Unsafe scan scope: {scope}")
            selected.append(parsed.as_posix())

    def within_scope(path: Path) -> bool:
        relative_path = path.relative_to(vault_root)
        if any(part.startswith(".") or part.casefold() in _SKIP_DIR_NAMES
               for part in relative_path.parts):
            return False
        relative = relative_path.as_posix()
        return selected is None or any(scope == "." or relative == scope or relative.startswith(scope + "/")
                   for scope in selected)

    paths = sorted(
        (p for p in vault_root.rglob("*.md") if within_scope(p)),
        key=lambda p: (p.parent, p.name),
    )
    groups = [list(members) for _, members in itertools.groupby(paths, key=lambda p: p.parent)]
    with ThreadPoolExecutor(max_workers=min(_SCAN_WORKERS, max(1, len(groups)))) as pool:
        for result in pool.map(lambda siblings: _scan_group(vault_root, siblings), groups):
            records.extend(result.records)
            skipped_decode.extend(result.skipped_decode)
            skipped_io.extend(result.skipped_io)
    records.sort(key=lambda record: Path(record.path))
    skipped_decode.sort(key=Path)
    skipped_io.sort(key=Path)
    return ScanReport(
        records=tuple(records),
        skipped_decode=tuple(skipped_decode),
        skipped_io=tuple(skipped_io),
    )


def _vault_identity(vault_root: Path) -> str:
    canonical = os.path.normcase(str(vault_root.expanduser().resolve()))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _create_schema(
    connection: sqlite3.Connection,
    *,
    generation: str,
    vault_root: Path,
    vault_id: str,
    scan: ScanReport,
) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode=DELETE;
        PRAGMA synchronous=FULL;
        CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE notes (
            ulid TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            content TEXT NOT NULL,
            path TEXT NOT NULL UNIQUE,
            modified_at TEXT NOT NULL,
            mtime_ns INTEGER NOT NULL,
            size INTEGER NOT NULL,
            checksum TEXT NOT NULL
        );
        CREATE INDEX idx_notes_path ON notes(path);
        CREATE VIRTUAL TABLE notes_fts USING fts5(
            ulid UNINDEXED, title, path, content, tokenize='porter unicode61'
        );
        CREATE TABLE note_bigrams (
            gram TEXT NOT NULL,
            ulid TEXT NOT NULL,
            PRIMARY KEY (gram, ulid)
        ) WITHOUT ROWID;
        CREATE TABLE bigram_stats (
            gram TEXT PRIMARY KEY,
            document_count INTEGER NOT NULL
        ) WITHOUT ROWID;
        """
    )
    connection.executemany(
        "INSERT INTO metadata(key, value) VALUES (?, ?)",
        (
            ("schema_version", _SCHEMA_VERSION),
            ("generation", generation),
            ("vault_root", str(vault_root.expanduser().resolve())),
            ("vault_id", vault_id),
            ("skipped_decode", json.dumps(scan.skipped_decode, ensure_ascii=False)),
            ("skipped_io", json.dumps(scan.skipped_io, ensure_ascii=False)),
            ("created_at", datetime.now(UTC).isoformat()),
        ),
    )


def _cjk_bigrams(*values: str) -> set[str]:
    grams: set[str] = set()
    for value in values:
        for run in re.findall(r"[\u3400-\u9fff]{2,}", value):
            grams.update(run[index : index + 2] for index in range(len(run) - 1))
    return grams


def build_database(
    index_path: Path,
    scan: ScanReport,
    *,
    generation: str,
    vault_root: Path,
    vault_id: str,
) -> None:
    """Build and verify a new immutable index at a path that must not exist.

    The index intentionally carries no trigram table. Earlier generations built one, but
    nothing ever queried it - the CJK path is served by `note_bigrams` and then a LIKE
    fallback, while `notes_fts` handles English. Dropping the write-only table removes
    roughly a third of the index size without changing any search result.
    """
    if index_path.exists():
        raise FileExistsError(index_path)
    index_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(index_path)
    try:
        _create_schema(
            connection,
            generation=generation,
            vault_root=vault_root,
            vault_id=vault_id,
            scan=scan,
        )
        connection.executemany(
            """
            INSERT INTO notes(
                ulid, title, content, path, modified_at, mtime_ns, size, checksum
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    record.ulid,
                    record.title,
                    record.content,
                    record.path,
                    record.modified_at,
                    record.mtime_ns,
                    record.size,
                    record.checksum,
                )
                for record in scan.records
            ),
        )
        connection.executemany(
            "INSERT INTO notes_fts VALUES (?, ?, ?, ?)",
            ((item.ulid, item.title, item.path, item.content) for item in scan.records),
        )
        connection.executemany(
            "INSERT INTO note_bigrams(gram, ulid) VALUES (?, ?)",
            (
                (gram, item.ulid)
                for item in scan.records
                for gram in _cjk_bigrams(item.title, item.path, item.content)
            ),
        )
        connection.execute(
            "INSERT INTO bigram_stats(gram, document_count) "
            "SELECT gram, COUNT(*) FROM note_bigrams GROUP BY gram"
        )
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        notes = int(connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0])
        fts = int(connection.execute("SELECT COUNT(*) FROM notes_fts").fetchone()[0])
        if integrity != ("ok",) or notes != len(scan.records) or notes != fts:
            raise RuntimeError(
                f"Index verification failed: integrity={integrity}, notes={notes}, fts={fts}"
            )
    finally:
        connection.close()


def _content_generation(scan: ScanReport) -> str:
    return _generation_from_pairs((record.path, record.checksum) for record in scan.records)


def _require_complete_scan(scan: ScanReport) -> None:
    if scan.skipped_decode or scan.skipped_io:
        details = [
            *(f"decode:{path}" for path in scan.skipped_decode),
            *(f"io:{path}" for path in scan.skipped_io),
        ]
        raise RuntimeError(
            "Index activation refused because some Markdown sources were unreadable: "
            + ", ".join(details[:20])
        )


def _generation_from_pairs(pairs: Any) -> str:
    # The schema version is part of the prefix, so a schema change yields a new generation
    # filename. Without that the content-addressed generation would match an existing index
    # and silently reuse it, keeping a stale layout alive forever.
    digest = hashlib.sha256(f"knowledge-router-index-schema-{_SCHEMA_VERSION}\0".encode())
    normalized = sorted((str(path), str(checksum)) for path, checksum in pairs)
    for path, checksum in normalized:
        digest.update(path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(checksum.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _verify_generation(
    index_path: Path,
    generation: str,
    expected_notes: int,
    *,
    vault_root: Path,
    vault_id: str,
    verify_projection: bool = False,
) -> None:
    uri = f"file:{index_path.resolve().as_posix()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=5.0)) as connection:
        metadata = _metadata(connection)
        integrity = connection.execute("PRAGMA integrity_check").fetchone()
        notes = int(connection.execute("SELECT COUNT(*) FROM notes").fetchone()[0])
        fts = int(connection.execute("SELECT COUNT(*) FROM notes_fts").fetchone()[0])
        database_generation = _generation_from_pairs(
            connection.execute("SELECT path, checksum FROM notes ORDER BY path")
        )
        if verify_projection:
            receipt = _projection_receipt(index_path, vault_root, vault_id, generation)
            if receipt is None:
                _validated_base(connection, vault_root, vault_id, check_fts_integrity=False)
            elif _index_digest(index_path) != receipt:
                raise RuntimeError("Index bytes no longer match the validated projection receipt")
    if (
        metadata.get("generation") != generation
        or database_generation != generation
        or metadata.get("vault_root") != str(vault_root.expanduser().resolve())
        or metadata.get("vault_id") != vault_id
        or integrity != ("ok",)
        or notes != expected_notes
        or fts != notes
    ):
        raise RuntimeError(f"Existing index generation failed verification: {index_path}")


_NOTE_COLUMNS = "ulid, title, content, path, modified_at, mtime_ns, size, checksum"


def _index_digest(path: Path) -> str:
    digest = hashlib.sha256()
    with SecureParent(path.parent, path.name, allow_hidden=True) as parent:
        descriptor = parent.open_existing(writable=False)
        try:
            while chunk := os.read(descriptor, 1024 * 1024):
                digest.update(chunk)
        finally:
            os.close(descriptor)
    return digest.hexdigest()


def _projection_receipt(path: Path, root: Path, identity: str, generation: str) -> str | None:
    name = path.with_suffix(".verified.json").name
    try:
        with SecureParent(path.parent, name) as parent:
            receipt = json.loads(parent.read_bytes().decode("utf-8"))
    except FileNotFoundError:
        return None
    if (not isinstance(receipt, dict) or receipt.get("schema_version") != 1
            or receipt.get("index_schema") != _SCHEMA_VERSION
            or receipt.get("generation") != generation
            or receipt.get("vault_root") != str(root)
            or receipt.get("vault_id") != identity
            or not re.fullmatch(r"[a-f0-9]{64}", str(receipt.get("index_sha256", "")))):
        raise ValueError("Invalid immutable index verification receipt")
    return receipt["index_sha256"]


def _write_projection_receipt(path: Path, root: Path, identity: str, generation: str) -> None:
    # Issued only after construction/validation and complete source verification.
    atomic_json_write(path.with_suffix(".verified.json"), {
        "schema_version": 1, "index_schema": _SCHEMA_VERSION,
        "generation": generation, "vault_root": str(root), "vault_id": identity,
        "index_sha256": _index_digest(path),
    })


def _copy_verified_projection(destination: Path, source: Path, expected: str) -> None:
    """Hash the bytes actually copied, so even a concurrent donor edit cannot be inherited."""
    digest = hashlib.sha256()
    with SecureParent(source.parent, source.name) as parent:
        descriptor = parent.open_existing(writable=False)
        try:
            with destination.open("xb") as target:
                while chunk := os.read(descriptor, 1024 * 1024):
                    if target.write(chunk) != len(chunk):
                        raise OSError("Short immutable index copy")
                    digest.update(chunk)
                target.flush()
                os.fsync(target.fileno())
        finally:
            os.close(descriptor)
    if digest.hexdigest() != expected:
        raise RuntimeError("Incremental base bytes differ from the verified projection")


def _validated_base(
    connection: sqlite3.Connection, vault_root: Path, vault_id: str, *,
    check_fts_integrity: bool = True,
    trusted_bytes: bool = False,
) -> dict[str, tuple]:
    """Validate all inherited data, including postings, before trusting a delta base."""
    metadata = _metadata(connection)
    rows = list(connection.execute(f"SELECT {_NOTE_COLUMNS} FROM notes"))
    if (metadata.get("schema_version") != _SCHEMA_VERSION
            or metadata.get("vault_root") != str(vault_root)
            or metadata.get("vault_id") != vault_id
            or metadata.get("generation") != _generation_from_pairs((r[3], r[7]) for r in rows)
            or (not trusted_bytes and connection.execute("PRAGMA integrity_check").fetchone() != ("ok",))):
        raise RuntimeError("Invalid incremental base identity, manifest or integrity")
    by_id = {r[0]: r for r in rows}
    if len(by_id) != len(rows):
        raise RuntimeError("Duplicate base note identity")
    if trusted_bytes:
        return {row[3]: row for row in rows}
    for row in rows:
        if (not all(isinstance(row[i], str) for i in (0, 1, 2, 3, 4, 7))
                or not all(isinstance(row[i], int) for i in (5, 6)) or row[6] < 0):
            raise RuntimeError("Invalid incremental base note fields")
        encoded = row[2].encode("utf-8")
        valid_payloads = (encoded, b"\xef\xbb\xbf" + encoded)
        if (row[0] != _stable_id(row[3])
                or row[1] != _extract_title(row[2], Path(row[3]).stem)
                or not any(len(raw) == row[6] and hashlib.sha256(raw).hexdigest() == row[7]
                           for raw in valid_payloads)):
            raise RuntimeError("Incremental base contains inconsistent note content")
    # FTS drives the join, so the indexed notes PK is used rather than an N*N FTS scan.
    mismatch = connection.execute("""
        SELECT 1 FROM notes_fts f LEFT JOIN notes n ON n.ulid=f.ulid
        WHERE n.ulid IS NULL OR f.title != n.title OR f.path != n.path OR f.content != n.content
        LIMIT 1
    """).fetchone()
    counts = connection.execute("SELECT COUNT(*), COUNT(DISTINCT ulid) FROM notes_fts").fetchone()
    if mismatch or counts != (len(rows), len(rows)):
        raise RuntimeError("Incremental base FTS content mismatch")
    if check_fts_integrity:
        connection.execute("INSERT INTO notes_fts(notes_fts) VALUES('integrity-check')")
    seen = set()
    grams = connection.execute("SELECT ulid, gram FROM note_bigrams ORDER BY ulid")
    for identity, group in itertools.groupby(grams, key=lambda row: row[0]):
        record = by_id.get(identity)
        if record is None or {row[1] for row in group} != _cjk_bigrams(record[1], record[3], record[2]):
            raise RuntimeError("Incremental base CJK postings mismatch")
        seen.add(identity)
    if any(_cjk_bigrams(row[1], row[3], row[2]) for identity, row in by_id.items() if identity not in seen):
        raise RuntimeError("Incremental base is missing CJK postings")
    expected = "SELECT gram, COUNT(*) FROM note_bigrams GROUP BY gram"
    actual = "SELECT gram, document_count FROM bigram_stats"
    if (connection.execute(f"SELECT 1 FROM ({expected} EXCEPT {actual}) LIMIT 1").fetchone()
            or connection.execute(f"SELECT 1 FROM ({actual} EXCEPT {expected}) LIMIT 1").fetchone()):
        raise RuntimeError("Incremental base CJK statistics mismatch")
    return {row[3]: row for row in rows}


def build_incremental_database(
    index_path: Path, previous_index: Path, scan: ScanReport, *,
    generation: str, vault_root: Path, vault_id: str,
) -> dict[str, int]:
    """Clone a validated immutable projection and change only affected note/posting rows."""
    if index_path.exists():
        raise FileExistsError(index_path)
    info = previous_index.lstat()
    if not stat.S_ISREG(info.st_mode) or is_reparse(info) or info.st_nlink != 1:
        raise ValueError("Incremental base must be an ordinary external index file")
    uri = f"file:{previous_index.resolve().as_posix()}?mode=ro"
    # Receipts bind whole immutable files, including FTS/CJK internals, rather
    # than trusting mtime or re-tokenizing every inherited note on each update.
    with closing(sqlite3.connect(uri, uri=True)) as source:
        previous_generation = _metadata(source).get("generation", "")
    receipt = _projection_receipt(previous_index, vault_root, vault_id, previous_generation)
    if receipt is not None:
        _copy_verified_projection(index_path, previous_index, receipt)
    target = sqlite3.connect(index_path)
    try:
        if receipt is None:
            with closing(sqlite3.connect(uri, uri=True)) as source:
                source.backup(target)
        old = _validated_base(target, vault_root, vault_id, trusted_bytes=receipt is not None)
        current = {record.path: record for record in scan.records}
        removed = sorted(set(old) - set(current))
        added, changed, metadata_only, untouched = [], [], [], 0
        for path, record in current.items():
            row = old.get(path)
            values = (record.ulid, record.title, record.content, record.path,
                      record.modified_at, record.mtime_ns, record.size, record.checksum)
            if row is None:
                added.append(values)
            elif (row[0:4], row[7]) != (values[0:4], values[7]):
                changed.append(values)
            elif row != values:
                metadata_only.append(values)
            else:
                untouched += 1
        if not untouched and not metadata_only:
            raise RuntimeError("No unchanged notes to inherit; using a compact full build")
        affected_ids = [old[p][0] for p in removed] + [r[0] for r in changed]
        with target:
            target.execute("CREATE TEMP TABLE affected_ids(ulid TEXT PRIMARY KEY)")
            target.executemany("INSERT INTO affected_ids VALUES(?)", ((i,) for i in affected_ids))
            target.execute("DELETE FROM notes_fts WHERE ulid IN (SELECT ulid FROM affected_ids)")
            # Materialize affected CJK statistics before removing postings.
            target.execute("CREATE TEMP TABLE affected_grams(gram TEXT PRIMARY KEY)")
            target.execute("INSERT OR IGNORE INTO affected_grams SELECT gram FROM note_bigrams WHERE ulid IN (SELECT ulid FROM affected_ids)")
            target.execute("DELETE FROM note_bigrams WHERE ulid IN (SELECT ulid FROM affected_ids)")
            target.execute("DELETE FROM notes WHERE ulid IN (SELECT ulid FROM affected_ids)")
            updates = added + changed
            target.executemany(f"INSERT INTO notes({_NOTE_COLUMNS}) VALUES(?,?,?,?,?,?,?,?)", updates)
            target.executemany("UPDATE notes SET modified_at=?, mtime_ns=?, size=? WHERE ulid=?",
                               ((r[4], r[5], r[6], r[0]) for r in metadata_only))
            target.executemany("INSERT INTO notes_fts VALUES(?,?,?,?)",
                               ((r[0], r[1], r[3], r[2]) for r in updates))
            new_grams = [(gram, r[0]) for r in updates for gram in _cjk_bigrams(r[1], r[3], r[2])]
            target.executemany("INSERT INTO note_bigrams VALUES(?,?)", new_grams)
            target.executemany("INSERT OR IGNORE INTO affected_grams VALUES(?)", ((g,) for g, _ in new_grams))
            target.execute("DELETE FROM bigram_stats WHERE gram IN (SELECT gram FROM affected_grams)")
            target.execute("""INSERT INTO bigram_stats SELECT gram, COUNT(*) FROM note_bigrams
                WHERE gram IN (SELECT gram FROM affected_grams) GROUP BY gram""")
            target.execute("UPDATE metadata SET value=? WHERE key='generation'", (generation,))
            target.execute("UPDATE metadata SET value=? WHERE key='created_at'", (datetime.now(UTC).isoformat(),))
    finally:
        target.close()
    _verify_generation(index_path, generation, len(scan.records), vault_root=vault_root, vault_id=vault_id)
    return {"added": len(added), "updated": len(changed), "removed": len(removed),
            "metadata_updated": len(metadata_only), "unchanged": untouched}


def build_generation_with_scan(
    vault_root: Path,
    state_dir: Path,
    *,
    source_scan: ScanReport | None = None,
    previous_index: Path | None = None,
    force_rebuild: bool = False,
) -> tuple[dict[str, Any], ScanReport]:
    """Build a generation and return both its manifest and verified source snapshot."""
    scan = source_scan or scan_notes(vault_root)
    _require_complete_scan(scan)
    vault_root = vault_root.expanduser().resolve()
    vault_id = _vault_identity(vault_root)
    generation = _content_generation(scan)
    filename = f"index.{generation}.db"
    final_path = state_dir / filename
    temporary = state_dir / f".{filename}.{uuid.uuid4().hex}.tmp"
    state_dir.mkdir(parents=True, exist_ok=True)
    reused = final_path.is_file() and not force_rebuild
    build_mode, delta, fallback = "reused" if reused else "full", None, None
    if reused:
        try:
            _verify_generation(
                final_path, generation, len(scan.records), vault_root=vault_root, vault_id=vault_id,
                verify_projection=True,
            )
        except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
            reused, build_mode = False, "full"
            fallback = f"{type(exc).__name__}: {exc}"
    if not reused:
        try:
            if previous_index is not None and not force_rebuild:
                try:
                    delta = build_incremental_database(
                        temporary, previous_index, scan, generation=generation,
                        vault_root=vault_root, vault_id=vault_id,
                    )
                    build_mode = "incremental"
                except (OSError, ValueError, RuntimeError, sqlite3.Error) as exc:
                    temporary.unlink(missing_ok=True)
                    fallback = f"{type(exc).__name__}: {exc}"
            if build_mode != "incremental":
                build_database(
                    temporary, scan, generation=generation,
                    vault_root=vault_root, vault_id=vault_id,
                )
        except BaseException:
            temporary.unlink(missing_ok=True)
            raise
    try:
        verification_scan = scan_notes(vault_root)
        _require_complete_scan(verification_scan)
        if _content_generation(verification_scan) != generation:
            raise RuntimeError("Vault changed during indexing; no new generation was activated")
        if not reused:
            os.replace(temporary, final_path)
        if not reused or _projection_receipt(final_path, vault_root, vault_id, generation) is None:
            _write_projection_receipt(final_path, vault_root, vault_id, generation)
    finally:
        temporary.unlink(missing_ok=True)
    return (
        {
            "generation": generation,
            "filename": filename,
            "index_path": str(final_path),
            "indexed_notes": len(scan.records),
            "skipped_decode": list(scan.skipped_decode),
            "skipped_io": list(scan.skipped_io),
            "reused": reused,
            "build_mode": build_mode,
            "delta": delta,
            "fallback_reason": fallback,
        },
        verification_scan,
    )


def build_generation(
    vault_root: Path,
    state_dir: Path,
    *,
    source_scan: ScanReport | None = None,
) -> dict[str, Any]:
    """Build a new immutable generation and return its external manifest fields."""
    result, _verified_scan = build_generation_with_scan(
        vault_root, state_dir, source_scan=source_scan
    )
    return result


def prune_generations(state_dir: Path, *, active_filename: str, keep: int = 2) -> list[str]:
    """Remove only superseded external projections, retaining rollback capacity."""
    if keep < 1:
        raise ValueError("keep must be at least 1")
    state_dir = state_dir.resolve()
    generations = sorted(
        (path for path in state_dir.glob("index.*.db") if path.is_file() and not path.is_symlink()),
        key=lambda path: path.stat().st_mtime_ns,
    )
    protected = set(generations[-keep:])
    protected.add(state_dir / active_filename)
    removed: list[str] = []
    for path in generations:
        if path in protected:
            continue
        path.unlink()
        path.with_suffix(".verified.json").unlink(missing_ok=True)
        removed.append(path.name)
    return removed


def build_or_sync_index(vault_root: Path, index_path: Path) -> int:
    """Compatibility helper: atomically replace a caller-selected derived index."""
    vault_root = vault_root.expanduser().resolve()
    scan = scan_notes(vault_root)
    _require_complete_scan(scan)
    generation = _content_generation(scan)
    index_path = index_path.expanduser().resolve()
    try:
        index_path.relative_to(vault_root)
    except ValueError:
        pass
    else:
        raise ValueError("Derived indexes must stay outside the Vault")
    index_path.parent.mkdir(parents=True, exist_ok=True)
    temporary = index_path.with_name(f".{index_path.name}.{uuid.uuid4().hex}.tmp")
    try:
        build_database(
            temporary,
            scan,
            generation=generation,
            vault_root=vault_root,
            vault_id=_vault_identity(vault_root),
        )
        os.replace(temporary, index_path)
    finally:
        temporary.unlink(missing_ok=True)
    return len(scan.records)


def _metadata(connection: sqlite3.Connection) -> dict[str, str]:
    return {
        str(key): str(value) for key, value in connection.execute("SELECT key, value FROM metadata")
    }


def _validate_index_binding(
    connection: sqlite3.Connection,
    index_path: Path,
    *,
    vault_root: Path | None = None,
    vault_id: str = "",
    generation: str = "",
) -> tuple[dict[str, str], str]:
    metadata = _metadata(connection)
    actual_generation = metadata.get("generation", "")
    if not actual_generation:
        raise RuntimeError(f"Index has no generation metadata: {index_path}")
    if generation and generation != actual_generation:
        raise RuntimeError(
            f"Index generation mismatch: expected {generation}, found {actual_generation}"
        )
    if vault_root is not None:
        resolved_root = vault_root.expanduser().resolve()
        expected_id = _vault_identity(resolved_root)
        if metadata.get("vault_root") != str(resolved_root):
            raise RuntimeError(f"Index is bound to a different Vault root: {index_path}")
        if metadata.get("vault_id") != expected_id:
            raise RuntimeError(f"Index Vault identity metadata is invalid: {index_path}")
        if vault_id and vault_id != expected_id:
            raise RuntimeError("Caller Vault identity does not match the canonical Vault root")
    if vault_id and metadata.get("vault_id") != vault_id:
        raise RuntimeError(f"Index is bound to a different Vault id: {index_path}")
    resolved_index = index_path.resolve()
    information = resolved_index.stat()
    signature = (
        information.st_dev,
        information.st_ino,
        information.st_size,
        information.st_mtime_ns,
        actual_generation,
        metadata.get("vault_root", ""),
        metadata.get("vault_id", ""),
    )
    cache_key = os.path.normcase(str(resolved_index))
    if _VALIDATED_INDEXES.get(cache_key) == signature:
        return metadata, actual_generation
    database_generation = _generation_from_pairs(
        connection.execute("SELECT path, checksum FROM notes ORDER BY path")
    )
    if database_generation != actual_generation:
        raise RuntimeError(f"Index content does not match its generation metadata: {index_path}")
    _VALIDATED_INDEXES[cache_key] = signature
    return metadata, actual_generation


def audit_index(
    vault_root: Path,
    index_path: Path,
    *,
    source_scan: ScanReport | None = None,
) -> dict[str, Any]:
    """Compare a projection with current Markdown paths and byte checksums."""
    scan = source_scan or scan_notes(vault_root)
    source = {item.path: item.checksum for item in scan.records}
    if not index_path.is_file():
        return {
            "status": "missing_index",
            "missing_paths": sorted(source),
            "removed_paths": [],
            "changed_paths": [],
            "skipped_decode": list(scan.skipped_decode),
            "skipped_io": list(scan.skipped_io),
        }
    uri = f"file:{index_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=5.0) as connection:
        _metadata_values, _actual_generation = _validate_index_binding(
            connection,
            index_path,
            vault_root=vault_root,
            vault_id=_vault_identity(vault_root),
        )
        indexed = {
            str(path): str(checksum)
            for path, checksum in connection.execute("SELECT path, checksum FROM notes")
        }
        metadata = _metadata(connection)
    shared = source.keys() & indexed.keys()
    missing = sorted(source.keys() - indexed.keys())
    removed = sorted(indexed.keys() - source.keys())
    changed = sorted(path for path in shared if source[path] != indexed[path])
    return {
        "status": "current" if not missing and not removed and not changed else "stale",
        "generation": metadata.get("generation", ""),
        "source_notes": len(source),
        "indexed_notes": len(indexed),
        "missing_paths": missing,
        "removed_paths": removed,
        "changed_paths": changed,
        "skipped_decode": list(scan.skipped_decode),
        "skipped_io": list(scan.skipped_io),
    }


def read_index_source(
    index_path: Path,
    relative_path: str,
    *,
    vault_root: Path,
    vault_id: str,
    generation: str,
    max_lines: int = 35,
) -> dict[str, Any] | None:
    """Read one path from the immutable projection and verify its live-source staleness."""
    uri = f"file:{index_path.resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=5.0) as connection:
        connection.row_factory = sqlite3.Row
        _metadata_values, actual_generation = _validate_index_binding(
            connection,
            index_path,
            vault_root=vault_root,
            vault_id=vault_id,
            generation=generation,
        )
        row = connection.execute(
            "SELECT title, content, path, checksum FROM notes WHERE path=?",
            (relative_path,),
        ).fetchone()
    if row is None:
        return None
    root = vault_root.expanduser().resolve()
    source = root / relative_path
    stale = True
    if _is_eligible(source, root):
        try:
            source_bytes = secure_read_bytes(root, relative_path)
            stale = hashlib.sha256(source_bytes).hexdigest() != str(row["checksum"])
        except OSError:
            stale = True
    lines = str(row["content"] or "").splitlines()[:max_lines]
    return {
        "vault_id": vault_id,
        "title": str(row["title"]),
        "path": str(row["path"]),
        "line_start": 1,
        "line_end": max(1, len(lines)),
        "source_sha256": str(row["checksum"]),
        "index_generation": actual_generation,
        "stale": stale,
        "excerpt": "\n".join(lines),
    }


def _clean_cjk_run(run: str) -> str:
    changed = True
    while changed:
        changed = False
        for prefix in _CJK_PREFIXES:
            if run.startswith(prefix) and len(run) > len(prefix) + 1:
                run = run.removeprefix(prefix)
                changed = True
                break
        for suffix in _CJK_SUFFIXES:
            if run.endswith(suffix) and len(run) > len(suffix) + 1:
                run = run.removesuffix(suffix)
                changed = True
                break
    return run


def extract_query_terms(text: str) -> list[str]:
    """Extract bounded English tokens and meaningful CJK runs."""
    terms: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        value = value.casefold().strip()
        if value and value not in seen and len(terms) < 64:
            seen.add(value)
            terms.append(value)

    for word in re.findall(r"[a-zA-Z0-9_][a-zA-Z0-9_.-]{1,}", text):
        if word.casefold() not in _MESSAGE_STOPWORDS:
            add(word)
    for raw in re.findall(r"[\u3400-\u9fff]{2,}", text):
        run = _clean_cjk_run(raw)
        if len(run) <= 12:
            add(run)
        for width in (4, 3, 2):
            for offset in range(min(max(0, len(run) - width + 1), 24)):
                add(run[offset : offset + width])
    for phrase, aliases in _CONCEPT_ALIASES:
        if phrase in text:
            for alias in aliases:
                add(alias)
    return terms


def _maximal_terms(terms: list[str]) -> list[str]:
    return [
        term
        for term in dict.fromkeys(terms)
        if not any(term != other and term in other for other in terms)
    ]


def _search_terms(terms: list[str]) -> tuple[list[str], list[str]]:
    english = _maximal_terms([item for item in terms if not re.search(r"[\u3400-\u9fff]", item)])
    cjk = list(
        dict.fromkeys(
            item for item in terms if re.search(r"[\u3400-\u9fff]", item) and 2 <= len(item) <= 4
        )
    )
    if not cjk:
        cjk = _maximal_terms([item for item in terms if re.search(r"[\u3400-\u9fff]", item)])
    return english, cjk


def _fts_escape(term: str) -> str:
    return re.sub(r"[\"'*(){}:+]", "", term.strip()).replace("-", " ").strip()


def _query_rows(
    connection: sqlite3.Connection,
    *,
    table: str,
    terms: Iterable[str],
    limit: int,
    match_all: bool = False,
) -> list[sqlite3.Row]:
    quoted = [
        f'"{escaped.replace(chr(34), chr(34) * 2)}"'
        for term in terms
        if (escaped := _fts_escape(term))
    ]
    if not quoted:
        return []
    return connection.execute(
        f"""
        SELECT n.title, n.content, n.path, n.ulid, n.checksum, n.mtime_ns, n.size,
               bm25({table}, 0.0, 6.0, 3.0, 1.0) AS lexical_score
        FROM {table}
        JOIN notes n ON n.ulid = {table}.ulid
        WHERE {table} MATCH ?
        ORDER BY bm25({table}, 0.0, 6.0, 3.0, 1.0), n.path
        LIMIT ?
        """,
        ((" AND " if match_all else " OR ").join(quoted), limit),
    ).fetchall()


def _english_rows(
    connection: sqlite3.Connection, terms: list[str], *, limit: int
) -> list[sqlite3.Row]:
    if not terms:
        return []
    strict = _query_rows(connection, table="notes_fts", terms=terms, limit=limit, match_all=True)
    if strict or len(terms) <= 2:
        return strict
    required = max(2, math.ceil(len(terms) * 0.6))
    collected: dict[str, sqlite3.Row] = {}
    attempts = 0
    for width in range(len(terms) - 1, required - 1, -1):
        for combination in itertools.combinations(terms, width):
            for row in _query_rows(
                connection,
                table="notes_fts",
                terms=combination,
                limit=limit,
                match_all=True,
            ):
                collected[str(row["ulid"])] = row
                if len(collected) >= limit:
                    return list(collected.values())
            attempts += 1
            if attempts >= 16:
                return list(collected.values())
    return list(collected.values())


def _bigram_rows(
    connection: sqlite3.Connection, terms: list[str], *, limit: int
) -> list[sqlite3.Row]:
    bigrams = list(dict.fromkeys(item for item in terms if len(item) == 2))
    if not bigrams:
        return []
    placeholders = ",".join("?" for _ in bigrams)
    try:
        selective = connection.execute(
            f"SELECT gram FROM bigram_stats WHERE gram IN ({placeholders}) "
            "ORDER BY document_count, gram LIMIT 4",
            bigrams,
        ).fetchall()
        bigrams = [str(row[0]) for row in selective]
    except sqlite3.OperationalError:
        bigrams = bigrams[:4]
    if not bigrams:
        return []
    placeholders = ",".join("?" for _ in bigrams)
    return connection.execute(
        f"""
        SELECT n.title, n.content, n.path, n.ulid, n.checksum, n.mtime_ns, n.size,
               -COUNT(*) AS lexical_score
        FROM note_bigrams b JOIN notes n ON n.ulid = b.ulid
        WHERE b.gram IN ({placeholders})
        GROUP BY n.ulid
        ORDER BY COUNT(*) DESC, n.path
        LIMIT ?
        """,
        (*bigrams, limit),
    ).fetchall()


def _fallback_like_rows(
    connection: sqlite3.Connection, terms: list[str], *, limit: int
) -> list[sqlite3.Row]:
    usable = [term for term in terms if len(term) >= 2][:6]
    if not usable:
        return []
    clauses = " OR ".join("instr(lower(n.title || '\n' || n.content), ?) > 0" for _ in usable)
    return connection.execute(
        f"""
        SELECT n.title, n.content, n.path, n.ulid, n.checksum, n.mtime_ns, n.size,
               0.0 AS lexical_score
        FROM notes n WHERE {clauses} ORDER BY n.path LIMIT ?
        """,
        (*[item.casefold() for item in usable], limit),
    ).fetchall()


def _excerpt_with_lines(content: str, terms: list[str], *, max_chars: int) -> tuple[str, int, int]:
    folded = content.casefold()
    positions = [
        position for term in terms if term and (position := folded.find(term.casefold())) >= 0
    ]
    position = min(positions) if positions else 0
    lines = content.splitlines()
    if not lines:
        return "", 1, 1
    line_starts = [0]
    line_starts.extend(match.end() for match in re.finditer(r"\r\n|\r|\n", content))
    hit_line = max(0, bisect.bisect_right(line_starts, position) - 1)
    start = max(0, hit_line - 2)
    selected: list[str] = []
    used = 0
    end = start
    for index in range(start, len(lines)):
        line = lines[index]
        cost = len(line) + (1 if selected else 0)
        if selected and used + cost > max_chars:
            break
        selected.append(line)
        used += cost
        end = index
        if used >= max_chars:
            break
    excerpt = "\n".join(selected)[:max_chars]
    return excerpt, start + 1, end + 1


def _qualifies(
    english: list[str], cjk: list[str], searchable: str, title_and_path: str
) -> tuple[bool, tuple[int, float, int]]:
    matched_english = sum(item in searchable for item in english)
    matched_cjk = sum(item in searchable for item in cjk)
    english_required = max(1, math.ceil(len(english) * 0.6)) if english else 0
    cjk_required = max(2, math.ceil(len(cjk) * 0.1)) if cjk else 0
    title_matches = sum(item in title_and_path for item in (*english, *cjk))
    acceptable = (bool(english) and matched_english >= english_required) or (
        bool(cjk) and (matched_cjk >= cjk_required or (title_matches >= 1 and matched_cjk >= 2))
    )
    total = matched_english + matched_cjk
    coverage = matched_english / max(1, len(english)) + matched_cjk / max(1, len(cjk))
    return acceptable, (title_matches, coverage, total)


def search_index(
    index_path: Path,
    query: str,
    *,
    limit: int = 5,
    excerpt_chars: int = 800,
    vault_root: Path | None = None,
    vault_id: str = "",
    generation: str = "",
) -> list[dict[str, Any]]:
    """Search one immutable index and return resolvable source evidence."""
    if not index_path.is_file():
        raise FileNotFoundError(f"Vault index does not exist: {index_path}")
    uri = f"file:{index_path.resolve().as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True, timeout=5.0)
    connection.row_factory = sqlite3.Row
    try:
        _metadata_values, actual_generation = _validate_index_binding(
            connection,
            index_path,
            vault_root=vault_root,
            vault_id=vault_id,
            generation=generation,
        )
        terms = extract_query_terms(query)
        if not terms:
            return []
        english, cjk = _search_terms(terms)
        native_alpha = any(
            item.casefold() not in _MESSAGE_STOPWORDS
            for item in re.findall(r"[a-zA-Z][a-zA-Z_.-]{2,}", query)
        )
        cjk_lookup = (
            cjk
            if not english or (not native_alpha and any(char.isdigit() for char in query))
            else []
        )
        candidates: dict[str, sqlite3.Row] = {}
        scores: dict[str, float] = {}
        candidate_limit = max(40, limit * 10)
        english_rows = _english_rows(connection, english, limit=candidate_limit)
        for row in english_rows:
            candidates[str(row["ulid"])] = row
            scores[str(row["ulid"])] = float(row["lexical_score"])
        if cjk_lookup:
            try:
                bigram_candidates = _bigram_rows(connection, cjk_lookup, limit=max(50, limit * 10))
            except sqlite3.OperationalError:
                bigram_candidates = _fallback_like_rows(
                    connection, cjk_lookup, limit=candidate_limit
                )
            for row in bigram_candidates:
                candidates[str(row["ulid"])] = row
                scores[str(row["ulid"])] = min(
                    scores.get(str(row["ulid"]), 0.0), float(row["lexical_score"])
                )

        ranked: list[tuple[tuple[int, float, int, float], sqlite3.Row]] = []
        for ulid, row in candidates.items():
            title = str(row["title"] or "")
            content = str(row["content"] or "")
            path = str(row["path"] or "")
            searchable = re.sub(r"[_/\\-]+", " ", f"{title}\n{path}\n{content}").casefold()
            acceptable, quality = _qualifies(
                english,
                cjk,
                searchable,
                re.sub(r"[_/\\-]+", " ", f"{title}\n{path}").casefold(),
            )
            if acceptable:
                ranked.append(((*quality, -scores.get(ulid, 0.0)), row))
        ranked.sort(key=lambda item: item[0], reverse=True)

        results: list[dict[str, Any]] = []
        resolved_root = vault_root.resolve() if vault_root else None
        for quality, row in ranked[:limit]:
            content = str(row["content"] or "")
            excerpt, line_start, line_end = _excerpt_with_lines(
                content, terms, max_chars=excerpt_chars
            )
            relative_path = str(row["path"])
            stale = False
            if resolved_root is not None:
                try:
                    source_bytes = secure_read_bytes(resolved_root, relative_path)
                    stale = hashlib.sha256(source_bytes).hexdigest() != str(row["checksum"])
                except (OSError, ValueError):
                    stale = True
            results.append(
                {
                    "vault_id": vault_id,
                    "title": str(row["title"]),
                    "path": relative_path,
                    "line_start": line_start,
                    "line_end": line_end,
                    "source_sha256": str(row["checksum"]),
                    "index_generation": actual_generation,
                    "stale": stale,
                    "excerpt": excerpt,
                    "score": round(
                        quality[0] * 1000 + quality[1] * 100 + quality[2] * 10 + quality[3],
                        6,
                    ),
                }
            )
        return results
    except sqlite3.Error as exc:
        raise RuntimeError(f"Vault index query failed: {index_path}: {exc}") from exc
    finally:
        connection.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    sync_parser = subparsers.add_parser("sync")
    sync_parser.add_argument("--vault", type=Path, required=True)
    sync_parser.add_argument("--index", type=Path, required=True)
    search_parser = subparsers.add_parser("search")
    search_parser.add_argument("query")
    search_parser.add_argument("--index", type=Path, required=True)
    search_parser.add_argument("--vault-root", type=Path)
    search_parser.add_argument("--limit", type=int, default=5)
    search_parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    if args.command == "sync":
        count = build_or_sync_index(args.vault, args.index)
        print(f"Indexed {count} Markdown notes into {args.index}")
        return 0
    results = search_index(args.index, args.query, limit=args.limit, vault_root=args.vault_root)
    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
    elif not results:
        print("No relevant notes found.")
    else:
        for item in results:
            print(
                f"## {item['title']}\n{item['excerpt']}\n"
                f"_Source: {item['path']}:{item['line_start']}_\n"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
