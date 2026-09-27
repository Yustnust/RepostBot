用阿里云 FC 定时在东方律师网发布招聘信息，技术上完全可行。核心思路是：FC 定时触发器 + Python 脚本模拟登录与表单提交。但由于网站有登录验证和表单保护，直接发 POST 请求通常行不通，下面给出具体方案。

整体架构

```
阿里云 FC 函数 (Python)
        ↓
定时触发器 (CRON: 每20天)
        ↓
Playwright (无头浏览器)
        ↓
东方律师网 (登录 → 填写招聘表单 → 提交)
```

为什么需要 Playwright 而非简单 POST

东方律师网的招聘系统 recruitment.lawyers.org.cn 需要登录才能操作。直接构造 POST 请求需要逆向登录接口和表单的 CSRF token，且网站可能动态生成字段。最稳妥的方式是用 Playwright 模拟真实浏览器操作，自动处理登录状态、验证码（如有）和动态表单。

阿里云 FC 本身支持运行 Playwright，但需要自行打包浏览器依赖。更省事的做法是用 AgentRun Browser Sandbox（基于 FC 构建的云原生浏览器沙箱）。不过为降低复杂度，下面给出自包含的 FC 函数方案。

具体实现步骤

1. 准备依赖

在函数代码目录中，需要安装 Playwright 并下载 Chromium：

```bash
pip install playwright -t ./code
python -m playwright install chromium
```

将生成的 code 目录连同 node_modules 里的 Chromium 一起打包上传到 FC。

2. 函数代码（核心逻辑）

```python
import asyncio
from playwright.async_api import async_playwright

async def publish_job():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context()
        page = await context.new_page()

        # 登录东方律师网
        await page.goto("https://www.lawyers.org.cn/")
        # 根据实际登录页面填写选择器，以下为示意
        await page.fill('input[name="username"]', '你的账号')
        await page.fill('input[name="password"]', '你的密码')
        await page.click('button[type="submit"]')
        await page.wait_for_load_state('networkidle')

        # 进入招聘发布页
        await page.goto("https://recruitment.lawyers.org.cn/")
        # 点击发布招聘或填写表单
        await page.fill('input[name="position"]', '专职律师')
        await page.fill('textarea[name="description"]', '招聘描述...')
        # 选择律所、薪水范围等字段
        await page.click('button:has-text("发布")')

        await browser.close()

def handler(event, context):
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(publish_job())
    finally:
        loop.close()
    return "发布完成"
```

实际选择器需根据东方律师网的真实 DOM 结构调整。建议先用本地浏览器打开招聘发布页面，用开发者工具查看输入框的 name 或 id 属性。

3. 配置定时触发器

在 FC 控制台为函数创建定时触发器，用 CRON 表达式设定频率。由于东方律师网要求招聘更新间隔 20 天，可以设置为：

```
CRON_TZ=Asia/Shanghai 0 0 9 */20 * *
```

这表示北京时间每 20 天的上午 9 点触发一次。注意 FC 的 Cron 默认是 UTC 时间，必须加 CRON_TZ=Asia/Shanghai 指定时区。

关键注意事项

反爬与验证码：如果登录时出现图形验证码，Playwright 需要额外处理（如调用 OCR 或人工介入）。若网站有滑块验证，方案复杂度会显著增加。

账号安全：密码等敏感信息不要硬编码在代码中。建议使用 FC 的环境变量功能，在函数配置中设置 LAWYER_USERNAME 和 LAWYER_PASSWORD，代码中通过 os.environ 读取。

测试策略：务必先在本机用 Playwright 的 headful 模式（headless=False）跑通完整流程，确认登录和表单提交无误后，再改为 headless 部署到 FC。

合规性：确认你的账号具备发布权限，且操作符合东方律师网的使用条款。自动化发布若被网站判定为异常行为，可能导致账号受限。

更省心的替代方案

如果觉得自行打包 Chromium 到 FC 太麻烦，可以关注 AgentRun Browser Sandbox，它原生支持 Playwright 通过 CDP 连接，无需在 FC 函数内安装浏览器。不过该服务目前可能仍处于公测阶段，需确认是否已对你开放。

根据我们的聊天，结合阿里云 FC 的技术特点，以下是邮件、短信、微信服务号三种回执方式的详细实施方案。

---

整体架构

定时触发器 → FC 函数执行发布 → 获取发布结果（链接/状态）→ 并行或串行调用三种通知通道 → 客户收到回执。

方案一：邮件回执（推荐首选）

适用场景：客户需要正式留档、可转发、无需客户做任何配置。

技术可行性：FC 原生支持 Python smtplib，官方文档提供了完整的邮件发送示例。

前置准备

1. 发件邮箱：建议使用阿里云企业邮箱或 163 邮箱。163 邮箱需在设置中开启 SMTP 服务，并获取授权码（非登录密码）。
2. 客户邮箱：提前收集客户的收件邮箱地址。

