"""金蝶单据同步：每 15 分钟从金蝶只读拉取领料 / 退料 / 报废 / A 转 B / 调整单，写入单据镜像，
再按备注里的申请号自动生成领用行——领料专员在金蝶开单过账后，使用人直接签收，不需要人工导入。

规则：
- 按单据号对齐：没有则新建（来源 API）；已有则更新过账状态、备注和各行数量、单价、项目号；
- 金蝶里删掉的行，镜像里数量置 0（保留历史引用），由对账发现差额；
- 已关账月份的单据在金蝶里被改，不改镜像，派待办给领料专员和管理员；
- 增量：按金蝶"修改日期"，水位记在 job_run（k3_docs），下次从水位往前回退 10 分钟再拉，避免漏单；
- 研发领料 = 其他出库单中单据类型为"研发测试其他出库单"的（K3_LL_BILL_TYPES），在 k3cloud.K3CloudReader.fetch_docs；
- 退料 / 报废单不批量拉，领料专员分摊时按单号取（fetch_one）；A 转 B、调整单一期不接，手工录入 / Excel 导入。
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..config import settings
from ..errors import BizError
from ..models import ErpDoc, ErpLine, JobRun, Material
from . import erp
from .common import D, add_todo, close_todos, users_with_role
from .master_sync import make_reader

log = logging.getLogger("rmlm.sync")

JOB = "k3_docs"
OVERLAP = timedelta(minutes=10)


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


def _dec(v):
    return D(v) if v not in (None, "") else None


def _update(db: Session, doc: ErpDoc, row: dict) -> bool:
    """把金蝶上的最新内容同步到已有单据镜像，返回是否有变化。"""
    from .recon import period_closed

    want_lines = {int(ln.get("line_no") or i): ln for i, ln in enumerate(row.get("lines") or [], start=1)}
    have = {ln.line_no: ln for ln in doc.lines}
    changes: list[tuple] = []
    posted = bool(row.get("posted"))
    if posted != doc.posted:
        changes.append(("posted", posted))
    remark = row.get("remark")
    if (remark or None) != (doc.remark or None):
        changes.append(("remark", remark))
    for no, ln in want_lines.items():
        code = str(ln.get("material_code") or "").strip()
        m = db.scalar(select(Material).where(Material.code == code))
        if not m:
            raise BizError(f"第 {no} 行物料编码不存在：{code}")
        qty, price, proj = D(ln.get("qty")), _dec(ln.get("unit_price")), ln.get("project_code")
        cur = have.get(no)
        if cur is None:
            changes.append(("add", no, m, qty, price, proj, ln.get("ref_line_no")))
        elif (cur.material_id, D(cur.qty), _dec(cur.unit_price), cur.project_code) != (m.id, qty, price, proj):
            changes.append(("line", cur, m, qty, price, proj))
    for no, cur in have.items():
        if no not in want_lines and D(cur.qty) != 0:
            changes.append(("line", cur, db.get(Material, cur.material_id), D(0), _dec(cur.unit_price),
                            cur.project_code))
    if not changes:
        return False
    if period_closed(db, doc.doc_date):
        raise BizError(f"{doc.doc_date:%Y-%m} 已关账，金蝶里修改了该月单据，请财务开调整单")
    for c in changes:
        if c[0] == "posted":
            doc.posted = c[1]
            if not c[1]:
                # 金蝶反审核 / 作废：已签收的领用行、已做的分摊仍在，对账会把差额列进差异池；先提醒领料专员核对
                for u in users_with_role(db, "KEEPER"):
                    add_todo(db, u.id, "ERP_UNPOSTED", f"金蝶单据 {doc.no} 已反审核或作废，请核对中台已签收 / 已分摊的明细",
                             "erpdoc", doc.id, due_hours=48, dedupe=True)
            else:
                close_todos(db, "erpdoc", doc.id, ("ERP_UNPOSTED",))
        elif c[0] == "remark":
            doc.remark = c[1]
        elif c[0] == "add":
            _, no, m, qty, price, proj, ref = c
            doc.lines.append(ErpLine(line_no=no, material_id=m.id, qty=qty, unit_price=price, project_code=proj,
                                     ref_line_no=int(ref) if ref else None))
        else:
            _, cur, m, qty, price, proj = c
            if m.id != cur.material_id and erp.line_bound(db, cur.id):
                raise BizError(f"第 {cur.line_no} 行已生成领用行，金蝶里不能再换物料，请领料专员核对")
            cur.material_id, cur.qty, cur.unit_price, cur.project_code = m.id, qty, price, proj
    db.flush()
    erp.after_doc_change(db, doc)
    return True


def apply_docs(db: Session, rows: list[dict]) -> dict:
    """把金蝶单据写进单据镜像，返回统计；单张失败不影响其他单据。"""
    stats: dict = {"fetched": len(rows), "created": 0, "updated": 0, "unchanged": 0, "errors": []}
    for row in rows:
        no = (row.get("no") or "").strip()
        data = {**row, "no": no}
        if isinstance(data.get("doc_date"), (date, datetime)):
            data["doc_date"] = (data["doc_date"].date() if isinstance(data["doc_date"], datetime)
                                else data["doc_date"]).isoformat()
        try:
            with db.begin_nested():
                doc = db.scalar(select(ErpDoc).where(ErpDoc.no == no)) if no else None
                if doc is None:
                    erp.save_doc(db, None, data, source="API")
                    stats["created"] += 1
                elif _update(db, doc, data):
                    stats["updated"] += 1
                else:
                    stats["unchanged"] += 1
        except BizError as e:
            stats["errors"].append(f"{no or '（无单号）'}：{e}")
    for msg in stats["errors"]:
        for u in users_with_role(db, "KEEPER") + users_with_role(db, "ADMIN"):
            add_todo(db, u.id, "SYNC_FAIL", f"金蝶单据同步跳过 {msg}"[:200], "erpsync", 0, dedupe=True)
    return stats


def sync_docs(db: Session, full: bool = False, reader=None) -> dict:
    """拉取金蝶单据、写入镜像、自动生成领用行。full=True 时忽略水位全量拉取。"""
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
        rows = reader.fetch_docs(since)
    finally:
        if own and hasattr(reader, "close"):
            reader.close()
    stats = apply_docs(db, rows)
    stats["auto_bound"] = erp.auto_bind(db)
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


def fetch_one(db: Session, bill_no: str, doc_type: str, reader=None) -> ErpDoc:
    """按单号从金蝶取一张退料 / 报废单写进单据镜像（领料专员分摊时用，不用先录入或导入）。
    中台已有这张单就按金蝶最新内容更新。"""
    if doc_type not in ("TL", "BF"):
        raise BizError("按单号取单只用于退料单 / 报废单")
    no = (bill_no or "").strip()
    if not no:
        raise BizError("请输入金蝶单号")
    have = db.scalar(select(ErpDoc).where(ErpDoc.no == no))
    if have is not None and have.doc_type != doc_type:
        raise BizError(f"单据 {no} 在中台已登记为其他类型（{have.doc_type}），请核对单号")
    own = reader is None
    reader = reader or make_reader()
    try:
        row = reader.fetch_doc(no, doc_type)
    finally:
        if own and hasattr(reader, "close"):
            reader.close()
    if row is None:
        raise BizError(f"金蝶里查不到单据 {no}（只取库存组织 {settings.k3_use_org} 已审核或重新审核的单据）")
    if not row.get("lines"):
        raise BizError(f"金蝶单据 {no} 没有明细")
    data = {**row, "no": no, "doc_type": doc_type}
    if isinstance(data.get("doc_date"), (date, datetime)):
        data["doc_date"] = (data["doc_date"].date() if isinstance(data["doc_date"], datetime)
                            else data["doc_date"]).isoformat()
    if have is None:
        return erp.save_doc(db, None, data, source="API")
    _update(db, have, data)
    return have


def quarter_sync(db: Session) -> dict | None:
    """每 15 分钟的定时任务调用：没配置金蝶就跳过；单据表单还没接入时跳过且不报警；
    其他失败通知管理员，不影响其他任务。"""
    if not settings.k3_enabled:
        return None
    try:
        with db.begin_nested():
            return sync_docs(db)
    except NotImplementedError as e:
        return {"skipped": str(e)[:200]}
    except Exception as e:  # noqa: BLE001  接口失败只记录，不中断巡检
        log.exception("doc sync failed")
        for a in users_with_role(db, "ADMIN"):
            add_todo(db, a.id, "SYNC_FAIL", f"金蝶单据同步失败：{str(e)[:180]}", "job", 1, dedupe=True)
        return {"error": str(e)[:200]}
