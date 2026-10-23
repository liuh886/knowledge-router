#!/usr/bin/env python3
"""Triage Engine: Contact entity linking and multi-dimensional tagging for inbox todo files."""

from __future__ import annotations

import os
import re
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from registry import FileLock, atomic_text_write

DEFAULT_TAG_RULES: dict[str, list[str]] = {
    "#travel": [
        "出差", "航班", "机票", "酒店", "高铁", "火车票", "接机", "送机",
        "登机", "住宿", "签证", "护照", "passport", "flight", "hotel", "travel"
    ],
    "#Qatar": [
        "多哈", "卡塔尔", "qatar", "doha", "qg", "qatargas", "noc", "north field"
    ],
    "#newbusiness": [
        "商务", "客户", "投标", "招标", "标书", "合同", "报价", "签约",
        "询价", "商机", "拜访", "回款", "垫付款", "新业务", "new business", "bidding", "proposal"
    ],
    "#research": [
        "论文", "审稿", "实验", "模型", "水文", "冰川", "遥感", "geoutils",
        "xdem", "降水", "气象", "科研", "paper", "manuscript", "experiment", "model"
    ],
    "#energy": [
        "新能源", "光伏", "水面光伏", "风电", "储能", "海上风电", "ccs", "ccus", "碳捕集"
    ],
    "#meeting": [
        "开会", "例会", "会议", "周会", "月会", "讨论", "商量", "拜访", "meeting", "sync"
    ],
}

# Non-contact files to skip under contact directories
_NON_CONTACT_STEMS = frozenset(
    {
        "bgp - onboard",
        "interveiw_20260124",
        "年度工作总结",
        "新业务推进问题访谈纪要与行动报告",
        "contact_radar",
        "zhihao liu",
    }
)


@dataclass
class ContactEntity:
    canonical: str
    relative_path: str
    chinese_name: str = ""
    english_name: str = ""
    aliases: list[str] = field(default_factory=list)


@dataclass
class TriageDiff:
    line_number: int
    original: str
    triaged: str
    contacts: list[str] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TriageReport:
    vault: str
    target_file: str
    year: int
    committed: bool
    total_lines: int = 0
    todo_count: int = 0
    triaged_count: int = 0
    diffs: list[TriageDiff] = field(default_factory=list)
    contacts_matched: list[str] = field(default_factory=list)
    tags_applied: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["diffs"] = [d.to_dict() for d in self.diffs]
        return result


