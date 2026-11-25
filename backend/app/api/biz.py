"""业务接口：申请审核、ERP 单据、领用台账与动作、退库报废、对账关账、报表。"""
from datetime import date

from fastapi import APIRouter, Depends, File, UploadFile
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from .. import clock
from ..db import get_db
from ..errors import BizError, forbid
from ..models import ErpDoc, ErpLine, Event, LedgerLine, Material, PeriodClose, ReconDiff, Requisition, ScrapBatch, User
from ..perms import active_delegates, can_view_line, has_role, require_role, visible_user_ids
from ..security import current_user
from ..services import disposal, doc_sync, erp as erpsvc, ledger, lineops, recon, reports
from ..services import requisition as reqsvc
from ..services.k3cloud import K3Error
from ..services.recon import KIND_NAMES
from .serial import check_photos, erp_doc_out, get_or_404, line_out, req_out, s

router = APIRouter(prefix="/api")


# ================================================================== 申请与审核
class ReqItemIn(BaseModel):
    material_id: int
    qty: float


class ReqIn(BaseModel):
    project_id: int
    reason_code: str
    remark: str
    expected_return: date
    long_cycle: bool = False
    long_cycle_months: int | None = None
    urgent: bool = False
    urgent_note: str | None = None
    user_id: int | None = None
    items: list[ReqItemIn]


