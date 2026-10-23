"""Lossless UTF-8 document framing shared by readers, profiles and the broker."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


def newline_style(existing: bytes) -> tuple[str, str]:
    if existing.startswith((b"\xff\xfe", b"\xfe\xff")):
        raise UnicodeError("UTF-16 notes require explicit conversion before mutation")
    existing.decode("utf-8-sig")
    bom = "utf-8" if existing.startswith(b"\xef\xbb\xbf") else "none"
    without_crlf = existing.replace(b"\r\n", b"")
    counts = {
        "\r\n": existing.count(b"\r\n"),
        "\n": without_crlf.count(b"\n"),
        "\r": without_crlf.count(b"\r"),
    }
    maximum = max(counts.values())
    if not maximum:
        return "\n", bom
    winners = {key for key, count in counts.items() if count == maximum}
    first = re.search(rb"\r\n|\r|\n", existing)
    preferred = first.group().decode("ascii") if first else "\n"
    return (preferred if preferred in winners else sorted(winners)[0]), bom


@dataclass(frozen=True)
class TextDocument:
    raw: bytes

    @property
    def text(self) -> str:
        newline_style(self.raw)
        return self.raw.decode("utf-8-sig")

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.raw).hexdigest()

    @property
    def newline(self) -> str:
        return newline_style(self.raw)[0]

    def encode(self, text: str) -> bytes:
        return (b"\xef\xbb\xbf" if self.raw.startswith(b"\xef\xbb\xbf") else b"") + text.encode(
            "utf-8"
        )


def append_section(document: TextDocument, heading: str, entry: str) -> bytes:
    """Append a section/entry without ever re-encoding the source prefix.

    Repeated headings are intentional when the matching section is no longer last.
    Inserting into an old section would be a full edit, not an append.
    """
    text, nl = document.text, document.newline
    sections = re.findall(r"(?m)^##[ \t]+([^\r\n]+)", text)
    suffix = "" if not text or text.endswith(("\r", "\n")) else nl
    if not sections or sections[-1].strip() != heading:
        if text and not (text + suffix).endswith(nl * 2):
            suffix += nl
        suffix += f"## {heading}{nl}{nl}"
    suffix += entry.replace("\r\n", "\n").replace("\r", "\n").replace("\n", nl) + nl
    return document.raw + suffix.encode("utf-8")
