# 页面镜像：构建前端，用 nginx 托管页面并把 /api 等转发到 api 容器
FROM node:22-alpine AS builder
ARG NPM_REGISTRY=https://registry.npmjs.org
WORKDIR /src
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund --registry="${NPM_REGISTRY}"
COPY . .
RUN npm run build

FROM nginx:1.27-alpine
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /src/dist /usr/share/nginx/html
EXPOSE 80
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/nginx-health || exit 1
