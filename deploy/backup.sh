#!/usr/bin/env bash
# 每日备份（Docker 部署）：数据库 + 照片（照片永久保存）。建议 crontab：30 2 * * * /path/RMLM/deploy/backup.sh
# 数据库在公司 MySQL 上：连接参数取自运行中的 api 容器，借 mysql:8.0 镜像里的 mysqldump 直连导出
# （一次性容器，用完即删，不是数据库服务）。公司 MySQL 已有统一备份时，可只备份照片（SKIP_DB=1）。
set -euo pipefail
cd "$(dirname "$0")/.."   # 仓库根目录（docker-compose.yml 所在）
DEST=${BACKUP_DIR:-/backup/rmlm}/$(date +%F)
mkdir -p "$DEST"

env_of() { docker compose exec -T api printenv "$1" 2>/dev/null || true; }

if [ "${SKIP_DB:-0}" != "1" ]; then
  DB_HOST=$(env_of DB_HOST); DB_PORT=$(env_of DB_PORT); DB_NAME=$(env_of DB_NAME)
  DB_USER=$(env_of DB_USER); DB_PASSWORD=$(env_of DB_PASSWORD)
  if [ -z "$DB_HOST" ]; then
    echo "取不到 DB_HOST：api 容器没在运行，或 .env 用的是 RMLM_DATABASE_URL（请改用 DB_* 分项填写）" >&2
    exit 1
  fi
  docker run --rm --network host -e MYSQL_PWD="$DB_PASSWORD" mysql:8.0 \
    mysqldump -h"$DB_HOST" -P"${DB_PORT:-3306}" -u"${DB_USER:-rmlm}" \
    --single-transaction --no-tablespaces "${DB_NAME:-rmlm}" | gzip > "$DEST/rmlm.sql.gz"
fi
docker compose cp api:/data/photos "$DEST/photos"
find "$(dirname "$DEST")" -maxdepth 1 -type d -mtime +30 -name '20*' -exec rm -rf {} +   # 备份保留 30 天
echo "备份完成：$DEST"
