"""调度单测：到期优先排序（多账号放开前的地基）。

运行：python -m unittest tests.test_scheduling -v
"""

import os
import sys
import types
import unittest

# index -> publisher 顶层会 import playwright；本环境未安装，打桩避免 sys.exit
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

from src import index  # noqa: E402


class TestOrderAccounts(unittest.TestCase):
    @staticmethod
    def _acc(firm_id):
        return {"id": firm_id}

    def test_never_published_goes_first(self):
        accounts = [self._acc("a"), self._acc("b"), self._acc("c")]
        state = {"accounts": {
            "a": {"last_published_at": "2026-09-01 09:00"},
            "b": {"last_published_at": "2026-08-01 09:00"},
        }}
        self.assertEqual(
            [a["id"] for a in index.order_accounts(accounts, state)],
            ["c", "b", "a"],  # c 无记录（最久没置顶）→ 排最前
        )

    def test_empty_state_keeps_order(self):
        accounts = [self._acc("a"), self._acc("b")]
        self.assertEqual([a["id"] for a in index.order_accounts(accounts, {})],
                         ["a", "b"])

    def test_missing_account_entry(self):
        accounts = [self._acc("x")]
        self.assertEqual([a["id"] for a in index.order_accounts(accounts, {"accounts": {}})],
                         ["x"])


if __name__ == "__main__":
    unittest.main()
