#!/usr/bin/env python3
"""Daily Log Migration Engine: Deconstruct legacy daily logs into Quick Capture streams.

Extracts:
  - Tasks (- [ ], - [x], - [> ]) -> 000_Inbox/todo.YYYY.md (grouped by ## YYYY-MM)
  - Memos (- **...**, - Record ...) -> 000_Inbox/memo.YYYY.md (grouped by ## YYYY-MM)
  - Ideas (- [idea:: ...], 💡 ...) -> 000_Inbox/idea.YYYY.md (grouped by ## YYYY-MM)
  - Contact interactions -> 305_Contact/<Person>.md (appended to ## Log)
  - Archives original files to 400_Archives/Legacy_Daily_Logs/

Usage:
  python scripts/migrate_daily_logs.py --vault "D:\\Documents\\zhihaol" [--dry-run] [--commit]
"""

from __future__ import annotations

import argparse
import glob
import os
import re
import shutil
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# Ensure local scripts can be imported
sys.path.insert(0, str(Path(__file__).parent))
try:
    from triage_engine import (
        ContactResolver,
        FileLock,
        TagClassifier,
        append_contact_log,
        atomic_text_write,
    )
except ImportError:
    # Fallback minimal stubs if standalone
    ContactResolver = None
    TagClassifier = None
    FileLock = None
    append_contact_log = None
    atomic_text_write = None


@dataclass
class ExtractedTask:
    raw: str
    status: str  # 'open', 'completed', 'migrated', 'cancelled'
    text: str
    date_str: str  # YYYY-MM-DD
    year: int
    month: str  # YYYY-MM
    time_str: str | None = None
    completion_date: str | None = None
    contacts: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    source_file: str = ""
    source_line: int = 0
    formatted_entry: str = ""


@dataclass
class ExtractedMemo:
    text: str
    date_str: str
    year: int
    month: str
    contacts: list[str] = field(default_factory=list)
    source_file: str = ""
    source_line: int = 0
    formatted_entry: str = ""


@dataclass
class ExtractedIdea:
    text: str
    date_str: str
    year: int
    month: str
    contacts: list[str] = field(default_factory=list)
    source_file: str = ""
    source_line: int = 0
    formatted_entry: str = ""


@dataclass
class MigrationPlan:
    vault_root: Path
    daily_files: list[Path] = field(default_factory=list)
    tasks: list[ExtractedTask] = field(default_factory=list)
    memos: list[ExtractedMemo] = field(default_factory=list)
    ideas: list[ExtractedIdea] = field(default_factory=list)
    contact_logs: dict[str, list[tuple[str, str]]] = field(
        default_factory=lambda: defaultdict(list)
    )  # contact_name -> list of (date_str, text)
    skipped_empty: int = 0
    subagent_reflections: int = 0


