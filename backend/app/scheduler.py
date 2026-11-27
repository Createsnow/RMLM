"""进程内定时任务，不依赖 Redis。每 15 分钟执行一次 tick（启动 15 分钟后第一次）。

跑在守护线程里：停服务时不等正在执行的 tick（如金蝶同步），进程可以马上退出；
没提交的事务由 MySQL 回滚，命名锁随连接断开释放，下一次 tick 会重新处理。
"""
import logging
import threading

from . import db as dbmod
from .services import jobs

log = logging.getLogger("rmlm.scheduler")
INTERVAL_SECONDS = 15 * 60
_stop = threading.Event()
_thread: threading.Thread | None = None


def _run() -> None:
    with dbmod.SessionLocal() as db:
        try:
            res = jobs.tick_locked(db)
            db.commit()
            if res:
                log.info("tick %s", res)
        except Exception:
            db.rollback()
            log.exception("tick failed")


def _loop() -> None:
    # 上一次 tick 跑完再等一个间隔，不会叠着跑
    while not _stop.wait(INTERVAL_SECONDS):
        _run()


def start() -> None:
    global _thread
    if _thread and _thread.is_alive():
        return
    _stop.clear()
    _thread = threading.Thread(target=_loop, name="rmlm-scheduler", daemon=True)
    _thread.start()


def stop() -> None:
    """停止调度；正在执行的 tick 不等它结束。"""
    global _thread
    _stop.set()
    _thread = None
