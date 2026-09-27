# ============================================================
# RepostBot —— 东方律师网招聘信息「20 天自动重发置顶」机器人
# ============================================================
# 基础镜像已内置 playwright + chromium，无需再装浏览器（这是选它的唯一理由）
# 若该标签不可用，请在 ACR 构建日志中查看可用标签，或改用 mcr.microsoft.com/playwright/python:latest
FROM mcr.microsoft.com/playwright/python:v1.47.0-jammy

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai

# 只装云端额外依赖；playwright 由基础镜像提供，重复安装可能覆盖掉镜像内的浏览器版本
COPY requirements.txt .
RUN pip install --no-cache-dir oss2==2.18.4 alibabacloud_dysmsapi20170525==3.1.0

COPY src/ ./src/
COPY config/ ./config/

# FC 容器镜像入口由控制台配置，此处作为本地 docker run 的默认命令
CMD ["python", "src/index.py"]