class ContactResolver:
    """Discovers contact notes and resolves text mentions to canonical [[wikilinks]]."""

    def __init__(self, vault_root: Path) -> None:
        self.vault_root = vault_root.resolve()
        self.entities: dict[str, ContactEntity] = {}
        self.alias_to_canonical: dict[str, str] = {}
        self.combined_regex: re.Pattern[str] | None = None
        self._load_contacts()

    def _find_contact_dir(self) -> Path | None:
        candidate = self.vault_root / "300_Resources" / "305_Contact"
        if candidate.is_dir():
            return candidate
        for sub in self.vault_root.glob("**/305_Contact"):
            if sub.is_dir():
                return sub
        return None

    def _load_contacts(self) -> None:
        contact_dir = self._find_contact_dir()
        if not contact_dir:
            return

        for path in sorted(contact_dir.rglob("*.md")):
            stem = path.stem
            stem_lower = stem.casefold()

            if (
                ".workflow" in path.name.lower()
                or stem_lower in _NON_CONTACT_STEMS
                or "zhihao liu" in str(path).lower()
            ):
                continue

            try:
                content = path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue

            # Check if it has a workflow marker in frontmatter
            fm_match = re.match(r"^---\s*\n(.*?)\n---", content, re.DOTALL)
            aliases: list[str] = []
            if fm_match:
                fm = fm_match.group(1)
                if "workflow_family:" in fm:
                    continue
                # Parse aliases list
                alias_block = re.search(r"^aliases:\s*\n((?:\s*-\s*.*\n?)+)", fm, re.MULTILINE)
                if alias_block:
                    aliases = [
                        re.sub(r"^\s*-\s*", "", line).strip(" '\"")
                        for line in alias_block.group(1).splitlines()
                        if line.strip()
                    ]
                else:
                    inline = re.search(r"^aliases:\s*\[(.*?)\]", fm, re.MULTILINE)
                    if inline:
                        aliases = [a.strip(" '\"") for a in inline.group(1).split(",") if a.strip()]
                    else:
                        single = re.search(r"^alias:\s*(.*)", fm, re.MULTILINE)
                        if single:
                            val = single.group(1).strip(" '\"")
                            if val:
                                aliases = [val]

            h1_match = re.search(r"^#\s+(.+)$", content, re.MULTILINE)
            if h1_match:
                h1_title = h1_match.group(1).strip()
                if (
                    h1_title
                    and len(h1_title) >= 2
                    and h1_title not in aliases
                    and h1_title != stem
                ):
                    aliases.append(h1_title)

            chinese_parts = re.findall(r"[\u4e00-\u9fa5]+", stem)
            chinese_name = "".join(chinese_parts) if chinese_parts else ""
            english_name = re.sub(r"[\u4e00-\u9fa5]+", "", stem).strip().replace("_", " ")

            rel_path = str(path.relative_to(self.vault_root)).replace("\\", "/")
            entity = ContactEntity(
                canonical=stem,
                relative_path=rel_path,
                chinese_name=chinese_name,
                english_name=english_name,
                aliases=aliases,
            )
            self.entities[stem] = entity

            # Populate alias mapping
            self.alias_to_canonical[stem] = stem
            if chinese_name and len(chinese_name) >= 2:
                self.alias_to_canonical.setdefault(chinese_name, stem)
            if english_name and len(english_name) >= 3:
                self.alias_to_canonical.setdefault(english_name, stem)
            for a in aliases:
                if len(a) >= 2:
                    self.alias_to_canonical.setdefault(a, stem)

        # Build single-pass regular expression sorted by length descending
        if not self.alias_to_canonical:
            self.combined_regex = None
            return

        sorted_aliases = sorted(
            self.alias_to_canonical.keys(), key=lambda x: len(x), reverse=True
        )
        regex_parts = []
        for alias in sorted_aliases:
            escaped = re.escape(alias)
            is_ascii = all(ord(ch) < 128 for ch in alias)
            if is_ascii:
                regex_parts.append(r"(?<![a-zA-Z0-9_])" + escaped + r"(?![a-zA-Z0-9_])")
            else:
                regex_parts.append(escaped)

        self.combined_regex = re.compile("|".join(regex_parts), re.IGNORECASE)

    def match_contact(self, query: str) -> tuple[ContactEntity | None, list[str]]:
        """Match a query string to a ContactEntity by exact alias, case-insensitive, or substring."""
        q = query.strip()
        if not q:
            return None, []

        # 1. Exact alias match
        canon = self.alias_to_canonical.get(q)
        if not canon:
            # 2. Case-insensitive match
            for k, v in self.alias_to_canonical.items():
                if k.casefold() == q.casefold():
                    canon = v
                    break
        if not canon:
            # 3. Substring match
            candidates: list[str] = []
            for k, v in self.alias_to_canonical.items():
                if q.casefold() in k.casefold() or k.casefold() in q.casefold():
                    if v not in candidates:
                        candidates.append(v)
            if len(candidates) == 1:
                canon = candidates[0]
            elif candidates:
                return None, candidates

        if canon and canon in self.entities:
            return self.entities[canon], []
        return None, []


    def link_entities(self, text: str) -> tuple[str, list[str]]:
        """Safely link contact mentions outside existing links and tags."""
        if not self.combined_regex or not self.alias_to_canonical:
            return text, []

        protected_re = re.compile(
            r"(\[\[[^\]]+\]\]|\[[^\]]+\]\([^\)]+\)|https?://\S+|\[\d{4}-\d{2}-\d{2}[^\]]*\]|#[a-zA-Z0-9_\u4e00-\u9fa5/-]+|^-\s*\[[ xX]\]\s*[⏳📌💡]?)"
        )

        tokens: list[tuple[bool, str]] = []
        last_idx = 0
        for m in protected_re.finditer(text):
            start, end = m.span()
            if start > last_idx:
                tokens.append((False, text[last_idx:start]))
            tokens.append((True, text[start:end]))
            last_idx = end
        if last_idx < len(text):
            tokens.append((False, text[last_idx:]))

        matched_contacts: set[str] = set()
        new_tokens: list[str] = []

        for is_prot, seg in tokens:
            if is_prot:
                new_tokens.append(seg)
                continue

            def _replace_match(m: re.Match[str]) -> str:
                matched_alias = m.group(0)
                canon = self.alias_to_canonical.get(matched_alias)
                if not canon:
                    for k, v in self.alias_to_canonical.items():
                        if k.casefold() == matched_alias.casefold():
                            canon = v
                            break
                if not canon:
                    return matched_alias
                matched_contacts.add(canon)
                if matched_alias == canon:
                    return f"[[{canon}]]"
                return f"[[{canon}|{matched_alias}]]"

            replaced = self.combined_regex.sub(_replace_match, seg)
            new_tokens.append(replaced)

        return "".join(new_tokens), sorted(list(matched_contacts))


