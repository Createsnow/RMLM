#!/usr/bin/env bash
# 研发物料管家 RMLM 本地一键启动（macOS / Linux）
#
#   ./start.sh            一键启动（首次会自动建 Python 虚拟环境、安装依赖、构建前端）
#   ./start.sh rebuild    重新构建前端后启动（改了 frontend/ 代码后用）
#
# 配置写在仓库根目录的 .env（首次运行会从 .env.example 复制一份）。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
MODE="${1:-start}"
case "$MODE" in
  start|rebuild) ;;
  -h|--help|help) sed -n '2,8p' "$0"; exit 0 ;;
  *) echo "未知参数：$MODE（可用：start / rebuild）"; exit 1 ;;
esac

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
fail() { printf '\033[1;31m错误：\033[0m%s\n' "$*" >&2; exit 1; }

# ---------------------------------------------------------------- 配置
if [ ! -f "$ROOT/.env" ]; then
  cp "$ROOT/.env.example" "$ROOT/.env"
  say "已生成 .env（默认连接本机 MySQL 的 rmlm 库），如需修改数据库地址请编辑 $ROOT/.env"
fi
set -a
# shellcheck disable=SC1091
. "$ROOT/.env"
set +a
HOST="${RMLM_HOST:-0.0.0.0}"
PORT="${RMLM_PORT:-8000}"
export RMLM_PHOTO_DIR="${RMLM_PHOTO_DIR:-$ROOT/backend/data/photos}"
export RMLM_STATIC_DIR="${RMLM_STATIC_DIR:-$ROOT/frontend/dist}"

# ---------------------------------------------------------------- Python 环境
PYTHON="${PYTHON:-python3}"
command -v "$PYTHON" >/dev/null || fail "未找到 python3，请先安装 Python 3.11 及以上版本"
"$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
  || fail "Python 版本过低（$("$PYTHON" -V 2>&1)），需要 3.11 及以上"

VENV="$ROOT/backend/.venv"
if [ ! -x "$VENV/bin/python" ]; then
  say "创建 Python 虚拟环境 backend/.venv"
  "$PYTHON" -m venv "$VENV"
fi
PY="$VENV/bin/python"
if ! cmp -s "$ROOT/backend/requirements.txt" "$VENV/.requirements.txt"; then
  say "安装后端依赖"
  "$PY" -m pip install -q --upgrade pip
  "$PY" -m pip install -q -r "$ROOT/backend/requirements.txt"
  cp "$ROOT/backend/requirements.txt" "$VENV/.requirements.txt"
fi

# ---------------------------------------------------------------- 前端
if [ "$MODE" = "rebuild" ] || [ ! -f "$RMLM_STATIC_DIR/index.html" ]; then
  command -v npm >/dev/null || fail "未找到 npm，请先安装 Node.js 18 及以上版本"
  cd "$ROOT/frontend"
  if ! cmp -s package-lock.json node_modules/.package-lock.copy; then
    say "安装前端依赖"
    npm ci --no-audit --no-fund
    cp package-lock.json node_modules/.package-lock.copy
  fi
  say "构建前端"
  npm run build
fi

# ---------------------------------------------------------------- 数据库连通性
cd "$ROOT/backend"
"$PY" - <<'PYEOF' || fail "连不上数据库。请确认 MySQL 已启动、库已创建，并检查 .env 里的 RMLM_DATABASE_URL"
import sys
from sqlalchemy import create_engine, text
from app.config import settings
try:
    with create_engine(settings.database_url).connect() as c:
        c.execute(text("SELECT 1"))
except Exception as e:
    print(f"  {type(e).__name__}: {e}", file=sys.stderr)
    sys.exit(1)
PYEOF

# ---------------------------------------------------------------- 启动
say "启动完成后浏览器打开 http://localhost:$PORT  （Ctrl+C 停止）"
exec "$PY" -m uvicorn app.main:app --host "$HOST" --port "$PORT"
