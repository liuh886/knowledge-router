#!/usr/bin/env python3
"""Read-only data-foundation inventory; no Context, registry or source mutation."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from fts_indexer import ScanReport, scan_notes

RULES_VERSION = "data-inventory-1"
_FRONTMATTER = re.compile(r"\A---[^\S\r\n]*\r?\n(.*?)\r?\n---[^\S\r\n]*(?:\r?\n|\Z)", re.S)
_FIELD = re.compile(r"^([A-Za-z_][\w-]*):[^\S\r\n]*(.*)$")
_BASE64 = re.compile(r"data:image/[\w.+-]+;base64,[A-Za-z0-9+/=\r\n]+")


def simple_scalar(value: str) -> tuple[str, str | None]:
    """Conservative scalar subset; unsupported YAML is unknown, not invalid."""
    value = value.strip()
    if not value or value.lower() in {"null", "~"}:
        return "empty", None
    if value.startswith('"'):
        try:
            decoded = json.loads(value)
        except ValueError:
            return "unparsed", None
        return ("scalar", decoded) if isinstance(decoded, str) else ("unparsed", None)
    if value.startswith("'"):
        if len(value) >= 2 and value.endswith("'"):
            inner = value[1:-1]
            if "'" not in inner.replace("''", ""):
                return "scalar", inner.replace("''", "'")
        return "unparsed", None
    if value[0] in "[{|>&*!%@`" or value.startswith("-") or re.search(r":\s", value):
        return "unparsed", None
    value = re.split(r"\s+#", value, maxsplit=1)[0].rstrip()
    if not value or value.startswith("#"):
        return "empty", None
    return "scalar", value


def inspect_note(path: str, content: str, checksum: str) -> dict[str, Any]:
    match = _FRONTMATTER.match(content)
    fields: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if match:
        for number, line in enumerate(match.group(1).splitlines(), 2):
            field = _FIELD.match(line)
            if field:
                state, value = simple_scalar(field.group(2))
                fields[field.group(1)].append({"line": number, "state": state, "value": value})

    def scalar(key: str) -> str | None:
        entries = fields.get(key, [])
        return entries[0]["value"] if len(entries) == 1 else None

    note_type = scalar("type")
    parts = path.casefold().split("/")
    historical = any(
        "backup" in part or "archive" in part or "snapshot" in part for part in parts[:-1]
    ) or "backup" in parts[-1]
    if any("template" in part for part in parts):
        role = "template_candidate"
    elif scalar("schema_version") and scalar("id"):
        role = "structured_record_candidate"
    elif note_type == "capture" or re.search(r"(?:^|/)(?:idea|memo|todo|biography|investment)\.\d{4}\.md$", path):
        role = "raw_capture_candidate"
    elif note_type in {"dashboard", "project-index", "system-guide"}:
        role = "navigation_candidate"
    else:
        role = "prose_or_unknown"
    body = content[match.end():] if match else content
    data = list(_BASE64.finditer(content))
    return {
        "path": path, "source_sha256": checksum,
        "frontmatter_present": bool(match), "role_hint": role,
        "historical_path_hint": historical,
        "type": note_type, "schema_version": scalar("schema_version"),
        "fields": dict(fields), "body_characters": len(body.strip()),
        "embedded_image_count": len(data),
        "embedded_image_characters": sum(len(item.group()) for item in data),
    }


def manifest_digest(scan: ScanReport) -> str:
    manifest = sorted((note.path, note.checksum) for note in scan.records)
    return hashlib.sha256(json.dumps(manifest, ensure_ascii=False).encode("utf-8")).hexdigest()


def build_inventory(scan: ScanReport, *, limit: int = 10) -> dict[str, Any]:
    if not 0 <= limit <= 100:
        raise ValueError("limit must be between 0 and 100")
    notes = [inspect_note(n.path, n.content, n.checksum) for n in scan.records]
    identifiers: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    duplicate_fields, unparsed, payloads = [], [], []
    field_counts: Counter[str] = Counter()
    for note in notes:
        evidence = {key: note[key] for key in ("path", "source_sha256", "role_hint", "historical_path_hint")}
        for key, entries in note["fields"].items():
            field_counts[key] += 1
            if len(entries) > 1:
                duplicate_fields.append({**evidence, "field": key, "lines": [e["line"] for e in entries]})
            if key in {"ulid", "id", "type", "schema_version"}:
                for entry in entries:
                    if entry["state"] == "unparsed":
                        unparsed.append({**evidence, "field": key, "line": entry["line"]})
            if key not in {"ulid", "id"} or len(entries) != 1:
                continue
            entry = entries[0]
            if entry["state"] != "scalar" or not entry["value"]:
                continue
            # Business IDs are not globally unique across unrelated schemas/types.
            namespace = (key,) if key == "ulid" else (
                key, note["schema_version"] or "unknown", note["type"] or "unknown"
            )
            identifiers[(*namespace, entry["value"])].append({**evidence, "line": entry["line"]})
        if note["embedded_image_count"]:
            payloads.append({**evidence, "count": note["embedded_image_count"],
                             "characters": note["embedded_image_characters"]})
    duplicates = [
        {"namespace": list(key[:-1]), "identifier": key[-1], "occurrences": len(items),
         "sources": items}
        for key, items in sorted(identifiers.items()) if len(items) > 1
    ]
    payloads.sort(key=lambda item: (-item["characters"], item["path"]))
    complete = not scan.skipped_decode and not scan.skipped_io

    def bounded(items: list[dict[str, Any]]) -> dict[str, Any]:
        # Source lists inside duplicate groups are bounded too; totals stay complete.
        visible = []
        for item in items[:limit]:
            if "sources" in item:
                item = {**item, "sources": item["sources"][:limit],
                        "sources_omitted": max(0, len(item["sources"]) - limit)}
            visible.append(item)
        return {"total": len(items), "items": visible, "omitted": max(0, len(items) - limit)}

    samples = {}
    for family in ("raw_capture_candidate", "prose_or_unknown", "structured_record_candidate"):
        members = [n for n in notes if n["role_hint"] == family and not n["historical_path_hint"]]
        samples[family] = bounded(members)
    return {
        "schema_version": 1, "rules_version": RULES_VERSION,
        "notes_scanned": len(notes), "scan_complete": complete,
        "manifest_sha256": manifest_digest(scan),
        "counts_by_role_hint": dict(Counter(n["role_hint"] for n in notes)),
        "field_presence_counts": dict(sorted(field_counts.items())),
        "identifier_candidates": bounded(duplicates),
        "duplicate_top_level_fields": bounded(duplicate_fields),
        "unparsed_identity_fields": bounded(unparsed),
        "embedded_image_payloads": bounded(payloads), "pilot_samples": samples,
        "skipped_decode": list(scan.skipped_decode), "skipped_io": list(scan.skipped_io),
        "source_manifest": [{"path": n.path, "source_sha256": n.checksum} for n in scan.records],
        "writes_performed": 0,
        "limits": ["Top-level simple scalars only; not YAML validation",
                   "Role/path hints are not authority, defects or deletion decisions",
                   "Shared object IDs may be legitimate snapshots; inspect domain schema",
                   "Missing IDs/short prose are not automatically findings"],
    }


def audit_inventory(root: Path, *, limit: int = 10) -> dict[str, Any]:
    first = scan_notes(root)
    report = build_inventory(first, limit=limit)
    second = scan_notes(root)
    report["checked_at"] = datetime.now(UTC).isoformat()
    report["verification_manifest_sha256"] = manifest_digest(second)
    report["verification_skipped_decode"] = list(second.skipped_decode)
    report["verification_skipped_io"] = list(second.skipped_io)
    report["source_set_stable"] = report["manifest_sha256"] == manifest_digest(second)
    report["complete"] = bool(report["scan_complete"] and not second.skipped_decode
                              and not second.skipped_io and report["source_set_stable"])
    report["status"] = "inventory" if report["complete"] else "incomplete"
    report["limits"].append("Two matching manifests are not an atomic filesystem snapshot")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vault-root", type=Path, required=True)
    parser.add_argument("--limit", type=int, choices=range(101), default=10, metavar="0..100")
    args = parser.parse_args()
    try:
        report = audit_inventory(args.vault_root, limit=args.limit)
    except (OSError, ValueError) as exc:
        print(json.dumps({"status": "error", "error": str(exc), "writes_performed": 0}))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
