"""领用行上的全部动作。每个动作 = 校验 + 追加一条事件（只增不改）。"""
from datetime import date
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..dicts import USE_DESTS
from ..errors import BizError, forbid
from ..models import ErpLine, Event, LedgerLine, Location, MaterialCode, User
from ..perms import active_delegates, default_approver, has_role, proxy_check
from . import ledger
from . import wms
from .common import D, add_months, add_todo, close_todos, dict_codes, notify, users_with_role


def _require_photos(photos: list[int] | None, what: str) -> list[int]:
    if not photos:
        raise BizError(f"{what}必须拍照留证")
    return photos


def _guard(line: LedgerLine, need_signed: bool = True) -> None:
    if line.voided:
        raise BizError("该行已作废")
    if line.locked:
        raise BizError(f"该行已锁定（{line.lock_reason or '结存异常'}），请联系 IT 处理")
    if need_signed and not line.signed:
        raise BizError("该行尚未签收")


def _event(db: Session, line: LedgerLine, etype: str, op: User, is_proxy: bool = False, **kw) -> Event:
    e = Event(line_id=line.id, code=line.code, etype=etype, operator_id=op.id,
              holder_id=line.holder_id, is_proxy=is_proxy, **kw)
    db.add(e)
    db.flush()
    if is_proxy:
        notify(db, line.holder_id, f"{op.name} 代您登记了「{etype_name(etype)}」：{line.code}", "line", line.id)
    return e


def etype_name(t: str) -> str:
    return {
        "SIGN": "签收", "CONSUME": "耗用", "SPLIT_OUT": "拆出", "SPLIT_IN": "拆入", "HOLD": "暂存",
        "MOVE": "更新位置", "TRANSFER_REQ": "发起转交", "TRANSFER_OK": "转交确认", "TRANSFER_REJ": "转交拒绝",
        "RETURN": "退库", "SCRAP": "报废", "REVERSAL": "冲销", "QCONFIRM": "季度在用确认",
        "RENEW": "续期申请", "DUE_CHANGE": "改归还日申请", "CONCLUSION": "回填实验结论", "BIND_CODE": "补绑仓储码",
    }.get(t, t)


# D02 送实验室后多少天内必须回填结论（超期派预警待办）
LAB_CONCLUSION_DAYS = 30


def _operator(db: Session, line: LedgerLine, op: User) -> bool:
    holder = db.get(User, line.holder_id)
    return proxy_check(db, op, holder)


def _check_qty(qty, balance: Decimal) -> Decimal:
    q = D(qty)
    if q <= 0:
        raise BizError("数量必须大于 0")
    if q > balance:
        raise BizError(f"数量超过当前结存 {balance}")
    return q


# ------------------------------------------------------------------ 签收
def sign(db: Session, line: LedgerLine, op: User, photos: list[int], code: str | None = None,
         no_code_reason: str | None = None) -> Event:
    """本人签收。扫实物上的仓储码绑定到本行；标签破损、未赋码或批量交接时可不扫码，但必须选原因，事后补绑。"""
    _guard(line, need_signed=False)
    if line.signed:
        raise BizError("该行已签收")
    if op.id != line.holder_id:
        raise forbid("签收必须由使用人本人操作，不允许代登")
    if not line.urgent:
        erp = db.get(ErpLine, line.erp_line_id) if line.erp_line_id else None
        if not erp or not erp.doc.posted:
            raise BizError("ERP 单据尚未过账，不能签收")
    _require_photos(photos, "签收")
    extra = None
    if (code or "").strip():
        w = wms.check_for_line(db, code, line)
        wms.bind(db, w, line, line.qty)
        line.code = w.code
    elif no_code_reason in wms.NO_CODE_REASONS:
        extra = {"no_code": no_code_reason}
    else:
        raise BizError("请扫描实物上的仓储码；标签破损、未赋码或批量交接时请选择不扫码的原因")
    line.signed, line.signed_at = True, clock.now()
    e = _event(db, line, "SIGN", op, qty=line.qty, photos=photos, extra=extra)
    close_todos(db, "line", line.id, ("SIGN",))
    return e


