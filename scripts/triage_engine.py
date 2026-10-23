#!/usr/bin/env python3
"""Triage Engine: Contact entity linking and multi-dimensional tagging for inbox todo files."""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from fts_indexer import ScanReport, scan_notes
from link_resolver import aliases as parse_aliases

DEFAULT_TAG_RULES: dict[str, list[str]] = {
    "#travel": [
        "出差",
        "航班",
        "机票",
        "酒店",
        "高铁",
        "火车票",
        "接机",
        "送机",
        "登机",
        "住宿",
        "签证",
        "护照",
        "passport",
        "flight",
        "hotel",
        "travel",
    ],
    "#Qatar": ["多哈", "卡塔尔", "qatar", "doha", "qg", "qatargas", "noc", "north field"],
    "#newbusiness": [
        "商务",
        "客户",
        "投标",
        "招标",
        "标书",
        "合同",
        "报价",
        "签约",
        "询价",
        "商机",
        "拜访",
        "回款",
        "垫付款",
        "新业务",
        "new business",
        "bidding",
        "proposal",
    ],
    "#research": [
        "论文",
        "审稿",
        "实验",
        "模型",
        "水文",
        "冰川",
        "遥感",
        "geoutils",
        "xdem",
        "降水",
        "气象",
        "科研",
        "paper",
        "manuscript",
        "experiment",
        "model",
    ],
    "#energy": ["新能源", "光伏", "水面光伏", "风电", "储能", "海上风电", "ccs", "ccus", "碳捕集"],
    "#meeting": ["开会", "例会", "会议", "周会", "月会", "讨论", "商量", "拜访", "meeting", "sync"],
}

_NON_CONTACT_STEMS = frozenset({"contact_radar", "readme", "index", "template", "模板"})


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
    """Read a verified snapshot; paths are identities and aliases retain ambiguity."""

    def __init__(self, vault_root: Path, *, source_scan: ScanReport | None = None) -> None:
        self.vault_root = vault_root.resolve()
        self.scan = source_scan or scan_notes(self.vault_root)
        if self.scan.skipped_decode or self.scan.skipped_io:
            raise ValueError("Contact resolution requires a complete readable source scan")
        self.notes = {note.path: note for note in self.scan.records}
        self.entities: dict[str, ContactEntity] = {}
        self.alias_to_canonical: dict[str, str] = {}
        self.alias_candidates: dict[str, set[str]] = defaultdict(set)
        self.combined_regex: re.Pattern[str] | None = None
        counts = Counter(Path(n.path).stem.casefold() for n in self.scan.records)
        spellings: set[str] = set()
        for note in self.scan.records:
            path = Path(note.path)
            stem = path.stem
            if not any(part.casefold() == "305_contact" for part in path.parts[:-1]):
                continue
            if (
                stem.startswith(("_", "."))
                or stem.casefold() in _NON_CONTACT_STEMS
                or ".workflow" in stem.casefold()
                or re.search(r"(?m)^workflow_family:", note.content)
            ):
                continue
            names = parse_aliases(note.content)
            title = re.search(r"(?m)^#[ \t]+(.+)$", note.content)
            if title and title.group(1).strip() != stem:
                names.append(title.group(1).strip())
            chinese_name = "".join(re.findall(r"[\u4e00-\u9fff]+", stem))
            english_name = re.sub(r"[\u4e00-\u9fff]+", "", stem).strip().replace("_", " ")
            canonical = stem if counts[stem.casefold()] == 1 else note.path[:-3]
            entity = ContactEntity(
                canonical, note.path, chinese_name, english_name, sorted(set(names))
            )
            self.entities[canonical] = entity
            for name in {stem, canonical, chinese_name, english_name, *names}:
                if len(name) >= 2:
                    spellings.add(name)
                    self.alias_candidates[name.casefold()].add(canonical)
        for name in spellings:
            owners = self.alias_candidates[name.casefold()]
            if len(owners) == 1:
                self.alias_to_canonical[name] = next(iter(owners))
        unique = sorted(spellings, key=lambda x: (-len(x), x))
        patterns = []
        for name in unique:
            escaped = re.escape(name)
            patterns.append(
                r"(?<![a-zA-Z0-9_])" + escaped + r"(?![a-zA-Z0-9_])" if name.isascii() else escaped
            )
        if patterns:
            self.combined_regex = re.compile("|".join(patterns), re.IGNORECASE)

    def match_contact(self, query: str) -> tuple[ContactEntity | None, list[str]]:
        q = query.strip().replace("\\", "/")
        if not q:
            return None, []
        paths = [
            e
            for e in self.entities.values()
            if q.casefold() in {e.relative_path.casefold(), e.relative_path[:-3].casefold()}
        ]
        if len(paths) == 1:
            return paths[0], []
        owners = self.alias_candidates.get(q.casefold())
        if owners is None:
            owners = {
                owner
                for alias, values in self.alias_candidates.items()
                if q.casefold() in alias
                for owner in values
            }
        if len(owners) == 1:
            return self.entities[next(iter(owners))], []
        return None, sorted(owners)

    def link_entities(self, text: str) -> tuple[str, list[str]]:
        if not self.combined_regex:
            return text, []
        protected = re.compile(
            r"(\[\[[^\]]+\]\]|\[[^\]]+\]\([^\)]+\)|https?://\S+|"
            r"\[\d{4}-\d{2}-\d{2}[^\]]*\]|#[a-zA-Z0-9_\u4e00-\u9fff/-]+)"
        )
        contacts: set[str] = set()

        def replace(match: re.Match[str]) -> str:
            alias = match.group(0)
            owners = self.alias_candidates[alias.casefold()]
            if len(owners) != 1:
                return alias
            canonical = next(iter(owners))
            contacts.add(canonical)
            return f"[[{canonical}]]" if alias == canonical else f"[[{canonical}|{alias}]]"

        pieces = []
        previous = 0
        for match in protected.finditer(text):
            pieces.extend(
                [self.combined_regex.sub(replace, text[previous : match.start()]), match.group()]
            )
            previous = match.end()
        pieces.append(self.combined_regex.sub(replace, text[previous:]))
        return "".join(pieces), sorted(contacts)


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


