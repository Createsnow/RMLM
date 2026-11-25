"""接口输出的序列化。"""
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..config import settings
from ..errors import BizError
from ..models import (ErpDoc, ErpLine, Event, LedgerLine, Location, Material, MaterialCode, Photo, Project, ReqItem,
                      Requisition, User)
from ..perms import ROLES, has_role, is_manager, proxy_check
from ..services import ledger
from ..services.common import D
from ..services.lineops import etype_name


def s(v):
    if isinstance(v, Decimal):
        return format(v.normalize(), "f")
    if hasattr(v, "isoformat"):
        return v.isoformat(sep=" ") if hasattr(v, "hour") else v.isoformat()
    return v


def user_out(u: User | None, db: Session | None = None) -> dict | None:
    if u is None:
        return None
    d = {"id": u.id, "username": u.username, "name": u.name, "title": u.title, "emp_no": u.emp_no,
         "manager_id": u.manager_id, "roles": sorted(u.role_set), "active": u.active, "frozen": u.frozen,
         "wecom_userid": u.wecom_userid}
    if db is not None:
        d["manager_name"] = u.manager.name if u.manager else None
        d["is_manager"] = is_manager(db, u)
        d["role_names"] = [ROLES.get(r, r) for r in sorted(u.role_set)]
    return d


def check_photos(db: Session, ids) -> list[int]:
    ids = [int(i) for i in (ids or [])]
    for i in ids:
        if not db.get(Photo, i):
            raise BizError("照片不存在，请重新上传")
    return ids


def req_out(db: Session, r: Requisition, detail: bool = False) -> dict:
    users = lambda i: db.get(User, i).name if i else None
    p = db.get(Project, r.project_id)
    d = {"id": r.id, "no": r.no, "user_id": r.user_id, "user": users(r.user_id), "created_by": users(r.created_by),
         "project": p.code, "project_name": p.name, "customer_code": r.customer_code, "reason_code": r.reason_code,
         "remark": r.remark, "expected_return": s(r.expected_return), "long_cycle": r.long_cycle,
         "long_cycle_months": r.long_cycle_months, "urgent": r.urgent, "urgent_note": r.urgent_note,
         "status": r.status, "approver": users(r.approver_id), "escalated_to": users(r.escalated_to_id),
         "escalation": r.escalation, "decided_by": users(r.decided_by), "decided_as": r.decided_as,
         "decided_at": s(r.decided_at), "decision_note": r.decision_note, "created_at": s(r.created_at)}
    d["items"] = []
    for it in r.items:
        m = db.get(Material, it.material_id)
        lines = list(db.scalars(select(LedgerLine).where(LedgerLine.req_item_id == it.id, LedgerLine.level == 1)))
        d["items"].append({"id": it.id, "material_id": m.id, "material_code": m.code, "material": m.name,
                           "spec": m.spec, "unit": m.unit, "qty": s(it.qty), "unit_price": s(m.unit_price),
                           "lines": [{"id": l.id, "code": l.code, "erp_bound": l.erp_line_id is not None,
                                      "urgent": l.urgent} for l in lines]})
    return d


def erp_doc_out(db: Session, doc: ErpDoc) -> dict:
    lines = []
    for ln in doc.lines:
        m = db.get(Material, ln.material_id)
        codes = list(db.scalars(select(LedgerLine.code).where(LedgerLine.erp_line_id == ln.id, LedgerLine.level == 1)
                                .order_by(LedgerLine.id)))
        bound = "、".join(codes) if codes else None
        lines.append({"id": ln.id, "line_no": ln.line_no, "material_code": m.code, "material": m.name, "unit": m.unit,
                      "qty": s(ln.qty), "unit_price": s(ln.unit_price), "project_code": ln.project_code,
                      "ref_line_no": ln.ref_line_no, "bound_line": bound})
    return {"id": doc.id, "no": doc.no, "doc_type": doc.doc_type, "doc_date": s(doc.doc_date), "posted": doc.posted,
            "remark": doc.remark, "ref_doc_no": doc.ref_doc_no, "finance_approved": doc.finance_approved,
            "received": doc.received, "received_at": s(doc.received_at), "source": doc.source, "lines": lines}


