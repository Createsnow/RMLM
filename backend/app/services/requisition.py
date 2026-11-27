"""领料申请、直属上级审核、紧急领用（先领后审）。"""
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..errors import BizError, forbid
from ..models import LedgerLine, Material, MaterialCode, Project, ReqItem, Requisition, User
from ..perms import active_delegates, default_approver, has_role, proxy_check, require_role
from . import wms
from .common import (D, add_months, add_todo, close_todos, dict_codes, new_code, new_line_no, new_no, notify,
                     users_with_role)


def create(db: Session, op: User, data: dict) -> Requisition:
    user = op
    if data.get("user_id") and int(data["user_id"]) != op.id:
        # 申请人必须是使用人本人；替别人申请按代登留痕
        user = db.get(User, int(data["user_id"]))
        if not user or not user.active:
            raise BizError("使用人不存在")
        proxy_check(db, op, user)
    if user.frozen:
        raise BizError(f"{user.name} 因超期未处理已被冻结新领用（含紧急领用），请先联系直属上级解冻")
    project = db.get(Project, int(data.get("project_id") or 0))
    if not project:
        raise BizError("请从项目档案中选择项目")
    if project.closed:
        raise BizError("该项目已结项，不能再领料")
    reason = data.get("reason_code")
    if reason not in dict_codes(db, "REASON"):
        raise BizError("请选择领料原因")
    remark = (data.get("remark") or "").strip()
    if not remark:
        raise BizError("明细备注必填（R01 请写明实际做的验证明细）")
    expected = date.fromisoformat(str(data.get("expected_return")))
    if expected < clock.today():
        raise BizError("预计归还日不能早于今天")
    long_cycle = bool(data.get("long_cycle"))
    months = None
    if long_cycle:
        months = int(data.get("long_cycle_months") or 0)
        if not 1 <= months <= 12:
            raise BizError("长周期预计周期为 1–12 个月")
        if expected > add_months(clock.today(), months):
            raise BizError("预计归还日不能超过长周期预计周期")
    items = data.get("items") or []
    if not items:
        raise BizError("至少填写一项物料")
    urgent = bool(data.get("urgent"))
    if urgent and not (data.get("urgent_note") or "").strip():
        raise BizError("紧急领用请填写口头同意情况（谁、何时同意）")
    approver = default_approver(db, user)
    req = Requisition(
        no=new_no(db, "REQ"), user_id=user.id, created_by=op.id, project_id=project.id,
        customer_code=project.customer_code, reason_code=reason, remark=remark, expected_return=expected,
        long_cycle=long_cycle, long_cycle_months=months, urgent=urgent, urgent_note=data.get("urgent_note"),
        approver_id=approver.id,
    )
    for it in items:
        m = db.get(Material, int(it.get("material_id") or 0))
        if not m:
            raise BizError("物料只能扫码或从基础资料选择")
        if not m.active:
            raise BizError(f"物料 {m.code} 已在金蝶禁用，不能再申请")
        q = D(it.get("qty"))
        if q <= 0:
            raise BizError("数量必须大于 0")
        req.items.append(ReqItem(material_id=m.id, qty=q))
    db.add(req)
    db.flush()
    _push_approval(db, req, approver.id)
    if op.id != user.id:
        notify(db, user.id, f"{op.name} 代您提交了领料申请 {req.no}", "req", req.id)
    return req


def _push_approval(db: Session, req: Requisition, approver_id: int) -> None:
    title = f"审核领料申请 {req.no}{'（紧急）' if req.urgent else ''}{'（长周期）' if req.long_cycle else ''}"
    add_todo(db, approver_id, "APPROVE", title, "req", req.id, due_hours=24)
    for agent in active_delegates(db, approver_id):
        if agent != req.user_id:
            add_todo(db, agent, "APPROVE", title + "【代理】", "req", req.id, due_hours=24)


