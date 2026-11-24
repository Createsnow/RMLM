"""对账与关账：单据号 + 行号 + 数量 三字段逐单核对；差额必须为 0 才能关账。"""
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock
from ..errors import BizError
from ..models import DocAlloc, ErpDoc, Event, LedgerLine, PeriodClose, ReconDiff, User
from ..perms import require_role
from .common import D, add_todo, close_todos, dict_codes, period_of, users_with_role

KIND_NAMES = {
    "LL_UNBOUND": "ERP 有单、中台无明细",
    "LL_MISMATCH": "领料数量不一致",
    "TL_MISMATCH": "退料分摊不平",
    "BF_MISMATCH": "报废分摊不平",
    "EVT_NO_ERP": "中台已退/报废、ERP 无单",
    "LINE_NO_ERP": "紧急交接未补 ERP 单",
}


def period_closed(db: Session, d: date | datetime) -> bool:
    pc = db.get(PeriodClose, period_of(d))
    return bool(pc and pc.closed)


def _adjustments(db: Session) -> dict[tuple[str, int], Decimal]:
    adj: dict[tuple[str, int], Decimal] = defaultdict(lambda: D(0))
    for doc in db.scalars(select(ErpDoc).where(ErpDoc.doc_type == "TZ", ErpDoc.finance_approved.is_(True))):
        for ln in doc.lines:
            adj[(doc.ref_doc_no, ln.ref_line_no or ln.line_no)] += D(ln.qty)
    return adj


def _compute(db: Session) -> dict[str, dict]:
    now = clock.now()
    cur_period = period_of(now)
    adj = _adjustments(db)
    rows: dict[str, dict] = {}
    alloc_by_line = dict(db.execute(select(DocAlloc.erp_line_id, func.sum(DocAlloc.qty)).group_by(DocAlloc.erp_line_id)).all())
    alloc_by_event = dict(db.execute(select(DocAlloc.event_id, func.sum(DocAlloc.qty)).group_by(DocAlloc.event_id)).all())
    for doc in db.scalars(select(ErpDoc).where(ErpDoc.doc_type != "TZ", ErpDoc.posted.is_(True))):
        for ln in doc.lines:
            erp_qty = D(ln.qty) + adj.get((doc.no, ln.line_no), D(0))
            base = {"period": period_of(doc.doc_date), "erp_doc_no": doc.no, "erp_line_no": ln.line_no,
                    "material_id": ln.material_id, "erp_qty": erp_qty}
            if doc.doc_type in ("LL", "AB"):
                # 一张单据行可能出了多个仓储条码 → 多条领用行，合计后再比
                bound = list(db.scalars(select(LedgerLine).where(LedgerLine.erp_line_id == ln.id,
                                                                 LedgerLine.level == 1).order_by(LedgerLine.id)))
                line = bound[0] if bound else None
                detail = sum((D(x.qty) for x in bound), D(0))
                kind = "LL_MISMATCH" if line else "LL_UNBOUND"
                rows[f"ERP:{ln.id}"] = {**base, "kind": kind, "detail_qty": detail, "line_id": line.id if line else None,
                                       "owner_hint": line.issued_by if line else None}
            else:
                detail = D(alloc_by_line.get(ln.id, 0))
                rows[f"ERP:{ln.id}"] = {**base, "kind": "TL_MISMATCH" if doc.doc_type == "TL" else "BF_MISMATCH",
                                       "detail_qty": detail, "line_id": None, "owner_hint": None}
    # 中台有、ERP 无：退库与整件报废事件在 7 天或跨月后仍未挂单
    reversed_ids = set(db.scalars(select(Event.reverses_id).where(Event.etype == "REVERSAL", Event.status == "EFFECTIVE")))
    for e in db.scalars(select(Event).where(Event.etype.in_(("RETURN", "SCRAP")), Event.status == "EFFECTIVE")):
        if e.id in reversed_ids or (e.etype == "SCRAP" and e.scrap_mode != "WHOLE"):
            continue
        rest = D(e.qty) - D(alloc_by_event.get(e.id, 0))
        aged = e.created_at < now - timedelta(days=7) or period_of(e.created_at) < cur_period
        key = f"EVT:{e.id}"
        if aged or key in rows:
            line = db.get(LedgerLine, e.line_id)
            rows[key] = {"period": period_of(e.created_at), "kind": "EVT_NO_ERP", "erp_doc_no": None, "erp_line_no": None,
                         "material_id": line.material_id, "erp_qty": D(0), "detail_qty": rest, "line_id": line.id,
                         "event_id": e.id, "owner_hint": line.issued_by}
    for line in db.scalars(select(LedgerLine).where(LedgerLine.level == 1, LedgerLine.erp_line_id.is_(None),
                                                    LedgerLine.voided.is_(False))):
        if line.created_at < now - timedelta(days=7) or period_of(line.created_at) < cur_period:
            rows[f"LINE:{line.id}"] = {"period": period_of(line.created_at), "kind": "LINE_NO_ERP", "erp_doc_no": None,
                                      "erp_line_no": None, "material_id": line.material_id, "erp_qty": D(0),
                                      "detail_qty": D(line.qty), "line_id": line.id, "owner_hint": line.issued_by}
    return rows