def bind_code(db: Session, line: LedgerLine, op: User, code: str) -> Event:
    """补绑仓储码：签收时没扫码的行、仓储条码上线前发了 RM 码的行。持有人（或可代登的人）和领料专员可操作。"""
    _guard(line)
    if not has_role(op, "KEEPER"):
        _operator(db, line, op)
    if wms.bound_codes(db, [line.code]):
        raise BizError("本行已绑定仓储码")
    c = ledger.load_one(db, line)
    if c.balance <= 0:
        raise BizError("结存为 0，无需补绑")
    w = wms.check_for_line(db, code, line)
    old = line.code
    old_rec = db.scalar(select(MaterialCode).where(MaterialCode.code == old))
    layer = old_rec.layer if old_rec else 1
    if old_rec:
        old_rec.retired = True  # 旧 RM 码作废，再扫会提示改扫仓储码
    wms.bind(db, w, line, c.balance, layer=layer, parent_code=old_rec.parent_code if old_rec else None)
    line.code = w.code
    return _event(db, line, "BIND_CODE", op, op.id != line.holder_id and not has_role(op, "KEEPER"),
                  extra={"old": old, "new": w.code})


# ------------------------------------------------------------------ 使用 / 损耗
def consume(db: Session, line: LedgerLine, op: User, qty, dest: str | None, is_loss: bool,
            loss_reason: str | None, note: str | None, photos: list[int], ship_no: str | None = None,
            location_id: int | None = None) -> Event:
    _guard(line)
    proxy = _operator(db, line, op)
    c = ledger.load_one(db, line)
    q = _check_qty(qty, c.balance)
    extra, loc_id = None, None
    if is_loss:
        if loss_reason not in dict_codes(db, "LOSS_REASON"):
            raise BizError("请选择损耗原因")
        if loss_reason == "L5" and not (note or "").strip():
            raise BizError("损耗原因为「其他」时必须写说明")
    elif dest not in USE_DESTS:
        raise BizError("使用登记的去向只能选 D01 项目成品 / D02 可靠性实验 / D03 产线试用")
    elif dest == "D01":
        # 蓝图 4.1：做成样品或成品交付出货，须关联项目与出货单
        if not (ship_no or "").strip():
            raise BizError("去向为 D01 项目成品时必须填写出货单号")
        extra = {"ship_no": ship_no.strip()}
    elif dest == "D03":
        # 蓝图 4.1：在产线试跑，须填产线工位位置码
        loc_id = _location(db, location_id, ("STATION",)).id if location_id else None
        if not loc_id:
            raise BizError("去向为 D03 产线试用时必须选择产线工位")
    _require_photos(photos, "耗用")
    return _event(db, line, "CONSUME", op, proxy, qty=q, dest=dest if not is_loss else (dest or None),
                  is_loss=is_loss, loss_reason=loss_reason if is_loss else None, note=note, photos=photos,
                  location_id=loc_id, extra=extra)


def lab_pending(db: Session, line_ids: list[int] | None = None) -> list[Event]:
    """D02 可靠性实验耗用里还没回填结论的事件。"""
    q = select(Event).where(Event.etype == "CONSUME", Event.dest == "D02", Event.is_loss.is_(False),
                            Event.status == "EFFECTIVE")
    if line_ids is not None:
        q = q.where(Event.line_id.in_(line_ids or [0]))
    events = list(db.scalars(q))
    if not events:
        return []
    done = {(e.extra or {}).get("for") for e in db.scalars(
        select(Event).where(Event.etype == "CONCLUSION", Event.line_id.in_({e.line_id for e in events})))}
    reversed_ids = set(db.scalars(select(Event.reverses_id).where(Event.etype == "REVERSAL",
                                                                  Event.status == "EFFECTIVE")))
    return [e for e in events if e.id not in done and e.id not in reversed_ids]


