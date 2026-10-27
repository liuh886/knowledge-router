"""Conservative capture-time normalization; never use creation time as event time."""
from __future__ import annotations

import re
from decimal import Decimal, localcontext
from datetime import datetime, timedelta, timezone
from due_time import _number

SHANGHAI = timezone(timedelta(hours=8))
DATE = r"(?:\d{4}年\s*\d{1,2}月\s*\d{1,2}日|\d{4}-\d{2}-\d{2}(?!\d)|今天|明天|后天|下周[一二三四五六日天])"
CLOCK = r"(?:上午|下午|晚上|中午)?\s*(?:\d{1,2}[:：]\d{2}(?::\d{2})?|[0-9零一二三四五六七八九十两]{1,3}点(?:半|[0-9零一二三四五六七八九十两]{1,3}分?)?)"
UNSUPPORTED_CLOCK = r"\d{1,2}\s*(?:am|pm)(?![A-Za-z0-9_])|\d{1,2}时|\d{1,2}[.]\d{2}(?!\d)"


def _offset_token(text: str, *, negative_allowed: bool = True) -> re.Match | None:
    """Validate offset components before Python can normalize e.g. +08:99."""
    if not text.startswith(("Z", "+")) and not (negative_allowed and text.startswith("-")):
        return None
    match = re.match(r"(Z|[+-]\d{2}:\d{2})(?![A-Za-z0-9:.])", text)
    if not match:
        raise ValueError("Malformed or unterminated ISO offset")
    if match[1] != "Z":
        if int(match[1][1:3]) > 23 or int(match[1][4:6]) > 59:
            raise ValueError("Invalid ISO offset components")
    return match


