"""进程内调度器（APScheduler），不依赖 Redis。每 15 分钟执行一次 tick。"""
import logging

from apscheduler.schedulers.background import BackgroundScheduler

from . import db as dbmod
from .services import jobs

log = logging.getLogger("rmlm.scheduler")
_sched: BackgroundScheduler | None = None


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


def start() -> None:
    global _sched
    if _sched:
        return
    _sched = BackgroundScheduler(timezone="Asia/Shanghai")
    _sched.add_job(_run, "interval", minutes=15, id="tick", max_instances=1, coalesce=True)
    _sched.start()
