"""退库多对多分摊、整件报废批次。"""
from collections import defaultdict
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock
from ..errors import BizError
from ..models import DocAlloc, ErpDoc, ErpLine, Event, LedgerLine, ScrapBatch, User
from ..perms import require_role
from .common import D, close_todos, next_seq, qs


def _reversed_ids(db: Session) -> set[int]:
    return set(db.scalars(select(Event.reverses_id).where(Event.etype == "REVERSAL", Event.status == "EFFECTIVE")))


def _allocated(db: Session, *, erp_line_id: int | None = None, event_id: int | None = None) -> Decimal:
    q = select(func.coalesce(func.sum(DocAlloc.qty), 0))
    if erp_line_id:
        # 单据行已摊数量不算被冲销的事件：冲销后这部分单据数量空出来，可重新分摊给正确的事件
        q = q.where(DocAlloc.erp_line_id == erp_line_id)
        rev = _reversed_ids(db)
        if rev:
            q = q.where(DocAlloc.event_id.not_in(rev))
    else:
        q = q.where(DocAlloc.event_id == event_id)
    return D(db.scalar(q))


def _effective_ids(db: Session, etype: str) -> list[Event]:
    reversed_ids = _reversed_ids(db)
    evs = db.scalars(select(Event).where(Event.etype == etype, Event.status == "EFFECTIVE").order_by(Event.created_at, Event.id))
    return [e for e in evs if e.id not in reversed_ids]


def pending_events(db: Session, etype: str, scrap_whole_only: bool = True) -> list[dict]:
    out = []
    for e in _effective_ids(db, etype):
        if etype == "SCRAP" and scrap_whole_only and e.scrap_mode != "WHOLE":
            continue
        rest = D(e.qty) - _allocated(db, event_id=e.id)
        if rest > 0:
            out.append({"event": e, "rest": rest})
    return out


def _root_doc(db: Session, line_id: int) -> str | None:
    line = db.get(LedgerLine, line_id)
    root = db.get(LedgerLine, line.root_id or line.id)
    if root and root.erp_line_id:
        return db.get(ErpLine, root.erp_line_id).doc.no
    return None


def allocate(db: Session, op: User, doc: ErpDoc, event_ids: list[int], kind: str) -> dict:
    """按物料把 ERP 单据行分摊到所选事件：同一行先领先退（按事件时间先进先出），可跨多张领料单、多张退料单。"""
    require_role(op, "KEEPER")
    expected = {"RET": ("TL", "RETURN"), "SCRAP": ("BF", "SCRAP")}[kind]
    if doc.doc_type != expected[0]:
        raise BizError("单据类型与分摊类型不一致")
    if not doc.posted:
        # 蓝图：未挂 ERP 单据的行不得结案——未过账的单据在金蝶里还不算数，不能拿来核销
        raise BizError(f"单据 {doc.no} 在金蝶尚未过账，过账后才能分摊核销")
    events = [db.get(Event, i) for i in event_ids]
    rev = _reversed_ids(db)
    if not events or any(e is None or e.etype != expected[1] or e.status != "EFFECTIVE" or e.id in rev
                         for e in events):
        raise BizError("所选事件无效")
    if kind == "SCRAP" and any(e.scrap_mode != "WHOLE" for e in events):
        raise BizError("残料报废不开 ERP 报废单")
    events.sort(key=lambda e: (e.created_at, e.id))
    by_mat: dict[int, list[Event]] = defaultdict(list)
    for e in events:
        by_mat[db.get(LedgerLine, e.line_id).material_id].append(e)
    created, leftover = [], []
    for ln in doc.lines:
        rest = D(ln.qty) - _allocated(db, erp_line_id=ln.id)
        pool = by_mat.get(ln.material_id, [])
        for e in pool:
            if rest <= 0:
                break
            e_rest = D(e.qty) - _allocated(db, event_id=e.id)
            if e_rest <= 0:
                continue
            q = min(rest, e_rest)
            a = DocAlloc(kind=kind, erp_line_id=ln.id, event_id=e.id, qty=q, cross_doc=False, created_by=op.id)
            db.add(a)
            db.flush()
            created.append(a)
            rest -= q
            if D(e.qty) - _allocated(db, event_id=e.id) <= 0:
                close_todos(db, "event", e.id, ("RETURN_DOC",))
        if rest > 0:
            leftover.append({"line_no": ln.line_no, "unallocated": str(rest)})
    # 跨单匹配：这张单据（含以前的分摊）实际摊到的事件来自不止一张领料单
    all_allocs = list(db.scalars(select(DocAlloc).where(DocAlloc.erp_line_id.in_([ln.id for ln in doc.lines] or [0]))))
    sources = {_root_doc(db, db.get(Event, a.event_id).line_id) for a in all_allocs}
    for a in all_allocs:
        a.cross_doc = len(sources) > 1
    db.flush()
    from . import recon

    recon.run(db)
    return {"allocations": len(created), "leftover": leftover,
            "cross_doc": any(a.cross_doc for a in created)}


def iso_week(d) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def build_batches(db: Session) -> list[ScrapBatch]:
    """每周按箱号汇总待处置的整件报废，生成批次清单。"""
    in_batch: set[int] = set()
    for b in db.scalars(select(ScrapBatch)):
        in_batch.update(b.event_ids or [])
    groups: dict[str, list[int]] = defaultdict(list)
    for item in pending_events(db, "SCRAP"):
        e = item["event"]
        if e.id not in in_batch:
            groups[e.box or "未指定"].append(e.id)
    week = iso_week(clock.today())
    out = []
    for box, ids in groups.items():
        b = ScrapBatch(no=f"SB-{week}-{next_seq(db, 'SB' + week):03d}", box=box, week=week, event_ids=ids)
        db.add(b)
        out.append(b)
    db.flush()
    return out


def bind_batch(db: Session, op: User, batch: ScrapBatch, doc: ErpDoc) -> dict:
    """批次挂 ERP 报废单。报废单数量不够摊完整个批次时，批次保持未核销，可再挂下一张报废单；
    全部摊完才置为已核销（否则剩下的明细既不在新批次里、也挂不了单，永远停在"待单据处置"）。"""
    if batch.status == "BOUND":
        raise BizError("该批次已核销")
    # 批次生成后其中某笔报废被冲销：该笔不再需要报废单，只拿其余明细去摊（否则整批"所选事件无效"、永远挂不了单）
    rev = _reversed_ids(db)
    live = [i for i in batch.event_ids if i not in rev]
    if not live:
        raise BizError("该批次的整件报废已全部冲销，无需挂报废单")
    res = allocate(db, op, doc, live, "SCRAP")
    batch.erp_doc_id = doc.id
    rest = sum((D(db.get(Event, i).qty) - _allocated(db, event_id=i) for i in live), D(0))
    if rest <= 0:
        batch.status = "BOUND"
    res["batch_rest"] = qs(max(rest, D(0)))
    return res