class DailyLogParser:
    def __init__(self, vault_root: Path) -> None:
        self.vault_root = vault_root.resolve()
        self.resolver = ContactResolver(self.vault_root) if ContactResolver else None
        self.classifier = TagClassifier() if TagClassifier else None

    def parse_vault(self) -> MigrationPlan:
        daily_dir = self.vault_root / "100_Logs" / "daily"
        plan = MigrationPlan(vault_root=self.vault_root)

        if not daily_dir.is_dir():
            print(f"Warning: Daily log directory not found at {daily_dir}")
            return plan

        all_mds = sorted(daily_dir.rglob("*.md"))
        for path in all_mds:
            if path.name.endswith(".bak") or path.name.lower() == "readme.md":
                continue
            plan.daily_files.append(path)
            self._parse_file(path, plan)

        return plan

    def _parse_file(self, path: Path, plan: MigrationPlan) -> None:
        stem = path.stem
        # Extract date from filename or frontmatter
        date_match = re.search(r"(\d{4})-(\d{2})(?:-(\d{2}))?", stem)
        if not date_match:
            return

        year = int(date_match.group(1))
        month_part = date_match.group(2)
        day_part = date_match.group(3) if date_match.group(3) else "01"
        date_str = f"{year:04d}-{month_part}-{day_part}"
        month_str = f"{year:04d}-{month_part}"

        try:
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            return

        lines = content.splitlines()
        in_unified_log = False
        in_agent_reflection = False
        in_code_block = False

        for line_idx, line in enumerate(lines, start=1):
            stripped = line.strip()

            # Track fenced code blocks (like ```contributionGraph, ```json, etc.)
            if stripped.startswith("```"):
                in_code_block = not in_code_block
                continue
            if in_code_block:
                continue

            if stripped.startswith("## 🪵 Unified Log"):
                in_unified_log = True
                in_agent_reflection = False
                continue
            elif stripped.startswith("## 🧠 Reflection"):
                in_unified_log = False
                in_agent_reflection = True
                continue
            elif stripped.startswith("## "):
                in_unified_log = False
                in_agent_reflection = False

            if in_agent_reflection:
                plan.subagent_reflections += 1
                continue

            # 1. Check for tasks
            task_match = re.match(r"^\s*-\s*\[([ xX\->])\]\s*(.*)$", line)
            if task_match:
                status_char = task_match.group(1)
                task_body = task_match.group(2).strip()

                if not task_body:
                    plan.skipped_empty += 1
                    continue

                self._process_task(
                    raw_line=line,
                    status_char=status_char,
                    body=task_body,
                    date_str=date_str,
                    year=year,
                    month=month_str,
                    path=path,
                    line_idx=line_idx,
                    plan=plan,
                )
                continue

            # 2. Check for ideas
            idea_match = re.search(r"-\s*\[idea::\s*(.*?)\]", line)
            if idea_match:
                idea_text = idea_match.group(1).strip()
                self._process_idea(
                    idea_text, date_str, year, month_str, path, line_idx, plan
                )
                continue

            if in_unified_log and (
                "初步打算" in line
                or "灵感" in line
                or stripped.startswith("- **Idea")
            ):
                cleaned = re.sub(r"^\s*-\s*(\*\*[^*]+\*\*:?)?\s*", "", line).strip()
                if cleaned:
                    self._process_idea(
                        cleaned, date_str, year, month_str, path, line_idx, plan
                    )
                    continue

            # 3. Check for memos under Unified Log (non-tasks, non-agent summaries)
            if in_unified_log and stripped.startswith("- "):
                # Skip subagent summary lines like `- **bot2-architect (@...):** ...`
                if re.match(r"^-\s*\*\*(?:bot\d|main|ccus-bot|seismic-bot)", stripped, re.I):
                    plan.subagent_reflections += 1
                    continue

                cleaned_memo = re.sub(r"^\s*-\s*", "", line).strip()
                if cleaned_memo and not cleaned_memo.startswith("[!]"):
                    self._process_memo(
                        cleaned_memo, date_str, year, month_str, path, line_idx, plan
                    )

    def _process_task(
        self,
        raw_line: str,
        status_char: str,
        body: str,
        date_str: str,
        year: int,
        month: str,
        path: Path,
        line_idx: int,
        plan: MigrationPlan,
    ) -> None:
        status_char_lower = status_char.lower()
        if status_char_lower == "x":
            status = "completed"
        elif status_char_lower == ">":
            status = "migrated"
        elif status_char_lower == "-":
            status = "cancelled"
        else:
            status = "open"

        # Check existing time and completion markers
        time_match = re.search(r"\b(\d{1,2}:\d{2})\b", body)
        time_str = time_match.group(1) if time_match else None

        completion_match = re.search(r"✅\s*(\d{4}-\d{2}-\d{2})", body)
        completion_date = completion_match.group(1) if completion_match else None

        # Clean body from existing ⏳/📅 if attached poorly
        clean_text = body

        # Entity linking with ContactResolver
        linked_contacts: list[str] = []
        if self.resolver:
            clean_text, linked_contacts = self.resolver.link_entities(clean_text)

        # Multi-dimensional Tagging with TagClassifier
        tags_added: list[str] = []
        if self.classifier:
            clean_text, tags_added = self.classifier.classify_and_tag(clean_text)

        # Build elegant, iCal-compatible formatted entry:
        # e.g.: - [ ] ⏳ 2026-03-12 09:30 Task text #todo/inbox
        # or:   - [x] ⏳ 2026-03-10 08:45 Task text ✅ 2026-03-10
        date_prefix = f"⏳ {date_str}"
        if time_str and time_str not in date_prefix:
            date_prefix += f" {time_str}"

        # Clean text so date_prefix isn't duplicated
        if clean_text.startswith(f"⏳ {date_str}"):
            inner_text = clean_text[len(f"⏳ {date_str}") :].strip()
        else:
            inner_text = clean_text.strip()

        # Remove leading time if it was already incorporated into date_prefix
        if time_str and inner_text.startswith(time_str):
            inner_text = inner_text[len(time_str) :].strip()

        if status == "completed":
            check_box = "- [x]"
            done_suffix = ""
            if not completion_date and "✅" not in inner_text:
                done_suffix = f" ✅ {date_str}"
            formatted = f"{check_box} {date_prefix} {inner_text}{done_suffix}".strip()
        elif status == "migrated":
            check_box = "- [> ]"
            formatted = f"{check_box} {date_prefix} {inner_text}".strip()
        elif status == "cancelled":
            check_box = "- [-]"
            formatted = f"{check_box} {date_prefix} {inner_text}".strip()
        else:
            check_box = "- [ ]"
            # Ensure #todo/inbox tag is present
            if "#todo" not in inner_text:
                inner_text = f"{inner_text} #todo/inbox"
            formatted = f"{check_box} {date_prefix} {inner_text}".strip()

        task = ExtractedTask(
            raw=raw_line,
            status=status,
            text=inner_text,
            date_str=date_str,
            year=year,
            month=month,
            time_str=time_str,
            completion_date=completion_date,
            contacts=linked_contacts,
            tags=tags_added,
            source_file=path.name,
            source_line=line_idx,
            formatted_entry=formatted,
        )
        plan.tasks.append(task)

        # Queue contact log if contacts linked
        for c in linked_contacts:
            plan.contact_logs[c].append((date_str, f"任务关联: {inner_text}"))

    def _process_memo(
        self,
        text: str,
        date_str: str,
        year: int,
        month: str,
        path: Path,
        line_idx: int,
        plan: MigrationPlan,
    ) -> None:
        linked_contacts: list[str] = []
        clean_text = text
        if self.resolver:
            clean_text, linked_contacts = self.resolver.link_entities(clean_text)

        formatted = f"- 📌 [{date_str}] {clean_text}"
        memo = ExtractedMemo(
            text=clean_text,
            date_str=date_str,
            year=year,
            month=month,
            contacts=linked_contacts,
            source_file=path.name,
            source_line=line_idx,
            formatted_entry=formatted,
        )
        plan.memos.append(memo)

        for c in linked_contacts:
            plan.contact_logs[c].append((date_str, f"互动记录: {clean_text}"))

    def _process_idea(
        self,
        text: str,
        date_str: str,
        year: int,
        month: str,
        path: Path,
        line_idx: int,
        plan: MigrationPlan,
    ) -> None:
        linked_contacts: list[str] = []
        clean_text = text
        if self.resolver:
            clean_text, linked_contacts = self.resolver.link_entities(clean_text)

        tag_suffix = " #idea/seed" if "#idea" not in clean_text else ""
        formatted = f"- 💡 [{date_str}] {clean_text}{tag_suffix}"
        idea = ExtractedIdea(
            text=clean_text,
            date_str=date_str,
            year=year,
            month=month,
            contacts=linked_contacts,
            source_file=path.name,
            source_line=line_idx,
            formatted_entry=formatted,
        )
        plan.ideas.append(idea)


