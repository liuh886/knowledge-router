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


def test_target_with_only_a_late_in_scope_source_is_still_proposed(tmp_path):
    """Scope is decided over every occurrence, not doctor's 20-source display sample."""
    root = tmp_path / "vault"
    (root / "Out").mkdir(parents=True)
    (root / "ZZ").mkdir()
    for index in range(1, 21):
        (root / "Out" / f"a{index:02d}.md").write_text("# Out\n[[Missing]]", encoding="utf-8")
    (root / "ZZ" / "note.md").write_text("# Note\n[[Missing]]", encoding="utf-8")
    definitions = [
        {
            "id": "research:zz",
            "type": "research",
            "name": "ZZ",
            "purpose": "Scoped",
            "scopes": ["ZZ"],
            "aliases": ["ZZ"],
        }
    ]
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name="late")
    register_contexts(
        registry, config, {"schema_version": 1, "contexts": definitions}
    )

    report = converge_targets(config, definitions, scan_notes(config.root_path))
    assert [item.target for item in report.items] == ["Missing"]
    item = report.items[0]
    assert item.occurrences == 1, "only the in-scope occurrence is the scope's business"
    assert item.context_ids == ("research:zz",)


def test_stage_refuses_a_path_that_would_not_resolve_the_links(tmp_path):
    """A create at an unrelated path succeeds for the broker but fixes no link.

    Reproduces the reported case: `[[Missing]]` staged as `Area/Different.md`. The old check
    only confirmed the broker had not renamed the file, so this looked like a fix.
    """
    registry, config, root = _vault(
        tmp_path, {"Area/S.md": "# S\n[[Ghost]]"}, write_policy="append-create"
    )
    try:
        stage_missing_note(registry, config, "Ghost", "Area/Different.md")
    except ValueError as error:
        assert "would not make any unresolved link resolve" in str(error)
    else:  # pragma: no cover - the guard must fire
        raise AssertionError("a create that resolves nothing must be refused")
    assert not (root / "Area" / "Different.md").exists()


def test_stage_reports_which_occurrences_the_create_would_fix(tmp_path):
    registry, config, _ = _vault(
        tmp_path,
        {
            "Area/A.md": "# A\n[[Ghost]]",
            "Area/B.md": "# B\n[[Ghost]]",
            "Area/C.md": "# C\n[[OtherMissing]]",
        },
    )
    staged = stage_missing_note(registry, config, "Ghost", "Area/Ghost.md")
    assert staged["resolved_occurrence_count"] == 2
    assert staged["still_unresolved_after_create"] == 1
    assert all("Ghost" in sample for sample in staged["resolved_occurrence_samples"])


def test_converge_without_registered_contexts_is_not_a_complete_empty_result(tmp_path):
    """No definitions is not "nothing to converge to"."""
    root = tmp_path / "vault"
    (root / "Area").mkdir(parents=True)
    (root / "Area" / "S.md").write_text("# S\n[[Missing]]", encoding="utf-8")
    registry = VaultRegistry(tmp_path / "state")
    config = registry.attach(root, name="bare")

    report = converge_targets(config, [], scan_notes(config.root_path))
    assert report.configured is False
    assert report.items == ()
    assert report.to_dict()["configured"] is False


def test_converge_refuses_an_unknown_context_id(tmp_path):
    registry, config, _ = _vault(tmp_path, {"Area/S.md": "# S\n[[Missing]]"})
    try:
        converge_targets(
            config,
            DEFINITIONS["contexts"],
            scan_notes(config.root_path),
            context_id="research:nope",
        )
    except ValueError as error:
        assert "No registered Context" in str(error)
    else:  # pragma: no cover - an unknown scope must not silently return nothing
        raise AssertionError("an unknown --context must be an error, not an empty result")


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
