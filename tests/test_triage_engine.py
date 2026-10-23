"""Tests for contact entity linking and todo multi-dimensional tagging."""

# ruff: noqa: E402
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from triage_engine import ContactResolver, TagClassifier, triage_todo_file
from memory_service import triage_todos
from registry import VaultRegistry


@pytest.fixture
def mock_vault(tmp_path: Path) -> Path:
    vault = tmp_path / "mock_vault"
    vault.mkdir()
    contact_dir = vault / "300_Resources" / "305_Contact"
    contact_dir.mkdir(parents=True)
    inbox_dir = vault / "000_Inbox"
    inbox_dir.mkdir(parents=True)

    # 1. Contact note with Chinese, English, and YAML aliases
    (contact_dir / "Xu ChongYu 许崇育.md").write_text(
        "---\n"
        "aliases:\n"
        "  - 许老师\n"
        "  - 许院士\n"
        "  - Xu Chongyu\n"
        "tags: [Contact/Academia]\n"
        "---\n"
        "# Xu ChongYu 许崇育\n",
        encoding="utf-8",
    )

    # 2. Contact note in subfolder
    sub_bgp = contact_dir / "BGP"
    sub_bgp.mkdir()
    (sub_bgp / "Du Haitao 杜海涛.md").write_text(
        "---\nCompany: BGP\ntags: [Contact/Management]\n---\n# Du Haitao 杜海涛\n",
        encoding="utf-8",
    )

    # 3. Company note
    (contact_dir / "中海达海洋工程公司.md").write_text(
        "---\naliases: [中海达海洋工程]\n---\n# 中海达\n",
        encoding="utf-8",
    )

    # 4. Non-contact workflow file (must be ignored)
    (contact_dir / "crm.workflow.md").write_text(
        "---\nworkflow_family: crm\n---\n", encoding="utf-8"
    )

    # 5. Todo file
    todo_file = inbox_dir / "todo.2026.md"
    todo_file.write_text(
        "---\ntags: [capture/todo]\n---\n\n"
        "## 2026-09\n\n"
        "- [ ] ⏳ [2026-09-28 23:25] 约许老师商量降水模型 #todo/inbox\n"
        "- [ ] ⏳ [2026-09-28 23:30] 和杜海涛讨论多哈光伏项目合同 #todo/inbox\n"
        "- [ ] 订去多哈的机票并预订酒店\n"
        "- [ ] 已经联系了[[Xu ChongYu 许崇育]]商量审稿 #todo/inbox\n"
        "- [x] 已经完成的旧待办 不应当被修改\n",
        encoding="utf-8",
    )

    return vault


def test_contact_resolver_aliases(mock_vault: Path):
    resolver = ContactResolver(mock_vault)
    assert "Xu ChongYu 许崇育" in resolver.entities
    assert "Du Haitao 杜海涛" in resolver.entities
    assert "中海达海洋工程公司" in resolver.entities
    assert "crm.workflow" not in resolver.entities

    # Aliases
    assert resolver.alias_to_canonical["许老师"] == "Xu ChongYu 许崇育"
    assert resolver.alias_to_canonical["许院士"] == "Xu ChongYu 许崇育"
    assert resolver.alias_to_canonical["许崇育"] == "Xu ChongYu 许崇育"
    assert resolver.alias_to_canonical["杜海涛"] == "Du Haitao 杜海涛"


def test_contact_linking_prevents_nested_brackets(mock_vault: Path):
    resolver = ContactResolver(mock_vault)
    text = "拜访中海达海洋工程公司洽谈业务"
    linked, contacts = resolver.link_entities(text)
    assert linked == "拜访[[中海达海洋工程公司]]洽谈业务"
    assert "[[[[" not in linked
    assert contacts == ["中海达海洋工程公司"]


def test_tag_classifier():
    classifier = TagClassifier()
    line = "- [ ] 约见客户讨论新业务合同与报价 #todo/inbox"
    tagged, tags = classifier.classify_and_tag(line)
    assert "#newbusiness" in tags
    assert "#meeting" in tags
    assert tagged.endswith("#newbusiness #meeting #todo/inbox")


def test_triage_dry_run_and_commit(mock_vault: Path):
    # Dry run
    report_dry = triage_todo_file(mock_vault, year=2026)
    assert not report_dry.committed
    assert report_dry.triaged_count == 4
    assert len(report_dry.diffs) == 4

    # Completed todo not changed
    assert not any(d.original.startswith("- [x]") for d in report_dry.diffs)

    # Verify dry run did NOT write to disk
    raw_before = (mock_vault / "000_Inbox" / "todo.2026.md").read_text(encoding="utf-8")
    assert "约许老师商量降水模型" in raw_before

    # Full edits require the explicit reviewed-edit policy.
    registry = VaultRegistry(mock_vault.parent / "State")
    config = registry.attach(mock_vault, write_policy="reviewed-edit")
    report_commit = triage_todos(registry, config, year=2026, commit=True)
    assert report_commit["committed"]
    assert report_commit["triaged_count"] == 4

    # Verify disk was updated
    raw_after = (mock_vault / "000_Inbox" / "todo.2026.md").read_text(encoding="utf-8")
    assert "[[Xu ChongYu 许崇育|许老师]]" in raw_after
    assert "#research" in raw_after
    assert "[[Du Haitao 杜海涛|杜海涛]]" in raw_after
    assert "#Qatar" in raw_after
    assert "#energy" in raw_after
    assert "#newbusiness" in raw_after
    assert "- [x] 已经完成的旧待办 不应当被修改" in raw_after

    # Idempotence: Second commit run should do 0 changes
    report_second = triage_todos(registry, config, year=2026, commit=True)
    assert not report_second["committed"]
    assert report_second["triaged_count"] == 0