def render_dry_run_report(plan: MigrationPlan) -> str:
    lines = []
    lines.append("=" * 72)
    lines.append("📋 DAILY LOG MIGRATION ENGINE · DRY-RUN AUDIT REPORT")
    lines.append("=" * 72)
    lines.append(f"Vault Root:       {plan.vault_root}")
    lines.append(f"Total Daily Mds:  {len(plan.daily_files)} files")
    lines.append(f"Skipped Template: {plan.skipped_empty} empty checkboxes")
    lines.append(f"Subagent Logs:    {plan.subagent_reflections} lines (preserved in archive)")
    lines.append("-" * 72)

    # Task breakdown
    tasks_by_year: dict[int, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for t in plan.tasks:
        tasks_by_year[t.year][t.status] += 1

    lines.append(f"📦 Total Tasks Extracted: {len(plan.tasks)}")
    for yr, statuses in sorted(tasks_by_year.items()):
        status_str = ", ".join(f"{k}: {v}" for k, v in sorted(statuses.items()))
        lines.append(f"   ▶ {yr}: {sum(statuses.values())} tasks ({status_str})")

    # Memos breakdown
    memos_by_year: dict[int, int] = defaultdict(int)
    for m in plan.memos:
        memos_by_year[m.year] += 1
    lines.append(f"📌 Total Memos Extracted: {len(plan.memos)}")
    for yr, count in sorted(memos_by_year.items()):
        lines.append(f"   ▶ {yr}: {count} memos")

    # Ideas breakdown
    ideas_by_year: dict[int, int] = defaultdict(int)
    for i in plan.ideas:
        ideas_by_year[i.year] += 1
    lines.append(f"💡 Total Ideas Extracted: {len(plan.ideas)}")
    for yr, count in sorted(ideas_by_year.items()):
        lines.append(f"   ▶ {yr}: {count} ideas")

    # Contact interactions
    lines.append("-" * 72)
    lines.append(f"👤 Contact Interactions Linked: {len(plan.contact_logs)} persons")
    for person, entries in sorted(plan.contact_logs.items(), key=lambda x: len(x[1]), reverse=True)[:10]:
        lines.append(f"   ▶ [[{person}]]: {len(entries)} interaction entries")
    if len(plan.contact_logs) > 10:
        lines.append(f"   ... and {len(plan.contact_logs) - 10} more contacts")

    # Sample preview
    lines.append("-" * 72)
    lines.append("🔍 SAMPLE PREVIEW OF EXTRACTED STREAMS:")
    lines.append("\n[Sample Todo Entries]:")
    for t in plan.tasks[:5]:
        lines.append(f"  {t.formatted_entry}")

    lines.append("\n[Sample Memo Entries]:")
    for m in plan.memos[:3]:
        lines.append(f"  {m.formatted_entry}")

    if plan.ideas:
        lines.append("\n[Sample Idea Entries]:")
        for i in plan.ideas[:3]:
            lines.append(f"  {i.formatted_entry}")

    lines.append("=" * 72)
    return "\n".join(lines)


def commit_migration(plan: MigrationPlan, archive_rel_dir: str = "400_Archives/Legacy_Daily_Logs") -> None:
    vault_root = plan.vault_root
    inbox_dir = vault_root / "000_Inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    archive_dir = vault_root / archive_rel_dir
    archive_dir.mkdir(parents=True, exist_ok=True)

    print("\n🚀 Beginning atomic commit...")

    # 1. Group tasks by year and month
    tasks_by_year_month: dict[int, dict[str, list[ExtractedTask]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for t in plan.tasks:
        tasks_by_year_month[t.year][t.month].append(t)

    # 2. Write to todo.YYYY.md
    for yr, months in sorted(tasks_by_year_month.items()):
        todo_file = inbox_dir / f"todo.{yr}.md"
        _merge_stream_file(
            file_path=todo_file,
            title=f"Todo {yr}",
            capture_type="todo",
            year=yr,
            month_entries=months,
        )
        print(f"  ✅ Updated {todo_file.name} with {sum(len(v) for v in months.values())} tasks")

    # 3. Write to memo.YYYY.md
    memos_by_year_month: dict[int, dict[str, list[ExtractedMemo]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for m in plan.memos:
        memos_by_year_month[m.year][m.month].append(m)

    for yr, months in sorted(memos_by_year_month.items()):
        memo_file = inbox_dir / f"memo.{yr}.md"
        _merge_stream_file(
            file_path=memo_file,
            title=f"Memo {yr}",
            capture_type="memo",
            year=yr,
            month_entries=months,
        )
        print(f"  ✅ Updated {memo_file.name} with {sum(len(v) for v in months.values())} memos")

    # 4. Write to idea.YYYY.md
    ideas_by_year_month: dict[int, dict[str, list[ExtractedIdea]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for i in plan.ideas:
        ideas_by_year_month[i.year][i.month].append(i)

    for yr, months in sorted(ideas_by_year_month.items()):
        idea_file = inbox_dir / f"idea.{yr}.md"
        _merge_stream_file(
            file_path=idea_file,
            title=f"Idea {yr}",
            capture_type="idea",
            year=yr,
            month_entries=months,
        )
        print(f"  ✅ Updated {idea_file.name} with {sum(len(v) for v in months.values())} ideas")

    # 5. Append contact logs
    if append_contact_log and plan.contact_logs:
        lock_path = vault_root / ".knowledge_router" / "locks" / "contact.lock"
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        appended_count = 0
        for person, logs in plan.contact_logs.items():
            for date_str, text in logs:
                res = append_contact_log(
                    vault_root,
                    target=person,
                    text=f"[{date_str}] {text}",
                    lock_path=lock_path,
                )
                if res.get("status") == "success":
                    appended_count += 1
        print(f"  ✅ Appended {appended_count} interaction logs to 305_Contact/")

    # 6. Safely move original daily logs to 400_Archives/Legacy_Daily_Logs
    daily_base = vault_root / "100_Logs" / "daily"
    moved_count = 0
    for path in plan.daily_files:
        rel = path.relative_to(daily_base)
        dest = archive_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(dest))
        moved_count += 1

    # Place a README in 100_Logs/daily to guide future references
    legacy_readme = daily_base / "README.md"
    legacy_readme.write_text(
        f"---\ntitle: Daily Log System Migration Notice\ndate: {datetime.now().strftime('%Y-%m-%d')}\n---\n\n"
        "# 🪵 Daily Log 系统迁移说明\n\n"
        "原按日拆分的每日日记已全面打散解构并升级为流式捕获体系：\n\n"
        "- 📋 **待办流水**: [[000_Inbox/todo.2026.md|todo.2026.md]] (持续自动同步 Google Calendar)\n"
        "- 📝 **知识备忘**: [[000_Inbox/memo.2026.md|memo.2026.md]]\n"
        "- 💡 **灵感闪念**: [[000_Inbox/idea.2026.md|idea.2026.md]]\n"
        "- 👤 **人脉互动**: 沉淀至 [[300_Resources/305_Contact/|305_Contact/]] 对应人物卡片\n"
        "- 📦 **历史档案**: 存量日记已安全归档至 [[400_Archives/Legacy_Daily_Logs/|Legacy_Daily_Logs/]]\n",
        encoding="utf-8",
    )
    print(f"  ✅ Archived {moved_count} legacy files to {archive_rel_dir}")
    print(f"  ✅ Left migration guidance in 100_Logs/daily/README.md")
    print("\n🎉 Migration successfully completed with 0 data loss!")


def _merge_stream_file(
    file_path: Path,
    title: str,
    capture_type: str,
    year: int,
    month_entries: dict[str, list[Any]],
) -> None:
    if file_path.is_file():
        content = file_path.read_text(encoding="utf-8")
    else:
        content = (
            f"---\n"
            f'title: "{title}"\n'
            f"date: {year}-01-01\n"
            f"tags:\n"
            f"  - capture/{capture_type}\n"
            f"type: capture\n"
            f"year: {year}\n"
            f"---\n\n"
            f"# {title}\n\n"
        )

    # Sort months chronologically
    for month_str in sorted(month_entries.keys()):
        entries = month_entries[month_str]
        month_heading = f"## {month_str}"
        month_match = re.search(rf"(?m)^##\s+{re.escape(month_str)}\s*$", content)

        # Collect formatted lines
        new_lines = []
        for e in entries:
            line_str = e.formatted_entry
            # Deduplication: check if already in file
            if line_str in content:
                continue
            new_lines.append(line_str)

        if not new_lines:
            continue

        block = "\n".join(new_lines) + "\n"

        if not month_match:
            content = content.rstrip() + f"\n\n{month_heading}\n\n{block}"
        else:
            start_pos = month_match.end()
            next_heading = re.search(r"(?m)^##\s+", content[start_pos:])
            if next_heading:
                insert_pos = start_pos + next_heading.start()
                content = (
                    content[:insert_pos].rstrip()
                    + f"\n{block}\n"
                    + content[insert_pos:].lstrip()
                )
            else:
                content = content.rstrip() + f"\n{block}"

    if atomic_text_write:
        atomic_text_write(file_path, content)
    else:
        file_path.write_text(content, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Migrate legacy daily logs into quick capture streams.")
    parser.add_argument("--vault", default=r"D:\Documents\zhihaol", help="Path to Obsidian vault")
    parser.add_argument("--commit", action="store_true", help="Execute real migration (default is dry-run)")
    parser.add_argument("--archive-dir", default="400_Archives/Legacy_Daily_Logs", help="Target archive folder")
    args = parser.parse_args()

    vault_root = Path(args.vault)
    if not vault_root.is_dir():
        print(f"Error: Vault path not found: {vault_root}")
        return 1

    daily_parser = DailyLogParser(vault_root)
    plan = daily_parser.parse_vault()

    report = render_dry_run_report(plan)
    print(report)

    if args.commit:
        commit_migration(plan, archive_rel_dir=args.archive_dir)
    else:
        print("\n💡 NOTE: This was a DRY-RUN. No files were modified.")
        print("To execute real migration, re-run with: python scripts/migrate_daily_logs.py --commit")

    return 0


if __name__ == "__main__":
    sys.exit(main())