@router.post("/requisitions")
def create_req(body: ReqIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    r = reqsvc.create(db, user, body.model_dump())
    return req_out(db, r)


@router.get("/requisitions")
def list_reqs(scope: str = "mine", status: str = "", q: str = "", user: User = Depends(current_user),
              db: Session = Depends(get_db)):
    stmt = select(Requisition).order_by(Requisition.id.desc()).limit(300)
    if scope == "mine":
        stmt = stmt.where(or_(Requisition.user_id == user.id, Requisition.created_by == user.id))
    elif scope == "approve":
        principals = [user.id] + [d for d in _delegating_principals(db, user)]
        stmt = stmt.where(Requisition.status == "PENDING",
                          or_(Requisition.approver_id.in_(principals), Requisition.escalated_to_id == user.id),
                          Requisition.user_id != user.id)
    elif scope == "issue":
        require_role(user, "KEEPER")
        stmt = stmt.where(or_(Requisition.status == "APPROVED",
                              (Requisition.urgent.is_(True)) & (Requisition.status == "PENDING")))
    else:
        ids = visible_user_ids(db, user)
        if ids is not None:
            stmt = stmt.where(Requisition.user_id.in_(ids))
    if status:
        stmt = stmt.where(Requisition.status == status)
    if q:
        stmt = stmt.where(Requisition.no.like(f"%{q}%"))
    return [req_out(db, r) for r in db.scalars(stmt)]


def _delegating_principals(db: Session, user: User) -> list[int]:
    from ..models import Delegate

    t = clock.now()
    return list(db.scalars(select(Delegate.principal_id).where(Delegate.agent_id == user.id, Delegate.start_at <= t,
                                                              Delegate.end_at >= t)))


@router.get("/requisitions/{rid}")
def get_req(rid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    r = get_or_404(db, Requisition, rid, "申请")
    ids = visible_user_ids(db, user)
    if ids is not None and r.user_id not in ids and reqsvc.approver_capacity(db, user, r) is None:
        raise forbid()
    d = req_out(db, r, detail=True)
    d["can_approve"] = r.status == "PENDING" and reqsvc.approver_capacity(db, user, r) is not None
    d["can_cancel"] = r.status == "PENDING" and user.id in (r.user_id, r.created_by)
    return d


class DecideIn(BaseModel):
    approve: bool
    note: str | None = None


@router.post("/requisitions/{rid}/decide")
def decide_req(rid: int, body: DecideIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    r = reqsvc.decide(db, user, get_or_404(db, Requisition, rid, "申请"), body.approve, body.note)
    return req_out(db, r)


@router.post("/requisitions/{rid}/cancel")
def cancel_req(rid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    reqsvc.cancel(db, user, get_or_404(db, Requisition, rid, "申请"))
    return {"ok": True}


@router.post("/requisitions/{rid}/urgent-handover/{item_id}")
def urgent_handover(rid: int, item_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    line = reqsvc.urgent_handover(db, user, get_or_404(db, Requisition, rid, "申请"), item_id)
    return line_out(db, line, user)


# ================================================================== ERP 单据（只读镜像）
class ErpLineIn(BaseModel):
    line_no: int | None = None
    material_code: str
    qty: float
    unit_price: float | None = None
    project_code: str | None = None
    ref_line_no: int | None = None


class ErpDocIn(BaseModel):
    no: str
    doc_type: str
    doc_date: date
    posted: bool = True
    remark: str | None = None
    ref_doc_no: str | None = None
    lines: list[ErpLineIn]


@router.get("/erp/docs")
def list_docs(doc_type: str = "", q: str = "", user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_role(user, "KEEPER", "FINANCE", "WAREHOUSE", "ADMIN")
    stmt = select(ErpDoc).order_by(ErpDoc.id.desc()).limit(300)
    if doc_type:
        stmt = stmt.where(ErpDoc.doc_type == doc_type)
    if q:
        stmt = stmt.where(or_(ErpDoc.no.like(f"%{q}%"), ErpDoc.remark.like(f"%{q}%")))
    return [erp_doc_out(db, d) for d in db.scalars(stmt)]


@router.post("/erp/docs")
def create_doc(body: ErpDocIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    doc = erpsvc.save_doc(db, user, body.model_dump())
    recon.run(db)
    return erp_doc_out(db, doc)


@router.post("/erp/docs/import")
def import_docs(file: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    n = erpsvc.import_docs_xlsx(db, user, file.file)
    recon.run(db)
    return {"count": n}


@router.post("/erp/docs/{did}/posted")
def set_posted(did: int, posted: bool, user: User = Depends(current_user), db: Session = Depends(get_db)):
    doc = erpsvc.set_posted(db, user, get_or_404(db, ErpDoc, did, "单据"), posted)
    recon.run(db)
    return erp_doc_out(db, doc)


@router.post("/erp/docs/{did}/finance-approve")
def finance_approve(did: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    doc = erpsvc.finance_approve(db, user, get_or_404(db, ErpDoc, did, "单据"))
    recon.run(db)
    return erp_doc_out(db, doc)


@router.post("/erp/docs/{did}/receive")
def receive(did: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return erp_doc_out(db, erpsvc.warehouse_receive(db, user, get_or_404(db, ErpDoc, did, "单据")))


@router.get("/erp/docs/sync/status")
def erp_doc_sync_status(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """金蝶单据同步状态：是否已配置接口、最近同步时间与结果。"""
    return doc_sync.status(db)


@router.post("/erp/docs/sync")
def erp_doc_sync(full: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """立即从金蝶拉取单据并自动生成领用行（定时任务每 15 分钟也会做）。默认增量，full=true 全量。"""
    require_role(user, "KEEPER", "ADMIN")
    try:
        out = doc_sync.sync_docs(db, full=full)
    except NotImplementedError as e:
        raise BizError(str(e))
    except K3Error as e:
        raise BizError(str(e), 502)
    recon.run(db)
    return out


@router.get("/erp/suggestions")
def suggestions(user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_role(user, "KEEPER")
    return erpsvc.suggestions(db)


class BindIn(BaseModel):
    req_item_id: int
    erp_line_id: int


@router.post("/erp/bind")
def bind(body: BindIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    line = erpsvc.bind(db, user, body.req_item_id, body.erp_line_id)
    recon.run(db)
    return line_out(db, line, user)


# ================================================================== 领用台账
@router.get("/lines")
def list_lines(scope: str = "visible", status: str = "", q: str = "", project_id: int | None = None,
               user: User = Depends(current_user), db: Session = Depends(get_db)):
    lines = reports.scoped_lines(db, user)
    if scope == "mine":
        lines = [l for l in lines if l.holder_id == user.id]
    if project_id:
        lines = [l for l in lines if l.project_id == project_id]
    if q:
        mats = {m.id: m for m in db.scalars(select(Material))}
        lines = [l for l in lines if q in l.code or q in mats[l.material_id].name or q in mats[l.material_id].code]
    calcs = ledger.load(db, lines)
    out = [line_out(db, l, user, calcs[l.id]) for l in lines]
    if status == "active":
        out = [o for o in out if o["status"] not in ("S6", "S7", "VOID")]
    elif status:
        out = [o for o in out if o["status"] == status]
    return out


@router.get("/lines/by-code/{code}")
def line_by_code(code: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..models import MaterialCode

    code = code.strip()
    line = db.scalar(select(LedgerLine).where(LedgerLine.code == code))
    if line is None:
        mc = db.scalar(select(MaterialCode).where(MaterialCode.code == code))
        if mc is not None and mc.replaced:
            raise BizError(f"物料码 {code} 已拆分替换（B4），不能再扫；请扫新码")
        pending = _pending_sign_by_barcode(db, user, code)
        if pending is not None:
            d = line_out(db, pending, user, full=True)
            d["sign_code"] = code  # 前端据此直接打开签收，并带上这个码
            return d
        raise BizError(f"未找到物料码 {code}")
    if not can_view_line(db, user, line):
        raise forbid("无权查看该物料")
    return line_out(db, line, user, full=True)


def _pending_sign_by_barcode(db: Session, user: User, code: str) -> LedgerLine | None:
    """已接仓储：扫到的是还没签收的仓储条码 → 找本人名下同物料、待签收的行（有出库单号时按单号对）。"""
    from ..models import ErpDoc, ErpLine, Material
    from ..services import wms

    if not wms.enabled():
        return None
    bc = wms.lookup(code)
    if bc is None:
        return None
    mat = db.scalar(select(Material).where(Material.code == bc.material_code))
    if mat is None:
        return None
    cands = list(db.scalars(select(LedgerLine).where(LedgerLine.holder_id == user.id, LedgerLine.signed.is_(False),
                                                     LedgerLine.voided.is_(False), LedgerLine.material_id == mat.id)
                            .order_by(LedgerLine.id)))
    if bc.out_doc_no:
        docs = {l.id: db.scalar(select(ErpDoc.no).join(ErpLine).where(ErpLine.id == l.erp_line_id))
                for l in cands if l.erp_line_id}
        matched = [l for l in cands if docs.get(l.id) == bc.out_doc_no]
        cands = matched or [l for l in cands if l.erp_line_id is None]  # 紧急领用还没挂单的行
    if not cands:
        raise BizError(f"条码 {code}（物料 {bc.material_code}）不在你名下的待签收物料里")
    return cands[0]


def _line(db: Session, user: User, lid: int) -> LedgerLine:
    line = get_or_404(db, LedgerLine, lid, "领用行")
    if not can_view_line(db, user, line):
        raise forbid("无权查看该物料")
    return line


@router.get("/lines/{lid}")
def get_line(lid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return line_out(db, _line(db, user, lid), user, full=True)


class PhotoIn(BaseModel):
    photos: list[int] = []
    note: str | None = None


class ConsumeIn(PhotoIn):
    qty: float
    dest: str | None = None
    is_loss: bool = False
    loss_reason: str | None = None
    ship_no: str | None = None  # D01 出货单号
    location_id: int | None = None  # D03 产线工位


class SignIn(PhotoIn):
    codes: list[str] = []  # 已接仓储时：扫到的仓储条码（一个码一条领用行）


class SplitIn(PhotoIn):
    pieces: list[float]
    loss_qty: float = 0
    loss_reason: str | None = None


class LocIn(PhotoIn):
    location_id: int


class TransferIn(BaseModel):
    to_user_id: int | None = None
    reason: str
    external_name: str | None = None  # 受让人不在通讯录：外部 / 待认领


class QtyIn(PhotoIn):
    qty: float


class ScrapIn(PhotoIn):
    mode: str
    qty: float | None = None
    reason: str | None = None
    box: str | None = None


class RenewIn(BaseModel):
    months: int
    reason: str


class DueIn(BaseModel):
    new_date: date
    reason: str


def _done(db: Session, user: User, line: LedgerLine, extra: dict | None = None) -> dict:
    db.flush()
    db.refresh(line)
    d = line_out(db, line, user, full=True)
    if extra:
        d["result"] = extra
    return d


@router.post("/lines/{lid}/sign")
def sign(lid: int, body: SignIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    line = _line(db, user, lid)
    lines = lineops.sign(db, line, user, check_photos(db, body.photos), body.codes)
    return _done(db, user, line, {"lines": [{"id": l.id, "code": l.code, "qty": s(l.qty)} for l in lines]}
                 if len(lines) > 1 else None)


@router.get("/wms/barcodes/{code}")
def wms_barcode(code: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """签收扫码时预览仓储条码（物料、数量、状态），并标出是否已被中台使用。"""
    from ..models import MaterialCode
    from ..services import wms

    if not wms.enabled():
        raise BizError("未配置仓储接口")
    code = code.strip()
    bc = wms.lookup(code)
    if bc is None:
        raise BizError(f"仓储系统查不到条码 {code}")
    d = bc.as_dict()
    d["used"] = db.scalar(select(MaterialCode.id).where(MaterialCode.code == code)) is not None
    return d


@router.post("/lines/{lid}/consume")
def consume(lid: int, body: ConsumeIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    line = _line(db, user, lid)
    lineops.consume(db, line, user, body.qty, body.dest, body.is_loss, body.loss_reason, body.note,
                    check_photos(db, body.photos), ship_no=body.ship_no, location_id=body.location_id)
    return _done(db, user, line)


@router.post("/lines/{lid}/split")
def split(lid: int, body: SplitIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    line = _line(db, user, lid)
    res = lineops.split(db, line, user, body.pieces, body.loss_qty, body.loss_reason, body.note,
                        check_photos(db, body.photos))
    return _done(db, user, line, res)


@router.post("/lines/{lid}/hold")
def hold(lid: int, body: LocIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    line = _line(db, user, lid)
    lineops.hold(db, line, user, body.location_id, body.note, check_photos(db, body.photos))
    return _done(db, user, line)


@router.post("/lines/{lid}/move")
def move(lid: int, body: LocIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    line = _line(db, user, lid)
    lineops.move(db, line, user, body.location_id, body.note, check_photos(db, body.photos))
    return _done(db, user, line)


@router.post("/lines/{lid}/transfer")
def transfer(lid: int, body: TransferIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    line = _line(db, user, lid)
    lineops.transfer_request(db, line, user, body.to_user_id, body.reason, body.external_name)
    return _done(db, user, line)


@router.post("/transfers/{eid}/assign")
def transfer_assign(eid: int, to_user_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ev = get_or_404(db, Event, eid, "转交")
    lineops.transfer_assign(db, ev, user, to_user_id)
    return _done(db, user, db.get(LedgerLine, ev.line_id))


@router.post("/transfers/{eid}/cancel")
def transfer_cancel(eid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ev = get_or_404(db, Event, eid, "转交")
    lineops.transfer_cancel(db, ev, user)
    return _done(db, user, db.get(LedgerLine, ev.line_id))


@router.get("/transfers/external")
def external_transfers(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """待认领的外部转交（领料专员处理）。"""
    require_role(user, "KEEPER", "ADMIN")
    out = []
    for e in db.scalars(select(Event).where(Event.etype == "TRANSFER_REQ", Event.status == "PENDING",
                                            Event.to_user_id.is_(None)).order_by(Event.id)):
        line = db.get(LedgerLine, e.line_id)
        d = line_out(db, line, user)
        out.append({"event_id": e.id, "external": (e.extra or {}).get("external"), "reason": e.reason,
                    "created_at": s(e.created_at), "line": d})
    return out


class ConclusionIn(PhotoIn):
    text: str


@router.post("/events/{eid}/conclusion")
def conclusion(eid: int, body: ConclusionIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ev = get_or_404(db, Event, eid, "事件")
    line = _line(db, user, ev.line_id)
    lineops.conclusion(db, ev, user, body.text, check_photos(db, body.photos) if body.photos else None)
    return _done(db, user, line)


# ================================================================== 扫码统一识别
@router.get("/scan/{code:path}")
def scan(code: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..services import scan as scansvc

    return scansvc.resolve(db, user, code)


@router.post("/transfers/{eid}/decide")
def transfer_decide(eid: int, accept: bool, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ev = get_or_404(db, Event, eid, "转交")
    lineops.transfer_decide(db, ev, user, accept)
    line = db.get(LedgerLine, ev.line_id)
    return _done(db, user, line)


@router.post("/lines/{lid}/return")
def return_(lid: int, body: QtyIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    line = _line(db, user, lid)
    lineops.return_(db, line, user, body.qty, body.note, check_photos(db, body.photos))
    return _done(db, user, line)


@router.post("/lines/{lid}/scrap")
def scrap(lid: int, body: ScrapIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    line = _line(db, user, lid)
    lineops.scrap(db, line, user, body.mode, body.qty, body.reason, body.box, body.note, check_photos(db, body.photos))
    return _done(db, user, line)


@router.post("/lines/{lid}/qconfirm")
def qconfirm(lid: int, body: PhotoIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    line = _line(db, user, lid)
    lineops.quarter_confirm(db, line, user, body.note, check_photos(db, body.photos))
    return _done(db, user, line)


@router.post("/lines/{lid}/renew")
def renew(lid: int, body: RenewIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    line = _line(db, user, lid)
    lineops.renew_request(db, line, user, body.months, body.reason)
    return _done(db, user, line)


@router.post("/lines/{lid}/due-change")
def due_change(lid: int, body: DueIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    line = _line(db, user, lid)
    lineops.due_change_request(db, line, user, body.new_date, body.reason)
    return _done(db, user, line)


class ReviewIn(BaseModel):
    approve: bool
    note: str | None = None


@router.get("/line-requests")
def line_requests(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """待我审核的续期 / 改归还日申请。"""
    out = []
    for e in db.scalars(select(Event).where(Event.etype.in_(("RENEW", "DUE_CHANGE")), Event.status == "PENDING")):
        line = db.get(LedgerLine, e.line_id)
        approver = lineops._line_approver(db, line)
        if user.id in (approver.id, *active_delegates(db, approver.id)) and user.id != e.operator_id:
            out.append({"id": e.id, "etype": e.etype, "line_id": line.id, "code": line.code, "reason": e.reason,
                        "extra": e.extra, "operator": db.get(User, e.operator_id).name, "created_at": s(e.created_at),
                        "due_date": s(line.due_date)})
    return out


@router.post("/line-requests/{eid}/decide")
def line_request_decide(eid: int, body: ReviewIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ev = lineops.line_request_decide(db, get_or_404(db, Event, eid, "申请"), user, body.approve, body.note)
    return {"ok": True, "status": ev.status}


class ReversalIn(BaseModel):
    event_id: int
    reason: str


@router.post("/reversals")
def reversal_request(body: ReversalIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ev = lineops.reversal_request(db, get_or_404(db, Event, body.event_id, "事件"), user, body.reason)
    return {"id": ev.id, "status": ev.status}


@router.get("/reversals")
def reversals(status: str = "PENDING", user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_role(user, "ADMIN", "KEEPER", "FINANCE")
    rows = db.scalars(select(Event).where(Event.etype == "REVERSAL", Event.status == status).order_by(Event.id.desc()))
    out = []
    for e in rows:
        t = db.get(Event, e.reverses_id)
        out.append({"id": e.id, "line_id": e.line_id, "code": db.get(LedgerLine, e.line_id).code, "reason": e.reason,
                    "target": {"id": t.id, "etype": lineops.etype_name(t.etype), "qty": s(t.qty), "at": s(t.created_at)},
                    "operator": db.get(User, e.operator_id).name, "status": e.status, "created_at": s(e.created_at)})
    return out


@router.post("/reversals/{eid}/decide")
def reversal_decide(eid: int, body: ReviewIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    ev = lineops.reversal_decide(db, get_or_404(db, Event, eid, "冲销"), user, body.approve, body.note)
    recon.run(db)
    return {"ok": True, "status": ev.status}


# ================================================================== 退库分摊、报废批次
def _pending_out(db: Session, items: list[dict]) -> list[dict]:
    out = []
    for it in items:
        e = it["event"]
        line = db.get(LedgerLine, e.line_id)
        m = db.get(Material, line.material_id)
        root = db.get(LedgerLine, line.root_id or line.id)
        src = db.get(ErpLine, root.erp_line_id).doc.no if root and root.erp_line_id else None
        out.append({"event_id": e.id, "line_id": line.id, "code": line.code, "material_code": m.code,
                    "material": m.name, "unit": m.unit, "qty": s(e.qty), "rest": s(it["rest"]), "box": e.box,
                    "holder": db.get(User, e.holder_id).name, "source_doc": src, "created_at": s(e.created_at)})
    return out


@router.get("/returns/pending")
def pending_returns(user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_role(user, "KEEPER", "WAREHOUSE", "ADMIN")
    return _pending_out(db, disposal.pending_events(db, "RETURN"))


class AllocIn(BaseModel):
    erp_doc_id: int
    event_ids: list[int]


@router.post("/returns/allocate")
def allocate(body: AllocIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    doc = get_or_404(db, ErpDoc, body.erp_doc_id, "退料单")
    return disposal.allocate(db, user, doc, body.event_ids, "RET")


@router.get("/scrap/pending")
def pending_scrap(user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_role(user, "KEEPER", "WAREHOUSE", "ADMIN")
    return _pending_out(db, disposal.pending_events(db, "SCRAP"))


@router.get("/scrap/batches")
def batches(user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_role(user, "KEEPER", "WAREHOUSE", "ADMIN", "FINANCE")
    out = []
    for b in db.scalars(select(ScrapBatch).order_by(ScrapBatch.id.desc())):
        evs = [db.get(Event, i) for i in b.event_ids]
        doc = db.get(ErpDoc, b.erp_doc_id) if b.erp_doc_id else None
        out.append({"id": b.id, "no": b.no, "box": b.box, "week": b.week, "status": b.status,
                    "erp_doc_no": doc.no if doc else None, "received": doc.received if doc else False,
                    "items": [{"event_id": e.id, "code": e.code, "qty": s(e.qty), "reason": e.scrap_reason,
                               "material": db.get(Material, db.get(LedgerLine, e.line_id).material_id).name}
                              for e in evs]})
    return out


@router.post("/scrap/batches/build")
def build_batches(user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_role(user, "KEEPER")
    return {"created": [b.no for b in disposal.build_batches(db)]}


@router.post("/scrap/batches/{bid}/bind")
def bind_batch(bid: int, erp_doc_id: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    b = get_or_404(db, ScrapBatch, bid, "批次")
    return disposal.bind_batch(db, user, b, get_or_404(db, ErpDoc, erp_doc_id, "报废单"))


# ================================================================== 对账与关账
@router.post("/recon/run")
def run_recon(user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_role(user, "KEEPER", "FINANCE", "ADMIN")
    return recon.run(db)


@router.get("/recon/diffs")
def diffs(period: str = "", state: str = "", user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_role(user, "KEEPER", "FINANCE", "ADMIN", "DEPT_HEAD", "VP")
    stmt = select(ReconDiff).order_by(ReconDiff.id.desc())
    if period:
        stmt = stmt.where(ReconDiff.period == period)
    if state:
        stmt = stmt.where(ReconDiff.state == state)
    names = {u.id: u.name for u in db.scalars(select(User))}
    mats = {m.id: m for m in db.scalars(select(Material))}
    return [{"id": d.id, "key": d.key, "period": d.period, "kind": d.kind, "kind_name": KIND_NAMES.get(d.kind, d.kind),
             "erp_doc_no": d.erp_doc_no, "erp_line_no": d.erp_line_no, "line_id": d.line_id, "event_id": d.event_id,
             "material": (mats[d.material_id].name if d.material_id in mats else None),
             "erp_qty": s(d.erp_qty), "detail_qty": s(d.detail_qty), "diff": s(d.diff), "owner": names.get(d.owner_id),
             "claimed_at": s(d.claimed_at), "reason_code": d.reason_code, "method": d.method, "method_ref": d.method_ref,
             "note": d.note, "state": d.state, "escalation": d.escalation, "created_at": s(d.created_at),
             "resolved_at": s(d.resolved_at)} for d in db.scalars(stmt)]


@router.post("/recon/diffs/{did}/claim")
def claim(did: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    recon.claim(db, user, get_or_404(db, ReconDiff, did, "差异"))
    return {"ok": True}


class ResolveIn(BaseModel):
    reason_code: str
    method: str
    ref: str | None = None
    note: str | None = None


@router.post("/recon/diffs/{did}/resolve")
def resolve(did: int, body: ResolveIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    d = recon.resolve(db, user, get_or_404(db, ReconDiff, did, "差异"), body.reason_code, body.method, body.ref, body.note)
    return {"ok": True, "state": d.state}


@router.get("/recon/summary")
def recon_summary(period: str = "", user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_role(user, "KEEPER", "FINANCE", "ADMIN", "DEPT_HEAD", "VP")
    return recon.summary(db, period)


@router.get("/recon/periods")
def periods(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = {p.period: p for p in db.scalars(select(PeriodClose))}
    diff_periods = set(db.scalars(select(ReconDiff.period).distinct()))
    cur = clock.today()
    months = set(diff_periods) | set(rows)
    for i in range(1, 4):
        y, m = cur.year, cur.month - i
        while m <= 0:
            y, m = y - 1, m + 12
        months.add(f"{y}-{m:02d}")
    out = []
    for p in sorted(months, reverse=True):
        open_cnt = len([d for d in db.scalars(select(ReconDiff).where(ReconDiff.period == p, ReconDiff.diff != 0))])
        pc = rows.get(p)
        out.append({"period": p, "closed": bool(pc and pc.closed), "closed_at": s(pc.closed_at) if pc else None,
                    "open_diffs": open_cnt})
    return out


@router.post("/recon/close/{period}")
def close(period: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    pc = recon.close_period(db, user, period)
    return {"period": pc.period, "closed": pc.closed}


# ================================================================== 报表
@router.get("/reports/dashboard")
def dashboard(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return reports.dashboard(db, user)


@router.get("/reports/weekly")
def weekly(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return reports.weekly_hanging(db, user)


@router.get("/reports/projects")
def project_view(project_id: int | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    return reports.project_view(db, user, project_id)


@router.get("/reports/loss")
def loss(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return reports.loss_report(db, user)


@router.get("/reports/monthly")
def monthly(period: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_role(user, "KEEPER", "FINANCE", "ADMIN", "DEPT_HEAD", "VP")
    return reports.monthly_lists(db, period)


@router.get("/reports/quarterly")
def quarterly(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return reports.quarterly_lists(db, user)


# ================================================================== 历史基线（只读）
@router.get("/history")
def history_list(q: str = "", project: str = "", dest: str = "", batch: str = "",
                 user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..services import history

    require_role(user, "KEEPER", "ADMIN", "FINANCE", "DEPT_HEAD", "VP", "WAREHOUSE")
    return history.query(db, q, project, dest, batch)


@router.get("/history/rules")
def history_rules(user: User = Depends(current_user)):
    from ..services import history

    return {"rules": history.rules(), "columns": history.COLUMNS, "virtual_project": history.VIRTUAL_PROJECT}


@router.post("/history/import")
def history_import(file: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..services import history

    return history.import_xlsx(db, user, file.file)


@router.get("/history/check")
def history_check(batch: str = "", user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..services import history

    require_role(user, "KEEPER", "ADMIN", "FINANCE", "DEPT_HEAD", "VP", "WAREHOUSE")
    return history.check(db, batch)


# ================================================================== 双轨抽查
@router.get("/parallel")
def parallel_list(week: str = "", user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..models import ParallelCheck
    from ..services import parallel

    require_role(user, "KEEPER", "ADMIN", "FINANCE", "DEPT_HEAD", "VP")
    week = week or parallel.week_of(clock.today())
    rows = list(db.scalars(select(ParallelCheck).where(ParallelCheck.week == week).order_by(ParallelCheck.id)))
    return {"week": week, "rows": parallel.rows_out(db, rows), "summary": parallel.summary(db)}


@router.post("/parallel/sample")
def parallel_sample(week: str = "", user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..services import parallel

    week = week or parallel.week_of(clock.today())
    rows = parallel.sample(db, user, week)
    return {"week": week, "rows": parallel.rows_out(db, rows), "summary": parallel.summary(db)}


class ParallelIn(BaseModel):
    result: str
    note: str | None = None


@router.post("/parallel/{pid}")
def parallel_mark(pid: int, body: ParallelIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from ..models import ParallelCheck
    from ..services import parallel

    pc = parallel.mark(db, user, get_or_404(db, ParallelCheck, pid, "抽查记录"), body.result, body.note)
    return parallel.rows_out(db, [pc])[0]