class TagClassifier:
    """Classifies and applies multi-dimensional business tags based on rule keywords."""

    def __init__(self, rules: dict[str, list[str]] | None = None) -> None:
        self.rules = rules or DEFAULT_TAG_RULES

    def classify_and_tag(self, line: str) -> tuple[str, list[str]]:
        """Add missing tags to a todo line before #todo/inbox or at line end."""
        existing_tags = set(re.findall(r"#([a-zA-Z0-9_\u4e00-\u9fa5/-]+)", line))
        line_lower = line.lower()

        tags_to_add: list[str] = []
        for tag, keywords in self.rules.items():
            tag_clean = tag.lstrip("#")
            if tag_clean in existing_tags:
                continue
            for kw in keywords:
                kw_clean = kw.lower()
                is_ascii = all(ord(ch) < 128 for ch in kw_clean)
                if is_ascii:
                    pat = r"(?<![a-zA-Z0-9_])" + re.escape(kw_clean) + r"(?![a-zA-Z0-9_])"
                    if re.search(pat, line_lower):
                        tags_to_add.append(tag)
                        break
                else:
                    if kw_clean in line_lower:
                        tags_to_add.append(tag)
                        break

        if not tags_to_add:
            return line, []

        status_match = re.search(r"(\s+#todo(?:/[a-zA-Z0-9_-]+)?)\s*$", line)
        tag_str = " ".join(tags_to_add)
        if status_match:
            idx = status_match.start(1)
            new_line = line[:idx] + " " + tag_str + status_match.group(1)
        else:
            new_line = line.rstrip() + " " + tag_str

        return new_line, tags_to_add


