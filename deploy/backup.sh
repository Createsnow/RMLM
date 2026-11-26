#!/usr/bin/env bash
# 每日备份（Docker 部署）：数据库 + 照片（照片永久保存）。建议 crontab：30 2 * * * /path/RMLM/deploy/backup.sh
# 连公司 MySQL 或随附数据库（COMPOSE_PROFILES=db）都适用：数据库连接参数取自运行中的 api 容器，
# 用 mysql:8.0 镜像在 compose 网络里执行 mysqldump。公司 MySQL 已有统一备份时，可只备份照片（SKIP_DB=1）。
set -euo pipefail
cd "$(dirname "$0")/.."   # 仓库根目录（docker-compose.yml 所在）
DEST=${BACKUP_DIR:-/backup/rmlm}/$(date +%F)
mkdir -p "$DEST"

env_of() { docker compose exec -T api printenv "$1" 2>/dev/null || true; }

if [ "${SKIP_DB:-0}" != "1" ]; then
  DB_HOST=$(env_of DB_HOST); DB_PORT=$(env_of DB_PORT); DB_NAME=$(env_of DB_NAME)
  DB_USER=$(env_of DB_USER); DB_PASSWORD=$(env_of DB_PASSWORD)
  docker run --rm --network "${COMPOSE_NETWORK:-rmlm_default}" -e MYSQL_PWD="$DB_PASSWORD" mysql:8.0 \
    mysqldump -h"${DB_HOST:-127.0.0.1}" -P"${DB_PORT:-3306}" -u"${DB_USER:-rmlm}" \
    --single-transaction --no-tablespaces "${DB_NAME:-rmlm}" | gzip > "$DEST/rmlm.sql.gz"
fi
docker compose cp api:/data/photos "$DEST/photos"
find "$(dirname "$DEST")" -maxdepth 1 -type d -mtime +30 -name '20*' -exec rm -rf {} +   # 备份保留 30 天
echo "备份完成：$DEST"