def conclusion(db: Session, target: Event, op: User, text: str, photos: list[int] | None) -> Event:
    """D02 回填实验结论：追加一条结论事件，不改原耗用事件。"""
    if target.etype != "CONSUME" or target.dest != "D02" or target.is_loss or target.status != "EFFECTIVE":
        raise BizError("只有 D02 可靠性实验的耗用需要回填结论")
    line = db.get(LedgerLine, target.line_id)
    if line.voided:
        raise BizError("该行已作废")
    proxy = _operator(db, line, op)
    if target not in lab_pending(db, [line.id]):
        raise BizError("该实验已回填结论")
    if not (text or "").strip():
        raise BizError("请填写实验结论")
    e = _event(db, line, "CONCLUSION", op, proxy, note=text.strip(), photos=photos or None,
               extra={"for": target.id})
    if not lab_pending(db, [line.id]):
        close_todos(db, "line", line.id, ("LAB_CONCLUSION",))
    return e


# ------------------------------------------------------------------ 拆分换码
def split(db: Session, line: LedgerLine, op: User, pieces: list, loss_qty, loss_reason: str | None,
          note: str | None, photos: list[int], remainder_code: str | None = None) -> dict:
    """拆分换码：拆出的每份、余量各扫一张仓储预发的新标签；原码提交即失效。
    pieces: [{"qty": 0.4, "code": "仓储码"}, ...]"""
    _guard(line)
    proxy = _operator(db, line, op)
    code_rec = db.scalar(select(MaterialCode).where(MaterialCode.code == line.code))
    if code_rec and code_rec.layer >= 3:
        raise BizError("物料码已到第三层，不能再拆分；请退库后由仓储重新赋码")
    c = ledger.load_one(db, line)
    items = []
    for p in pieces or []:
        q, code = (D(p.get("qty")), p.get("code")) if isinstance(p, dict) else (D(p), None)
        items.append((q, (code or "").strip()))
    if not items or any(q <= 0 for q, _ in items):
        raise BizError("至少拆出一份，且每份数量大于 0")
    loss = D(loss_qty)
    if loss < 0:
        raise BizError("损耗不能为负")
    total = sum(q for q, _ in items)
    remainder = c.balance - total - loss
    if remainder < 0:
        raise BizError(f"拆出合计 + 损耗 超过当前结存 {c.balance}")
    if loss > 0 and loss_reason not in dict_codes(db, "LOSS_REASON"):
        raise BizError("有损耗时必须选择损耗原因")
    # 新码：每份一张，余量一张，都必须是仓储清单里没用过的码
    codes = [code for _, code in items] + ([(remainder_code or "").strip()] if remainder > 0 else [])
    if any(not k for k in codes):
        raise BizError("拆出的每一份" + ("和余量" if remainder > 0 else "") + "都要扫一张仓储预发的新标签")
    if len(set(codes)) != len(codes):
        raise BizError("同一张标签不能贴两份")
    wcodes = [wms.check_for_split(db, k, line) for k in codes]
    _require_photos(photos, "拆分")
    layer = (code_rec.layer if code_rec else 1) + 1
    if loss > 0:
        _event(db, line, "CONSUME", op, proxy, qty=loss, is_loss=True, loss_reason=loss_reason,
               note=note or "拆分时损耗", photos=photos)
    out = _event(db, line, "SPLIT_OUT", op, proxy, qty=total, note=note, photos=photos,
                 extra={"pieces": [str(q) for q, _ in items], "remainder": str(remainder)})
    now = clock.now()
    parent_id = line.id if line.level == 1 else line.parent_id
    children = []
    for (q, _), w in zip(items, wcodes):
        child = LedgerLine(
            code=w.code, root_id=line.root_id, parent_id=parent_id, level=2,
            requisition_id=line.requisition_id, req_item_id=line.req_item_id, project_id=line.project_id,
            material_id=line.material_id, qty=q, unit_price=line.unit_price, user_id=line.user_id,
            holder_id=line.holder_id, location_id=line.location_id, due_date=line.due_date,
            long_cycle=line.long_cycle, long_cycle_max=line.long_cycle_max, signed=True, signed_at=now,
            urgent=line.urgent, urgent_resolved=True, issued_by=line.issued_by, split_event_id=out.id,
        )
        db.add(child)
        db.flush()
        wms.bind(db, w, child, q, layer=layer, parent_code=line.code)
        db.add(Event(line_id=child.id, code=child.code, etype="SPLIT_IN", qty=q, operator_id=op.id,
                     holder_id=child.holder_id, is_proxy=proxy, photos=photos, extra={"from": line.code}))
        children.append(child)
    # 原码提交即失效：余量换贴新标签
    if code_rec:
        code_rec.replaced, code_rec.replaced_at = True, now
    old_code = line.code
    if remainder > 0:
        wms.bind(db, wcodes[-1], line, remainder, layer=layer, parent_code=old_code)
        line.code = wcodes[-1].code
    out.extra = {**(out.extra or {}), "children": [ch.code for ch in children], "new_code": line.code if remainder > 0 else None}
    db.flush()
    return {"children": [ch.code for ch in children], "remainder": str(remainder),
            "remainder_code": line.code if remainder > 0 else None}


