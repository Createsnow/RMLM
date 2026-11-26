#!/usr/bin/env python3
"""研发物料管家 RMLM 本地一键启动（Windows / macOS / Linux 通用）

    python start.py            一键启动（首次会自动建虚拟环境、安装依赖、构建前端）
    python start.py rebuild    重新构建前端后启动（改了 frontend/ 代码后用）

配置写在仓库根目录的 .env（首次运行会从 .env.example 复制一份）。
本脚本只用 Python 标准库，可以直接用系统里的 Python 运行。
"""
import argparse
import filecmp
import os
import shutil
import socket
import subprocess
import sys
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
VENV = BACKEND / ".venv"
WINDOWS = os.name == "nt"
VENV_PY = VENV / ("Scripts/python.exe" if WINDOWS else "bin/python")


def say(msg: str) -> None:
    print(f"==> {msg}", flush=True)


def fail(msg: str) -> None:
    print(f"\n错误：{msg}", file=sys.stderr, flush=True)
    # Windows 资源管理器里双击运行时（没有经过 cmd，环境变量里没有 PROMPT），出错后停住，窗口不要一闪而过
    if WINDOWS and "PROMPT" not in os.environ:
        input("按回车键退出…")
    sys.exit(1)


def run(cmd: list, **kw) -> None:
    if subprocess.run([str(c) for c in cmd], **kw).returncode != 0:
        fail(f"命令执行失败：{' '.join(str(c) for c in cmd)}")


def same(a: Path, b: Path) -> bool:
    return b.exists() and filecmp.cmp(a, b, shallow=False)


# ---------------------------------------------------------------- 配置
def ensure_jwt_secret(env_file: Path) -> None:
    """本地 .env 的登录令牌密钥为空或仍是示例值时，自动生成随机密钥写回（服务端会拒绝弱密钥启动）。"""
    import secrets

    lines = env_file.read_text(encoding="utf-8").splitlines()
    weak = {"", "dev-secret-change-me", "changeme", "change-me", "secret"}
    for i, raw in enumerate(lines):
        if raw.strip().startswith("RMLM_JWT_SECRET="):
            value = raw.split("=", 1)[1].strip().strip("\"'")
            if value in weak or value.startswith("请") or len(value) < 16:
                lines[i] = f"RMLM_JWT_SECRET={secrets.token_urlsafe(32)}"
                env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
                say("已为本地 .env 生成随机的 RMLM_JWT_SECRET")
            return
    env_file.write_text("\n".join(lines + [f"RMLM_JWT_SECRET={secrets.token_urlsafe(32)}"]) + "\n", encoding="utf-8")
    say("已为本地 .env 生成随机的 RMLM_JWT_SECRET")


def load_env() -> dict:
    env_file = ROOT / ".env"
    if not env_file.exists():
        shutil.copyfile(ROOT / ".env.example", env_file)
        say(f"已生成 .env（默认连接本机 MySQL 的 rmlm 库），数据库与金蝶连接请编辑 {env_file}")
    ensure_jwt_secret(env_file)
    env = os.environ.copy()
    for raw in env_file.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]  # 只去掉成对的外层引号，密码里的引号、# = 等字符原样保留
        env.setdefault(key.strip(), value)  # 命令行里已设置的环境变量优先
    env.setdefault("RMLM_PHOTO_DIR", str(BACKEND / "data" / "photos"))
    env.setdefault("RMLM_STATIC_DIR", str(FRONTEND / "dist"))
    env["PYTHONIOENCODING"] = "utf-8"
    return env