核心代码

```python
import smtplib
from email.mime.text import MIMEText

def send_success_email(client_email, job_url):
    html = f"""
    <html><body style="font-family:sans-serif;padding:20px">
      <div style="max-width:480px;margin:auto;background:#f0fdf4;
                  border:1px solid #bbf7d0;border-radius:12px;padding:32px;text-align:center">
        <div style="font-size:48px">✅</div>
        <h2 style="color:#166534">招聘信息发布成功</h2>
        <p style="color:#555">您的招聘信息已发布至东方律师网</p >
        <p>点击查看已发布的招聘页面</p >
      </div>
    </body></html>
    """
    msg = MIMEText(html, 'html', 'utf-8')
    msg['Subject'] = '招聘信息发布成功通知'
    msg['From'] = '你的发件邮箱'
    msg['To'] = client_email

    smtp = smtplib.SMTP_SSL('smtp.163.com', 465)  # 端口 465 对应 SMTP_SSL
    smtp.login('你的发件邮箱', '授权码')
    smtp.sendmail('你的发件邮箱', [client_email], msg.as_string())
    smtp.quit()
```

注意事项：FC 中发邮件常见报错是 [Errno 101] Network is unreachable，通常是用了 smtp.connect(host, 25) 而非 SMTP_SSL。务必使用 465 端口 + SMTP_SSL。

方案二：短信回执（实时性最强）

适用场景：客户需要即时知晓，且客户手机号已知。

技术可行性：通过阿里云短信服务 SDK 调用。但需注意硬性门槛：短信服务仅支持企业实名认证账号，个人账号无法申请签名。

前置准备

1. 阿里云账号企业认证：个人认证不支持短信签名申请。
2. 申请短信签名：在短信服务控制台创建，需审核通过。
3. 申请短信模板：例如“您的招聘信息已于${time}发布成功，点击查看：${url}”。模板也需审核。
4. 获取 AccessKey：建议创建 RAM 子账号，授予 AliyunDysmsFullAccess 权限。

核心代码

```python
from alibabacloud_dysmsapi20170525.client import Client
from alibabacloud_dysmsapi20170525 import models as dysms_models
from alibabacloud_tea_openapi import models as open_api_models
import os

def send_sms(client_phone, job_url):
    config = open_api_models.Config(
        access_key_id=os.environ['ALIBABA_CLOUD_ACCESS_KEY_ID'],
        access_key_secret=os.environ['ALIBABA_CLOUD_ACCESS_KEY_SECRET']
    )
    config.endpoint = 'dysmsapi.aliyuncs.com'
    client = Client(config)

    req = dysms_models.SendSmsRequest(
        phone_numbers=client_phone,
        sign_name='你的签名',
        template_code='SMS_xxxxxx',
        template_param=f'{{"url":"{job_url}"}}'
    )
    resp = client.send_sms(req)
    return resp.body.code == 'OK'
```

FC 部署注意：需安装 SDK 依赖：pip install -t . alibabacloud_dysmsapi20170525。

方案三：微信服务号模板消息（客户体验最好）

适用场景：客户已关注你的微信服务号，且你拥有认证服务号。

技术可行性：微信服务号模板消息是官方能力，但门槛最高。

前置准备

1. 认证服务号：订阅号不支持模板消息，必须认证服务号。
2. 申请模板消息功能：在服务号后台“功能 → 添加功能插件”中申请。
3. 选用模板：从公共模板库选用招聘/通知类模板，获取 template_id。
4. 获取客户 openid：需要客户在服务号内产生交互（如关注后发消息、点击菜单）才能获取其 openid，无法凭空获取。

核心代码

```python
import requests

def send_wechat_template(access_token, client_openid, template_id, job_url):
    url = f'https://api.weixin.qq.com/cgi-bin/message/template/send?access_token={access_token}'
    data = {
        "touser": client_openid,
        "template_id": template_id,
        "url": job_url,
        "data": {
            "first": {"value": "招聘信息发布成功"},
            "keyword1": {"value": "东方律师网"},
            "keyword2": {"value": "已发布"},
            "remark": {"value": "点击查看详情"}
        }
    }
    resp = requests.post(url, json=data)
    return resp.json().get('errcode') == 0
```

关键难点：

· Access Token 管理：token 有效期 2 小时，需在 FC 中定时刷新或每次调用前重新获取。
· openid 获取：这是最大的工程障碍。如果客户未关注你的服务号，或你无法通过网页授权获取其 openid，此方案无法落地。

推荐组合策略

通道 客户成本 你方成本 可靠性 推荐度
邮件 零成本 低（一个邮箱即可） 高 ⭐⭐⭐⭐⭐
短信 零成本 中（需企业认证+审核） 高 ⭐⭐⭐⭐
微信服务号 需关注+授权 高（认证+开发） 中 ⭐⭐⭐