def approver_capacity(db: Session, op: User, req: Requisition) -> str | None:
    if op.id == req.user_id:
        return None
    if op.id == req.approver_id:
        return "SELF"
    if op.id in active_delegates(db, req.approver_id):
        return "DELEGATE"
    if req.escalated_to_id and op.id == req.escalated_to_id:
        return "ESCALATED"
    return None


def decide(db: Session, op: User, req: Requisition, approve: bool, note: str | None) -> Requisition:
    if req.status != "PENDING":
        raise BizError("该申请已处理")
    if op.id == req.user_id:
        raise forbid("不能审核自己的申请")
    cap = approver_capacity(db, op, req)
    if not cap:
        raise forbid("您不是该申请的审核人")
    if not approve and not (note or "").strip():
        raise BizError("驳回请填写原因")
    if not approve and req.urgent and db.scalar(select(LedgerLine.id).where(LedgerLine.requisition_id == req.id).limit(1)):
        # 业务确认（2026-11-25）：先交接是凭审核人的口头同意，未通过审核就不可能领到实物，所以交接后只能通过
        raise BizError("该紧急申请已凭口头同意先交接了物料，只能审核通过；如有异议，请先与领料专员、使用人核实处理")
    req.status = "APPROVED" if approve else "REJECTED"
    req.decided_by, req.decided_as, req.decided_at, req.decision_note = op.id, cap, clock.now(), note
    close_todos(db, "req", req.id, ("APPROVE",))
    notify(db, req.user_id, f"领料申请 {req.no} 已{'通过' if approve else '驳回'}", "req", req.id)
    if approve:  # 已交接的紧急申请只能通过（见上），驳回时不会有领用行
        for l in db.scalars(select(LedgerLine).where(LedgerLine.requisition_id == req.id)):
            refresh_urgent(db, l)
        push_issue(db, req)
    return req


def issue_done(db: Session, req: Requisition) -> bool:
    """开单已完成：申请不再是"审核通过"（已撤回等），或每项物料绑定的金蝶领料单合计已开够申请数量。"""
    if req.status != "APPROVED":
        return True
    from .erp import issued_qty

    return all(issued_qty(db, it) >= D(it.qty) for it in req.items)


def push_issue(db: Session, req: Requisition) -> None:
    """蓝图 3.1 ②：审核通过即给领料专员派"待开单"待办；开够申请数量（自动或手工绑定金蝶单）后自动关闭。"""
    if issue_done(db, req):
        return
    for k in users_with_role(db, "KEEPER"):
        add_todo(db, k.id, "ISSUE", f"待开单：领料申请 {req.no}{'（紧急，已先交接）' if req.urgent else ''}",
                 "req", req.id, dedupe=True)


def close_issue_if_done(db: Session, req: Requisition) -> None:
    if issue_done(db, req):
        close_todos(db, "req", req.id, ("ISSUE",))


def can_cancel(db: Session, op: User, req: Requisition) -> bool:
    if req.status == "PENDING":
        return op.id in (req.user_id, req.created_by)
    if req.status == "APPROVED" and not issue_done(db, req):
        # 审核通过、还没开够的申请：本人、代申请人、领料专员、管理员可撤回剩余未开单部分（离职 / 调岗前清理用）
        return op.id in (req.user_id, req.created_by) or has_role(op, "KEEPER", "ADMIN")
    return False


def cancel(db: Session, op: User, req: Requisition) -> None:
    if req.status not in ("PENDING", "APPROVED"):
        raise BizError("只能撤回待审核或审核通过、还没开够单的申请")
    if req.status == "APPROVED" and issue_done(db, req):
        raise BizError("该申请已全部开单，不能撤回")
    if not can_cancel(db, op, req):
        raise forbid()
    if db.scalar(select(LedgerLine.id).where(LedgerLine.requisition_id == req.id,
                                             LedgerLine.erp_line_id.is_(None)).limit(1)):
        raise BizError("已紧急交接、还没补 ERP 单的申请不能撤回")
    if req.status == "PENDING" and db.scalar(select(LedgerLine.id).where(LedgerLine.requisition_id == req.id).limit(1)):
        raise BizError("已交接物料的申请不能撤回")
    # 审核通过、部分已开单的：已生成的领用行照常使用，只是剩余数量不再开单（之后的金蝶单不会再自动绑定）
    req.status = "CANCELLED"
    close_todos(db, "req", req.id)