# ------------------------------------------------------------------ 暂存 / 更新位置
def _location(db: Session, location_id: int, kinds: tuple[str, ...] = ("STOCK", "STATION")) -> Location:
    loc = db.get(Location, location_id) if location_id else None
    if not loc or not loc.active or loc.kind not in kinds:
        raise BizError("请选择有效的库位")
    return loc


def hold(db: Session, line: LedgerLine, op: User, location_id: int, note: str | None, photos: list[int]) -> Event:
    _guard(line)
    proxy = _operator(db, line, op)
    c = ledger.load_one(db, line)
    st = ledger.status_of(line, c)
    if st == "S5":
        raise BizError("超期未结案的行不能暂存，请先退库、报废或申请改归还日")
    if st == "S3":
        raise BizError("已在暂存中，换位置请用「更新位置」")
    if c.balance <= 0:
        raise BizError("结存为 0，无需暂存")
    loc = _location(db, location_id)
    _require_photos(photos, "暂存")
    line.location_id = loc.id
    return _event(db, line, "HOLD", op, proxy, location_id=loc.id, dest="D04", note=note, photos=photos)


def move(db: Session, line: LedgerLine, op: User, location_id: int, note: str | None, photos: list[int] | None) -> Event:
    _guard(line)
    proxy = _operator(db, line, op)
    c = ledger.load_one(db, line)
    if ledger.status_of(line, c) != "S3":
        raise BizError("只有暂存中的行可以更新位置")
    loc = _location(db, location_id)
    line.location_id = loc.id
    return _event(db, line, "MOVE", op, proxy, location_id=loc.id, note=note, photos=photos or None)


# ------------------------------------------------------------------ 转交
def _pending_transfer(db: Session, line: LedgerLine) -> Event | None:
    return db.scalar(select(Event).where(Event.line_id == line.id, Event.etype == "TRANSFER_REQ",
                                         Event.status == "PENDING"))


def transfer_request(db: Session, line: LedgerLine, op: User, to_user_id: int | None, reason: str,
                     external_name: str | None = None) -> Event:
    _guard(line)
    proxy = _operator(db, line, op)
    if not (reason or "").strip():
        raise BizError("请填写转交原因")
    external = (external_name or "").strip()
    to = None
    if not external:
        to = db.get(User, to_user_id) if to_user_id else None
        if not to or not to.active:
            raise BizError("受让人不存在或已停用")
        if to.id == line.holder_id:
            raise BizError("受让人不能是当前持有人")
    if ledger.load_one(db, line).balance <= 0:
        raise BizError("结存为 0，无需转交")
    if _pending_transfer(db, line):
        raise BizError("已有待确认的转交，请等待受让人处理")
    if external:
        # 蓝图 3.3：受让人不在通讯录时标"外部 / 待认领"，不得直接改持有人；由领料专员指派账号后再确认
        e = _event(db, line, "TRANSFER_REQ", op, proxy, reason=reason, dest="D07", status="PENDING",
                   extra={"external": external})
        for k in users_with_role(db, "KEEPER"):
            add_todo(db, k.id, "TRANSFER_ASSIGN", f"外部受让待认领：{line.code} → {external}", "event", e.id,
                     due_hours=48)
        return e
    e = _event(db, line, "TRANSFER_REQ", op, proxy, to_user_id=to.id, reason=reason, dest="D07", status="PENDING")
    add_todo(db, to.id, "TRANSFER_CONFIRM", f"请确认接收转交：{line.code}", "event", e.id)
    return e


