"""Shared wikilink resolution and conservative per-occurrence triage.

The precedence levels are treated as hypotheses: these tests pin the behaviour we
actually implement and the honesty guarantees (no silent resolution, no verdict
from a name alone), not an assertion that Obsidian is reproduced exactly.
"""

# ruff: noqa: E402
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from fts_indexer import scan_notes
from link_graph import build_link_graph
from link_resolver import (
    LEVEL_ALIAS,
    LEVEL_BASENAME,
    LEVEL_RELATIVE,
    LEVEL_VAULT_PATH,
    STATUS_AMBIGUOUS,
    STATUS_RESOLVED,
    STATUS_UNRESOLVED,
    LinkReference,
    LinkResolver,
    aliases,
    extract_links,
)
from vault_doctor import diagnose_vault, triage_hint


def _resolver(notes: dict[str, str], assets: list[str] | None = None) -> LinkResolver:
    return LinkResolver.from_sources(notes=notes.items(), assets=assets or [])


def _decide(resolver: LinkResolver, source: str, target: str):
    return resolver.resolve(LinkReference(source=source, line=1, target=target))


def test_relative_path_beats_a_vault_unique_basename():
    resolver = _resolver(
        {
            "Area/Note.md": "",
            "Other/Note.md": "",
            "Area/Source.md": "",
        }
    )
    decision = _decide(resolver, "Area/Source.md", "Note")
    assert decision.status == STATUS_RESOLVED
    assert decision.level == LEVEL_RELATIVE
    assert decision.destination == "Area/Note.md"


def test_vault_path_is_used_when_no_relative_match():
    resolver = _resolver({"Area/Deep/Note.md": "", "Area/Source.md": ""})
    decision = _decide(resolver, "Area/Source.md", "Area/Deep/Note")
    assert decision.status == STATUS_RESOLVED
    assert decision.level == LEVEL_VAULT_PATH
    assert decision.destination == "Area/Deep/Note.md"


def test_unique_basename_resolves_from_an_unrelated_folder():
    resolver = _resolver({"Elsewhere/Only.md": "", "Area/Source.md": ""})
    decision = _decide(resolver, "Area/Source.md", "Only")
    assert decision.status == STATUS_RESOLVED
    assert decision.level == LEVEL_BASENAME


def test_first_level_with_matches_decides_even_when_a_later_level_is_unique():
    """Two basename matches stay ambiguous; a unique alias must not silently win."""
    resolver = _resolver(
        {
            "Deep/A/Note.md": "---\naliases:\n  - Note\n---\n",
            "Deep/B/Note.md": "",
            "Area/Source.md": "",
        }
    )
    decision = _decide(resolver, "Area/Source.md", "Note")
    assert decision.status == STATUS_AMBIGUOUS
    assert decision.level == LEVEL_BASENAME
    assert set(decision.candidates) == {"Deep/A/Note.md", "Deep/B/Note.md"}


def test_ambiguity_reports_a_proximity_candidate_without_resolving():
    resolver = _resolver(
        {
            "Deep/Sub/Note.md": "",
            "Far/Note.md": "",
            "Deep/Source.md": "",
        }
    )
    decision = _decide(resolver, "Deep/Source.md", "Note")
    assert decision.status == STATUS_AMBIGUOUS
    assert decision.destination is None
    assert decision.nearest_candidate == "Deep/Sub/Note.md"
    assert decision.nearest_unique is True


def test_alias_resolution_and_unresolved_target():
    resolver = _resolver(
        {"Area/Person.md": "---\naliases:\n  - 张一帆\n---\n", "Area/Source.md": ""}
    )
    assert _decide(resolver, "Area/Source.md", "张一帆").level == LEVEL_ALIAS
    missing = _decide(resolver, "Area/Source.md", "NoSuchThing")
    assert missing.status == STATUS_UNRESOLVED
    assert missing.level == "none"
    assert missing.destination is None


def test_asset_reference_resolves_by_name_and_stays_out_of_edges():
    resolver = _resolver({"Area/Source.md": "![[diagram.png]]"}, ["Area/diagram.png"])
    decision = _decide(resolver, "Area/Source.md", "diagram.png")
    assert decision.status == STATUS_RESOLVED
    assert decision.destination == "Area/diagram.png"