# ---------------------------------------------------------------- 后端依赖
def ensure_backend() -> None:
    if not VENV_PY.exists():
        say("创建 Python 虚拟环境 backend/.venv")
        try:
            venv.create(VENV, with_pip=True)
        except Exception as e:  # 常见于 Ubuntu 未装 python3-venv
            shutil.rmtree(VENV, ignore_errors=True)
            fail(f"创建虚拟环境失败：{e}\n（Ubuntu/Debian 请先执行：sudo apt install python3-venv）")
    req = BACKEND / "requirements.txt"
    marker = VENV / ".requirements.txt"
    if not same(req, marker):
        say("安装后端依赖")
        run([VENV_PY, "-m", "pip", "install", "-q", "--upgrade", "pip"])
        run([VENV_PY, "-m", "pip", "install", "-q", "-r", req])
        shutil.copyfile(req, marker)


# ---------------------------------------------------------------- 前端
def build_frontend() -> None:
    npm = shutil.which("npm")
    if not npm:
        fail("未找到 npm，请先安装 Node.js 18 及以上版本")
    lock = FRONTEND / "package-lock.json"
    marker = FRONTEND / "node_modules" / ".package-lock.copy"
    if not same(lock, marker):
        say("安装前端依赖")
        run([npm, "ci", "--no-audit", "--no-fund"], cwd=FRONTEND)
        shutil.copyfile(lock, marker)
    say("构建前端")
    run([npm, "run", "build"], cwd=FRONTEND)


# ---------------------------------------------------------------- 启动前检查
DB_CHECK = """
import sys
from sqlalchemy import create_engine, text
from app.config import settings
try:
    with create_engine(settings.database_url).connect() as c:
        c.execute(text("SELECT 1"))
except Exception as e:
    print(f"  {type(e).__name__}: {str(e).splitlines()[0]}", file=sys.stderr)
    sys.exit(1)
"""


def check_db(env: dict) -> None:
    if subprocess.run([str(VENV_PY), "-c", DB_CHECK], cwd=BACKEND, env=env).returncode != 0:
        fail("连不上数据库。请确认 MySQL 已启动、库已创建，并检查 .env 里的 DB_HOST / DB_PORT / DB_NAME / DB_USER / DB_PASSWORD")


def check_port(host: str, port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        try:
            s.bind(("" if host == "0.0.0.0" else host, port))
        except OSError:
            fail(f"端口 {port} 已被占用（可能系统已经在运行），请先关闭占用程序，或在 .env 里修改 RMLM_PORT")


# ---------------------------------------------------------------- 主流程
def main() -> None:
    parser = argparse.ArgumentParser(description="研发物料管家本地一键启动")
    parser.add_argument("mode", nargs="?", default="start", choices=["start", "rebuild"],
                        help="start：一键启动（默认）；rebuild：重新构建前端后启动")
    args = parser.parse_args()

    if sys.version_info < (3, 11):
        fail(f"Python 版本过低（{sys.version.split()[0]}），需要 3.11 及以上")

    env = load_env()
    host = env.get("RMLM_HOST", "0.0.0.0")
    port = int(env.get("RMLM_PORT", "8000"))

    ensure_backend()
    if args.mode == "rebuild" or not (Path(env["RMLM_STATIC_DIR"]) / "index.html").exists():
        build_frontend()
    check_db(env)
    check_port(host, port)

    say(f"启动完成后浏览器打开 http://localhost:{port}  （Ctrl+C 停止）")
    # 停止时最多等 10 秒让正在处理的请求结束
    cmd = [str(VENV_PY), "-m", "uvicorn", "app.main:app", "--host", host, "--port", str(port),
           "--timeout-graceful-shutdown", "10"]
    if not WINDOWS:
        # 让 uvicorn 直接接管本进程：Ctrl+C / kill 由 uvicorn 自己优雅停机，不会留下占着端口的进程
        os.chdir(BACKEND)
        os.execve(cmd[0], cmd, env)
    # Windows 没有 exec：Ctrl+C 会同时发给 uvicorn，这里等它自己退出，不要提前结束它
    child = subprocess.Popen(cmd, cwd=BACKEND, env=env)
    while True:
        try:
            sys.exit(child.wait())
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()