def transfer_assign(db: Session, req: Event, op: User, to_user_id: int) -> Event:
    """领料专员把"外部 · 待认领"的转交指派给系统账号，之后走受让人确认。"""
    if not has_role(op, "KEEPER"):
        raise forbid("外部受让由领料专员认领指派")
    if req.etype != "TRANSFER_REQ" or req.status != "PENDING" or not (req.extra or {}).get("external"):
        raise BizError("只有待认领的外部转交可以指派")
    line = db.get(LedgerLine, req.line_id)
    _guard(line)
    to = db.get(User, to_user_id) if to_user_id else None
    if not to or not to.active:
        raise BizError("受让人不存在或已停用")
    if to.id == line.holder_id:
        raise BizError("受让人不能是当前持有人")
    req.status, req.reviewed_by, req.reviewed_at = "ASSIGNED", op.id, clock.now()
    close_todos(db, "event", req.id)
    e = Event(line_id=line.id, code=line.code, etype="TRANSFER_REQ", operator_id=req.operator_id,
              holder_id=line.holder_id, is_proxy=req.is_proxy, to_user_id=to.id, reason=req.reason, dest="D07",
              status="PENDING", extra={"external": req.extra["external"], "assigned_from": req.id,
                                       "assigned_by": op.id})
    db.add(e)
    db.flush()
    add_todo(db, to.id, "TRANSFER_CONFIRM", f"请确认接收转交：{line.code}", "event", e.id)
    notify(db, line.holder_id, f"外部转交已指派给 {to.name}，待其确认：{line.code}", "line", line.id)
    return e


def transfer_cancel(db: Session, req: Event, op: User) -> Event:
    """发起人（或持有人）撤回待确认的转交。"""
    if req.etype != "TRANSFER_REQ" or req.status != "PENDING":
        raise BizError("该转交已处理")
    line = db.get(LedgerLine, req.line_id)
    if op.id not in (req.operator_id, line.holder_id):
        raise forbid("只有发起人或持有人可以撤回转交")
    req.status, req.reviewed_by, req.reviewed_at = "CANCELLED", op.id, clock.now()
    close_todos(db, "event", req.id)
    if req.to_user_id and req.to_user_id != op.id:
        notify(db, req.to_user_id, f"{op.name} 已撤回转交：{line.code}", "line", line.id)
    return req


def transfer_decide(db: Session, req: Event, op: User, accept: bool) -> Event:
    if req.etype != "TRANSFER_REQ" or req.status != "PENDING":
        raise BizError("该转交已处理")
    if op.id != req.to_user_id:
        raise forbid("只有受让人本人可以确认")
    line = db.get(LedgerLine, req.line_id)
    _guard(line)
    req.status = "EFFECTIVE" if accept else "REJECTED"
    req.reviewed_by, req.reviewed_at = op.id, clock.now()
    close_todos(db, "event", req.id)
    if not accept:
        notify(db, req.holder_id, f"{op.name} 拒绝接收转交：{line.code}", "line", line.id)
        return req
    old_holder = line.holder_id
    line.holder_id = op.id  # 超期责任与升级对象随新持有人
    e = Event(line_id=line.id, code=line.code, etype="TRANSFER_OK", operator_id=op.id, holder_id=op.id,
              to_user_id=op.id, reason=req.reason, dest="D07", extra={"from": old_holder, "req": req.id})
    db.add(e)
    db.flush()
    notify(db, old_holder, f"{op.name} 已确认接收：{line.code}", "line", line.id)
    return e


# ------------------------------------------------------------------ 退库 / 报废
def return_(db: Session, line: LedgerLine, op: User, qty, note: str | None, photos: list[int] | None) -> Event:
    _guard(line)
    proxy = _operator(db, line, op)
    c = ledger.load_one(db, line)
    q = _check_qty(qty, c.balance)
    e = _event(db, line, "RETURN", op, proxy, qty=q, dest="D06", note=note, photos=photos or None)
    for k in users_with_role(db, "KEEPER"):
        add_todo(db, k.id, "RETURN_DOC", f"待开退料单：{line.code} 退 {q}", "event", e.id, due_hours=48)
    return e


