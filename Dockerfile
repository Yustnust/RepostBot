# ============================================================
# RepostBot —— 东方律师网招聘信息「20 天自动重发置顶」机器人
# 基础镜像：mcr.microsoft.com/playwright/python（已内置 chromium 浏览器）
# ============================================================
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai

COPY requirements.txt .

# ⚠️ 关键：必须用「与 CMD 同一个解释器」安装，否则运行时 import 不到 playwright
#    （曾出现 FC 日志报 "未安装 playwright"，原因就是装的和跑的不是同一个 python）
# playwright 锁 1.47.0：与基础镜像内已下载的 chromium 版本一致，避免重复下载浏览器
RUN python -V \
 && python -m pip install --no-cache-dir \
        playwright==1.47.0 \
        oss2==2.19.1 \
        alibabacloud_dysmsapi20170525==3.1.0 \
 && python -m playwright install chromium
# 构建期自检：任一依赖装不上或 import 失败，构建直接红（不要拖到 FC 运行时才发现）
RUN python -c "import playwright.sync_api, oss2; print('deps ok')"

COPY src/ ./src/
COPY config/ ./config/

# FC 事件函数 + 容器镜像要求容器内监听 9000 端口提供 HTTP 服务，
# 定时触发器的调用由 bootstrap.py 转交给 index.handler（事件模型）。
# 本机 CLI 仍用 python src/index.py 直跑 main()，不受影响。
CMD ["python", "src/bootstrap.py"]
