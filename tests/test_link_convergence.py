"""Convergence of repeated unresolved link targets into one decision each.

The guarantee under test is that grouping never disguises a problem: excluded noise is
counted, suggestions are labelled as suggestions, and the only write path goes through the
existing capture broker preview/commit pair.
"""

# ruff: noqa: E402
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from capture_broker import commit_capture_plan  # noqa: E402
from context_store import register_contexts  # noqa: E402
from fts_indexer import scan_notes  # noqa: E402
from link_convergence import (  # noqa: E402
    RECOMMEND_CREATE_OR_RELINK,
    RECOMMEND_JUDGEMENT,
    RECOMMEND_NAMING_VARIANT,
    converge_targets,
    normalise_name,
    stage_missing_note,
)
from registry import VaultRegistry  # noqa: E402
from vault_doctor import diagnose_vault  # noqa: E402

DEFINITIONS = {
    "schema_version": 1,
    "contexts": [
        {
            "id": "research:area",
            "type": "research",
            "name": "Area",
            "purpose": "Scoped work",
            "scopes": ["Area"],
            "aliases": ["Area"],
            "maintenance": {"enabled": True, "review_interval_days": 1},
        }
    ],
}


def _vault(tmp_path, notes: dict[str, str], *, write_policy: str = "off"):
    root = tmp_path / "vault"
    for relative, body in notes.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name="conv", write_policy=write_policy)
    register_contexts(registry, config, DEFINITIONS)
    return registry, config, root


def _by_target(report):
    return {item.target: item for item in report.items}


def test_normalise_name_keeps_cjk_and_drops_separators():
    assert normalise_name("Digital Carbon") == normalise_name("Digital_Carbon")
    assert normalise_name("CCS") == normalise_name("ccs")
    assert normalise_name("2026 香港开户") != normalise_name("2026_新业务专班")


def test_case_variants_collapse_into_one_decision(tmp_path):
    registry, config, _ = _vault(
        tmp_path,
        {
            "Area/A.md": "# A\n[[CCS]]",
            "Area/B.md": "# B\n[[ccs]]",
        },
    )
    report = converge_targets(config, DEFINITIONS["contexts"], scan_notes(config.root_path))
    assert len(report.items) == 1
    item = report.items[0]
    assert item.occurrences == 2
    assert set(item.variants) == {"CCS", "ccs"}


def test_naming_variant_candidate_is_named_without_being_resolved(tmp_path):
    registry, config, _ = _vault(
        tmp_path,
        {
            "Area/Digital_Carbon.md": "# Digital Carbon\nbody",
            "Area/Source.md": "# S\n[[Digital Carbon]]",
        },
    )
    report = converge_targets(config, DEFINITIONS["contexts"], scan_notes(config.root_path))
    item = _by_target(report)["Digital Carbon"]
    assert item.recommended == RECOMMEND_NAMING_VARIANT
    assert item.candidate_host == "Digital_Carbon"
    # The link is still broken: a suggestion is not a resolution.
    assert diagnose_vault(config.root_path)["broken_link_occurrences"] == 1


def test_no_resemblance_recommends_create_or_relink(tmp_path):
    registry, config, _ = _vault(tmp_path, {"Area/S.md": "# S\n[[NothingLikeThis]]"})
    report = converge_targets(config, DEFINITIONS["contexts"], scan_notes(config.root_path))
    item = _by_target(report)["NothingLikeThis"]
    assert item.recommended == RECOMMEND_CREATE_OR_RELINK
    assert item.candidate_host is None


def test_generic_short_target_is_not_guessed(tmp_path):
    registry, config, _ = _vault(tmp_path, {"Area/S.md": "# S\n[[ab]]"})
    report = converge_targets(config, DEFINITIONS["contexts"], scan_notes(config.root_path))
    item = _by_target(report)["ab"]
    assert item.recommended == RECOMMEND_JUDGEMENT
    assert item.candidate_host is None