def triage_todo_file(
    vault_root: Path,
    *,
    vault_name: str = "",
    year: int | None = None,
    commit: bool = False,
    lock_path: Path | None = None,
) -> TriageReport:
    """Triage inbox todo file with contact entity linking and multi-dimensional tagging."""
    vault_root = vault_root.resolve()
    current_year = year or datetime.now().year
    target_rel = f"000_Inbox/todo.{current_year}.md"
    target_path = vault_root / "000_Inbox" / f"todo.{current_year}.md"

    if not target_path.is_file():
        return TriageReport(
            vault=vault_name or vault_root.name,
            target_file=target_rel,
            year=current_year,
            committed=commit,
        )

    if commit:
        drain_quick_capture_buffer(
            vault_root, vault_name=vault_name, lock_path=lock_path
        )

    resolver = ContactResolver(vault_root)
    classifier = TagClassifier()

    raw_text = target_path.read_text(encoding="utf-8")
    lines = raw_text.splitlines(keepends=True)

    new_lines: list[str] = []
    diffs: list[TriageDiff] = []
    all_contacts: set[str] = set()
    all_tags: set[str] = set()
    todo_count = 0

    for i, line in enumerate(lines):
        stripped = line.lstrip()
        # Process uncompleted todo lines: starts with - [ ]
        if stripped.startswith("- [ ]"):
            todo_count += 1
            original_line = line.rstrip("\r\n")

            # 1. Entity linking
            linked_line, contacts = resolver.link_entities(original_line)
            # 2. Tagging
            triaged_line, tags = classifier.classify_and_tag(linked_line)

            if triaged_line != original_line:
                diffs.append(
                    TriageDiff(
                        line_number=i + 1,
                        original=original_line,
                        triaged=triaged_line,
                        contacts=contacts,
                        tags=tags,
                    )
                )
                all_contacts.update(contacts)
                all_tags.update(tags)
                newline_char = "\r\n" if line.endswith("\r\n") else "\n"
                new_lines.append(triaged_line + newline_char)
            else:
                new_lines.append(line)
        else:
            new_lines.append(line)

    if commit and diffs:
        # Atomic commit
        full_content = "".join(new_lines)
        if lock_path is not None:
            with FileLock(lock_path):
                atomic_text_write(target_path, full_content)
        else:
            atomic_text_write(target_path, full_content)

    return TriageReport(
        vault=vault_name or vault_root.name,
        target_file=target_rel,
        year=current_year,
        committed=commit and bool(diffs),
        total_lines=len(lines),
        todo_count=todo_count,
        triaged_count=len(diffs),
        diffs=diffs,
        contacts_matched=sorted(list(all_contacts)),
        tags_applied=sorted(list(all_tags)),
    )


def format_triage_summary(report: TriageReport) -> str:
    """Format human-readable CLI summary."""
    status_str = "已提交 (Committed)" if report.committed else "预览模式 (Dry Run)"
    lines = [
        f"📋 知识库收件分诊报告 [{report.vault}]",
        f"文件: {report.target_file} ({report.year} 年待办)",
        f"状态: {status_str}",
        f"统计: 共扫描 {report.todo_count} 条待办 · 待分诊处理 {report.triaged_count} 条",
    ]
    if report.contacts_matched:
        lines.append(f"🔗 关联人物: {', '.join(report.contacts_matched)}")
    if report.tags_applied:
        lines.append(f"🏷️  应用标签: {', '.join(report.tags_applied)}")

    if report.diffs:
        lines.append("\n变更明细:")
        for d in report.diffs:
            lines.append(f"  行 {d.line_number}:")
            lines.append(f"    - 原文: {d.original}")
            lines.append(f"    + 分诊: {d.triaged}")
            if d.contacts:
                lines.append(f"      人物: {', '.join(d.contacts)}")
            if d.tags:
                lines.append(f"      标签: {', '.join(d.tags)}")

    if not report.committed and report.triaged_count > 0:
        lines.append("\n💡 提示: 加上 --commit 参数以安全写入分诊结果。")
    elif report.triaged_count == 0:
        lines.append("✨ 所有待办均已完成分诊对齐，无需变更。")

    return "\n".join(lines)


