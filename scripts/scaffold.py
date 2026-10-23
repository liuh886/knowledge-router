#!/usr/bin/env python3
"""Scaffold generator for the Symbiotic Memory (共生记忆) Vault.

Initializes or adapts an Obsidian Vault with the 6 cognitive slices:
- 000_Inbox: FOCUS.md, idea.YYYY.md, memo.YYYY.md, todo.YYYY.md
- 100_Project: Active delivery workspaces
- 200_Area/Biography: Long-horizon areas and lifebook
- 300_Resources/305_Contact: Entity contact cards with dynamic queries
- WYQD/ (Ownly): Local-first ownership, gear, accounts, and journeys
- 400_Archives: Historical archives
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from bootstrap import bootstrap_vault


def _ensure_file(path: Path, content: str, created_files: list[str], skipped_files: list[str]) -> None:
    if path.exists():
        skipped_files.append(str(path))
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        created_files.append(str(path))


def scaffold_symbiotic_vault(
    vault_root: Path,
    *,
    name: str | None = None,
    attach: bool = True,
    write_policy: str = "append-create",
    state_root: Path | None = None,
) -> dict[str, Any]:
    """Scaffold the 6 symbiotic memory slices non-destructively."""
    vault = Path(vault_root).expanduser().resolve()
    vault.mkdir(parents=True, exist_ok=True)

    now = datetime.now()
    year = now.strftime("%Y")
    month = now.strftime("%Y-%m")

    created_dirs: list[str] = []
    created_files: list[str] = []
    skipped_files: list[str] = []

    # 1. Directory Tree
    directories = [
        vault / "000_Inbox",
        vault / "100_Project",
        vault / "200_Area" / "Biography",
        vault / "300_Resources" / "305_Contact",
        vault / "400_Archives",
        vault / "WYQD" / "Objects",
        vault / "WYQD" / "Accounts",
        vault / "WYQD" / "Snapshots",
        vault / "WYQD" / "Reviews",
        vault / "WYQD" / "Trips",
        vault / "WYQD" / "Trip Places",
        vault / "WYQD" / "Trip Visits",
        vault / "WYQD" / "Trip Legs",
        vault / "WYQD" / "Trip Expenses",
        vault / "WYQD" / "Logs",
    ]

    for d in directories:
        if not d.exists():
            d.mkdir(parents=True, exist_ok=True)
            created_dirs.append(str(d))

    # 2. Slice 1: Focus (Single Active Dynamic Anchor)
    focus_content = """# 🎯 Active Focus

## 🧭 North Star (当前季度核心方向)
> 在此记录当前季度最重要的战略意图与北极星愿景。

## ⚔️ Active Battlegrounds (核心战役)
- [ ] 核心战役 1：明确当前推进重点
- [ ] 核心战役 2：明确关键结果与交付物

## 🚫 Not-To-Do List (边界与反向清单)
- 坚决不做的事 / 保护注意力与认知主权的防线
"""
    _ensure_file(vault / "000_Inbox" / "FOCUS.md", focus_content, created_files, skipped_files)

    # 3. Slice 2: Idea Stream (idea.YYYY.md)
    idea_content = f"""---
title: "💡 Idea {year}"
date: {year}-01-01
tags:
  - capture/idea
type: capture
year: {year}
---

# 💡 Idea {year}

## {month}

"""
    _ensure_file(vault / "000_Inbox" / f"idea.{year}.md", idea_content, created_files, skipped_files)

    # 4. Slice 3: Memo Stream (memo.YYYY.md)
    memo_content = f"""---
title: "📝 Memo {year}"
date: {year}-01-01
tags:
  - capture/memo
type: capture
year: {year}
---

# 📝 Memo {year}

## {month}

"""
    _ensure_file(vault / "000_Inbox" / f"memo.{year}.md", memo_content, created_files, skipped_files)

    # 5. Slice 4: Todo Stream (todo.YYYY.md)
    todo_content = f"""---
title: "📋 Todo {year}"
date: {year}-01-01
tags:
  - capture/todo
type: capture
year: {year}
---

# 📋 Todo {year}

## {month}

"""
    _ensure_file(vault / "000_Inbox" / f"todo.{year}.md", todo_content, created_files, skipped_files)

    # 6. Slice 5: Contact Template & Readme
    contact_tpl = """---
name: "模板联系人"
aliases: []
relationship: "collaborator"
tags:
  - contact
---

# 模板联系人

## 📋 关联待办 (Dynamic Dataview)
```dataview
TASK
FROM "000_Inbox"
WHERE !completed AND contains(text, this.file.name)
```

## 🪵 交互流 (Interaction Timeline)
- [YYYY-MM-DD HH:mm]: 初次沟通与记录
"""
    _ensure_file(vault / "300_Resources" / "305_Contact" / "_Template.md", contact_tpl, created_files, skipped_files)

    # 7. Inbox Assistant Handbook
    inbox_readme = """# 000_Inbox 助理与共生记忆操作手册

- `FOCUS.md`: 当前核心注意力与季度战役单一锚点
- `idea.YYYY.md`: 闪念灵感苗圃，流式追加 (`#idea/seed`)
- `memo.YYYY.md`: 事实与关键备忘流
- `todo.YYYY.md`: 待办与日程行动流，带时间块 `[start:: ...] [end:: ...]`
- `300_Resources/305_Contact/`: 实体人脉卡片
- `WYQD/` (Ownly): 个人所有权、物品装备与旅行足迹数据库
"""
    _ensure_file(vault / "000_Inbox" / "README.md", inbox_readme, created_files, skipped_files)

    report: dict[str, Any] = {
        "status": "scaffolded",
        "vault_root": str(vault),
        "created_directories_count": len(created_dirs),
        "created_files_count": len(created_files),
        "skipped_files_count": len(skipped_files),
        "created_files": created_files,
        "skipped_files": skipped_files,
    }

    if attach:
        attach_report = bootstrap_vault(
            vault,
            name=name,
            write_policy=write_policy,
            state_root=state_root,
        )
        report["attach"] = attach_report
        report["vault_id"] = attach_report["vault_id"]
        report["vault_name"] = attach_report["vault_name"]

    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("vault", type=Path, help="Target Vault directory path")
    parser.add_argument("--name", help="Custom name for the vault in registry")
    parser.add_argument("--no-attach", action="store_true", help="Only scaffold folders and files without attaching")
    parser.add_argument("--write-policy", choices=("off", "append-create"), default="append-create",
                        help="Default write policy upon attachment (default: append-create)")
    parser.add_argument("--state-root", type=Path, help="Custom state root directory (isolated testing)")
    parser.add_argument("--json", action="store_true", help="Output machine-readable JSON")

    args = parser.parse_args(argv)
    try:
        res = scaffold_symbiotic_vault(
            args.vault,
            name=args.name,
            attach=not args.no_attach,
            write_policy=args.write_policy,
            state_root=args.state_root,
        )
    except Exception as exc:
        print(f"Scaffold failed: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        print(f"✓ 成功生产共生记忆架构于: {res['vault_root']}")
        print(f"  新建目录数: {res['created_directories_count']}, 新建模板文件: {res['created_files_count']}")
        if res.get("attach"):
            print(f"  已自动挂载至注册表: [{res['attach']['vault_name']}] (ID: {res['attach']['vault_id']})")
            print(f"  已建立只读检索索引: {res['attach']['indexed_notes']} 篇笔记")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
