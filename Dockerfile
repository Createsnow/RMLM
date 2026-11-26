# API 镜像：Python 3.13 + FastAPI。前端由 frontend/Dockerfile 的 nginx 托管，本镜像不含前端
FROM python:3.13-slim AS builder
ARG PIP_INDEX_URL=https://pypi.org/simple
# 与运行阶段同路径：venv 里脚本的 shebang 写的是绝对路径
WORKDIR /app
RUN python -m venv /app/.venv
COPY backend/requirements.txt ./
RUN /app/.venv/bin/pip install --no-cache-dir --index-url "${PIP_INDEX_URL}" -r requirements.txt

FROM python:3.13-slim
# fonts-wqy-zenhei：照片水印里的中文；tzdata：容器时区
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata fonts-wqy-zenhei \
    && ln -snf /usr/share/zoneinfo/Asia/Shanghai /etc/localtime \
    && echo Asia/Shanghai > /etc/timezone \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --create-home --home-dir /home/appuser appuser
WORKDIR /app
COPY --from=builder /app/.venv /app/.venv
COPY backend/app ./app
COPY backend/scripts ./scripts
# 照片目录：挂数据卷；预先建好并交给 appuser，新建的卷会继承这个属主
RUN mkdir -p /data/photos && chown -R appuser:appuser /app /data
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Asia/Shanghai \
    RMLM_PHOTO_DIR=/data/photos \
    RMLM_WATERMARK_FONT=/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc
USER appuser
EXPOSE 8000
# 健康检查用镜像自带的 Python 发请求，不另装 curl
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health/live', timeout=3)" || exit 1
# 进程数 RMLM_WORKERS（.env，默认 1）：大于 1 时 uvicorn 起一个守护进程管理 N 个工作进程（挂掉的自动拉起），共用 8000 端口；
# 定时任务用 MySQL 命名锁防重，多进程不会重复执行。
# --proxy-headers：只有 web 容器（nginx）能访问本容器，信任它转发的 X-Forwarded-For，审计日志记真实来源地址。
# 停机（docker stop）时最多等 10 秒让进行中的请求处理完，与 compose 的 stop_grace_period 配合
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers \"${RMLM_WORKERS:-1}\" --proxy-headers --forwarded-allow-ips '*' --timeout-graceful-shutdown 10"]
