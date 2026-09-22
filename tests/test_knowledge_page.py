"""Knowledge pages: versioned, budgeted, and honest about sourced vs inferred claims."""

# ruff: noqa: E402
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from context_store import register_contexts  # noqa: E402
from knowledge_page import (  # noqa: E402
    CLAIM_CONFLICT,
    CLAIM_INFERENCE,
    CLAIM_SOURCED,
    DraftError,
    context_pack,
    estimate_tokens,
    expand_claim,
    load_page,
    pages_root,
    page_status,
    parse_draft,
    publish_page,
)
from registry import VaultRegistry  # noqa: E402

DEFINITIONS = {
    "schema_version": 1,
    "contexts": [
        {
            "id": "research:ccs",
            "type": "research",
            "name": "CCS",
            "purpose": "追踪 CCS 技术与项目",
            "scopes": ["Area"],
            "aliases": ["CCS"],
        }
    ],
}


@pytest.fixture
def vault(tmp_path):
    root = tmp_path / "vault"
    (root / "Area").mkdir(parents=True)
    (root / "Area" / "Concept.md").write_text(
        "# Concept of CCS\nCarbon capture and storage traps CO2.\nStorage is permanent.\n",
        encoding="utf-8",
    )
    (root / "Area" / "Other.md").write_text(
        "# Other\nA second independent source.\nIt disagrees on permanence.\n",
        encoding="utf-8",
    )
    # Deliberately out of scope: an unrelated note must never make a page look stale.
    (root / "Outside").mkdir()
    (root / "Outside" / "Unrelated.md").write_text("# Unrelated\nnoise\n", encoding="utf-8")
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name="pages")
    register_contexts(registry, config, DEFINITIONS)
    return registry, config, root


DRAFT = """
## 当前认识
- C1. CCS 捕集并封存 CO2 [src: Area/Concept.md#L2-L2]
- C2. 封存并非永久 [src: Area/Concept.md#L3-L3, Area/Other.md#L3-L3] [conflict]
- C3. 这条判断还没有出处 [inference]

## 未决问题
- Q1. 封存期限究竟多长 [open]
"""


