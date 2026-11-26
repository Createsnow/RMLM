#!/usr/bin/env python3
"""研发物料管家 RMLM 本地一键启动（Windows / macOS / Linux 通用）

    python start.py            一键启动（首次会自动建虚拟环境、安装依赖、构建前端）
    python start.py rebuild    重新构建前端后启动（改了 frontend/ 代码后用）
    python start.py stop       停止正在运行的服务（在另一个终端里，或服务在后台运行时用）

前台运行时按 Ctrl+C 停止：等正在处理的请求结束（最多约 15 秒），再按一次立即强制停止。

配置写在仓库根目录的 .env（首次运行会从 .env.example 复制一份）。
本脚本只用 Python 标准库，可以直接用系统里的 Python 运行。
"""
import argparse
import filecmp
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
import venv
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"
VENV = BACKEND / ".venv"
WINDOWS = os.name == "nt"
VENV_PY = VENV / ("Scripts/python.exe" if WINDOWS else "bin/python")
PID_FILE = BACKEND / "data" / "rmlm.pid"  # 内容："启动脚本进程号 服务进程号"
GRACEFUL_SECONDS = 10  # uvicorn 等正在处理的请求结束的上限
STOP_WAIT = 30  # 超过这个时间服务还没退出，就强制结束


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
def load_env() -> dict:
    env_file = ROOT / ".env"
    if not env_file.exists():
        shutil.copyfile(ROOT / ".env.example", env_file)
        say(f"已生成 .env（默认连接本机 MySQL 的 rmlm 库），数据库与金蝶连接请编辑 {env_file}")
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
            fail(f"端口 {port} 已被占用（可能系统已经在运行）。先运行 python start.py stop 停止服务，"
                 "若仍被占用请关闭占用该端口的程序，或在 .env 里修改 RMLM_PORT")


# ---------------------------------------------------------------- 进程管理
def alive(pid: int, marker: str) -> bool:
    """进程还在，且命令行里含 marker（防止进程号被系统复用后误杀别的程序）。"""
    if pid <= 0:
        return False
    if WINDOWS:
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, errors="replace").stdout
        return f'"{pid}"' in out and "python" in out.lower()  # Windows 这里拿不到命令行，只核对是 Python 进程
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    out = subprocess.run(["ps", "-o", "command=", "-p", str(pid)], capture_output=True, text=True).stdout
    return marker in out


def read_pids() -> tuple[int, int]:
    try:
        a, b = PID_FILE.read_text().split()
        return int(a), int(b)
    except (OSError, ValueError):
        return 0, 0


def kill_tree(pid: int) -> None:
    """强制结束服务进程及其子进程。"""
    if WINDOWS:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)
    else:
        try:
            os.killpg(pid, signal.SIGKILL)  # 服务进程单独一个进程组，组号 = 进程号
        except (ProcessLookupError, PermissionError):
            pass


def wait_gone(pid: int, marker: str, seconds: float) -> bool:
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if not alive(pid, marker):
            return True
        time.sleep(0.3)
    return not alive(pid, marker)


def stop() -> None:
    launcher, server = read_pids()
    up_l, up_s = alive(launcher, "start.py"), alive(server, "uvicorn")
    if not (up_l or up_s):
        PID_FILE.unlink(missing_ok=True)
        say("服务没有在运行")
        return
    say("正在停止服务…")
    if WINDOWS:
        # Windows 无法给另一个控制台里的程序发 Ctrl+C，只能直接结束
        for pid in (server, launcher):
            if alive(pid, ""):
                kill_tree(pid)
    elif up_l:
        os.kill(launcher, signal.SIGTERM)  # 启动脚本收到后会让服务正常退出，超时再强制结束
        wait_gone(launcher, "start.py", STOP_WAIT + 5)
    else:  # 启动脚本已经不在了，服务还在
        try:
            os.killpg(server, signal.SIGTERM)
        except ProcessLookupError:
            pass
        if not wait_gone(server, "uvicorn", STOP_WAIT):
            kill_tree(server)
    if alive(launcher, "start.py") or alive(server, "uvicorn"):
        fail("服务没能停止，请手动结束 Python 进程")
    PID_FILE.unlink(missing_ok=True)
    say("服务已停止")


def serve(cmd: list, env: dict) -> int:
    """启动 uvicorn 并等它退出。Ctrl+C / 被 kill / 关闭终端时让 uvicorn 正常退出，超时再强制结束。"""
    # Linux / macOS：服务单独一个进程组，Ctrl+C 只发给本脚本，由本脚本转发一次，避免 uvicorn 收到两次直接强退。
    # Windows：同一控制台的 Ctrl+C 本来就会同时发给 uvicorn。
    child = subprocess.Popen(cmd, cwd=BACKEND, env=env, **({} if WINDOWS else {"process_group": 0}))
    PID_FILE.parent.mkdir(parents=True, exist_ok=True)
    PID_FILE.write_text(f"{os.getpid()} {child.pid}")
    deadline = None

    def request_stop(signum, _frame):
        nonlocal deadline
        if deadline is not None:
            say("强制停止")
            kill_tree(child.pid)
            return
        deadline = time.monotonic() + STOP_WAIT
        print(flush=True)
        say("正在停止服务，等待正在处理的请求结束…（再按一次 Ctrl+C 立即强制停止）")
        if not WINDOWS:
            child.send_signal(signal.SIGINT)

    for name in ("SIGINT", "SIGTERM", "SIGHUP", "SIGBREAK"):
        if hasattr(signal, name):
            signal.signal(getattr(signal, name), request_stop)
    try:
        while True:
            try:
                code = child.wait(timeout=0.5)  # 带超时轮询：Windows 上无限等待时收不到 Ctrl+C
                break
            except subprocess.TimeoutExpired:
                if deadline is not None and time.monotonic() > deadline:
                    say(f"服务 {STOP_WAIT} 秒内没有退出，强制停止")
                    kill_tree(child.pid)
                    deadline = time.monotonic() + 5
    finally:
        if read_pids() == (os.getpid(), child.pid):
            PID_FILE.unlink(missing_ok=True)
    if deadline is not None:
        say("服务已停止")
        return 0
    return code


# ---------------------------------------------------------------- 主流程
def main() -> None:
    parser = argparse.ArgumentParser(description="研发物料管家本地一键启动")
    parser.add_argument("mode", nargs="?", default="start", choices=["start", "rebuild", "stop"],
                        help="start：一键启动（默认）；rebuild：重新构建前端后启动；stop：停止正在运行的服务")
    args = parser.parse_args()

    if args.mode == "stop":
        stop()
        return

    if sys.version_info < (3, 11):
        fail(f"Python 版本过低（{sys.version.split()[0]}），需要 3.11 及以上")

    env = load_env()
    host = env.get("RMLM_HOST", "0.0.0.0")
    port = int(env.get("RMLM_PORT", "8000"))

    ensure_backend()
    if args.mode == "rebuild" or not (Path(env["RMLM_STATIC_DIR"]) / "index.html").exists():
        build_frontend()
    launcher, server = read_pids()
    if alive(launcher, "start.py") or alive(server, "uvicorn"):
        fail("服务已经在运行。要重启请先运行 python start.py stop")
    check_db(env)
    check_port(host, port)

    say(f"启动完成后浏览器打开 http://localhost:{port}  （Ctrl+C 停止）")
    cmd = [str(VENV_PY), "-m", "uvicorn", "app.main:app", "--host", host, "--port", str(port),
           "--timeout-graceful-shutdown", str(GRACEFUL_SECONDS)]
    sys.exit(serve(cmd, env))


if __name__ == "__main__":
    main()
