from datetime import datetime
from pathlib import Path
import sys
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from todo_time import normalize_todo_time, SHANGHAI

REF = datetime(2026, 10, 8, 0, 30, tzinfo=SHANGHAI)

@pytest.mark.parametrize("raw,start,end", [
    ("2026年10月14日，北京时间下午15：00 跟 PTTEP & SpotSeismic 线上会议", "2026-10-14T15:00:00+08:00", "2026-10-14T15:45:00+08:00"),
    ("明天下午三点会议", "2026-10-09T15:00:00+08:00", "2026-10-09T15:45:00+08:00"),
    ("下周二14:30会议", "2026-10-13T14:30:00+08:00", "2026-10-13T15:15:00+08:00"),
    ("2026-10-14T15:00:00Z meeting", "2026-10-14T15:00:00+00:00", "2026-10-14T15:45:00+00:00"),
    ("2026-10-14T15:00:00-04:00 meeting", "2026-10-14T15:00:00-04:00", "2026-10-14T15:45:00-04:00"),
    ("2026-10-14 15:00-16:30会议", "2026-10-14T15:00:00+08:00", "2026-10-14T16:30:00+08:00"),
    ("2026-10-14下午3点到下午4点会议", "2026-10-14T15:00:00+08:00", "2026-10-14T16:00:00+08:00"),
    ("2026-10-14下午3点到4点会议", "2026-10-14T15:00:00+08:00", "2026-10-14T16:00:00+08:00"),
    ("2026-10-14 23:30-00:15会议", "2026-10-14T23:30:00+08:00", "2026-10-15T00:15:00+08:00"),
    ("2026-10-14 15:00持续60分钟会议", "2026-10-14T15:00:00+08:00", "2026-10-14T16:00:00+08:00"),
    ("2026-10-14 15:00时长1小时30分钟会议", "2026-10-14T15:00:00+08:00", "2026-10-14T16:30:00+08:00"),
    ("2026-10-14 23:30 到 2026-10-15 00:15会议", "2026-10-14T23:30:00+08:00", "2026-10-15T00:15:00+08:00"),
])
def test_explicit_and_relative_clocks(raw, start, end):
    result = normalize_todo_time(raw, REF)
    assert result["status"] == "timed"
    assert result["start"] == start
    assert result["end"] == end

@pytest.mark.parametrize("raw", ["今天25:00会议", "2026-02-30 15:00会议", "明天15:99会议", "今天15:00:会议", "今天下午0点会议", "2026-10-14 15:00和2026-10-15 16:00会议", "明天15:00-14:00会议", "明天15:00持续0小时会议", "明天晚上1点会议", "周二下午3点会议", "15:00会议", "10月14日15:00会议", "2026-10-14纽约15:00会议"])
def test_ambiguity_and_malformed_times_never_guess(raw):
    result = normalize_todo_time(raw, REF)
    assert result["status"] == "unresolved"
    assert result["start"] is result["end"] is None
    assert result["warning"]

def test_date_only_does_not_invent_a_clock():
    assert normalize_todo_time("明天交报告", REF)["start"] == "2026-10-09"
    assert normalize_todo_time("明天交报告", REF)["end"] is None
    assert normalize_todo_time("买牛奶 #shopping", REF)["status"] == "none"

@pytest.mark.parametrize("raw", ["明天15:00-16:001会议", "明天15:00-16:00:会议", "明天15:00持续1小时再时长2小时会议"])
def test_invalid_trailing_clock_and_competing_durations(raw):
    assert normalize_todo_time(raw, REF)["status"] == "unresolved"

@pytest.mark.parametrize("raw", ["2026-10-14 9am 客户会议", "2026年10月14日15时 客户会议", "2026-10-14 15.00 客户会议", "15:00会议2026-10-14", "15:00会议2026-10-14 16:00", "2026-10-14 15:00:59.5会议", "2026-10-14 15:00-16:00:59.5会议"])
def test_unsupported_or_misplaced_clocks_are_not_all_day(raw):
    result = normalize_todo_time(raw, REF)
    assert result["status"] == "unresolved"
    assert result["start"] is result["end"] is None

@pytest.mark.parametrize("raw", [
    "明天15:00-16:00 持续30分钟 会议",
    "明天15:00-16:00 时长2小时 会议",
    "明天15:00 客户会议 9pm",
    "明天15:00-16:00 客户会议 9PM",
    "明天15:00持续60分钟 客户会议 9pm",
    "2026-10-14 15:00-16:00 9pm会议",
    "2026-10-14 15:00-16:00 9PM客户",
    "2026-10-14 15:00持续60分钟 9pm会议",
    "2026-10-14 15:00 9pm会议",
    "2026-10-14 15:00pm会议",
    "2026-10-14 9am会议",
    "2026-10-14 15:00-16:30-17:00 会议",
    "2026-10-14T15:00+08:99 会议",
    "2026-10-14T15:00+24:00 会议",
    "2026-10-14T15:00+08:00foo 会议",
    "2026-10-14T15:00+08:00:30 会议",
    "2026-10-14T15:00Zfoo 会议",
    "2026-10-14T15:00Z-16:00Zfoo 会议",
    "2026-10-14T15:00Z-16:00+08:99 会议",
    "明天15:00持续0.001分钟 会议",
    "明天15:00持续0.0001小时 会议",
    "明天15:00持续1.000001分钟 会议",
    "明天15:00持续1.00000000000000000000000000001分钟 会议",
])
def test_conflicts_invalid_offset_tokens_and_subsecond_durations(raw):
    result = normalize_todo_time(raw, REF)
    assert result["status"] == "unresolved"
    assert result["start"] is result["end"] is None
    assert not result["default_duration"]

@pytest.mark.parametrize("raw,start,end", [
    ("2026-10-14T15:00+08:00 到16:00+08:00会议", "2026-10-14T15:00:00+08:00", "2026-10-14T16:00:00+08:00"),
    ("2026-10-14T15:00-04:00 到16:00-04:00会议", "2026-10-14T15:00:00-04:00", "2026-10-14T16:00:00-04:00"),
    ("2026-10-14T15:00Z 到16:00Z会议", "2026-10-14T15:00:00+00:00", "2026-10-14T16:00:00+00:00"),
    ("2026-10-14 15:00持续0.1分钟会议", "2026-10-14T15:00:00+08:00", "2026-10-14T15:00:06+08:00"),
    ("2026-10-14 15:00持续0.01小时会议", "2026-10-14T15:00:00+08:00", "2026-10-14T15:00:36+08:00"),
])
def test_valid_offsets_and_whole_second_duration_precision(raw, start, end):
    result = normalize_todo_time(raw, REF)
    assert result["status"] == "timed"
    assert result["start"] == start
    assert result["end"] == end
    assert not result["default_duration"]