def append_contact_log(
    vault_root: Path,
    target: str,
    text: str,
    *,
    vault_name: str = "",
    lock_path: Path | None = None,
) -> dict[str, Any]:
    """Match a contact in 305_Contact/ and append interaction text under ## Log."""
    vault_root = vault_root.resolve()
    resolver = ContactResolver(vault_root)
    entity, candidates = resolver.match_contact(target)

    if not entity:
        if candidates:
            return {
                "status": "ambiguous",
                "target": target,
                "message": f"找到多个匹配联系人: {', '.join(candidates)}，请指定更精确的名称",
                "candidates": candidates,
            }
        return {
            "status": "not_found",
            "target": target,
            "message": f"未在 305_Contact/ 中找到匹配的联系人: '{target}'",
            "candidates": [],
        }

    note_path = vault_root / entity.relative_path
    if not note_path.is_file():
        return {
            "status": "error",
            "target": target,
            "message": f"联系人笔记文件不存在: {entity.relative_path}",
        }

    raw = note_path.read_text(encoding="utf-8")
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    entry_line = f"- [{now_str}] {text.strip()}"

    # Check for Log heading: ## Log, ## Interaction Log, ## Interaction History, ## 汇报记录 (Log), etc.
    log_match = re.search(
        r"(?mi)^##\s+(?:.*(?:Log|Interaction History|Interaction Log).*|互动记录|沟通记录|记录)\s*$",
        raw,
    )
    if log_match:
        # Find if there is a next section ## after this heading
        start_pos = log_match.end()
        next_heading = re.search(r"(?m)^##\s+", raw[start_pos:])
        if next_heading:
            insert_pos = start_pos + next_heading.start()
            new_content = (
                raw[:insert_pos].rstrip()
                + f"\n{entry_line}\n\n"
                + raw[insert_pos:].lstrip()
            )
        else:
            new_content = raw.rstrip() + f"\n{entry_line}\n"
    else:
        # Append ## Log at the end of the note
        new_content = raw.rstrip() + f"\n\n## Log\n{entry_line}\n"

    if lock_path is not None:
        with FileLock(lock_path):
            atomic_text_write(note_path, new_content)
    else:
        atomic_text_write(note_path, new_content)

    return {
        "status": "success",
        "canonical": entity.canonical,
        "relative_path": entity.relative_path,
        "entry": entry_line,
        "message": f"👤 已成功追加记录至联系人 [[{entity.canonical}]]:\n{entry_line}",
    }


def drain_quick_capture_buffer(
    vault_root: Path,
    *,
    vault_name: str = "",
    lock_path: Path | None = None,
) -> dict[str, Any]:
    """Drain backward-compatibility buffer (000_Inbox/Quick_Capture.md) into annual streams."""
    vault_root = vault_root.resolve()
    buffer_file = vault_root / "000_Inbox" / "Quick_Capture.md"
    if not buffer_file.is_file():
        return {"status": "skipped", "message": "Buffer file does not exist"}

    try:
        content = buffer_file.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return {"status": "error", "message": "Cannot read buffer file"}

    lines = content.splitlines()
    entries_to_route = []
    header_lines = []

    in_header = True
    for line in lines:
        stripped = line.strip()
        if (
            stripped.startswith("- [ ]")
            or stripped.startswith("- [x]")
            or stripped.startswith("- 💡")
            or stripped.startswith("- 📌")
        ):
            in_header = False
            entries_to_route.append(stripped)
        elif not in_header:
            if stripped:
                entries_to_route.append(stripped)
        else:
            header_lines.append(line)

    if not entries_to_route:
        return {"status": "clean", "routed_count": 0}

    routed = []
    for entry in entries_to_route:
        if entry.startswith(("- [ ]", "- [x]")):
            m = re.match(
                r"^-\s*\[[ x]\]\s*(?:⏳\s*(?:\d{4}-\d{2}-\d{2}\s*)?(?:\d{2}:\d{2}\s*)?)?(.*)$",
                entry,
            )
            task_text = m.group(1).strip() if m else entry[5:].strip()
            task_text = re.sub(
                r"\s*#todo/(?:inbox|next|ing|someday)\b", "", task_text
            ).strip()
            if task_text:
                append_inbox_stream(
                    vault_root,
                    "todo",
                    task_text,
                    vault_name=vault_name,
                    lock_path=lock_path,
                )
                routed.append(("todo", task_text))
        elif entry.startswith("- 💡"):
            m = re.match(
                r"^-\s*💡\s*(?:\[\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2}\]\s*)?(.*)$", entry
            )
            idea_text = m.group(1).strip() if m else entry[3:].strip()
            idea_text = re.sub(
                r"\s*#idea/(?:seed|grow|evergreen)\b", "", idea_text
            ).strip()
            if idea_text:
                append_inbox_stream(
                    vault_root,
                    "idea",
                    idea_text,
                    vault_name=vault_name,
                    lock_path=lock_path,
                )
                routed.append(("idea", idea_text))
        else:
            m = re.match(
                r"^-\s*(?:📌\s*)?(?:\[\d{4}-\d{2}-\d{2}\s*\d{2}:\d{2}\]\s*)?(.*)$",
                entry,
            )
            memo_text = m.group(1).strip() if m else entry.lstrip("- *").strip()
            if memo_text:
                append_inbox_stream(
                    vault_root,
                    "memo",
                    memo_text,
                    vault_name=vault_name,
                    lock_path=lock_path,
                )
                routed.append(("memo", memo_text))

    clean_content = "\n".join(header_lines).rstrip() + "\n\n"
    if lock_path:
        with FileLock(lock_path):
            atomic_text_write(buffer_file, clean_content)
    else:
        atomic_text_write(buffer_file, clean_content)

    return {"status": "success", "routed_count": len(routed), "routed": routed}


