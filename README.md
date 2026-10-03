# RepostBot

> ⚠️ **v1 范围：本仓库当前仅服务「上海臻至律师事务所」一家客户（`firm_a`）。** 多账号能力已通过 `config/accounts.json` 的数组结构预留，但暂不实现。

东方律师网招聘信息「20 天自动重发置顶」机器人。

## 它做什么

东方律师网的招聘信息按「上一次发布时间」排序，**距上次发布满 20 天后重新发布才会置顶**，而修改内容不影响排序。

因此本机器人只做一件事：**每隔满 20 天，自动登录律所 OA，点一次「重新发布」，把招聘信息顶到前面。**

它**不**修改招聘内容——内容由客户自己在 OA 里改，机器人只在第 20 天把最新内容一并推上去。

支持多家律所（多账号），各自独立计时；完成后通过邮件 / 短信回执。

> 完整设计见 [`项目方案.md`](项目方案.md)。阶段进度以 [`docs/进度对账.md`](docs/进度对账.md) 为准（已统一 README 与方案里不一致的阶段表）。开始动手前请先读它的 §0 一页速览。

## 快速上手（本地）

```
# 1. 安装依赖
pip install -r requirements.txt

# 2. 安装浏览器内核
playwright install chromium

# 3. 配置环境变量（复制样例并填写）
Copy-Item .env.example .env
# 至少先填 OA_USER / OA_PASS

# 4. 阶段1：勘察页面结构（headful，会真的打开浏览器）
python tools/recon.py
```

勘察产物输出在 `recon_output/<时间戳>/`，含页面 HTML、截图、表单元素清单、表格清单。 把结果填进 `docs/页面结构.md` 的勘察清单，再据此填充 `src/selectors.py`。

## 目录结构

```
├── 项目方案.md              # 完整实施方案（唯一事实来源）
├── README.md                # 本文件
├── requirements.txt         # 本地依赖
├── Dockerfile               # 云端容器镜像（阿里云 FC）
├── config/
│   ├── accounts.example.json  # 账号配置样例
│   └── state.example.json     # 运行状态样例
├── src/                     # 主程序
│   ├── index.py             # 入口（CLI + FC handler）
│   ├── publisher.py         # 核心发布流程
│   ├── page_selectors.py    # 所有页面选择器（页面改版只改这里）
│   ├── storage.py           # 存储：本地文件 / 阿里云 OSS
│   ├── logger.py            # 统一日志（控制台 + logs/repostbot.log）
│   └── notifier/            # 邮件 + 短信回执
├── tools/
│   ├── recon.py             # 阶段1 勘察工具
│   ├── init_oss.py          # 一键上传/回读 OSS 三件套
│   ├── diag_login.py        # 登录链路诊断（登录失败时必用）
│   └── probe_api.py         # 接口探针：从页面 JS 找真实接口（只读，不点按钮）
├── tests/                   # 纯逻辑单测（python -m unittest discover -s tests）
├── .github/workflows/       # push main → 构建镜像 → 部署 FC
└── docs/
    ├── 页面结构.md           # 页面结构、选择器 + §6 真实接口（HTTP 直调依据）
    ├── 首次发布清单.md        # ★ 2026-10-14 首次真实发布的操作清单
    ├── 阶段1-2总结.md        # 已完成工作的总结与踩坑记录
    ├── 部署手册.md           # 阿里云 FC 部署步骤
    └── 进度对账.md           # 阶段进度权威状态表（README 与方案冲突以它为准）
```

## 安全约定

- 密码、SMTP 授权码、AccessKey **只**通过环境变量传入，不写进代码和配置文件。
- `config/accounts.json`、`config/state.json`、`.env`、`.auth/`、`recon_output/` 均已被 `.gitignore` 排除。
- 提交前用 `git status` 确认没有敏感文件被跟踪。

## 实施阶段（统一口径，详见 [docs/进度对账.md](docs/进度对账.md)）

| 阶段 | 内容 | 状态 |
|---|---|---|
| 0 仓库基建 | 目录 / .gitignore / 样例 / 依赖 / README | ✅ 完成 |
| 1 本地勘察页面结构 | `page_selectors.py` + `页面结构.md` | ✅ 完成 |
| 2 单账号跑通（dry-run） | `publisher.py` 全链路 | ✅ 完成（真实发布待 2026-10-14 后验证） |
| 3 状态存储 | `storage.py`（本地 + OSS、状态记录、失败自动停用） | ✅ 完成 |
| 3 多账号调度 | 限流 / 排序 / 串行（单客户暂不需要） | ⏸ 暂缓 |
| 4 邮件 / 短信回执 | `notifier/` | ✅ 完成（代码已写） |
| 5 容器化 + FC 上线 | `Dockerfile` / `deploy.yml` / 部署手册 | ⚠️ 半个（FC 运行需配 AK） |
| 6 试运行观察 | 首个可实测日期 2026-10-14 | ⬜ |

> **v1 仅服务「上海臻至律师事务所」一家客户。** 多账号能力已通过 `accounts.json` 结构预留，暂不实现。

## 常用命令

```
# 常规运行（默认 dry-run，不会真提交）
python src/index.py --firm firm_a --browser msedge

# 强制走完点击链路（仍取消弹窗，用于验证链路）
python src/index.py --firm firm_a --browser msedge --force

# 真发布（务必确认已满 20 天）
python src/index.py --firm firm_a --browser msedge --live

# 登录态失效时重新生成
python tools/recon.py --firm firm_a --browser msedge

# 配置体检（不启浏览器、不产生真实动作，发布前先跑）
python src/index.py --doctor

# 验证邮件回执通道（默认发给 ADMIN_EMAIL，可用 --to 指定）
python src/index.py --test-email
python src/index.py --test-email --to someone@example.com

# 验证短信通道（默认发给 ADMIN_PHONE）
python src/index.py --test-sms
python src/index.py --test-sms --to 13800000000

# 登录失败排障：控制台 reasons + logs/diag_<时间戳>/ 下的截图与 HTML
python tools/diag_login.py

# 登录入口巡检：空 cookie 逐个打开常见入口，看各入口最终落在哪个登录页
python tools/diag_login.py --entries
python tools/diag_login.py --url https://www.lawyers.org.cn/openid/login.jsp

# 接口探针：读页面 JS，找「更新排序时间」背后的接口（只读，绝不点击按钮）
python tools/probe_api.py
python tools/probe_api.py --url https://passport3.lawyers.org.cn/login.jsp
```

> 排障时可强制本机存储：`$env:STORAGE_BACKEND="local"`
> （注意 PowerShell 里 `$env:X=""` 是**删除**变量，不是设为空。）

> `--browser msedge` 是必需的：Playwright 自带 Chromium 打不开该站点。

## 运行日志

每次运行会同时输出到控制台和 `logs/repostbot.log`（`logs/` 已被 .gitignore 排除，不会误提交）。
日志含每账号的「距上次置顶 N 天」判断、点击/弹窗动作、成功/失败结论，便于回溯与排查。
