"""CLI adapter for the optional annual-stream/contact memory profile."""

from __future__ import annotations

import argparse
from typing import Callable

from memory_service import (
    append_contact_log,
    append_inbox_stream,
    drain_quick_capture_buffer,
    triage_todos,
)
from registry import VaultConfig, VaultRegistry
from triage_engine import ContactResolver, get_contact_detail, get_contact_radar


def add_memory_commands(sub: argparse._SubParsersAction) -> None:
    triage = sub.add_parser(
        "triage", help="Triage inbox notes (entity linking, multi-dimensional tagging)"
    )
    triage_sub = triage.add_subparsers(dest="triage_command", required=True)
    triage_todo = triage_sub.add_parser("todo", help="Triage inbox todo items")
    triage_todo.add_argument("--vault")
    triage_todo.add_argument(
        "--year", type=int, help="Target year for todo file (defaults to current year)"
    )
    triage_todo.add_argument(
        "--commit",
        action="store_true",
        help="Commit modifications safely to disk (defaults to dry-run preview)",
    )
    triage_todo.add_argument("--json", action="store_true")

    contact = sub.add_parser("contact", help="Manage and append interaction logs to contact notes")
    contact_sub = contact.add_subparsers(dest="contact_command", required=True)
    contact_append = contact_sub.add_parser(
        "append", help="Append interaction log to a contact note in 305_Contact/"
    )
    contact_append.add_argument("target", help="Name, alias, or substring of the contact")
    contact_append.add_argument("text", help="Interaction log text to append")
    contact_append.add_argument("--vault")
    contact_append.add_argument("--json", action="store_true")
    contact_append.add_argument("--preview", action="store_true")
    contact_append.add_argument("--idempotency-key")

    contact_search = contact_sub.add_parser("search", help="Search contact by name or alias")
    contact_search.add_argument("query", help="Contact name or alias query")
    contact_search.add_argument("--vault")
    contact_search.add_argument("--json", action="store_true")

    contact_radar = contact_sub.add_parser(
        "radar", help="View global contact radar and follow-up status"
    )
    contact_radar.add_argument("--vault")
    contact_radar.add_argument("--year", type=int, help="Target year")
    contact_radar.add_argument("--json", action="store_true")

    contact_show = contact_sub.add_parser(
        "show", help="View detailed contact card, open tasks, and associated projects"
    )
    contact_show.add_argument("query", help="Contact name or alias query")
    contact_show.add_argument("--vault")
    contact_show.add_argument("--year", type=int, help="Target year")
    contact_show.add_argument("--json", action="store_true")

    inbox = sub.add_parser("inbox", help="Capture stream notes into 000_Inbox/<type>.<year>.md")
    inbox_sub = inbox.add_subparsers(dest="inbox_command", required=True)
    inbox_append = inbox_sub.add_parser(
        "append", help="Append capture entry to 000_Inbox/<type>.<year>.md"
    )
    inbox_append.add_argument("type", choices=("todo", "memo", "idea"), help="Type of capture")
    inbox_append.add_argument("text", help="Content to record")
    inbox_append.add_argument("--vault")
    inbox_append.add_argument("--year", type=int, help="Target year")
    inbox_append.add_argument("--json", action="store_true")
    inbox_append.add_argument("--preview", action="store_true")
    inbox_append.add_argument("--idempotency-key")
    drain = inbox_sub.add_parser(
        "drain", help="Import legacy Quick Capture entries once; preserve buffer"
    )
    drain.add_argument("--vault")
    drain.add_argument("--year", type=int)
    drain.add_argument("--json", action="store_true")