def _can_operate(db: Session, viewer: User, line: LedgerLine) -> bool:
    try:
        proxy_check(db, viewer, db.get(User, line.holder_id))
        return True
    except BizError:
        return False


def line_out(db: Session, line: LedgerLine, viewer: User, c=None, full: bool = False) -> dict:
    c = c or ledger.load_one(db, line)
    st = ledger.status_of(line, c)
    m = db.get(Material, line.material_id)
    p = db.get(Project, line.project_id)
    holder = db.get(User, line.holder_id)
    price = D(line.unit_price or 0)
    loc = db.get(Location, line.location_id) if line.location_id else None
    flags = []
    if line.urgent and not line.urgent_resolved:
        flags.append("紧急·待补")
    if line.long_cycle:
        flags.append("长周期" + ("（已按标准类）" if line.long_cycle_lapsed else ""))
    if holder.frozen:
        flags.append("冻结")
    if c.proxy_events:
        flags.append("代登")
    if line.self_issue:
        flags.append("自领自开")
    if st not in ("S6", "S7") and (c.scrap_pending > 0 or c.return_pending > 0):
        flags.append("待开单")
    if line.locked:
        flags.append("锁定")
    if any(e.etype == "TRANSFER_REQ" and e.status == "PENDING" and not e.to_user_id for e in c.events):
        flags.append("外部·待认领")
    wms_bound = db.scalar(select(MaterialCode.id).where(MaterialCode.code == line.code,
                                                        MaterialCode.source == "WMS")) is not None
    d = {
        "id": line.id, "code": line.code, "level": line.level, "parent_id": line.parent_id, "root_id": line.root_id,
        "material_id": m.id, "material_code": m.code, "material": m.name, "spec": m.spec, "unit": m.unit,
        "project_id": p.id, "project": p.code, "project_name": p.name, "customer_code": p.customer_code,
        "qty": s(D(line.qty)), "unit_price": s(line.unit_price), "user": db.get(User, line.user_id).name,
        "user_id": line.user_id, "holder": holder.name, "holder_id": holder.id,
        "location": loc.name if loc else None, "location_id": line.location_id, "due_date": s(line.due_date),
        "long_cycle_max": s(line.long_cycle_max), "signed": line.signed, "status": st,
        "status_name": ledger.STATUS_NAMES[st], "flags": flags, "overdue": st == "S5", "locked": line.locked,
        "consumed": s(c.consumed), "loss": s(c.loss), "split_out": s(c.split_out), "returned": s(c.returned),
        "scrapped": s(c.scrapped), "balance": s(c.balance), "balance_amount": str((max(c.balance, D(0)) * price).quantize(Decimal("0.01"))),
        "return_pending": s(c.return_pending), "scrap_pending": s(c.scrap_pending), "created_at": s(line.created_at),
        "voided": line.voided, "wms_bound": wms_bound,
        "code_kind": "WMS" if wms_bound else ("LINE" if line.code.startswith("LN") else "RM"),
    }
    if line.erp_line_id or line.root_id:
        root = db.get(LedgerLine, line.root_id or line.id)
        if root and root.erp_line_id:
            ln = db.get(ErpLine, root.erp_line_id)
            d["erp_doc_no"], d["erp_line_no"], d["erp_posted"] = ln.doc.no, ln.line_no, ln.doc.posted
    if not full:
        return d
    req = db.get(Requisition, line.requisition_id)
    d["requisition"] = {"id": req.id, "no": req.no, "reason_code": req.reason_code, "remark": req.remark,
                        "status": req.status}
    users = {}

    def uname(i):
        if i and i not in users:
            users[i] = db.get(User, i).name
        return users.get(i)

    reversed_ids = {e.reverses_id for e in c.events if e.etype == "REVERSAL" and e.status == "EFFECTIVE"}
    d["events"] = [{
        "id": e.id, "etype": e.etype, "etype_name": etype_name(e.etype), "qty": s(e.qty), "dest": e.dest,
        "is_loss": e.is_loss, "loss_reason": e.loss_reason, "scrap_mode": e.scrap_mode, "scrap_reason": e.scrap_reason,
        "box": e.box, "to_user": uname(e.to_user_id), "to_user_id": e.to_user_id, "reason": e.reason, "note": e.note,
        "operator": uname(e.operator_id), "holder": uname(e.holder_id), "is_proxy": e.is_proxy,
        "photos": e.photos or [], "status": e.status, "reverses_id": e.reverses_id, "reversed": e.id in reversed_ids,
        "extra": e.extra, "code": e.code, "created_at": s(e.created_at),
        "location": (db.get(Location, e.location_id).name if e.location_id else None),
    } for e in c.events]
    codes = list(db.scalars(select(MaterialCode).where(MaterialCode.line_id == line.id).order_by(MaterialCode.id)))
    d["codes"] = [{"code": k.code, "layer": k.layer, "parent_code": k.parent_code, "qty_init": s(k.qty_init),
                   "state": ledger.code_state(k, line, c), "replaced_at": s(k.replaced_at),
                   "source": k.source} for k in codes]
    d["children"] = [{"id": k.id, "code": k.code, "qty": s(k.qty), "voided": k.voided}
                     for k in db.scalars(select(LedgerLine).where(LedgerLine.parent_id == line.id))]
    can = _can_operate(db, viewer, line) and not line.locked and not line.voided
    active = line.signed and c.balance > 0
    pending_transfer = next((e for e in c.events if e.etype == "TRANSFER_REQ" and e.status == "PENDING"), None)
    d["pending_transfer"] = {
        "id": pending_transfer.id, "to_user": uname(pending_transfer.to_user_id),
        "external": (pending_transfer.extra or {}).get("external"), "reason": pending_transfer.reason,
        "operator_id": pending_transfer.operator_id,
    } if pending_transfer else None
    # D02 可靠性实验：未回填结论的耗用事件
    concluded = {(e.extra or {}).get("for") for e in c.events if e.etype == "CONCLUSION"}
    d["lab_pending"] = [e.id for e in ledger.effective(c.events)
                        if e.etype == "CONSUME" and e.dest == "D02" and not e.is_loss and e.id not in concluded]
    d["actions"] = {
        "sign": (not line.signed) and viewer.id == line.holder_id and not line.locked,
        "sign_needs_codes": (not line.signed) and settings.wms_enabled,
        "consume": can and active, "split": can and active, "return": can and active, "scrap": can and active,
        "hold": can and active and st not in ("S3", "S5"), "move": can and st == "S3",
        "transfer": can and active and pending_transfer is None,
        "confirm_transfer": bool(pending_transfer and pending_transfer.to_user_id
                                 and pending_transfer.to_user_id == viewer.id),
        "cancel_transfer": bool(pending_transfer and viewer.id in (pending_transfer.operator_id, line.holder_id)),
        "assign_transfer": bool(pending_transfer and not pending_transfer.to_user_id and has_role(viewer, "KEEPER")),
        "conclusion": can or viewer.id == line.holder_id,
        "qconfirm": can and active and line.long_cycle,
        "renew": can and active and line.long_cycle,
        "due_change": can and active,
        "reversal": has_role(viewer, "KEEPER"),
    }
    return d


def get_or_404(db: Session, model, ident, what: str = "记录"):
    obj = db.get(model, ident)
    if obj is None:
        from ..errors import not_found

        raise not_found(what)
    return obj
