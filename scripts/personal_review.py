"""Read-only checks for the optional personal profile, over the shared source scan.

These checks flag missing review information; they never certify claims, calculate
portfolio returns, reconcile trades, or infer that an input has been processed.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any

PERSONAL_DIAGNOSTICS_VERSION = 1


def stream_catalog(root: Path) -> dict[str, Any]:
    from memory_service import STREAMS

    rows = []
    for kind, (folder, stem, _title, _type) in STREAMS.items():
        parent = root / folder
        linked = any(p.is_symlink() or getattr(p, "is_junction", lambda: False)() for p in [parent, *parent.parents]
                     if p != root and root in p.parents)
        rows.append({
            "type": kind, "target_pattern": f"{folder}/{stem}.{{year}}.md",
            "status": "available" if parent.is_dir() and not linked else "unavailable",
            "meaning": "raw input; no automatic promotion or ledger mutation",
        })
    return {"profile": "personal", "streams": rows, "creates_directories": False}


def _fields(text: str) -> dict[str, str]:
    # Only inspect the profile's simple top-level scalars. This is not a YAML parser.
    match = re.match(r"\A\ufeff?---\s*\r?\n(.*?)\r?\n---", text, re.S)
    if not match:
        return {}
    return {key: value.strip().strip('\"\'') for key, value in
            re.findall(r"(?m)^([a-z_]+):[ \t]*(.*)$", match[1])}


def _empty(value: str | None) -> bool:
    return value is None or value.casefold() in {"", "null", "~", "[]"}


def personal_source_state(path: str, content: str) -> dict[str, Any] | None:
    """Disclose source-declared state beside excerpts; never certify a source."""
    fields = _fields(content)
    if path.startswith("200_Area/Biography/Milestones/"):
        return {"kind": "achievement", "status": fields.get("status"),
                "verification_status": fields.get("verification_status", "unknown"),
                "verified_at": None if _empty(fields.get("verified_at")) else fields["verified_at"],
                "meaning": "source declaration, not Router certification"}
    if path.startswith("200_Area/Investment/"):
        return {"kind": fields.get("type", "investment-reference"),
                "last_verified": None if _empty(fields.get("last_verified")) else fields["last_verified"],
                "meaning": "raw intake is not a booked transaction; snapshot is not a live balance"}
    return None


def personal_findings(scan: Any, *, today: date | None = None) -> list[dict[str, Any]]:
    today = today or date.today()
    items: list[dict[str, Any]] = []
    tickers: dict[tuple[str, str], list[str]] = defaultdict(list)

    def add(note: Any, code: str, reason: str, suggestion: str, severity: str = "review") -> None:
        items.append({
            "kind": "domain_review", "code": code, "path": note.path,
            "reason": reason, "suggestion": suggestion, "severity": severity,
            "source_sha256": note.checksum,
        })

    for note in scan.records:
        fields = _fields(note.content)
        if note.path.startswith("200_Area/Biography/Milestones/"):
            verification = fields.get("verification_status")
            if verification not in {"draft", "pending", "verified", "disputed"}:
                add(note, "biography_verification_missing", "No explicit verification state",
                    "Review legacy status; do not infer verification from an existing evidence path")
            elif verification != "verified":
                add(note, "biography_pending_review", f"Verification state: {verification}",
                    "Check source passages and personal contribution before external use")
            else:
                missing = [key for key in ("verified_at", "verified_by", "source_locator")
                           if _empty(fields.get(key))]
                if missing:
                    add(note, "biography_review_receipt_missing", "Missing: " + ", ".join(missing),
                        "Record an actual review, not the file modification date", "high")
                if not _empty(fields.get("verified_at")):
                    try:
                        if date.fromisoformat(fields["verified_at"]) > today:
                            raise ValueError
                    except ValueError:
                        add(note, "biography_review_date_invalid", "Invalid or future review date",
                            "Record the date on which sources were actually checked", "high")
            if _empty(fields.get("evidence")) and _empty(fields.get("source_ref")):
                add(note, "biography_evidence_missing", "No evidence/source_ref",
                    "Provide a specific source; an unverified narrative is not evidence", "high")
            if fields.get("type") != "milestone" or fields.get("tier") not in {
                "L1-milestone", "L2-deliverable", "L3-breakthrough"
            }:
                add(note, "biography_schema_invalid", "Invalid type or tier", "Use the area schema")
            try:
                date.fromisoformat(fields.get("date", ""))
            except ValueError:
                add(note, "biography_date_invalid", "Missing or invalid date",
                    "Record an ISO date and preserve uncertainty with date_precision")
        elif (note.path.startswith(("200_Area/Investment/Pool/", "200_Area/Investment/Archives/"))
              and fields.get("type") == "stock-holding"):
            ticker = fields.get("ticker", "")
            if ticker:
                tickers[(fields.get("market", ""), ticker.casefold())].append(note.path)
            missing = [key for key in ("ticker", "currency", "data_source")
                       if _empty(fields.get(key))]
            if missing:
                add(note, "investment_source_incomplete", "Missing: " + ", ".join(missing),
                    "Keep unknown facts explicit; obtain the source before posting")
            for key in ("shares", "total_cost"):
                try:
                    number = Decimal(fields.get(key, ""))
                    if not number.is_finite() or number < 0:
                        raise InvalidOperation
                except InvalidOperation:
                    add(note, "investment_snapshot_invalid", f"Invalid {key}",
                        "Reconcile with the ledger using the investment schema", "high")
            verified = fields.get("last_verified", "")
            try:
                verified_date = date.fromisoformat(verified)
                if verified_date > today:
                    raise ValueError
            except ValueError:
                add(note, "investment_verification_missing", "No valid past verification date",
                    "Compare the current snapshot with broker evidence when needed")
            else:
                if (today - verified_date).days > 30:
                    add(note, "investment_review_due", "Snapshot was verified over 30 days ago",
                        "Review before using balances; age alone does not mean data is wrong")
            if "pending_checks" in fields and fields["pending_checks"] != "[]":
                block = re.search(r"(?m)^pending_checks:[ \t]*\r?\n((?:[ \t]+[^\r\n]*\r?\n?)+)", note.content)
                if not _empty(fields["pending_checks"]) or (block and "- " in block[1]):
                    add(note, "investment_pending_checks", "Recorded unresolved checks",
                        "Resolve using source evidence; do not change ledger values to match a snapshot")
        if re.search(r"/(?:idea|memo|todo|biography|investment)\.\d{4}\.md$", note.path):
            heading_years = set(re.findall(r"(?m)^##[ \t]+(\d{4})-\d{2}[ \t]*$", note.content))
            file_year = note.path.rsplit(".", 2)[1]
            if heading_years - {file_year}:
                add(note, "capture_year_mismatch", "Month heading disagrees with annual file",
                    "Inspect source dates before correcting; no automatic relocation")
    for (_market, ticker), paths in tickers.items():
        if len(paths) > 1:
            for path in paths:
                items.append({"kind": "domain_review", "code": "investment_duplicate_ticker",
                              "path": path, "target": ticker, "severity": "high",
                              "reason": "Multiple files share ticker and market",
                              "suggestion": "Inspect Pool and Archives before recording another transaction"})
    return sorted(items, key=lambda row: (row["code"], row["path"]))