def test_machine_noise_is_excluded_but_counted(tmp_path):
    registry, config, _ = _vault(
        tmp_path,
        {
            "Area/S.md": "# S\n[[Link]]\n[[RealMissing]]",
        },
    )
    report = converge_targets(config, DEFINITIONS["contexts"], scan_notes(config.root_path))
    assert [item.target for item in report.items] == ["RealMissing"]
    assert report.excluded_noisy_targets == 1
    assert report.excluded_noisy_occurrences == 1

    noisy = converge_targets(
        config, DEFINITIONS["contexts"], scan_notes(config.root_path), include_noisy=True
    )
    assert "Link" in {item.target for item in noisy.items}
    assert noisy.excluded_noisy_targets == 0


def test_unscoped_targets_are_not_proposed(tmp_path):
    registry, config, _ = _vault(
        tmp_path,
        {
            "Area/Keep.md": "# Keep\nplain note in scope",
            "Out/S.md": "# S\n[[OutOfScope]]",
        },
    )
    report = converge_targets(config, DEFINITIONS["contexts"], scan_notes(config.root_path))
    assert report.items == ()


def test_stage_missing_note_previews_without_writing(tmp_path):
    registry, config, root = _vault(tmp_path, {"Area/S.md": "# S\n[[Ghost]]"})
    before = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}

    staged = stage_missing_note(registry, config, "Ghost", "Area/Ghost.md")
    assert staged["status"] == "staged"
    assert staged["action"] == "create_new"
    assert staged["relative_path"] == "Area/Ghost.md"
    assert staged["vault_writes"] == 0
    assert not (root / "Area" / "Ghost.md").exists()
    after = {p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file()}
    assert after == before


def test_stage_refuses_a_path_that_already_exists(tmp_path):
    registry, config, _ = _vault(
        tmp_path, {"Area/S.md": "# S\n[[Ghost]]", "Area/Ghost.md": "# existing"}
    )
    try:
        stage_missing_note(registry, config, "Ghost", "Area/Ghost.md")
    except FileExistsError as error:
        assert "already exists" in str(error)
    else:  # pragma: no cover - the guard must fire
        raise AssertionError("staging over an existing note must be refused")


def test_stage_refuses_a_non_markdown_or_escaping_path(tmp_path):
    registry, config, _ = _vault(tmp_path, {"Area/S.md": "# S\n[[Ghost]]"})
    for bad in ("../outside.md", "/abs.md", "Area/Ghost.txt"):
        try:
            stage_missing_note(registry, config, "Ghost", bad)
        except ValueError:
            continue
        raise AssertionError(f"path {bad!r} must be refused")


def test_committed_stub_actually_resolves_the_link(tmp_path):
    """The one real write path: stage, commit under append-create, link resolves."""
    registry, config, root = _vault(
        tmp_path, {"Area/S.md": "# S\n[[Ghost]]"}, write_policy="append-create"
    )
    assert diagnose_vault(root)["broken_link_occurrences"] == 1

    staged = stage_missing_note(registry, config, "Ghost", "Area/Ghost.md")
    commit_capture_plan(
        config.root_path, registry.vault_state_dir(config), staged["plan_id"]
    )
    assert (root / "Area" / "Ghost.md").is_file()

    report = diagnose_vault(root)
    assert report["broken_link_occurrences"] == 0
    assert report["links_resolved"] == 1


def test_commit_is_refused_without_the_write_policy(tmp_path):
    registry, config, root = _vault(tmp_path, {"Area/S.md": "# S\n[[Ghost]]"})
    staged = stage_missing_note(registry, config, "Ghost", "Area/Ghost.md")
    try:
        commit_capture_plan(
            config.root_path, registry.vault_state_dir(config), staged["plan_id"]
        )
    except PermissionError:
        pass
    else:  # pragma: no cover - read-only vaults must stay read-only
        raise AssertionError("a read-only Vault must refuse the commit")
    assert not (root / "Area" / "Ghost.md").exists()