def urgent_handover(db: Session, op: User, req: Requisition, item_id: int) -> LedgerLine:
    """紧急领用：领料专员凭口头同意先交接，24 小时内补齐审核与 ERP 单。"""
    require_role(op, "KEEPER")
    if not req.urgent:
        raise BizError("非紧急申请，请先审核再开单")
    if req.status not in ("PENDING", "APPROVED"):
        raise BizError("申请状态不允许交接")
    item = db.get(ReqItem, item_id)
    if not item or item.req_id != req.id:
        raise BizError("申请明细不存在")
    if db.scalar(select(LedgerLine.id).where(LedgerLine.req_item_id == item.id).limit(1)):
        raise BizError("该明细已交接或已开单")
    line = _new_line(db, op, req, item, qty=item.qty, erp_line=None)
    line.urgent, line.urgent_resolved = True, False
    line.urgent_deadline = clock.now() + timedelta(hours=24)
    add_todo(db, line.holder_id, "SIGN", f"请签收紧急领用物料 {line.code}", "line", line.id)
    return line


def _new_line(db: Session, op: User | None, req: Requisition, item: ReqItem, qty, erp_line) -> LedgerLine:
    """生成领用行。op 为空表示系统按金蝶单据自动生成（没有经手的领料专员）。"""
    m = db.get(Material, item.material_id)
    price = (erp_line.unit_price if erp_line is not None and erp_line.unit_price is not None else m.unit_price)
    line = LedgerLine(
        code=new_line_no(db) if wms.enabled() else new_code(db), level=1, requisition_id=req.id, req_item_id=item.id,
        erp_line_id=erp_line.id if erp_line is not None else None, project_id=req.project_id,
        material_id=item.material_id, qty=D(qty), unit_price=price, user_id=req.user_id, holder_id=req.user_id,
        due_date=req.expected_return, long_cycle=req.long_cycle,
        long_cycle_max=req.expected_return if req.long_cycle else None,
        issued_by=op.id if op else None, self_issue=(op is not None and op.id == req.user_id),
    )
    db.add(line)
    db.flush()
    line.root_id = line.id
    if not wms.enabled():  # 接了仓储：签收时扫仓储条码再建码
        db.add(MaterialCode(code=line.code, line_id=line.id, layer=1, qty_init=D(qty), source="RM"))
    db.flush()
    return line


def refresh_urgent(db: Session, line: LedgerLine) -> None:
    """紧急行补齐判定：申请已审核通过 + 已绑定 ERP 单 + ERP 已过账（2026-11-25 业务确认：必须审核通过才算补齐，
    驳回不算；口头同意后才会先交接，正常不会出现驳回）。"""
    if not line.urgent or line.level != 1:  # 拆出的子行跟随父行，不单独判定
        return
    req = db.get(Requisition, line.requisition_id)
    from ..models import ErpLine

    erp = db.get(ErpLine, line.erp_line_id) if line.erp_line_id else None
    ok = req.status == "APPROVED" and erp is not None and erp.doc.posted
    if ok and not line.urgent_resolved:
        line.urgent_resolved = True
        line.urgent_overdue = False
        close_todos(db, "line", line.id, ("URGENT_PENDING",))
    elif not ok and line.urgent_resolved:
        # 补挂的领料单在金蝶被反审核 / 作废：恢复"紧急·待补"；已过 24 小时的，下一次巡检照常进挂账
        line.urgent_resolved = False