def append_inbox_stream(
    vault_root: Path,
    capture_type: str,
    text: str,
    *,
    vault_name: str = "",
    year: int | None = None,
    lock_path: Path | None = None,
) -> dict[str, Any]:
    """Append a captured entry (todo, memo, idea) to 000_Inbox/<type>.<year>.md."""
    vault_root = vault_root.resolve()
    capture_type = capture_type.lower().strip()
    if capture_type not in {"todo", "memo", "idea"}:
        raise ValueError(
            f"Invalid capture type '{capture_type}'. Must be 'todo', 'memo', or 'idea'."
        )

    now = datetime.now()
    cur_year = year or now.year
    cur_month = now.strftime("%Y-%m")
    timestamp = now.strftime("%Y-%m-%d %H:%M")

    inbox_dir = vault_root / "000_Inbox"
    inbox_dir.mkdir(parents=True, exist_ok=True)
    file_path = inbox_dir / f"{capture_type}.{cur_year}.md"

    # Prefix and tags
    if capture_type == "todo":
        prefix = "- [ ] ⏳"
        tag = "#todo/inbox"
    elif capture_type == "memo":
        prefix = "- 📌"
        tag = ""
    else:  # idea
        prefix = "- 💡"
        tag = "#idea/seed"

    tag_suffix = f" {tag}" if tag else ""
    if capture_type == "todo":
        raw_entry = f"- [ ] ⏳ {timestamp} {text.strip()}{tag_suffix}"
    else:
        raw_entry = f"{prefix} [{timestamp}] {text.strip()}{tag_suffix}"

    # Header initialization if file does not exist
    if not file_path.is_file():
        title_map = {"todo": "📋 Todo", "memo": "📝 Memo", "idea": "💡 Idea"}
        title_name = title_map.get(capture_type, capture_type.capitalize())
        header = (
            f"---\n"
            f'title: "{title_name} {cur_year}"\n'
            f"date: {cur_year}-01-01\n"
            f"tags:\n"
            f"  - capture/{capture_type}\n"
            f"type: capture\n"
            f"year: {cur_year}\n"
            f"---\n\n"
            f"# {title_name} {cur_year}\n\n"
        )
        content = header
    else:
        content = file_path.read_text(encoding="utf-8")

    month_heading = f"## {cur_month}"
    month_match = re.search(rf"(?m)^##\s+{re.escape(cur_month)}\s*$", content)

    if not month_match:
        content = content.rstrip() + f"\n\n{month_heading}\n\n{raw_entry}\n"
    else:
        # Month heading exists. Find insertion point (before next ## section or at end)
        start_pos = month_match.end()
        next_heading = re.search(r"(?m)^##\s+", content[start_pos:])
        if next_heading:
            insert_pos = start_pos + next_heading.start()
            content = (
                content[:insert_pos].rstrip()
                + f"\n{raw_entry}\n\n"
                + content[insert_pos:].lstrip()
            )
        else:
            content = content.rstrip() + f"\n{raw_entry}\n"

    # Write file atomically
    if lock_path is not None:
        with FileLock(lock_path):
            atomic_text_write(file_path, content)
    else:
        atomic_text_write(file_path, content)

    # For todo, immediately run triage so entities and tags are linked on the fly!
    triage_result = None
    final_entry = raw_entry
    if capture_type == "todo":
        triage_report = triage_todo_file(
            vault_root,
            vault_name=vault_name,
            year=cur_year,
            commit=True,
            lock_path=lock_path,
        )
        for diff in triage_report.diffs:
            if diff.original.strip() == raw_entry.strip():
                final_entry = diff.triaged
                triage_result = {
                    "contacts": diff.contacts,
                    "tags": diff.tags,
                }
                break

    icon_map = {"todo": "📋", "memo": "📝", "idea": "💡"}
    icon = icon_map.get(capture_type, "📋")
    return {
        "status": "success",
        "type": capture_type,
        "year": cur_year,
        "file": f"000_Inbox/{capture_type}.{cur_year}.md",
        "entry": final_entry,
        "triage": triage_result,
        "message": f"{icon} 已记录至 {capture_type}.{cur_year}.md [{cur_month}]:\n{final_entry}",
    }


