#!/usr/bin/env bash
# 每日备份：数据库 + 照片（照片永久保存）。建议 crontab：30 2 * * * /path/deploy/backup.sh
set -euo pipefail
cd "$(dirname "$0")"
DEST=${BACKUP_DIR:-/backup/rmlm}/$(date +%F)
mkdir -p "$DEST"
docker compose exec -T db sh -c 'mysqldump -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction rmlm' | gzip > "$DEST/rmlm.sql.gz"
docker compose cp app:/data/photos "$DEST/photos"
find "$(dirname "$DEST")" -maxdepth 1 -type d -mtime +30 -name '20*' -exec rm -rf {} +   # 数据库备份保留 30 天