def _snapshot(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


def test_parse_draft_classifies_every_item():
    draft = parse_draft(DRAFT)
    kinds = {claim.claim_id: claim.kind for claim in draft.claims}
    assert kinds == {"C1": CLAIM_SOURCED, "C2": CLAIM_CONFLICT, "C3": CLAIM_INFERENCE}
    assert draft.claims[2].citations == ()
    assert draft.open_questions == ("封存期限究竟多长",)


@pytest.mark.parametrize(
    "body,message",
    [
        ("- C1. 没有标记", "no marker"),
        ("- C1. X [conflict]", "needs at least two sources"),
        ("- C1. X [inference] [src: Area/Concept.md]", "both an inference and sourced"),
        ("- C1. X [src: Area/Concept.md]", None),
    ],
)
def test_parse_draft_rejects_ambiguous_items(body, message):
    text = f"## 当前认识\n{body}\n"
    if message is None:
        assert parse_draft(text).claims[0].kind == CLAIM_SOURCED
        return
    with pytest.raises(DraftError, match=message):
        parse_draft(text)


def test_parse_draft_refuses_items_outside_a_known_section():
    with pytest.raises(DraftError, match="outside"):
        parse_draft("- C1. X [inference]\n")


def test_parse_draft_refuses_duplicate_ids():
    with pytest.raises(DraftError, match="Duplicate"):
        parse_draft("## 当前认识\n- C1. A [inference]\n- C1. B [inference]\n")


def test_publish_refuses_a_citation_that_does_not_exist(vault):
    registry, config, root = vault
    before = _snapshot(root)
    draft = "## 当前认识\n- C1. 编造的结论 [src: Area/Nowhere.md#L1-L1]\n"
    with pytest.raises(DraftError, match="does not exist"):
        publish_page(registry, config, "research:ccs", draft)
    assert _snapshot(root) == before
    assert load_page(registry, config, "research:ccs") is None


def test_publish_refuses_lines_beyond_the_note(vault):
    registry, config, _ = vault
    draft = "## 当前认识\n- C1. 越界 [src: Area/Concept.md#L1-L99]\n"
    with pytest.raises(DraftError, match="lines"):
        publish_page(registry, config, "research:ccs", draft)


def test_publish_writes_outside_the_vault_and_records_the_manifest(vault):
    registry, config, root = vault
    before = _snapshot(root)
    result = publish_page(registry, config, "research:ccs", DRAFT)

    assert result["status"] == "published"
    assert result["version"] == 1
    assert result["claims_by_kind"] == {
        CLAIM_SOURCED: 1,
        CLAIM_CONFLICT: 1,
        CLAIM_INFERENCE: 1,
    }
    assert result["citation_count"] == 3
    assert result["vault_writes"] == 0
    assert _snapshot(root) == before, "the Vault must not be touched"

    out = Path(result["path"])
    assert out.is_file()
    assert out.parent == pages_root(config)
    text = out.read_text(encoding="utf-8")
    assert "依据 manifest" in text
    assert "Area/Concept.md#L2-L2" in text

    page = load_page(registry, config, "research:ccs")
    assert page["version"] == 1
    inference = next(row for row in page["claims"] if row["id"] == "C3")
    assert inference["kind"] == CLAIM_INFERENCE
    assert inference["citations"] == []
    sourced = next(row for row in page["claims"] if row["id"] == "C1")
    assert sourced["citations"][0]["sha256"], "the source checksum must be recorded"


def test_publish_versions_and_requires_overwrite(vault):
    registry, config, _ = vault
    first = publish_page(registry, config, "research:ccs", DRAFT)
    with pytest.raises(FileExistsError, match="already exists"):
        publish_page(registry, config, "research:ccs", DRAFT)
    second = publish_page(registry, config, "research:ccs", DRAFT, overwrite=True)
    assert second["version"] == 2
    assert second["path"] == first["path"]


def test_pack_is_empty_and_honest_before_a_page_exists(vault):
    registry, config, _ = vault
    pack = context_pack(registry, config, "research:ccs")
    assert pack["status"] == "unpublished"
    assert pack["items"] == []
    assert "not a claim that" in pack["meaning"]


def test_pack_respects_the_budget_and_reports_what_it_omitted(vault):
    registry, config, _ = vault
    publish_page(registry, config, "research:ccs", DRAFT)
    full = context_pack(registry, config, "research:ccs", budget_tokens=4000)
    assert full["status"] == "current"
    assert len(full["items"]) == 3
    assert full["claims_omitted_by_budget"] == 0
    assert full["budget_basis"].startswith("estimated")

    # Long claims so a small budget genuinely has to drop some.
    long_claims = "## 当前认识\n" + "".join(
        f"- C{index}. {'这是一条较长的结论，用于验证预算截断。' * 12} [inference]\n"
        for index in range(1, 4)
    )
    publish_page(registry, config, "research:ccs", long_claims, overwrite=True)
    tiny = context_pack(registry, config, "research:ccs", budget_tokens=300)
    assert len(tiny["items"]) < 3
    assert tiny["claims_omitted_by_budget"] == 3 - len(tiny["items"])
    assert tiny["claims_total"] == 3, "the total is always reported"
    assert tiny["used_tokens"] <= tiny["budget_tokens"]


def test_pack_marks_inference_so_it_is_never_read_as_sourced(vault):
    registry, config, _ = vault
    publish_page(registry, config, "research:ccs", DRAFT)
    pack = context_pack(registry, config, "research:ccs")
    kinds = {item["id"]: item for item in pack["items"]}
    assert kinds["C3"]["kind"] == CLAIM_INFERENCE
    assert kinds["C3"]["expandable"] is False
    assert kinds["C1"]["kind"] == CLAIM_SOURCED
    assert kinds["C1"]["expandable"] is True


def test_pack_goes_stale_when_a_scoped_source_changes(vault):
    registry, config, root = vault
    publish_page(registry, config, "research:ccs", DRAFT)
    assert context_pack(registry, config, "research:ccs")["status"] == "current"

    note = root / "Area" / "Concept.md"
    note.write_text(note.read_text(encoding="utf-8") + "A new finding.\n", encoding="utf-8")
    stale = context_pack(registry, config, "research:ccs")
    assert stale["status"] == "stale"
    assert stale["coverage"]["drift_count"] >= 1
    assert stale["coverage"]["citation_drift_count"] >= 1


def test_pack_detects_a_rescoped_context(vault):
    registry, config, root = vault
    publish_page(registry, config, "research:ccs", DRAFT)
    (root / "Elsewhere").mkdir()
    (root / "Elsewhere" / "N.md").write_text("# N\nbody", encoding="utf-8")
    moved = json.loads(json.dumps(DEFINITIONS))
    moved["contexts"][0]["scopes"] = ["Elsewhere"]
    register_contexts(registry, config, moved, expected_revision=1)

    status = page_status(registry, config, load_page(registry, config, "research:ccs"))
    assert status["scope_changed"] is True
    assert status["stale"] is True


def test_expand_returns_excerpts_and_flags_drift(vault):
    registry, config, root = vault
    publish_page(registry, config, "research:ccs", DRAFT)

    current = expand_claim(registry, config, "research:ccs", "C1")
    citation = current["citations"][0]
    assert citation["status"] == "current"
    assert "Carbon capture and storage traps CO2." in citation["excerpt"]
    assert current["claim"]["kind"] == CLAIM_SOURCED

    (root / "Area" / "Concept.md").write_text("# Concept\nRewritten.\n", encoding="utf-8")
    drifted = expand_claim(registry, config, "research:ccs", "C1")
    assert drifted["citations"][0]["status"] == "changed"


def test_expand_reports_a_deleted_source_as_missing(vault):
    registry, config, root = vault
    publish_page(registry, config, "research:ccs", DRAFT)
    (root / "Area" / "Other.md").unlink()
    expanded = expand_claim(registry, config, "research:ccs", "C2")
    statuses = {item["path"]: item["status"] for item in expanded["citations"]}
    assert statuses["Area/Other.md"] == "missing"


def test_expand_refuses_an_unknown_claim(vault):
    registry, config, _ = vault
    publish_page(registry, config, "research:ccs", DRAFT)
    with pytest.raises(FileNotFoundError, match="no claim"):
        expand_claim(registry, config, "research:ccs", "C9")


def test_unreadable_page_registry_is_not_reported_as_unpublished(tmp_path):
    """"No page" and "cannot read the registry" are different claims."""
    registry, config, _ = tmp_path, None, None
    root = tmp_path / "vault"
    (root / "Area").mkdir(parents=True)
    (root / "Area" / "Concept.md").write_text("# C\nbody\n", encoding="utf-8")
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name="pages")
    register_contexts(registry, config, DEFINITIONS)
    publish_page(registry, config, "research:ccs", "## 当前认识\n- C1. X [inference]\n")

    (registry.vault_state_dir(config) / "knowledge_pages.json").write_text(
        "{ broken", encoding="utf-8"
    )
    pack = context_pack(registry, config, "research:ccs")
    assert pack["status"] == "registry_unreadable"
    assert pack["registry_state"] == "invalid"
    assert pack["items"] == []
    assert "not a claim that nothing is known" in pack["meaning"]

    with pytest.raises(RuntimeError, match="page registry is unreadable"):
        publish_page(registry, config, "research:ccs", "## 当前认识\n- C1. Y [inference]\n")


