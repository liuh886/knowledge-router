"""Relative dates never guess missing times or ambiguous timezone transitions."""

# ruff: noqa: E402
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from due_time import resolve_due_time

REFERENCE = datetime.fromisoformat("2026-09-08T08:00:00+08:00")


@pytest.mark.parametrize(
    "text, expected",
    [
        ("明天上午9点", "2026-09-09T01:00:00+00:00"),
        ("明天上午九点半", "2026-09-09T01:30:00+00:00"),
        ("下周二14:30", "2026-09-15T06:30:00+00:00"),
        ("后天下午三点十五分", "2026-09-10T07:15:00+00:00"),
        ("2026-09-10 09:00", "2026-09-10T01:00:00+00:00"),
    ],
)
def test_explicit_relative_time(text, expected):
    assert (
        resolve_due_time(text, timezone="Asia/Shanghai", reference=REFERENCE)["due_at"]
        == expected
    )


@pytest.mark.parametrize(
    "text", ["下周二", "明天", "6月7日9点", "周二9点", "明天晚上12点", "明天25:00"]
)
def test_underspecified_or_invalid_input_rejected(text):
    with pytest.raises(ValueError):
        resolve_due_time(text, timezone="Asia/Shanghai", reference=REFERENCE)


def test_timezone_required_unless_explicit_offset():
    with pytest.raises(ValueError, match="timezone"):
        resolve_due_time("明天9点", reference=REFERENCE)
    assert (
        resolve_due_time("2026-09-09T09:00:00+08:00")["due_at"]
        == "2026-09-09T01:00:00+00:00"
    )


@pytest.mark.parametrize("text", ["2026-03-08 02:30", "2026-11-01 01:30"])
def test_dst_gap_and_overlap_require_offset(text):
    with pytest.raises(ValueError, match="transition"):
        resolve_due_time(text, timezone="America/New_York", reference=REFERENCE)


def test_next_week_uses_calendar_week_at_year_boundary():
    result = resolve_due_time(
        "下周二9点",
        timezone="Asia/Shanghai",
        reference=datetime.fromisoformat("2026-12-31T23:00:00+08:00"),
    )
    assert result["local_time"] == "2027-01-05T09:00:00+08:00"
