"""结存与状态推导（只读计算，不落库）。

结存 = 领用 − 耗用（含损耗）− 拆出 − 退库 − 报废；被有效冲销的事件不计入。
"""
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock
from ..models import DocAlloc, Event, LedgerLine, MaterialCode
from .common import D

STOCK_TYPES = {"CONSUME", "SPLIT_OUT", "RETURN", "SCRAP"}
ACTION_TYPES = STOCK_TYPES | {"SIGN", "HOLD", "MOVE", "TRANSFER_OK", "QCONFIRM", "CONCLUSION"}
STATUS_NAMES = {
    "S0": "待签收", "S1": "在制未使用", "S2": "使用中", "S3": "WIP 暂存", "S4": "已转交",
    "S5": "超期未结案", "S6": "待单据处置", "S7": "已结案", "VOID": "已作废",
}


@dataclass
class Calc:
    consumed: Decimal = D(0)  # 正常耗用
    loss: Decimal = D(0)  # 损耗（含残料）
    split_out: Decimal = D(0)
    returned: Decimal = D(0)
    scrapped: Decimal = D(0)  # 整件报废
    balance: Decimal = D(0)
    return_pending: Decimal = D(0)  # 退库未挂 ERP 单的数量
    scrap_pending: Decimal = D(0)  # 整件报废未挂 ERP 单的数量
    last_action_at: object = None
    proxy_events: int = 0
    events: list = field(default_factory=list)


def effective(events: list[Event]) -> list[Event]:
    reversed_ids = {e.reverses_id for e in events if e.etype == "REVERSAL" and e.status == "EFFECTIVE"}
    return [e for e in events if e.status == "EFFECTIVE" and e.id not in reversed_ids and e.etype != "REVERSAL"]


def alloc_map(db: Session, event_ids: list[int], lock: bool = False) -> dict[int, Decimal]:
    if not event_ids:
        return {}
    q = select(DocAlloc.event_id, DocAlloc.qty).where(DocAlloc.event_id.in_(event_ids))
    out: dict[int, Decimal] = {}
    for eid, qty in db.execute(q.with_for_update(read=True) if lock else q):
        out[eid] = out.get(eid, D(0)) + D(qty)
    return out


def calc(line: LedgerLine, events: list[Event], allocs: dict[int, Decimal]) -> Calc:
    c = Calc(events=events)
    for e in effective(events):
        q = D(e.qty)
        if e.etype == "CONSUME":
            if e.is_loss:
                c.loss += q
            else:
                c.consumed += q
        elif e.etype == "SPLIT_OUT":
            c.split_out += q
        elif e.etype == "RETURN":
            c.returned += q
            c.return_pending += max(D(0), q - allocs.get(e.id, D(0)))
        elif e.etype == "SCRAP":
            c.scrapped += q
            c.scrap_pending += max(D(0), q - allocs.get(e.id, D(0)))
        if e.etype in ACTION_TYPES:
            c.last_action_at = e.created_at
        if e.is_proxy:
            c.proxy_events += 1
    c.balance = D(line.qty) - c.consumed - c.loss - c.split_out - c.returned - c.scrapped
    return c


def is_overdue(line: LedgerLine, c: Calc, today: date | None = None) -> bool:
    today = today or clock.today()
    return line.signed and not line.voided and c.balance > 0 and today > line.due_date


def status_of(line: LedgerLine, c: Calc, today: date | None = None) -> str:
    if line.voided:
        return "VOID"
    if not line.signed:
        return "S0"
    if c.balance <= 0:
        if c.return_pending > 0 or c.scrap_pending > 0 or not line.urgent_resolved:
            return "S6"
        return "S7"
    if is_overdue(line, c, today):
        return "S5"
    last = None
    for e in effective(c.events):
        if e.etype in {"CONSUME", "HOLD", "MOVE", "TRANSFER_OK", "RETURN", "SCRAP", "SPLIT_OUT"}:
            last = e.etype
    if last in {"HOLD", "MOVE"}:
        return "S3"
    if any(e.etype == "TRANSFER_OK" for e in effective(c.events)):
        return "S4"
    if c.consumed + c.loss + c.split_out + c.returned + c.scrapped > 0:
        return "S2"
    return "S1"


def code_state(code: MaterialCode, line: LedgerLine, c: Calc) -> str:
    if code.replaced:
        return "B4"
    if c.balance <= 0:
        last = None
        for e in effective(c.events):
            if e.etype in STOCK_TYPES:
                last = e.etype
        return {"RETURN": "B6", "SCRAP": "B7"}.get(last, "B5")
    if c.consumed + c.loss + c.split_out + c.returned + c.scrapped > 0:
        return "B3"
    return "B2"


def load(db: Session, lines: list[LedgerLine], lock: bool = False) -> dict[int, Calc]:
    """批量计算一组行的结存。lock=True 用锁定读取（读最新已提交的数据）：动作校验结存时用，
    配合行锁，避免 MySQL 可重复读下读到本事务开始时的旧快照、把并发提交的事件漏算。"""
    ids = [l.id for l in lines]
    if not ids:
        return {}
    q = select(Event).where(Event.line_id.in_(ids)).order_by(Event.id)
    if lock:
        q = q.with_for_update(read=True)
    events = list(db.scalars(q))
    by_line: dict[int, list[Event]] = {i: [] for i in ids}
    for e in events:
        by_line[e.line_id].append(e)
    allocs = alloc_map(db, [e.id for e in events if e.etype in ("RETURN", "SCRAP")], lock=lock)
    return {l.id: calc(l, by_line[l.id], allocs) for l in lines}


def load_one(db: Session, line: LedgerLine, lock: bool = False) -> Calc:
    return load(db, [line], lock=lock)[line.id]
