"""状态记录单测：覆盖成功/失败计数、连续失败自动停用、历史截断。

运行：python -m unittest tests.test_storage -v
"""

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

from src import storage  # noqa: E402


class TestRecordRun(unittest.TestCase):
    def _base_state(self):
        return {"version": 1, "updated_at": None, "accounts": {}}

    def test_success_updates_published_at_and_resets_fail(self):
        state = self._base_state()
        result = {"status": "success", "sort_time_after": "2026-09-27 09:00",
                  "message": "ok"}
        storage.record_run(state, "firm_a", result)
        acc = state["accounts"]["firm_a"]
        self.assertEqual(acc["last_status"], "success")
        self.assertEqual(acc["last_published_at"], "2026-09-27 09:00")
        self.assertEqual(acc["fail_count"], 0)
        self.assertEqual(len(acc["history"]), 1)

    def test_failure_increments_fail_count(self):
        state = self._base_state()
        for _ in range(3):
            storage.record_run(state, "firm_a", {"status": "failed", "message": "x"})
        acc = state["accounts"]["firm_a"]
        self.assertEqual(acc["fail_count"], 3)
        self.assertTrue(acc.get("auto_disabled"))

    def test_fail_count_resets_on_success(self):
        state = self._base_state()
        storage.record_run(state, "firm_a", {"status": "failed", "message": "x"})
        storage.record_run(state, "firm_a", {"status": "failed", "message": "x"})
        storage.record_run(state, "firm_a",
                          {"status": "success", "sort_time_after": "2026-09-27 09:00",
                           "message": "ok"})
        self.assertEqual(state["accounts"]["firm_a"]["fail_count"], 0)
        self.assertNotIn("auto_disabled", state["accounts"]["firm_a"])

    def test_history_capped_at_20(self):
        state = self._base_state()
        for i in range(25):
            storage.record_run(state, "firm_b",
                              {"status": "success", "sort_time_after": "2026-09-27 09:00",
                               "message": str(i)})
        self.assertLessEqual(len(state["accounts"]["firm_b"]["history"]), 20)


if __name__ == "__main__":
    unittest.main()