def scrap(db: Session, line: LedgerLine, op: User, mode: str, qty, reason: str | None, box: str | None,
          note: str | None, photos: list[int]) -> Event:
    _guard(line)
    proxy = _operator(db, line, op)
    c = ledger.load_one(db, line)
    _require_photos(photos, "报废")
    if mode == "RESIDUE":
        # 路径 A：用完后的残料，按损耗清零；不隔离、不开 ERP 报废单
        if c.balance <= 0:
            raise BizError("结存已为 0")
        return _event(db, line, "CONSUME", op, proxy, qty=c.balance, is_loss=True, loss_reason="L2",
                      scrap_mode="RESIDUE", note=note or "残料直接报废", photos=photos)
    if mode != "WHOLE":
        raise BizError("报废方式只能是残料或整件")
    q = _check_qty(qty, c.balance)
    if reason not in dict_codes(db, "SCRAP_REASON"):
        raise BizError("请选择报废原因")
    box_loc = db.scalar(select(Location).where(Location.code == box, Location.kind == "SCRAP_BOX",
                                               Location.active.is_(True))) if box else None
    if not box_loc:
        raise BizError("整件报废必须选择报废暂存箱号")
    return _event(db, line, "SCRAP", op, proxy, qty=q, dest="D05", scrap_mode="WHOLE", scrap_reason=reason,
                  box=box_loc.code, location_id=box_loc.id, note=note, photos=photos)


# ------------------------------------------------------------------ 长周期：季度确认、续期；标准类：改归还日
def quarter_confirm(db: Session, line: LedgerLine, op: User, note: str | None, photos: list[int] | None) -> Event:
    _guard(line)
    proxy = _operator(db, line, op)
    if not line.long_cycle:
        raise BizError("只有长周期物料需要季度在用确认")
    e = _event(db, line, "QCONFIRM", op, proxy, note=note, photos=photos or None,
               extra={"quarter": f"{clock.today().year}-Q{(clock.today().month - 1) // 3 + 1}"})
    close_todos(db, "line", line.id, ("QCONFIRM",))
    line.long_cycle_lapsed = False
    holder = db.get(User, line.holder_id)
    if holder.manager_id:
        notify(db, holder.manager_id, f"{holder.name} 已确认长周期物料仍在使用：{line.code}", "line", line.id)
    return e


def _line_approver(db: Session, line: LedgerLine) -> User:
    return default_approver(db, db.get(User, line.holder_id))


def renew_request(db: Session, line: LedgerLine, op: User, months: int, reason: str) -> Event:
    _guard(line)
    proxy = _operator(db, line, op)
    if not line.long_cycle:
        raise BizError("只有长周期物料可以续期")
    if not 1 <= int(months) <= 12:
        raise BizError("每次续期 1–12 个月")
    if not (reason or "").strip():
        raise BizError("请填写续期原因")
    e = _event(db, line, "RENEW", op, proxy, reason=reason, status="PENDING", extra={"months": int(months)})
    add_todo(db, _line_approver(db, line).id, "LINE_APPROVE", f"续期审核：{line.code} 续 {months} 个月", "event", e.id,
             due_hours=24)
    return e


def due_change_request(db: Session, line: LedgerLine, op: User, new_date: date, reason: str) -> Event:
    _guard(line)
    proxy = _operator(db, line, op)
    if new_date <= clock.today():
        raise BizError("新的归还日必须晚于今天")
    if line.long_cycle and line.long_cycle_max and new_date > line.long_cycle_max:
        raise BizError("超过长周期最长期限，请走续期")
    if not (reason or "").strip():
        raise BizError("请填写原因")
    e = _event(db, line, "DUE_CHANGE", op, proxy, reason=reason, status="PENDING",
               extra={"new_date": new_date.isoformat()})
    add_todo(db, _line_approver(db, line).id, "LINE_APPROVE", f"改归还日审核：{line.code} → {new_date}", "event",
             e.id, due_hours=24)
    return e