def get_contact_radar(vault_root: Path, year: int | None = None) -> dict[str, Any]:
    """Analyze all contacts in 305_Contact/ against 000_Inbox/todo.YYYY.md to produce a contact radar."""
    vault_root = vault_root.resolve()
    resolver = ContactResolver(vault_root)
    cur_year = year or datetime.now().year
    now = datetime.now()

    # Load open tasks from todo.YYYY.md
    todo_file = vault_root / "000_Inbox" / f"todo.{cur_year}.md"
    open_tasks: list[str] = []
    if todo_file.is_file():
        for line in todo_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            if re.match(r"^\s*-\s*\[ \]", line):
                open_tasks.append(line.strip())

    items = []
    for canon, entity in sorted(resolver.entities.items()):
        c_path = vault_root / entity.relative_path
        c_content = (
            c_path.read_text(encoding="utf-8", errors="ignore")
            if c_path.is_file()
            else ""
        )

        # Match tasks mentioning this contact
        all_names = (
            [canon]
            + entity.aliases
            + ([entity.chinese_name] if entity.chinese_name else [])
        )
        matched_tasks = []
        for t in open_tasks:
            for name in all_names:
                if len(name) >= 2 and (
                    f"[[{canon}" in t
                    or f"|{name}]]" in t
                    or f"[[{name}]]" in t
                    or name in t
                ):
                    matched_tasks.append(t)
                    break

        # Find interaction dates from ## Log or text
        log_dates = re.findall(
            r"\[(\d{4}-\d{2}-\d{2})\]|\b(\d{4}-\d{2}-\d{2})\b", c_content
        )
        dates = []
        for d1, d2 in log_dates:
            d = d1 or d2
            try:
                dates.append(datetime.strptime(d, "%Y-%m-%d"))
            except ValueError:
                pass

        last_date_str = max(dates).strftime("%Y-%m-%d") if dates else "无记录"
        days_ago = (now - max(dates)).days if dates else 999

        # Extract company from inline or text
        company_m = re.search(r"(?:Company::|Company:)\s*([^\n\r]+)", c_content)
        company = company_m.group(1).strip() if company_m else ""
        if company.startswith("Phone::") or company in ("[[]]", "[[", "]]"):
            company = ""

        # Health tier
        if matched_tasks:
            tier = "🔥 待办推进中"
        elif days_ago <= 30:
            tier = "🟢 活跃交流"
        elif days_ago <= 60:
            tier = "🟡 正常维系"
        else:
            tier = "💤 沉睡需跟进"

        items.append(
            {
                "canonical": canon,
                "chinese_name": entity.chinese_name,
                "relative_path": entity.relative_path,
                "company": company,
                "aliases": entity.aliases,
                "pending_tasks_count": len(matched_tasks),
                "pending_tasks": matched_tasks,
                "last_contact_date": last_date_str,
                "days_ago": days_ago,
                "tier": tier,
            }
        )

    # Sort items: pending tasks descending, then days_ago ascending
    items.sort(key=lambda x: (-x["pending_tasks_count"], x["days_ago"]))

    # Summary counts
    tier_counts = defaultdict(int)
    total_pending = sum(x["pending_tasks_count"] for x in items)
    for x in items:
        tier_counts[x["tier"]] += 1

    return {
        "status": "success",
        "total_contacts": len(items),
        "total_pending_tasks": total_pending,
        "tier_summary": dict(tier_counts),
        "contacts": items,
    }