def test_doctor_and_graph_agree_because_they_share_one_resolver(tmp_path):
    root = tmp_path / "vault"
    (root / "Area").mkdir(parents=True)
    (root / "Other").mkdir()
    (root / "Area" / "Note.md").write_text("# Note\n[[Other Note]]", encoding="utf-8")
    (root / "Other" / "Other Note.md").write_text("# Other\n[[Note]]", encoding="utf-8")
    (root / "Area" / "Broken.md").write_text("# B\n[[Nope]]", encoding="utf-8")
    (root / "Area" / "Dup.md").write_text("# D\n[[Note]]", encoding="utf-8")
    (root / "Other" / "Note.md").write_text("# N2\n", encoding="utf-8")

    scan = scan_notes(root)
    doctor = diagnose_vault(root, source_scan=scan)
    graph = build_link_graph(scan, vault_root=root)

    assert doctor["broken_link_occurrences"] == graph["unresolved_links"]
    assert doctor["ambiguous_link_occurrences"] == graph["ambiguous_links"]
    resolved_occurrences = doctor["links_resolved"]
    self_links = sum(
        1
        for decision in LinkResolver.from_scan(scan, vault_root=root).decide_all(
            link for record in scan.records for link in extract_links(record.path, record.content)
        )
        if decision.status == STATUS_RESOLVED and decision.destination == decision.source
    )
    assert graph["edge_count"] + graph["asset_links"] + self_links == resolved_occurrences
    assert sum(doctor["resolution_levels"].values()) == (
        resolved_occurrences
        + doctor["broken_link_occurrences"]
        + doctor["ambiguous_link_occurrences"]
    )


def test_triage_labels_are_hints_and_never_verdicts():
    hint, reason, confidence = triage_hint(
        "300_Resources/301_ZoteroNotes/2004_Liston_KEY-X.md", "Snow_model"
    )
    assert hint == "zotero_generated"
    assert confidence == "hint"
    assert "zotero" in reason.casefold()

    assert triage_hint("200_Area/Templates/T.md", "anything")[0] == "template_placeholder"
    assert triage_hint("000_Inbox/README.md", "Link")[0] == "template_placeholder"
    assert (
        triage_hint("200_Area/234_Qatar/2 Contract Dispute.md", "02.07 Attachment C.pdf")[0]
        == "missing_asset_reference"
    )
    # A plausible note name must not be promoted to a verdict.
    assert triage_hint("200_Area/233_Tech_note/x.md", "CCS")[0] == "needs_review"

    for source, target in (
        ("300_Resources/301_ZoteroNotes/x.md", "Snow_model"),
        ("000_Inbox/README.md", "任务"),
        ("somewhere/plain.md", "CCS"),
    ):
        hint, reason, confidence = triage_hint(source, target)
        assert confidence == "hint"
        assert reason
        assert hint in {
            "zotero_generated",
            "template_placeholder",
            "missing_asset_reference",
            "needs_review",
        }
        assert hint not in {"actionable", "noise", "ignore"}


def test_triage_reasons_are_attached_to_every_broken_occurrence(tmp_path):
    root = tmp_path / "vault"
    (root / "300_Resources" / "301_ZoteroNotes").mkdir(parents=True)
    (root / "300_Resources" / "301_ZoteroNotes" / "z.md").write_text(
        "# z\n[[Snow_model]]\n[[CCS]]", encoding="utf-8"
    )
    report = diagnose_vault(root)
    assert report["broken_link_occurrences"] == 2
    assert all(item["triage_reason"] for item in report["broken_links"])
    assert report["broken_by_hint"]["zotero_generated"] == 2
    assert report["scan_complete"] is True
    # The original total is retained, never replaced by the hint breakdown.
    assert sum(report["broken_by_hint"].values()) == report["broken_link_occurrences"]


def test_alias_and_link_extraction_helpers():
    content = "---\naliases: [A, B]\n---\n[[One]] and [[Two|alias]] and [[Three#head]]"
    assert aliases(content) == ["A", "B"]
    targets = [link.target for link in extract_links("n.md", content)]
    assert targets == ["One", "Two", "Three"]


def test_scan_incomplete_is_reported_not_hidden(tmp_path):
    root = tmp_path / "vault"
    root.mkdir()
    (root / "ok.md").write_text("# ok\n", encoding="utf-8")
    (root / "bad.md").write_bytes(b"\xff\xfe\x00")
    report = diagnose_vault(root)
    assert report["scan_complete"] is False
    assert report["skipped_decode"] == ["bad.md"]
