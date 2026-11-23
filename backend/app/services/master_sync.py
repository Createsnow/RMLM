"""主数据同步：从金蝶只读拉取物料档案，写入中台物料表。

规则：
- 按编码对齐：有则更新名称、规格、单位、分组，没有则新建；
- 金蝶禁用的物料在中台置为停用：保留历史领用行的引用，但不能再申请；
- 单价不从物料档案取（金蝶物料档案基本不维护单价），金额以 ERP 单据行单价为准；
- 增量：按金蝶"修改日期"，水位记在 job_run（k3_material），下次从水位往前回退 10 分钟再拉，避免漏单。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..config import settings
from ..errors import BizError
from ..models import JobRun, Material
from .common import add_todo, users_with_role

log = logging.getLogger("rmlm.sync")

JOB = "k3_material"
OVERLAP = timedelta(minutes=10)


def make_reader():
    """返回金蝶读取器；没有配置金蝶接口时报错。测试里可替换此函数。"""
    if not settings.k3_enabled:
        raise BizError("未配置金蝶接口（K3_SERVER_URL 等环境变量），请先用 Excel 导入物料")
    from .k3cloud import K3CloudReader

    return K3CloudReader()


def _cut(v: str | None, n: int) -> str | None:
    return v[:n] if v else v


def _job(db: Session) -> JobRun:
    jr = db.get(JobRun, JOB)
    if jr is None:
        jr = JobRun(name=JOB)
        db.add(jr)
    return jr


def status(db: Session) -> dict:
    jr = db.get(JobRun, JOB)
    return {
        "configured": settings.k3_enabled,
        "last_run_at": jr.last_run_at.isoformat(sep=" ", timespec="seconds") if jr and jr.last_run_at else None,
        "watermark": jr.last_key if jr else None,
        "last_result": json.loads(jr.last_result) if jr and jr.last_result else None,
    }


def apply_materials(db: Session, rows: list[dict]) -> dict:
    """把金蝶物料行写进中台物料表，返回统计。"""
    stats = {"fetched": len(rows), "created": 0, "updated": 0, "disabled": 0, "enabled": 0}
    now = clock.now()
    by_code: dict[str, dict] = {}
    for r in rows:  # 同一编码多行时以最后修改的为准
        code = (r.get("code") or "").strip()
        if code:
            prev = by_code.get(code)
            if prev is None or (r.get("modified_at") or datetime.min) >= (prev.get("modified_at") or datetime.min):
                by_code[code] = r
    existing = {m.code: m for m in db.scalars(select(Material).where(Material.code.in_(list(by_code) or [""])))}
    for code, r in by_code.items():
        m = existing.get(code)
        if m is None:
            if r.get("forbidden"):
                continue  # 中台从没用过的禁用物料不用建
            m = Material(code=code)
            db.add(m)
            stats["created"] += 1
        else:
            stats["updated"] += 1
        m.name = _cut(r.get("name") or m.name or code, 128)
        m.spec = _cut(r.get("spec"), 255)
        m.unit = _cut(r.get("unit") or m.unit or "个", 16)
        m.category = _cut(r.get("category"), 64)
        m.erp_id = r.get("erp_id") or m.erp_id
        active = not r.get("forbidden")
        if m.active is not None and m.id is not None and m.active != active:
            stats["disabled" if not active else "enabled"] += 1
        m.active = active
        m.synced_at = now
    db.flush()
    return stats


def sync_materials(db: Session, full: bool = False, reader=None) -> dict:
    """拉取并写入。full=True 时忽略水位全量拉取。"""
    jr = _job(db)
    since = None
    if not full and jr.last_key:
        try:
            since = datetime.fromisoformat(jr.last_key) - OVERLAP
        except ValueError:
            since = None
    own = reader is None
    reader = reader or make_reader()
    try:
        rows = reader.fetch_materials(since)
    finally:
        if own and hasattr(reader, "close"):
            reader.close()
    stats = apply_materials(db, rows)
    marks = [r["modified_at"] for r in rows if r.get("modified_at")]
    if marks:
        jr.last_key = max(marks).isoformat(timespec="seconds")
    elif not jr.last_key:
        jr.last_key = clock.now().isoformat(timespec="seconds")
    stats["mode"] = "full" if since is None else "incremental"
    stats["since"] = since.isoformat(sep=" ", timespec="seconds") if since else None
    jr.last_run_at = clock.now()
    jr.last_result = json.dumps(stats, ensure_ascii=False)
    db.flush()
    return stats


def daily_sync(db: Session) -> dict | None:
    """每日定时任务调用：没配置就跳过；失败不影响其他任务，通知管理员。"""
    if not settings.k3_enabled:
        return None
    try:
        with db.begin_nested():
            return sync_materials(db)
    except Exception as e:  # noqa: BLE001  接口失败只记录，不中断每日任务
        log.exception("material sync failed")
        for a in users_with_role(db, "ADMIN"):
            add_todo(db, a.id, "SYNC_FAIL", f"金蝶物料同步失败：{str(e)[:180]}", "job", 0, dedupe=True)
        return {"error": str(e)[:200]}
