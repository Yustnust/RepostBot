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


if __name__ == "__main__":
    unittest.main()
