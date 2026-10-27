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


def test_bare_name_resolves_by_unique_filename():
    """Documented default: only the filename is used when it is unique."""
    resolver = _resolver({"Elsewhere/Only.md": "", "Area/Source.md": ""})
    decision = _decide(resolver, "Area/Source.md", "Only")
    assert decision.status == STATUS_RESOLVED
    assert decision.level == LEVEL_BASENAME
    assert decision.destination == "Elsewhere/Only.md"


def test_escaped_table_alias_resolves_existing_dotted_markdown_path():
    resolver = _resolver({"100_Project/Builder.PRD.md": ""})
    refs = extract_links(
        "000_Inbox/inbox.md", r"| [[100_Project/Builder.PRD.md\|Builder]] |"
    )
    decision = resolver.resolve(refs[0])
    assert decision.status == STATUS_RESOLVED
    assert decision.destination == "100_Project/Builder.PRD.md"
    # The fix must not make a genuinely missing path resolve by its basename.
    missing = extract_links("000_Inbox/inbox.md", r"[[Old/Builder.PRD.md\|Builder]]")
    assert resolver.resolve(missing[0]).status == STATUS_UNRESOLVED


def test_table_alias_preserves_asset_and_heading_targets():
    resolver = _resolver({"Area/Note.md": ""}, assets=["Assets/image.png"])
    for text, target in [
        (r"[[Area/Note.md#Section\|section]]", "Area/Note.md"),
        (r"![[Assets/image.png\|100]]", "Assets/image.png"),
        (r"[[Area\Note.md\|note]]", "Area/Note.md"),
    ]:
        decision = resolver.resolve(extract_links("Source.md", text)[0])
        assert decision.destination == target


def test_table_escape_diagnostics_and_graph_agree(tmp_path):
    root = tmp_path / "vault"
    (root / "Projects").mkdir(parents=True)
    (root / "Projects/One.PRD.md").write_text("# One", encoding="utf-8")
    (root / "Index.md").write_text(
        r"| [[Projects/One.PRD.md\|One]] |", encoding="utf-8"
    )
    report = diagnose_vault(root)
    assert report["broken_link_occurrences"] == 0
    assert report["links_resolved"] == 1
    graph = build_link_graph(scan_notes(root), vault_root=root)
    assert graph["unresolved_links"] == 0
    assert graph["edges"] == [("Index.md", "Projects/One.PRD.md")]


def test_explicit_path_cannot_be_overridden_by_a_unique_bare_name():
    """A path-qualified link must resolve by path, not by an unrelated bare match."""
    resolver = _resolver({"Area/Deep/Note.md": "", "Elsewhere/Note.md": "", "Area/Source.md": ""})
    decision = _decide(resolver, "Area/Source.md", "Area/Deep/Note")
    assert decision.status == STATUS_RESOLVED
    assert decision.level == LEVEL_VAULT_PATH
    assert decision.destination == "Area/Deep/Note.md"


def test_unresolved_path_reports_a_unique_basename_fallback_without_using_it():
    """The path fails and a uniquely named file exists elsewhere: undocumented, so
    the link stays unresolved and the possibility is reported instead."""
    resolver = _resolver({"Elsewhere/README.md": "", "Area/Source.md": ""})
    decision = _decide(resolver, "Area/Source.md", "Missing/Folder/README")
    assert decision.status == STATUS_UNRESOLVED
    assert decision.destination is None
    assert decision.basename_fallback == "Elsewhere/README.md"


def test_unresolved_path_with_a_duplicated_basename_reports_no_fallback():
    resolver = _resolver(
        {"Elsewhere/README.md": "", "Other/README.md": "", "Area/Source.md": ""}
    )
    decision = _decide(resolver, "Area/Source.md", "Missing/Folder/README")
    assert decision.status == STATUS_UNRESOLVED
    assert decision.basename_fallback is None


def test_doctor_counts_the_undocumented_fallback_case(tmp_path):
    root = tmp_path / "vault"
    (root / "Area").mkdir(parents=True)
    (root / "Elsewhere").mkdir()
    (root / "Elsewhere" / "README.md").write_text("# R\n", encoding="utf-8")
    (root / "Area" / "Source.md").write_text(
        "# S\n[[Missing/Folder/README]]", encoding="utf-8"
    )
    report = diagnose_vault(root)
    assert report["unresolved_with_unique_basename_fallback"] == 1
    assert report["broken_link_occurrences"] == 1
    assert report["broken_links"][0]["basename_fallback"] == "Elsewhere/README.md"
    assert "unspecified" in report["broken_links"][0]["triage_reason"]
    # Still unresolved: the unknown rule is never decided silently.
    assert report["links_resolved"] == 0


def test_relative_path_form_resolves_next_to_the_note():
    """Documented "relative path from current note" form."""
    resolver = _resolver({"Area/Sub/Note.md": "", "Area/Source.md": ""})
    decision = _decide(resolver, "Area/Source.md", "Sub/Note")
    assert decision.status == STATUS_RESOLVED
    assert decision.level == LEVEL_RELATIVE
    assert decision.destination == "Area/Sub/Note.md"


def test_parent_directory_relative_link_resolves():
    """`[[../Target]]` from a subfolder points at the sibling of the folder.

    This used to stay unresolved because `PurePosixPath` kept the `..` segment, so the
    candidate was `Area/Sub/../Target` and never matched the indexed `Area/Target.md`.
    """
    resolver = _resolver({"Area/Sub/Source.md": "", "Area/Target.md": ""})
    decision = _decide(resolver, "Area/Sub/Source.md", "../Target")
    assert decision.status == STATUS_RESOLVED
    assert decision.level == LEVEL_RELATIVE
    assert decision.destination == "Area/Target.md"


def test_same_directory_explicit_link_is_not_demoted_to_a_bare_name():
    """`[[./Target]]` is an explicit path, so a same-named note elsewhere must not win."""
    resolver = _resolver(
        {"Area/Source.md": "", "Area/Target.md": "", "Elsewhere/Target.md": ""}
    )
    decision = _decide(resolver, "Area/Source.md", "./Target")
    assert decision.status == STATUS_RESOLVED
    assert decision.level == LEVEL_RELATIVE
    assert decision.destination == "Area/Target.md"


def test_relative_link_that_escapes_the_vault_stays_unresolved():
    """A `..` that would leave the Vault is not silently matched to anything."""
    resolver = _resolver({"Source.md": "", "Target.md": ""})
    decision = _decide(resolver, "Source.md", "../Target")
    assert decision.status == STATUS_UNRESOLVED
    assert decision.destination is None


def test_vault_root_path_still_resolves_from_a_subfolder():
    """A vault-absolute path is tried after the note-relative one fails."""
    resolver = _resolver({"Area/Sub/Source.md": "", "Area/Target.md": ""})
    decision = _decide(resolver, "Area/Sub/Source.md", "Area/Target")
    assert decision.status == STATUS_RESOLVED
    assert decision.level == LEVEL_VAULT_PATH
    assert decision.destination == "Area/Target.md"


def test_parent_directory_relative_asset_resolves():
    """Non-Markdown targets keep their extension and resolve the same way."""
    resolver = _resolver(
        {"Area/Sub/Source.md": ""}, assets=["Area/Figure 1.png"]
    )
    decision = _decide(resolver, "Area/Sub/Source.md", "../Figure 1.png")
    assert decision.status == STATUS_RESOLVED
    assert decision.destination == "Area/Figure 1.png"


def test_shared_filename_stays_ambiguous_because_resolution_is_undocumented():
    """Obsidian documents link *creation*; a pre-existing bare duplicate stays ambiguous."""
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
