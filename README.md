# RepostBot

东方律师网招聘信息「20 天自动重发置顶」机器人。

## 它做什么

东方律师网的招聘信息按「上一次发布时间」排序，**距上次发布满 20 天后重新发布才会置顶**，而修改内容不影响排序。

因此本机器人只做一件事：**每隔满 20 天，自动登录律所 OA，点一次「重新发布」，把招聘信息顶到前面。**

它**不**修改招聘内容——内容由客户自己在 OA 里改，机器人只在第 20 天把最新内容一并推上去。

支持多家律所（多账号），各自独立计时；完成后通过邮件 / 短信回执。

> 完整设计见 [`项目方案.md`](./项目方案.md)。开始动手前请先读它的 §0 一页速览。

## 快速上手（本地）

```powershell
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

勘察产物输出在 `recon_output/<时间戳>/`，含页面 HTML、截图、表单元素清单、表格清单。
把结果填进 `docs/页面结构.md` 的勘察清单，再据此填充 `src/selectors.py`。

## 目录结构

```
├── 项目方案.md              # 完整实施方案（唯一事实来源）
├── README.md                # 本文件
├── requirements.txt         # 本地依赖
├── Dockerfile               # 云端容器镜像（阿里云 FC）
├── config/
│   ├── accounts.example.json  # 账号配置样例
│   └── state.example.json     # 运行状态样例
├── src/                     # 主程序（阶段2起填充）
├── tools/
│   └── recon.py             # 阶段1 勘察工具
└── docs/
    └── 页面结构.md           # 勘察清单与页面结构记录
```

## 安全约定

- 密码、SMTP 授权码、AccessKey **只**通过环境变量传入，不写进代码和配置文件。
- `config/accounts.json`、`config/state.json`、`.env`、`.auth/`、`recon_output/` 均已被 `.gitignore` 排除。
- 提交前用 `git status` 确认没有敏感文件被跟踪。

## 实施阶段

| 阶段 | 内容 | 状态 |
|---|---|---|
| 0 | 仓库基建 | ✅ 完成 |
| 1 | 本地勘察页面结构 | ✅ 完成 |
| 2 | 单账号跑通（dry-run） | ✅ 完成（真实发布待 2026-10-14 后验证） |
| 3 | 多账号调度 + 状态存储 | ⬜ 下一步 |
| 4 | 邮件 / 短信回执 | ⬜ |
| 5 | 容器化与 FC 上线 | ⬜ |
| 6 | 试运行观察 | ⬜ |

> **改代码前请先读 [`docs/阶段1-2总结.md`](docs/阶段1-2总结.md)**：那里记着所有踩过的坑和定下的约定。

## 常用命令

```powershell
# 常规运行（默认 dry-run，不会真提交）
python src/index.py --firm firm_a --browser msedge

# 强制走完点击链路（仍取消弹窗，用于验证链路）
python src/index.py --firm firm_a --browser msedge --force

# 真发布（务必确认已满 20 天）
python src/index.py --firm firm_a --browser msedge --live

# 登录态失效时重新生成
python tools/recon.py --firm firm_a --browser msedge
```

> `--browser msedge` 是必需的：Playwright 自带 Chromium 打不开该站点。