def run_memory_command(
    args: argparse.Namespace, registry: VaultRegistry, config: VaultConfig, emit: Callable
) -> int:
    if args.command == "triage":
        result = triage_todos(registry, config, year=args.year, commit=args.commit)
        emit(result, json_output=args.json, message=result["message"])
        return 2 if result["status"] == "applied_pending_index" else 0
    if args.command == "contact":
        if args.contact_command == "append":
            res = append_contact_log(
                registry,
                config,
                args.target,
                args.text,
                preview=args.preview,
                idempotency_key=args.idempotency_key,
            )
            emit(res, json_output=args.json, message=res.get("message"))
            return 2 if res["status"] == "applied_pending_index" else 0
        if args.contact_command == "search":
            resolver = ContactResolver(config.root_path)
            entity, candidates = resolver.match_contact(args.query)
            if entity:
                res = {
                    "status": "found",
                    "canonical": entity.canonical,
                    "relative_path": entity.relative_path,
                    "chinese_name": entity.chinese_name,
                    "english_name": entity.english_name,
                    "aliases": entity.aliases,
                }
                emit(
                    res,
                    json_output=args.json,
                    message=f"👤 找到联系人: [[{entity.canonical}]] ({entity.relative_path})\n别名: {', '.join(entity.aliases) if entity.aliases else '无'}",
                )
                return 0
            if candidates:
                res = {
                    "status": "ambiguous",
                    "query": args.query,
                    "candidates": candidates,
                }
                emit(
                    res,
                    json_output=args.json,
                    message=f"❓ 找到多个候选联系人: {', '.join(candidates)}，请指定更精确的名称",
                )
                return 1
            res = {"status": "not_found", "query": args.query}
            emit(
                res,
                json_output=args.json,
                message=f"未在 305_Contact/ 中找到匹配联系人: '{args.query}'",
            )
            return 1
        if args.contact_command == "radar":
            radar = get_contact_radar(config.root_path, year=args.year)
            lines = [
                f"🌐 人脉协同雷达 (共 {radar['total_contacts']} 位联系人，{radar['total_pending_tasks']} 项待办推进中)",
                "-" * 65,
            ]
            for c in radar["contacts"]:
                if c["pending_tasks_count"] > 0 or c["tier"] == "🟢 活跃交流":
                    tasks_str = (
                        f"【{c['pending_tasks_count']} 项待办】"
                        if c["pending_tasks_count"] > 0
                        else ""
                    )
                    comp_str = f"({c['company']})" if c["company"] else ""
                    lines.append(
                        f"{c['tier']} | [[{c['canonical']}]] {comp_str} {tasks_str} 最近: {c['last_contact_date']}"
                    )
            lines.append("-" * 65)
            lines.append(
                f"📊 状态分布: {', '.join(f'{k}: {v}' for k, v in radar['tier_summary'].items())}"
            )
            emit(radar, json_output=args.json, message="\n".join(lines))
            return 0

        if args.contact_command == "show":
            detail = get_contact_detail(config.root_path, args.query, year=args.year)
            if detail["status"] != "success":
                candidates = detail.get("candidates", [])
                if candidates:
                    msg = f"❓ 找到多个候选联系人: {', '.join(candidates)}，请指定更精确的名称"
                else:
                    msg = f"未在 305_Contact/ 中找到匹配联系人: '{args.query}'"
                emit(detail, json_output=args.json, message=msg)
                return 1

            lines = [
                f"👤 联系人画像: [[{detail['canonical']}]]",
                f"📁 笔记路径: {detail['relative_path']}",
            ]
            if detail["aliases"]:
                lines.append(f"🏷️ 别名: {', '.join(detail['aliases'])}")
            if detail["info"].get("company"):
                lines.append(f"🏢 单位/机构: {detail['info']['company']}")
            if detail["associated_projects"]:
                lines.append(f"📂 关联项目: {', '.join(detail['associated_projects'])}")

            lines.append("-" * 50)
            lines.append(f"📌 当前待办 ({len(detail['pending_tasks'])} 项):")
            if detail["pending_tasks"]:
                for t in detail["pending_tasks"]:
                    lines.append(f"  {t}")
            else:
                lines.append("  (暂无未完成待办)")

            lines.append("-" * 50)
            lines.append(f"🪵 最近交互纪要 ({len(detail['recent_logs'])} 条):")
            if detail["recent_logs"]:
                for log in detail["recent_logs"]:
                    lines.append(f"  {log}")
            else:
                lines.append("  (暂无记录)")

            emit(detail, json_output=args.json, message="\n".join(lines))
            return 0

    if args.command == "inbox":
        if args.inbox_command == "drain":
            result = drain_quick_capture_buffer(registry, config, year=args.year)
        else:
            result = append_inbox_stream(
                registry,
                config,
                args.type,
                args.text,
                year=args.year,
                preview=args.preview,
                idempotency_key=args.idempotency_key,
            )
        emit(result, json_output=args.json, message=result.get("message"))
        return 2 if result["status"] == "applied_pending_index" else 0
    raise ValueError(f"Unknown memory command: {args.command}")
