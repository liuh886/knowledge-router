import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from triage_engine import ContactResolver
from memory_service import append_contact_log, append_inbox_stream
from registry import VaultRegistry


def _writable(vault):
    registry = VaultRegistry(vault.parent / "State")
    return registry, registry.attach(vault, write_policy="append-create")


def _create_mock_vault(root: Path) -> None:
    (root / "000_Inbox").mkdir(parents=True, exist_ok=True)
    contact_dir = root / "300_Resources" / "305_Contact"
    contact_dir.mkdir(parents=True, exist_ok=True)

    # Note 1: with aliases and existing ## Log
    note1 = contact_dir / "Xu ChongYu 许崇育.md"
    note1.write_text(
        "---\n"
        "aliases:\n"
        "  - 许老师\n"
        "  - 许院士\n"
        "  - Xu Chongyu\n"
        "---\n\n"
        "# Xu ChongYu 许崇育\n\n"
        "水文学家，中国科学院外籍院士。\n\n"
        "## Log\n"
        "- [2026-08-01 10:00] 初步建立学术联系\n\n"
        "## 备注\n"
        "奥斯陆大学教授\n",
        encoding="utf-8",
    )

    # Note 2: with H1 title and no ## Log
    note2 = contact_dir / "Song Xuanyi 宋宣仪.md"
    note2.write_text(
        "---\naliases: [Xiao Song]\n---\n\n# 小宋\n\n团队成员\n",
        encoding="utf-8",
    )


def test_contact_search() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp) / "Vault"
        _create_mock_vault(vault)
        resolver = ContactResolver(vault)

        # 1. Alias match
        entity, cands = resolver.match_contact("许老师")
        assert entity is not None
        assert entity.canonical == "Xu ChongYu 许崇育"

        # 2. H1 match
        entity2, cands2 = resolver.match_contact("小宋")
        assert entity2 is not None
        assert entity2.canonical == "Song Xuanyi 宋宣仪"

        # 3. Not found
        entity3, cands3 = resolver.match_contact("张三李四")
        assert entity3 is None
        assert len(cands3) == 0


def test_contact_append_with_existing_log() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp) / "Vault"
        _create_mock_vault(vault)

        res = append_contact_log(*_writable(vault), "许老师", "讨论降水模型评估指标")
        assert res["status"] == "success"
        assert res["canonical"] == "Xu ChongYu 许崇育"

        note = vault / "300_Resources" / "305_Contact" / "Xu ChongYu 许崇育.md"
        content = note.read_text(encoding="utf-8")
        assert "讨论降水模型评估指标" in content
        # Appending preserves the existing source prefix; repeated sections are intentional.
        log_pos = content.find("## Log")
        entry_pos = content.find("讨论降水模型评估指标")
        remark_pos = content.find("## 备注")
        assert log_pos < remark_pos < entry_pos


def test_contact_append_without_existing_log() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp) / "Vault"
        _create_mock_vault(vault)

        res = append_contact_log(*_writable(vault), "小宋", "同步数据准备进度")
        assert res["status"] == "success"
        assert res["canonical"] == "Song Xuanyi 宋宣仪"

        note = vault / "300_Resources" / "305_Contact" / "Song Xuanyi 宋宣仪.md"
        content = note.read_text(encoding="utf-8")
        assert "## Log" in content
        assert "同步数据准备进度" in content


def test_inbox_append_todo_with_auto_triage() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp) / "Vault"
        _create_mock_vault(vault)

        res = append_inbox_stream(*_writable(vault), "todo", "约许老师商量降水模型", year=2026)
        assert res["status"] == "success"
        assert res["type"] == "todo"

        todo_file = vault / "000_Inbox" / "todo.2026.md"
        assert todo_file.is_file()
        content = todo_file.read_text(encoding="utf-8")
        # Check auto triage linked entity and tag
        assert "[[Xu ChongYu 许崇育|许老师]]" in content
        assert "#research" in content
        assert "#todo/inbox" in content


def test_inbox_append_memo_and_idea() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp) / "Vault"
        _create_mock_vault(vault)

        res_memo = append_inbox_stream(
            *_writable(vault), "memo", "记录关于冰川流动速度的观察", year=2026
        )
        assert res_memo["status"] == "success"
        memo_file = vault / "000_Inbox" / "memo.2026.md"
        assert memo_file.is_file()
        assert "记录关于冰川流动速度的观察" in memo_file.read_text(encoding="utf-8")

        res_idea = append_inbox_stream(
            *_writable(vault), "idea", "探索大语言模型辅助审稿流程", year=2026
        )
        assert res_idea["status"] == "success"
        idea_file = vault / "000_Inbox" / "idea.2026.md"
        assert idea_file.is_file()
        assert "#idea/seed" in idea_file.read_text(encoding="utf-8")