def line_request_decide(db: Session, req: Event, op: User, approve: bool, note: str | None) -> Event:
    if req.etype not in ("RENEW", "DUE_CHANGE") or req.status != "PENDING":
        raise BizError("该申请已处理")
    line = db.get(LedgerLine, req.line_id)
    approver = _line_approver(db, line)
    if op.id == req.operator_id or op.id == line.holder_id:
        raise forbid("不能审核自己的申请")
    if op.id != approver.id and op.id not in active_delegates(db, approver.id):
        raise forbid("只有持有人的直属上级（或其代理人）可以审核")
    req.status = "EFFECTIVE" if approve else "REJECTED"
    req.reviewed_by, req.reviewed_at = op.id, clock.now()
    req.note = note
    close_todos(db, "event", req.id)
    if approve:
        if req.etype == "RENEW":
            months = int(req.extra["months"])
            base = max(line.due_date, clock.today())
            line.due_date = add_months(base, months)
            line.long_cycle_max = line.due_date
            line.long_cycle_lapsed = False
        else:
            line.due_date = date.fromisoformat(req.extra["new_date"])
    notify(db, req.operator_id, f"{etype_name(req.etype)}已{'通过' if approve else '驳回'}：{line.code}", "line", line.id)
    return req


# ------------------------------------------------------------------ 冲销（中台侧差错）
def reversal_request(db: Session, target: Event, op: User, reason: str) -> Event:
    if not has_role(op, "KEEPER"):
        raise forbid("冲销由领料专员发起")
    if target.etype not in ("CONSUME", "RETURN", "SCRAP", "SPLIT_OUT") or target.status != "EFFECTIVE":
        raise BizError("只能冲销有效的耗用 / 退库 / 报废 / 拆分事件")
    if db.scalar(select(Event.id).where(Event.reverses_id == target.id, Event.status.in_(("PENDING", "EFFECTIVE")))):
        raise BizError("该事件已有冲销")
    if not (reason or "").strip():
        raise BizError("请填写冲销原因")
    from .recon import period_closed

    if period_closed(db, target.created_at):
        raise BizError("该事件所在月份已关账，不能冲销")
    line = db.get(LedgerLine, target.line_id)
    e = Event(line_id=line.id, code=line.code, etype="REVERSAL", qty=target.qty, operator_id=op.id,
              holder_id=line.holder_id, reverses_id=target.id, reason=reason, status="PENDING")
    db.add(e)
    db.flush()
    for a in users_with_role(db, "ADMIN"):
        if a.id != op.id:
            add_todo(db, a.id, "REVERSAL_REVIEW", f"冲销复核：{line.code} 事件 #{target.id}", "event", e.id, due_hours=24)
    return e


def reversal_decide(db: Session, rev: Event, op: User, approve: bool, note: str | None) -> Event:
    if not has_role(op, "ADMIN"):
        raise forbid("冲销由 IT（系统管理员）复核")
    if rev.etype != "REVERSAL" or rev.status != "PENDING":
        raise BizError("该冲销已处理")
    if rev.operator_id == op.id:
        raise forbid("发起人和复核人不能是同一人")
    target = db.get(Event, rev.reverses_id)
    line = db.get(LedgerLine, rev.line_id)
    if approve and target.etype == "SPLIT_OUT":
        kids = list(db.scalars(select(LedgerLine).where(LedgerLine.split_event_id == target.id)))
        for k in kids:
            used = db.scalar(select(Event.id).where(Event.line_id == k.id, Event.etype != "SPLIT_IN",
                                                    Event.status != "REJECTED").limit(1))
            if used:
                raise BizError(f"子行 {k.code} 已发生业务，不能冲销该拆分")
        for k in kids:
            k.voided = True
            code = db.scalar(select(MaterialCode).where(MaterialCode.code == k.code))
            if code:
                code.replaced, code.replaced_at = True, clock.now()
    rev.status = "EFFECTIVE" if approve else "REJECTED"
    rev.reviewed_by, rev.reviewed_at, rev.note = op.id, clock.now(), note
    close_todos(db, "event", rev.id)
    if approve:
        c = ledger.load_one(db, line)
        if c.balance < 0 or c.balance > D(line.qty):
            line.locked, line.lock_reason = True, "冲销后结存异常"
    notify(db, rev.operator_id, f"冲销已{'通过' if approve else '驳回'}：{line.code}", "line", line.id)
    return rev