def get_contact_detail(
    vault_root: Path, query: str, year: int | None = None
) -> dict[str, Any]:
    """Retrieve full detail card for a contact including info, pending tasks, recent logs, and projects."""
    vault_root = vault_root.resolve()
    resolver = ContactResolver(vault_root)
    entity, candidates = resolver.match_contact(query)
    if not entity:
        return {"status": "not_found", "query": query, "candidates": candidates}

    cur_year = year or datetime.now().year
    c_path = vault_root / entity.relative_path
    content = (
        c_path.read_text(encoding="utf-8", errors="ignore") if c_path.is_file() else ""
    )

    # Parse metadata fields
    fields: dict[str, str] = {}
    for line in content.splitlines():
        m = re.match(r"^([a-zA-Z\s]+)::\s*(.*)", line)
        if m:
            fields[m.group(1).strip().lower()] = m.group(2).strip()

    # Find pending tasks
    todo_file = vault_root / "000_Inbox" / f"todo.{cur_year}.md"
    matched_tasks: list[str] = []
    all_names = (
        [entity.canonical]
        + entity.aliases
        + ([entity.chinese_name] if entity.chinese_name else [])
    )
    if todo_file.is_file():
        for line in todo_file.read_text(encoding="utf-8", errors="ignore").splitlines():
            if re.match(r"^\s*-\s*\[ \]", line):
                if any(name in line for name in all_names if len(name) >= 2):
                    matched_tasks.append(line.strip())

    # Find recent interaction logs from ## Log / ## Interaction History / ## 汇报记录
    interaction_logs: list[str] = []
    in_log = False
    for line in content.splitlines():
        line_s = line.strip()
        lower = line_s.lower()
        if line_s.startswith("## ") and any(k in lower for k in ("log", "interaction", "汇报")):
            in_log = True
            continue
        elif line_s.startswith("## ") and in_log:
            in_log = False
        if in_log and line_s.startswith(("- ", "* ")):
            interaction_logs.append(line_s)

    # Find associated projects from 100_Project/
    associated_projects: list[str] = []
    prj_dir = vault_root / "100_Project"
    if prj_dir.is_dir():
        for prj_folder in prj_dir.iterdir():
            if prj_folder.is_dir() and not prj_folder.name.startswith("."):
                found = False
                for p_file in prj_folder.glob("*.md"):
                    try:
                        prj_text = p_file.read_text(encoding="utf-8", errors="ignore")
                        if any(n in prj_text for n in all_names if len(n) >= 2):
                            associated_projects.append(prj_folder.name)
                            found = True
                            break
                    except OSError:
                        pass

    return {
        "status": "success",
        "canonical": entity.canonical,
        "relative_path": entity.relative_path,
        "chinese_name": entity.chinese_name,
        "english_name": entity.english_name,
        "aliases": entity.aliases,
        "info": fields,
        "pending_tasks": matched_tasks,
        "recent_logs": interaction_logs[-5:],
        "associated_projects": associated_projects,
    }


