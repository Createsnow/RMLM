# 构建前端
FROM node:22-alpine AS web
WORKDIR /web
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# 运行后端（同时托管前端静态文件）
FROM python:3.13-slim
ENV TZ=Asia/Shanghai PYTHONUNBUFFERED=1 \
    RMLM_STATIC_DIR=/app/web RMLM_PHOTO_DIR=/data/photos \
    RMLM_WATERMARK_FONT=/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc
RUN apt-get update && apt-get install -y --no-install-recommends fonts-wqy-zenhei tzdata \
    && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY backend/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY backend/ ./
COPY --from=web /web/dist /app/web
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
