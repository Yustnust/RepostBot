"""纯逻辑单测：不依赖浏览器，覆盖 20 天判定 / 时间解析 / 目标行选择。

运行：python -m unittest tests.test_logic -v
"""

import os
import sys
import types
import unittest
from datetime import datetime, timedelta, timezone

# publisher 顶层会 import playwright；本环境未安装，打桩避免 sys.exit
if "playwright" not in sys.modules:
    _pw = types.ModuleType("playwright")
    _spa = types.ModuleType("playwright.sync_api")
    _spa.sync_playwright = lambda: None
    _pw.sync_api = _spa
    sys.modules["playwright"] = _pw
    sys.modules["playwright.sync_api"] = _spa

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

import src.publisher as publisher  # noqa: E402

CST = timezone(timedelta(hours=8))


class TestParseSortTime(unittest.TestCase):
    def test_full_format(self):
        dt = publisher.parse_sort_time("2026-09-24 09:34")
        self.assertIsNotNone(dt)
        self.assertEqual((dt.year, dt.month, dt.day, dt.hour, dt.minute),
                         (2026, 9, 24, 9, 34))

    def test_date_only_format(self):
        dt = publisher.parse_sort_time("2026-09-03")
        self.assertIsNotNone(dt)
        self.assertEqual((dt.year, dt.month, dt.day), (2026, 9, 3))

    def test_invalid_returns_none(self):
        self.assertIsNone(publisher.parse_sort_time(""))
        self.assertIsNone(publisher.parse_sort_time("不是时间"))
        self.assertIsNone(publisher.parse_sort_time(None))


class TestPickTarget(unittest.TestCase):
    def _row(self, status, sort_text, idx=0):
        return {
            "index": idx, "title": "t", "company": "c",
            "publish_date": "", "sort_time_text": sort_text, "status": status,
        }

    def test_picks_recruiting_with_earliest_sort_time(self):
        rows = [
            self._row("招聘中", "2026-09-24 09:34", 0),
            self._row("招聘中", "2026-09-01 08:22", 1),
            self._row("已结束", "2026-08-01 00:00", 2),
        ]
        target = publisher.pick_target(rows)
        self.assertEqual(target["index"], 1)

    def test_none_when_no_recruiting(self):
        rows = [self._row("已结束", "2026-09-01 08:22")]
        self.assertIsNone(publisher.pick_target(rows))

    def test_fallback_when_sort_time_unparseable(self):
        # 招聘中但时间解析不出：退回候选集合，仍能选出该行
        rows = [self._row("招聘中", "无时间")]
        target = publisher.pick_target(rows)
        self.assertEqual(target["index"], 0)


class TestShouldRepost(unittest.TestCase):
    def test_due_after_interval(self):
        sort_dt = datetime(2026, 9, 1, 9, 0, tzinfo=CST)
        now = datetime(2026, 9, 21, 9, 0, tzinfo=CST)
        self.assertTrue(publisher.should_repost(sort_dt, now, 20))

    def test_not_due_before_interval(self):
        sort_dt = datetime(2026, 9, 1, 9, 0, tzinfo=CST)
        now = datetime(2026, 9, 20, 9, 0, tzinfo=CST)
        self.assertFalse(publisher.should_repost(sort_dt, now, 20))

    def test_boundary_one_minute_short(self):
        # 差 1 分钟满 20 天，不应消耗机会
        sort_dt = datetime(2026, 9, 1, 9, 0, tzinfo=CST)
        now = datetime(2026, 9, 21, 8, 59, tzinfo=CST)
        self.assertFalse(publisher.should_repost(sort_dt, now, 20))


class TestConsumedRecently(unittest.TestCase):
    """防重复消耗：同一 20 天周期里绝不点第二次「确定」"""

    def test_blocked_within_guard_window(self):
        # 10-14 09:05 点过确定，10-16 09:00 只过了 1 个整天 → 仍在保护窗口
        now = datetime(2026, 10, 16, 9, 0, tzinfo=CST)
        blocked, elapsed = publisher.consumed_recently("2026-10-14T09:05:00+08:00", now, 3)
        self.assertTrue(blocked)
        self.assertEqual(elapsed, 1)

    def test_allowed_after_guard_window(self):
        now = datetime(2026, 10, 20, 9, 0, tzinfo=CST)
        blocked, elapsed = publisher.consumed_recently("2026-10-14T09:05:00+08:00", now, 3)
        self.assertFalse(blocked)
        self.assertEqual(elapsed, 5)

    def test_guard_boundary_exactly_guard_days(self):
        # 正好满 guard_days：解除保护（与 20 天判定口径一致，差一分钟都不行）
        now = datetime(2026, 10, 17, 9, 6, tzinfo=CST)
        blocked, elapsed = publisher.consumed_recently("2026-10-14T09:05:00+08:00", now, 3)
        self.assertFalse(blocked)
        self.assertEqual(elapsed, 3)

    def test_no_record_never_blocks(self):
        for raw in (None, "", "不是时间"):
            blocked, elapsed = publisher.consumed_recently(raw)
            self.assertFalse(blocked)
            self.assertEqual(elapsed, -1)

    def test_naive_iso_treated_as_cst(self):
        now = datetime(2026, 10, 15, 9, 0, tzinfo=CST)
        blocked, _ = publisher.consumed_recently("2026-10-14T09:05:00", now, 3)
        self.assertTrue(blocked)


class _FakeRequest:
    method = "POST"
    url = "https://recruitment.lawyers.org.cn/manager/updateSortTime.jsp"
    post_data = "id=123"


class _FakeResponse:
    request = _FakeRequest()
    status = 200
    headers = {"content-type": "application/json"}

    def text(self):
        return '{"success":true}'


class TestBuildTrace(unittest.TestCase):
    """抓包序列化：含响应体，并按位置插入「点击」标记"""

    def test_serializes_and_inserts_marker(self):
        trace = publisher._build_trace([_FakeResponse()], [(0, "click")])
        self.assertEqual(trace[0], {"marker": "click"})
        self.assertEqual(trace[1]["status"], 200)
        self.assertEqual(trace[1]["body"], '{"success":true}')

    def test_body_failure_is_tolerated(self):
        class Boom(_FakeResponse):
            def text(self):
                raise RuntimeError("boom")
        trace = publisher._build_trace([Boom()], [])
        self.assertIsNone(trace[0]["body"])

    def test_trailing_marker_kept(self):
        trace = publisher._build_trace([_FakeResponse()], [(9, "click")])
        self.assertEqual(trace[-1], {"marker": "click"})


class TestParseIsoDt(unittest.TestCase):
    def test_with_offset(self):
        dt = publisher.parse_iso_dt("2026-10-14T09:05:00+08:00")
        self.assertEqual(dt.hour, 9)

    def test_invalid(self):
        self.assertIsNone(publisher.parse_iso_dt(""))
        self.assertIsNone(publisher.parse_iso_dt(None))
        self.assertIsNone(publisher.parse_iso_dt("2026/10/14 09:05"))


if __name__ == "__main__":
    unittest.main()