我的建议：邮件作为主通道，短信作为可选补充。微信服务号投入产出比最低——除非客户本来就是你的服务号粉丝且有 openid 池，否则不值得为此单独开发。

如果你告诉我客户数量级、是否已有企业认证的阿里云账号、以及客户是否关注了你的服务号，我可以进一步帮你收窄方案。



结合我们之前的聊天，以下是完整的定时发布方案。核心难点已确认：东方律师网要求登录后才能发布，且发布间隔必须≥20天。

前置确认：账号权限

招聘功能仅向上海市律师事务所开放，需用律所账号登录会员中心操作。如果账号无此权限，后续自动化方案无法落地。

整体架构

定时触发器（每20天）→ FC 函数执行 Playwright 自动化 → 模拟登录 → 填写招聘表单 → 提交发布 → 获取成功页面 → 发送回执（邮件/短信/微信）。

第一步：本地跑通发布流程

先在本机用 Playwright 的 headful 模式（headless=False）手动操作一遍，记录每一步的 DOM 选择器。

关键操作路径（据官方指引）

1. 用律所账号登录 OA 系统 https://oa.lawyers.org.cn/
2. 进入“事务所招聘”模块
3. 点击“创建新招聘”或从列表选中已有信息
4. 填写岗位信息，保存后点击“发布招聘信息”

发布规则

· 无需审核，发布后直接显示
· 距上次发布时间间隔20天后，重新发布才会置顶
· 如果律所名称更新但招聘信息未变，点击“同步招聘单位”即可

第二步：FC 函数部署

依赖打包（关键）

Playwright 和 Chromium 无法通过 FC 内置环境直接运行，必须手动打包上传：

```bash
pip install playwright -t ./code
python -m playwright install chromium
```

将生成的 code 目录（含 Chromium）整体打包上传。

函数代码骨架

```python
from playwright.sync_api import sync_playwright

def publish_job():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        # 登录 OA
        page.goto("https://oa.lawyers.org.cn/login.jsp")
        page.fill('input[name="username"]', os.environ['LAWYER_USER'])
        page.fill('input[name="password"]', os.environ['LAWYER_PASS'])
        page.click('button[type="submit"]')
        page.wait_for_load_state('networkidle')
        
        # 进入事务所招聘
        # ... 根据实际页面结构定位元素
        
        # 获取发布后的链接
        page.wait_for_selector('text=发布成功')
        job_url = page.url  # 或从列表页抓取
        
        return True, job_url
```

账号密码务必通过 FC 环境变量传入，不要硬编码。

第三步：配置定时触发器

在 FC 控制台创建定时触发器，必须用 CRON_TZ 指定北京时间：

```
CRON_TZ=Asia/Shanghai 0 0 9 */20 * *
```

含义：北京时间每20天的上午9点触发。Cron 表达式默认 UTC 时间，不加 CRON_TZ 会偏差8小时。

第四步：发布成功后的回执

发布成功后，从 Playwright 抓取该条招聘的公开链接（格式如 https://recruitment.lawyers.org.cn/recruitment.jsp?id=xxx），然后调用通知：

· 邮件：最可靠，FC 内置 smtplib 直接用
· 短信：需企业认证阿里云账号 + 短信模板审核
· 微信服务号：需认证服务号 + 客户 openid 池，投入最高

建议先用邮件跑通全流程，短信作为可选补充。

需要你确认的信息

1. 律所是否已有东方律师网 OA 登录权限？ 无权限则方案不可行
2. 客户希望收到哪种回执？ 邮件/短信/微信，决定后续通知开发量
3. 发布内容是否固定？ 固定则只需“重新发布置顶”；需要修改则要处理表单填写

与之前方案的关键差异

之前讨论的“东方律师网发布”基于通用假设，实际确认了：必须登录 OA 系统操作，而非直接向招聘页面 POST 数据。这意味着 Playwright 是必需项，纯 requests 方案不可行。


你的理解完全正确。

根据东方律师网的官方规则，“置顶”只跟“距离上一次发布时间是否满 20 天”有关，跟内容改没改没有任何关系。官方原话是：“招聘信息会按照上一次发布时间与最新发布时间间隔20天后重新发布才会在前面展示……不满20天的删除或者重新编辑发布都不会靠前展示，只会在原位置展示。”

所以你的流程可以简化为：

1. 客户随时改内容：改完保存发布后，招聘内容更新了，但排序位置不变，仍然停留在原来的位置。
2. 20 天后自动化重发：我们的定时任务只需要点击“重新发布”或“更新排序时间”，系统就会把这 20 天内已经被客户更新过的最新内容一并推到前面。

这意味着 FC 函数完全不需要处理任何“内容修改”的逻辑，它只需要老老实实在第 20 天执行一次“置顶重发”动作即可。方案可以比之前简化不少。

