"""初始化 OSS：把配置、状态、登录态上传到云端。

用法：
    python tools/init_oss.py              # 交互式（会让你确认上次置顶时间）
    python tools/init_oss.py --yes        # 全部用默认值，不询问

前置：在 .env 里填好
    OSS_BUCKET=repostbot
    OSS_REGION=oss-cn-shanghai
    OSS_ENDPOINT=oss-cn-shanghai.aliyuncs.com
    ALIBABA_CLOUD_ACCESS_KEY_ID=...
    ALIBABA_CLOUD_ACCESS_KEY_SECRET=...

注意：state.json 里的 last_published_at 必须正确填写。
      若留空，程序会认为「从未发布」而在第一次运行时立即置顶，白白消耗一次 20 天机会。
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

try:
    from dotenv import load_dotenv
    load_dotenv(REPO_ROOT / ".env")
except ImportError:
    pass

import config  # noqa: E402
import storage as storage_mod  # noqa: E402

CST = timezone(timedelta(hours=8))


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M")


def build_state(firm_id: str, sort_time: str, assume_yes: bool) -> dict:
    dt = None
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(sort_time.strip(), fmt)
            break
        except ValueError:
            continue
    if dt is None:
        sys.exit(f"时间格式不对：{sort_time!r}，应为 2026-09-24 09:34")

    next_due = dt + timedelta(days=20)
    print(f"\n上次置顶（排序）时间：{_fmt(dt)}")
    print(f"=> 下次可置顶时间  ：{next_due:%Y-%m-%d}")
    if not assume_yes:
        ok = input("确认无误？[Y/n] ").strip().lower()
        if ok not in ("", "y", "yes"):
            sys.exit("已取消，请重新运行并填写正确的排序时间")

    return {
        "version": 1,
        "updated_at": None,
        "accounts": {
            firm_id: {
                "last_published_at": dt.replace(tzinfo=CST).isoformat(),
                "source": "manual",
                "last_run_at": None,
                "last_status": None,
                "last_message": "由 init_oss.py 初始化",
                "fail_count": 0,
                "history": [],
            }
        },
    }


def build_accounts(firm_id: str, firm_name: str) -> dict:
    local = REPO_ROOT / "config" / "accounts.json"
    if local.exists():
        print(f"使用本地已有配置：{local}")
        return json.loads(local.read_text(encoding="utf-8"))
    return {
        "version": 1,
        "accounts": [{
            "id": firm_id,
            "name": firm_name or "上海臻至律师事务所",
            "enabled": True,
            "oa_user_ref": f"env:{firm_id.upper()}_OA_USER",
            "oa_pass_ref": f"env:{firm_id.upper()}_OA_PASS",
            "notify": {
                "email": config.env("NOTIFY_EMAIL"),
                "phone": config.env("NOTIFY_PHONE"),
                "email_enabled": bool(config.env("NOTIFY_EMAIL")),
                "sms_enabled": bool(config.env("NOTIFY_PHONE")),
            },
            "remark": "由 init_oss.py 生成",
        }],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="初始化 OSS 上的配置与状态")
    parser.add_argument("--firm", default=config.env("FIRM_ID", "firm_a"))
    parser.add_argument("--name", default=config.env("FIRM_NAME", "上海臻至律师事务所"))
    parser.add_argument("--sort-time", default="2026-09-24 09:34",
                        help="上次置顶（排序）时间，格式 2026-09-24 09:34")
    parser.add_argument("--yes", action="store_true", help="不询问，直接用默认值")
    args = parser.parse_args()

    if not config.env("OSS_BUCKET"):
        sys.exit("未配置 OSS_BUCKET，请先在 .env 中填写")
    if not (config.env("ALIBABA_CLOUD_ACCESS_KEY_ID") and config.env("ALIBABA_CLOUD_ACCESS_KEY_SECRET")):
        sys.exit("未配置 AccessKey，请先在 .env 中填写 ALIBABA_CLOUD_ACCESS_KEY_ID / SECRET")

    store = storage_mod.get_storage()
    if store.kind != "OSSStorage":
        sys.exit(f"当前未启用 OSS 存储（{store.kind}），请检查 .env")
    print(f"存储类型：{store.kind}")

    # 1. accounts.json
    accounts = build_accounts(args.firm, args.name)
    store._put("accounts.json", json.dumps(accounts, ensure_ascii=False, indent=2))
    print("[上传] accounts.json")

    # 2. state.json
    state = build_state(args.firm, args.sort_time, args.yes)
    store.save_state(state)
    print("[上传] state.json")

    # 3. 登录态
    auth_file = REPO_ROOT / ".auth" / f"{args.firm}.json"
    if auth_file.exists():
        store.save_auth(args.firm, auth_file.read_text(encoding="utf-8"))
        print(f"[上传] auth/{args.firm}.json（登录态，可加快执行、降低风控概率）")
    else:
        print(f"[跳过] 未找到 {auth_file}，将依赖自动登录（已实测可用）")

    # 4. 回读校验
    print("\n校验：")
    back = store.load_state()
    acc = back.get("accounts", {}).get(args.firm, {})
    print(f"  state.json  last_published_at = {acc.get('last_published_at')}")
    print(f"  accounts.json 账号数 = {len(store.load_accounts().get('accounts', []))}")
    print("\n完成。OSS 目录结构：")
    prefix = config.env("OSS_KEY_PREFIX", "repostbot/")
    print(f"  {prefix}accounts.json")
    print(f"  {prefix}state.json")
    print(f"  {prefix}auth/{args.firm}.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