def test_record_without_a_context_fingerprint_is_flagged_for_reverification(tmp_path):
    """A record predating per-Context fingerprints must be re-verified, not assumed current."""
    root = tmp_path / "vault"
    (root / "Area").mkdir(parents=True)
    (root / "Area" / "Concept.md").write_text("# C\nbody\n", encoding="utf-8")
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name="pages")
    register_contexts(registry, config, DEFINITIONS)
    publish_page(registry, config, "research:ccs", "## 当前认识\n- C1. X [inference]\n")

    registry_file = registry.vault_state_dir(config) / "knowledge_pages.json"
    data = json.loads(registry_file.read_text(encoding="utf-8"))
    data["pages"][0].pop("context_fingerprint")
    registry_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    status = page_status(registry, config, load_page(registry, config, "research:ccs"))
    assert status["scope_changed"] is True
    assert status["stale"] is True


def test_unrelated_context_does_not_stale_a_page(tmp_path):
    """A page depends on its own Context, not on the whole definition set."""
    root = tmp_path / "vault"
    (root / "Area").mkdir(parents=True)
    (root / "Other").mkdir()
    (root / "Area" / "Concept.md").write_text("# C\nbody\n", encoding="utf-8")
    (root / "Other" / "Note.md").write_text("# O\nbody\n", encoding="utf-8")
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name="pages")
    register_contexts(registry, config, DEFINITIONS)
    publish_page(registry, config, "research:ccs", "## 当前认识\n- C1. X [inference]\n")
    assert context_pack(registry, config, "research:ccs")["status"] == "current"

    widened = json.loads(json.dumps(DEFINITIONS))
    widened["contexts"].append(
        {
            "id": "research:other",
            "type": "research",
            "name": "Other",
            "purpose": "Unrelated",
            "scopes": ["Other"],
            "aliases": ["Other"],
        }
    )
    register_contexts(registry, config, widened, expected_revision=1)
    assert context_pack(registry, config, "research:ccs")["status"] == "current"


def test_estimate_tokens_over_estimates_rather_than_under():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert estimate_tokens("中文") == 2
    assert estimate_tokens("中文ab") >= 3
