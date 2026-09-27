"""命令行入口（阶段2：单账号跑通）。

用法：
    python src/index.py --firm firm_a                 # 默认 dry-run，不真提交
    python src/index.py --firm firm_a --live          # 真正执行（务必确认已满 20 天）
    python src/index.py --firm firm_a --headful       # 显示浏览器
    python src/index.py --firm firm_a --browser msedge
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import publisher  # noqa: E402


def main() -> int:
    try:
        from dotenv import load_dotenv
        load_dotenv(config.REPO_ROOT / ".env")
    except ImportError:
        pass

    parser = argparse.ArgumentParser(description="东方律师网招聘置顶机器人")
    parser.add_argument("--firm", default="default", help="律所标识，对应 .auth/<firm>.json")
    parser.add_argument("--live", action="store_true", help="真正执行置顶（默认 dry-run）")
    parser.add_argument("--force", action="store_true",
                        help="测试用：跳过 20 天判断，继续点击按钮以验证链路（仍受 dry-run 保护）")
    parser.add_argument("--headful", action="store_true", help="显示浏览器窗口")
    parser.add_argument("--browser", default="chromium", choices=["chromium", "msedge", "chrome"])
    parser.add_argument("--user", default="", help="OA 账号，缺省读环境变量 OA_USER")
    parser.add_argument("--pass", dest="pwd", default="", help="OA 密码，缺省读环境变量 OA_PASS")
    args = parser.parse_args()

    dry_run = not args.live
    if dry_run and not config.env_bool("DRY_RUN", True):
        dry_run = False

    result = publisher.run(
        args.firm,
        user=args.user or config.env("OA_USER"),
        pwd=args.pwd or config.env("OA_PASS"),
        storage=config.storage_path(args.firm),
        dry_run=dry_run,
        headless=not args.headful,
        browser=args.browser,
        ignore_interval=args.force,
    )

    print("\n" + "=" * 60)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print("=" * 60)

    # 注意：Windows 控制台可能是 GBK，不要用 emoji，否则会 UnicodeEncodeError
    if result["status"] == "success":
        print("[成功] 置顶成功")
    elif result["status"] == "skipped":
        print("[跳过] 未到期或 dry-run")
    else:
        print("[失败] 请看 message 字段")
    return 0 if result["status"] in ("success", "skipped") else 1


if __name__ == "__main__":
    sys.exit(main())