def normalize_todo_time(text: str, reference: datetime) -> dict:
    result = {"status": "none", "start": None, "end": None, "default_duration": False, "warning": None}
    def unresolved():
        return {**result, "status": "unresolved", "warning": "时间未能唯一确认，已保留原文为待办；请提供明确日期、开始和结束时间。"}
    # User-provided machine metadata or Tasks dates are already authoritative.
    if re.search(r"\[(?:start|end)::|[⏳🛫📅]", text):
        return result
    dates = list(re.finditer(DATE, text))
    if not dates:
        return unresolved() if re.search(r"\d{1,2}[:：]\d{2}|\d{1,2}月\d{1,2}日|[今明后]天|[上下本]?周[一二三四五六日天]|\d+点|" + UNSUPPORTED_CLOCK, text, re.I) else result
    if len(dates) > 2:
        return unresolved()
    if re.search(CLOCK + "|" + UNSUPPORTED_CLOCK, text[:dates[0].start()], re.I):
        return unresolved()
    if re.search(r"UTC|GMT|美国|伦敦|东京|纽约|时区|(?<!北京)时间|[A-Za-z]+/[A-Za-z]+", text, re.I):
        # Only explicit ISO offsets and the user's Beijing timezone are supported.
        cleaned = re.sub(r"北京时间|https?://\S+|#[\w/-]+", "", text)
        if re.search(r"UTC|GMT|美国|伦敦|东京|纽约|时区|时间|[A-Za-z]+/[A-Za-z]+", cleaned, re.I):
            return unresolved()
    def parse_date(token):
        token = re.sub(r"(\d{4})年\s*(\d{1,2})月\s*(\d{1,2})日", lambda m: f"{int(m[1]):04d}-{int(m[2]):02d}-{int(m[3]):02d}", token)
        if token in {"今天", "明天", "后天"}:
            return (reference.astimezone(SHANGHAI).date() + timedelta(days={"今天":0,"明天":1,"后天":2}[token])).isoformat()
        if token.startswith("下周"):
            today = reference.astimezone(SHANGHAI).date()
            weekday = "一二三四五六日".index(token[-1].replace("天", "日"))
            return (today + timedelta(days=7-today.weekday()+weekday)).isoformat()
        return datetime.strptime(token, "%Y-%m-%d").date().isoformat()
    def parse_clock(date, token):
        token = token.strip().replace("：", ":")
        period_match = re.match(r"(上午|下午|晚上|中午)\s*(.*)", token)
        period, token = period_match.groups() if period_match else (None, token)
        if "点" in token:
            hour, mins = token.split("点", 1)
            h, m, s = _number(hour), (30 if mins == "半" else _number(mins.rstrip("分")) if mins else 0), 0
        else:
            parts = [int(v) for v in token.split(":")]
            h, m = parts[:2]
            s = parts[2] if len(parts) == 3 else 0
        if period == "上午" and not 0 <= h <= 11:
            raise ValueError("Ambiguous morning clock")
        if period in {"下午", "晚上"}:
            if not 1 <= h <= 23:
                raise ValueError("Invalid afternoon clock")
            if period == "晚上" and h < 6:
                raise ValueError("Ambiguous evening clock")
            if h < 12:
                h += 12
        if period == "中午" and h != 12:
            raise ValueError("Ambiguous noon clock")
        return datetime.fromisoformat(f"{date}T{h:02d}:{m:02d}:{s:02d}").replace(tzinfo=SHANGHAI)
    try:
        date = parse_date(dates[0].group())
        tail = text[dates[0].end():]
        lead = re.match(r"[，,\s]*(?:北京时间\s*)?T?\s*(" + CLOCK + r")", tail)
        if not lead:
            date_removed = text[:dates[0].start()] + tail
            if len(dates) != 1 or re.search(CLOCK + "|" + UNSUPPORTED_CLOCK, date_removed, re.I):
                return unresolved()
            return {**result, "status": "date", "start": date}
        start = parse_clock(date, lead[1])
        remaining = tail[lead.end():]
        if re.match(r"\s*(?:am|pm)(?![A-Za-z0-9_])", remaining, re.I):
            return unresolved()
        if remaining.startswith((":", "：")) or re.match(r"\.\d", remaining) or (remaining and remaining[0].isdigit()):
            return unresolved()
        # 15:00-16:30 is a clock range; ISO T15:00-04:00 has an explicit offset.
        offset = _offset_token(remaining, negative_allowed=tail.lstrip().startswith("T"))
        if offset:
            start = datetime.fromisoformat(start.replace(tzinfo=None).isoformat() + offset[1])
            remaining = remaining[offset.end():]
        end_match = re.match(r"\s*(?:至|到|[-–—~～]|结束(?:时间)?[：:]?)\s*(?:北京时间\s*)?(?:([\d]{4}-\d{2}-\d{2})[ T]*)?(" + CLOCK + r")", remaining)
        if end_match:
            end_clock = end_match[2]
            start_period = re.match(r"(上午|下午|晚上|中午)", lead[1].strip())
            if not end_match[1] and start_period and not re.match(r"(上午|下午|晚上|中午)", end_clock.strip()):
                end_clock = start_period[1] + end_clock.strip()
            end = parse_clock(end_match[1] or date, end_clock)
            rest = remaining[end_match.end():]
            if rest.startswith((":", "：")) or re.match(r"\.\d", rest) or (rest and rest[0].isdigit()):
                return unresolved()
            end_offset = _offset_token(rest, negative_allowed=bool(offset) or bool(end_match[1]) or tail.lstrip().startswith("T"))
            if end_offset:
                end = datetime.fromisoformat(end.replace(tzinfo=None).isoformat()+end_offset[1])
                rest = rest[end_offset.end():]
            elif offset:
                end = end.replace(tzinfo=start.tzinfo)
            if not end_match[1] and not start_period and start.hour >= 18 and end.hour <= 6 and end <= start:
                end += timedelta(days=1)  # unmistakable evening-to-morning overnight range
            if end <= start or re.search(DATE + "|" + CLOCK + "|" + UNSUPPORTED_CLOCK + "|持续|时长|小时|分钟", rest, re.I):
                return unresolved()
            return {**result, "status": "timed", "start": start.isoformat(), "end": end.isoformat()}
        duration = re.search(r"(?:持续|时长)\s*(\d+(?:\.\d+)?)\s*(分钟|小时)(?:\s*(\d+)\s*分钟)?", remaining)
        if duration:
            rest = remaining[:duration.start()] + remaining[duration.end():]
            with localcontext() as context:
                context.prec = max(28, len(duration[1]) + len(duration[3] or "") + 10)
                minutes = Decimal(duration[1]) * (60 if duration[2] == "小时" else 1)
                minutes += int(duration[3] or 0)
                seconds = minutes * 60
            if not 0 < minutes <= 7 * 24 * 60 or seconds != seconds.to_integral_value() or re.search(DATE + "|" + CLOCK + "|" + UNSUPPORTED_CLOCK + "|持续|时长|小时|分钟|结束", rest, re.I):
                return unresolved()
            end = start + timedelta(seconds=int(seconds))
            return {**result, "status": "timed", "start": start.isoformat(), "end": end.isoformat()}
        if re.search(DATE + "|" + CLOCK + "|" + UNSUPPORTED_CLOCK + r"|持续|时长|小时|分钟|结束|(?:至|到)\s*\d|^\s*[-–—~～]", remaining, re.I):
            return unresolved()
        return {**result, "status": "timed", "start": start.isoformat(), "end": (start+timedelta(minutes=45)).isoformat(), "default_duration": True}
    except (ValueError, KeyError, OverflowError):
        return unresolved()