def run(db: Session) -> dict:
    rows = _compute(db)
    existing = {d.key: d for d in db.scalars(select(ReconDiff))}
    keepers = users_with_role(db, "KEEPER")
    opened = 0
    for key, r in rows.items():
        diff = D(r["erp_qty"]) - D(r["detail_qty"])
        d = existing.get(key)
        if d is None:
            if diff == 0:
                continue
            owner = r.get("owner_hint") or (keepers[0].id if keepers else None)
            d = ReconDiff(key=key, period=r["period"], kind=r["kind"], erp_doc_no=r["erp_doc_no"],
                          erp_line_no=r["erp_line_no"], line_id=r.get("line_id"), event_id=r.get("event_id"),
                          material_id=r["material_id"], owner_id=owner)
            db.add(d)
            db.flush()
            if owner:
                add_todo(db, owner, "RECON_CLAIM", f"对账差异待认领：{KIND_NAMES[r['kind']]} {r['erp_doc_no'] or key}",
                         "diff", d.id, due_hours=48)
            opened += 1
        d.kind, d.erp_qty, d.detail_qty, d.diff = r["kind"], r["erp_qty"], r["detail_qty"], diff
        d.line_id = r.get("line_id")
        if diff == 0 and d.state != "RESOLVED":
            d.state, d.resolved_at = "RESOLVED", clock.now()
            if not d.reason_code:
                d.note = d.note or "系统自动平账（单据已补齐）"
            close_todos(db, "diff", d.id)
        elif diff != 0 and d.state == "RESOLVED":
            d.state, d.resolved_at = ("CLAIMED" if d.claimed_at else "OPEN"), None
    db.flush()
    return {"checked": len(rows), "opened": opened}


def claim(db: Session, op: User, d: ReconDiff) -> ReconDiff:
    require_role(op, "KEEPER")
    if d.state == "RESOLVED":
        raise BizError("该差异已平")
    d.owner_id, d.claimed_at, d.state = op.id, clock.now(), "CLAIMED"
    close_todos(db, "diff", d.id, ("RECON_CLAIM",))
    return d


def resolve(db: Session, op: User, d: ReconDiff, reason_code: str, method: str, ref: str | None, note: str | None) -> ReconDiff:
    require_role(op, "KEEPER", "FINANCE")
    if reason_code not in dict_codes(db, "RECON_REASON"):
        raise BizError("请选择差异原因码（不允许「其他」）")
    if method not in ("REVERSAL", "ADJUST", "BIND", "ALLOC"):
        raise BizError("消差方式不正确")
    if method == "ADJUST":
        doc = db.scalar(select(ErpDoc).where(ErpDoc.no == (ref or "")))
        if not doc or doc.doc_type != "TZ":
            raise BizError("ERP 侧差错须引用调整单号")
        if not doc.finance_approved:
            raise BizError("调整单尚未经财务审核")
    if method == "REVERSAL":
        ev = db.get(Event, int(ref)) if (ref or "").isdigit() else None
        if not ev or ev.etype != "REVERSAL" or ev.status != "EFFECTIVE":
            raise BizError("中台侧差错须引用已复核生效的冲销事件编号")
    d.reason_code, d.method, d.method_ref, d.note = reason_code, method, ref, note
    if not d.claimed_at:
        d.owner_id, d.claimed_at = op.id, clock.now()
    run(db)
    db.refresh(d)
    if d.diff != 0:
        raise BizError(f"差额仍为 {d.diff}，账未平，不能关闭；请先完成冲销 / 调整单 / 补登 / 分摊")
    return d


def close_period(db: Session, op: User, period: str) -> PeriodClose:
    require_role(op, "FINANCE")
    if period >= period_of(clock.today()):
        raise BizError("只能关已结束的月份")
    run(db)
    open_cnt = db.scalar(select(func.count()).select_from(ReconDiff).where(ReconDiff.period <= period,
                                                                         ReconDiff.diff != 0))
    if open_cnt:
        raise BizError(f"{period} 及之前还有 {open_cnt} 条差额不为 0 的差异，账不平不能关账")
    pc = db.get(PeriodClose, period) or PeriodClose(period=period)
    pc.closed, pc.closed_by, pc.closed_at = True, op.id, clock.now()
    db.add(pc)
    close_todos(db, "period", int(period.replace("-", "")))
    return pc
