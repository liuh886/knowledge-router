#!/usr/bin/env python3
"""Taxonomy Scanner: Universal Obsidian Vault Archetype & Entity Induction Engine.

Scans any directory/vault without external dependencies and infers:
- Archetype: JohnnyDecimal, PARA, Zettelkasten, DailyChronological, or FlatUnstructured.
- Key locations: Inbox, Projects, Areas, Resources, Archives, Logs/Daily, Templates.
- Active entities: List of active projects, areas, resource categories, and high-frequency tags.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from collections.abc import Iterable
from typing import Any

from secure_io import is_reparse, secure_read_bytes

_SKIP_DIR_NAMES = frozenset(
    {
        ".obsidian",
        ".git",
        ".trash",
        ".makemd",
        ".pandoc",
        ".infio_json_db",
        "node_modules",
        "__pycache__",
        ".knowledge_router",
    }
)


@dataclass
class VaultFolder:
    name: str
    relative_path: str
    category: str  # inbox, project, area, resource, archive, log, template, other
    sub_count: int = 0
    note_count: int = 0


@dataclass
class TaxonomyProfile:
    vault_root: str
    archetype: str  # JohnnyDecimal, PARA, Zettelkasten, DailyChronological, Flat
    inbox_dir: str | None = None
    log_dir: str | None = None
    project_dir: str | None = None
    area_dir: str | None = None
    resource_dir: str | None = None
    archive_dir: str | None = None
    template_dir: str | None = None
    active_projects: list[str] = field(default_factory=list)
    active_areas: list[str] = field(default_factory=list)
    resource_categories: list[str] = field(default_factory=list)
    detected_tags: list[str] = field(default_factory=list)
    folders: list[VaultFolder] = field(default_factory=list)
    total_notes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)


def _is_eligible_dir(path: Path, root: Path) -> bool:
    if path == root:
        return True
    try:
        relative = path.relative_to(root)
    except ValueError:
        return False
    return not any(
        part.startswith(".") or part.casefold() in _SKIP_DIR_NAMES
        for part in relative.parts
    )


def _safe_directory(path: Path, root: Path) -> bool:
    try:
        info = path.lstat()
        path.resolve(strict=True).relative_to(root)
    except (OSError, ValueError):
        return False
    return (
        stat.S_ISDIR(info.st_mode)
        and not stat.S_ISLNK(info.st_mode)
        and not is_reparse(info)
        and _is_eligible_dir(path, root)
    )


def _safe_markdown(path: Path, root: Path) -> bool:
    try:
        info = path.lstat()
        path.resolve(strict=True).relative_to(root)
    except (OSError, ValueError):
        return False
    return (
        path.suffix.casefold() == ".md"
        and stat.S_ISREG(info.st_mode)
        and not stat.S_ISLNK(info.st_mode)
        and not is_reparse(info)
        and _is_eligible_dir(path.parent, root)
    )


def _iter_markdown(root: Path) -> Any:
    for current_text, directory_names, filenames in os.walk(root, followlinks=False):
        current = Path(current_text)
        directory_names[:] = sorted(
            name for name in directory_names if _safe_directory(current / name, root)
        )
        for filename in sorted(filenames):
            path = current / filename
            if _safe_markdown(path, root):
                yield path


def _classify_folder_name(name: str) -> str:
    name_lower = name.casefold()
    if any(k in name_lower for k in ("template", "700_", "tpl")):
        return "template"
    if any(
        k in name_lower
        for k in ("inbox", "000_", "00_inbox", "fleeting", "capture", "00_temp")
    ):
        return "inbox"
    if any(k in name_lower for k in ("project", "100_project", "active")):
        return "project"
    if any(k in name_lower for k in ("area", "200_area", "responsibility")):
        return "area"
    if any(
        k in name_lower
        for k in (
            "resource",
            "300_resource",
            "reference",
            "references",
            "source",
            "literature",
        )
    ):
        return "resource"
    if any(k in name_lower for k in ("archive", "400_archive", "history", "old")):
        return "archive"
    if any(k in name_lower for k in ("log", "daily", "journal", "100_logs", "diary")):
        return "log"
    return "other"


def _record_tags(head: str, tag_counts: dict[str, int]) -> None:
    frontmatter = re.search(r"^tags:\s*\[(.*?)\]", head, re.MULTILINE)
    if frontmatter:
        for tag in re.split(r"[, ]+", frontmatter.group(1)):
            tag = tag.strip().strip("'\"#")
            if tag:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
    for inline_tag in re.findall(r"(?:^|\s)#([a-zA-Z0-9_\u4e00-\u9fa5/-]+)", head):
        tag_counts[inline_tag] = tag_counts.get(inline_tag, 0) + 1


def scan_taxonomy(
    vault_root: Path,
    *,
    source_records: Iterable[Any] | None = None,
) -> TaxonomyProfile:
    """Analyze the directory tree of a vault and construct its TaxonomyProfile."""
    vault_root = vault_root.resolve()
    if not vault_root.is_dir():
        raise FileNotFoundError(f"Vault directory does not exist: {vault_root}")

    folders: list[VaultFolder] = []
    top_level_categories: dict[str, str] = {}
    active_projects: list[str] = []
    active_areas: list[str] = []
    resource_categories: list[str] = []
    tag_counts: dict[str, int] = {}
    total_notes = 0

    # Scan top-level directories first
    for item in sorted(vault_root.iterdir()):
        if not _safe_directory(item, vault_root):
            continue
        rel = item.relative_to(vault_root).as_posix()
        cat = _classify_folder_name(item.name)
        top_level_categories[cat] = rel

        # Count direct child notes and subdirectories
        direct_notes = sum(
            1 for path in item.glob("*.md") if _safe_markdown(path, vault_root)
        )
        direct_subdirs = [
            path for path in item.iterdir() if _safe_directory(path, vault_root)
        ]

        folders.append(
            VaultFolder(
                name=item.name,
                relative_path=rel,
                category=cat,
                sub_count=len(direct_subdirs),
                note_count=direct_notes,
            )
        )

        # Inspect projects
        if cat == "project":
            for sub in sorted(direct_subdirs):
                active_projects.append(sub.name)
        # Inspect areas
        elif cat == "area":
            for sub in sorted(direct_subdirs):
                active_areas.append(sub.name)
        # Inspect resources
        elif cat == "resource":
            for sub in sorted(direct_subdirs):
                resource_categories.append(sub.name)

    # Reuse an already verified source scan when the caller has one. This keeps taxonomy and
    # indexing on the same snapshot and avoids a redundant pass over every note during sync.
    if source_records is not None:
        records = tuple(source_records)
        total_notes = len(records)
        for record in records:
            _record_tags(str(record.content)[:2048], tag_counts)
    else:
        for md_file in _iter_markdown(vault_root):
            total_notes += 1
            try:
                relative = md_file.relative_to(vault_root).as_posix()
                head = secure_read_bytes(vault_root, relative)[:2048].decode(
                    "utf-8", errors="ignore"
                )
                _record_tags(head, tag_counts)
            except OSError:
                pass

    # Archetype detection
    has_johnny = any(re.match(r"^\d{3}_", f.name) for f in folders)
    has_para = (
        "project" in top_level_categories or "area" in top_level_categories
    ) and ("resource" in top_level_categories or "archive" in top_level_categories)
    folder_names = {folder.name.casefold() for folder in folders}
    has_zettelkasten = "permanent" in folder_names and bool(
        folder_names & {"literature", "fleeting"}
    )

    if has_johnny:
        archetype = "JohnnyDecimal"
    elif has_zettelkasten:
        archetype = "Zettelkasten"
    elif has_para:
        archetype = "PARA"
    elif "log" in top_level_categories and len(folders) <= 3:
        archetype = "DailyChronological"
    elif len(folders) <= 1 and total_notes > 0:
        archetype = "Flat"
    else:
        archetype = "PARA" if has_para else "Hybrid"

    # Resolve specific paths
    inbox_path = top_level_categories.get("inbox")
    log_path = top_level_categories.get("log")
    project_path = top_level_categories.get("project")
    area_path = top_level_categories.get("area")
    resource_path = top_level_categories.get("resource")
    archive_path = top_level_categories.get("archive")
    template_path = top_level_categories.get("template")

    sorted_tags = [
        tag for tag, _ in sorted(tag_counts.items(), key=lambda x: -x[1])[:30]
    ]

    return TaxonomyProfile(
        vault_root=vault_root.as_posix(),
        archetype=archetype,
        inbox_dir=inbox_path,
        log_dir=log_path,
        project_dir=project_path,
        area_dir=area_path,
        resource_dir=resource_path,
        archive_dir=archive_path,
        template_dir=template_path,
        active_projects=active_projects,
        active_areas=active_areas,
        resource_categories=resource_categories,
        detected_tags=sorted_tags,
        folders=folders,
        total_notes=total_notes,
    )


def save_taxonomy_cache(profile: TaxonomyProfile, cache_file: Path) -> None:
    root = Path(profile.vault_root).resolve()
    cache_file = cache_file.expanduser().resolve()
    try:
        cache_file.relative_to(root)
    except ValueError:
        pass
    else:
        raise ValueError("Taxonomy cache must stay outside the Vault")
    cache_file.parent.mkdir(parents=True, exist_ok=True)
    with cache_file.open("w", encoding="utf-8") as f:
        json.dump(profile.to_dict(), f, ensure_ascii=False, indent=2)


def load_taxonomy_cache(cache_file: Path) -> TaxonomyProfile | None:
    if not cache_file.is_file():
        return None
    try:
        with cache_file.open("r", encoding="utf-8") as f:
            data = json.load(f)
            folders = [VaultFolder(**item) for item in data.pop("folders", [])]
            return TaxonomyProfile(folders=folders, **data)
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Scan and induce vault taxonomy structure."
    )
    parser.add_argument("vault", help="Path to Obsidian vault root directory")
    parser.add_argument("--json", action="store_true", help="Emit JSON output")
    parser.add_argument("--save", type=str, help="Path to save taxonomy cache JSON")
    args = parser.parse_args()

    vault_path = Path(args.vault)
    profile = scan_taxonomy(vault_path)

    if args.save:
        save_taxonomy_cache(profile, Path(args.save))
        print(f"Taxonomy cache saved to {args.save}")

    if args.json:
        print(json.dumps(profile.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(f"--- Vault Taxonomy: {profile.vault_root} ---")
        print(f"Archetype: {profile.archetype}")
        print(f"Total Notes: {profile.total_notes}")
        print(f"Inbox: {profile.inbox_dir or '[None detected]'}")
        print(
            f"Projects ({len(profile.active_projects)}): {', '.join(profile.active_projects[:10])}"
        )
        print(
            f"Areas ({len(profile.active_areas)}): {', '.join(profile.active_areas[:10])}"
        )
        print(
            f"Resources ({len(profile.resource_categories)}): {', '.join(profile.resource_categories[:10])}"
        )
        print(f"Top Tags: {', '.join(profile.detected_tags[:10])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