def render_triage(
    content: str, resolver: ContactResolver, *, vault_name: str, year: int
) -> tuple[TriageReport, str]:
    """Analyze a snapshot and return a proposal; never mutate a source."""
    lines = content.splitlines(keepends=True)
    classifier = TagClassifier()
    diffs: list[TriageDiff] = []
    output: list[str] = []
    contacts: set[str] = set()
    tags: set[str] = set()
    todo_count = 0
    fenced = False
    for number, line in enumerate(lines, 1):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        if fenced or not line.lstrip().startswith("- [ ]"):
            output.append(line)
            continue
        todo_count += 1
        original = line.rstrip("\r\n")
        linked, people = resolver.link_entities(original)
        triaged, applied_tags = classifier.classify_and_tag(linked)
        if triaged != original:
            diffs.append(TriageDiff(number, original, triaged, people, applied_tags))
            contacts.update(people)
            tags.update(applied_tags)
        output.append(triaged + line[len(original) :])
    report = TriageReport(
        vault=vault_name,
        target_file=f"000_Inbox/todo.{year}.md",
        year=year,
        committed=False,
        total_lines=len(lines),
        todo_count=todo_count,
        triaged_count=len(diffs),
        diffs=diffs,
        contacts_matched=sorted(contacts),
        tags_applied=sorted(tags),
    )
    return report, "".join(output)


def triage_todo_file(
    vault_root: Path, *, vault_name: str = "", year: int | None = None
) -> TriageReport:
    """Read-only compatibility analysis. Commits belong to memory_service.triage_todos."""
    resolver = ContactResolver(vault_root)
    current_year = year or datetime.now().year
    note = resolver.notes.get(f"000_Inbox/todo.{current_year}.md")
    return render_triage(
        note.content if note else "",
        resolver,
        vault_name=vault_name or vault_root.name,
        year=current_year,
    )[0]


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


def _mentions_contact(resolver: ContactResolver, entity: ContactEntity, text: str) -> bool:
    for target in re.findall(r"\[\[([^\]|]+)(?:\|[^\]]*)?\]\]", text):
        target = target.split("#", 1)[0].removesuffix(".md")
        if target.casefold() == entity.relative_path[:-3].casefold():
            return True
        owners = resolver.alias_candidates.get(target.casefold(), set())
        if len(owners) == 1 and entity.canonical in owners:
            return True
    return entity.canonical in resolver.link_entities(text)[1]


def get_contact_radar(vault_root: Path, year: int | None = None) -> dict[str, Any]:
    """Analyze all contacts in 305_Contact/ against 000_Inbox/todo.YYYY.md to produce a contact radar."""
    vault_root = vault_root.resolve()
    resolver = ContactResolver(vault_root)
    cur_year = year or datetime.now().year
    now = datetime.now()

    # Load open tasks from todo.YYYY.md
    todo_note = resolver.notes.get(f"000_Inbox/todo.{cur_year}.md")
    open_tasks: list[str] = []
    if todo_note:
        for line in todo_note.content.splitlines():
            if re.match(r"^\s*-\s*\[ \]", line):
                open_tasks.append(line.strip())

    items = []
    for canon, entity in sorted(resolver.entities.items()):
        c_content = resolver.notes[entity.relative_path].content

        matched_tasks = [task for task in open_tasks if _mentions_contact(resolver, entity, task)]

        # Find interaction dates from ## Log or text
        log_dates = re.findall(r"\[(\d{4}-\d{2}-\d{2})\]|\b(\d{4}-\d{2}-\d{2})\b", c_content)
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


def get_contact_detail(vault_root: Path, query: str, year: int | None = None) -> dict[str, Any]:
    """Retrieve full detail card for a contact including info, pending tasks, recent logs, and projects."""
    vault_root = vault_root.resolve()
    resolver = ContactResolver(vault_root)
    entity, candidates = resolver.match_contact(query)
    if not entity:
        return {
            "status": "ambiguous" if candidates else "not_found",
            "query": query,
            "candidates": candidates,
        }

    cur_year = year or datetime.now().year
    content = resolver.notes[entity.relative_path].content

    # Parse metadata fields
    fields: dict[str, str] = {}
    for line in content.splitlines():
        m = re.match(r"^([a-zA-Z\s]+)::\s*(.*)", line)
        if m:
            fields[m.group(1).strip().lower()] = m.group(2).strip()

    # Find pending tasks
    todo_note = resolver.notes.get(f"000_Inbox/todo.{cur_year}.md")
    matched_tasks: list[str] = []
    if todo_note:
        for line in todo_note.content.splitlines():
            if re.match(r"^\s*-\s*\[ \]", line) and _mentions_contact(resolver, entity, line):
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

    # Project evidence comes from the same verified snapshot.
    associated_projects = sorted(
        {
            note.path.split("/")[1]
            for note in resolver.scan.records
            if note.path.startswith("100_Project/")
            and len(note.path.split("/")) >= 3
            and _mentions_contact(resolver, entity, note.content)
        }
    )

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
