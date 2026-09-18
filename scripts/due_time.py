"""Conservative Chinese relative due-time resolution with explicit timezones."""

from __future__ import annotations

import re
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def _number(text: str) -> int:
    if text.isdecimal():
        return int(text)
    digits = {c: i for i, c in enumerate("零一二三四五六七八九")}
    digits["两"] = 2
    if "十" in text:
        left, right = text.split("十")
        return (digits[left] if left else 1) * 10 + (digits[right] if right else 0)
    return digits[text]


def resolve_due_time(
    expression: str, *, timezone: str | None = None, reference: datetime | None = None
) -> dict[str, str]:
    reference = reference or datetime.now(UTC)
    if reference.tzinfo is None or reference.utcoffset() is None:
        raise ValueError("Reference time must include a timezone")
    raw = expression.strip()
    try:
        explicit = datetime.fromisoformat(raw)
    except ValueError:
        explicit = None
    if explicit and explicit.tzinfo is not None and explicit.utcoffset() is not None:
        return {
            "input": expression,
            "due_at": explicit.astimezone(UTC).isoformat(),
            "local_time": explicit.isoformat(),
            "timezone": "explicit_offset",
            "reference": reference.isoformat(),
        }
    if not timezone:
        raise ValueError(
            "Relative/local due times require --timezone, such as Asia/Shanghai"
        )
    try:
        zone = ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(
            "Unknown/unavailable timezone; supply an installed IANA zone or explicit ISO offset"
        ) from exc
    local_now = reference.astimezone(zone)
    if explicit and re.search(r"[T ]\d{2}:\d{2}", raw):
        naive = explicit
    else:
        match = re.fullmatch(
            r"(今天|明天|后天|下周[一二三四五六日天]|\d{4}-\d{2}-\d{2})\s*"
            r"(上午|下午|晚上|中午)?\s*([0-9零一二三四五六七八九十两]{1,3})"
            r"(?::([0-9]{2})|点(半)?([0-9零一二三四五六七八九十两]{1,3})?分?)",
            raw,
        )
        if not match:
            raise ValueError(
                "Specify a supported date and clock time, e.g. 明天上午9点 or 下周二14:30; date-only input is ambiguous"
            )
        day, period, hour, minute, half, chinese_minute = match.groups()
        try:
            hour_value = _number(hour)
            minute_value = 30 if half else _number(minute or chinese_minute or "0")
        except (KeyError, ValueError) as exc:
            raise ValueError("Invalid clock time") from exc
        if half and chinese_minute:
            raise ValueError("Use either half an hour or explicit minutes")
        if period:
            if not 1 <= hour_value <= 12 or (
                period in {"上午", "晚上"} and hour_value == 12
            ):
                raise ValueError("Ambiguous 12-hour time; use explicit 24-hour time")
            if period in {"下午", "晚上"} and hour_value < 12:
                hour_value += 12
            if period == "中午" and hour_value != 12:
                raise ValueError("For 中午 use 12点 or specify 24-hour time")
        today = local_now.date()
        if day in {"今天", "明天", "后天"}:
            date = today + timedelta(days={"今天": 0, "明天": 1, "后天": 2}[day])
        elif day.startswith("下周"):
            weekday = "一二三四五六日".index(day[-1].replace("天", "日"))
            date = today + timedelta(days=7 - today.weekday() + weekday)
        else:
            date = datetime.strptime(day, "%Y-%m-%d").date()
        naive = datetime.combine(date, time(hour_value, minute_value))
    candidates = {}
    for fold in (0, 1):
        localized = naive.replace(tzinfo=zone, fold=fold)
        utc = localized.astimezone(UTC)
        if utc.astimezone(zone).replace(tzinfo=None) == naive:
            candidates[utc.isoformat()] = localized
    if len(candidates) != 1:
        raise ValueError(
            "Clock time is nonexistent or ambiguous due to timezone transition; use an explicit ISO offset"
        )
    due_at, local = next(iter(candidates.items()))
    return {
        "input": expression,
        "due_at": due_at,
        "local_time": local.isoformat(),
        "timezone": timezone,
        "reference": reference.isoformat(),
    }
