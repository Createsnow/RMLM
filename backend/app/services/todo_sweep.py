"""处置类待办的自动关闭。

这些待办不能手动关（"待办事项需完成对应操作后自动关闭"），而通用升级规则是 48 小时升级直属上级、
3 天进三级挂账通知研发副总。事情办完了（行已清零、归还日已延后、事件已冲销、单据已反审核、名下已清零……）
却不关，就会把已经处理好的事一路升级上去，周报三级挂账清单也越积越多。

两个入口：
- sweep_line：领用行上的动作做完后立即调用，待办当场消失；
- sweep：每 15 分钟巡检（在升级之前）兜底，覆盖不经过行动作的变化（改归还日审核、冲销复核、
  金蝶反审核、项目重新打开、离职人员名下被转走等）。
"""
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..models import ErpDoc, ErpLine, Event, LedgerLine, Project, Requisition, Todo
from . import ledger
from .common import D, close_todos
from .disposal import _allocated

# 挂在领用行上、条件消失即可关闭的待办
LINE_KINDS = ("OVERDUE", "MUST_RETURN", "PROJECT_CLOSED", "RENEW_REMIND", "LAB_CONCLUSION")


def _line_done_kinds(db: Session, line: LedgerLine, c: ledger.Calc) -> list[str]:
    today, now = clock.today(), clock.now()
    cleared = line.voided or c.balance <= 0
    done = []
    if cleared or not ledger.is_overdue(line, c, today):
        done.append("OVERDUE")  # 已清零，或改归还日 / 续期后不再超期
    if cleared:
        done += ["MUST_RETURN", "PROJECT_CLOSED", "RENEW_REMIND"]
    else:
        if not db.get(Project, line.project_id).closed:
            done.append("PROJECT_CLOSED")  # 项目重新打开
        if not line.long_cycle or not line.long_cycle_max or (line.long_cycle_max - today).days > 30:
            done.append("RENEW_REMIND")  # 已续期，离最长期限超过 30 天
    from .lineops import LAB_CONCLUSION_DAYS, lab_pending

    if line.voided or not any(e.created_at < now - timedelta(days=LAB_CONCLUSION_DAYS)
                              for e in lab_pending(db, [line.id])):
        done.append("LAB_CONCLUSION")  # 超 30 天未回填的 D02 耗用已回填或已被冲销
    return done


def _close_line(db: Session, line: LedgerLine, c: ledger.Calc) -> None:
    kinds = _line_done_kinds(db, line, c)
    if kinds:
        close_todos(db, "line", line.id, tuple(kinds))


def sweep_line(db: Session, line: LedgerLine) -> None:
    _close_line(db, line, ledger.load_one(db, line))


def sweep(db: Session) -> None:
    open_todos = list(db.scalars(select(Todo).where(Todo.state == "OPEN", Todo.is_notice.is_(False), Todo.kind.in_(
        LINE_KINDS + ("RETURN_DOC", "ERP_UNBOUND", "OFFBOARD", "ISSUE")))))
    # 1. 领用行上的
    line_ids = {t.ref_id for t in open_todos if t.ref_type == "line" and t.kind in LINE_KINDS}
    lines = list(db.scalars(select(LedgerLine).where(LedgerLine.id.in_(line_ids)))) if line_ids else []
    calcs = ledger.load(db, lines)
    for line in lines:
        _close_line(db, line, calcs[line.id])
    # 2. 待开退料单：退库事件已被冲销或已摊满退料单（摊满时 allocate 已关；这里兜底冲销的情况）
    reversed_ids = set(db.scalars(select(Event.reverses_id).where(Event.etype == "REVERSAL",
                                                                  Event.status == "EFFECTIVE")))
    ev_ids = {t.ref_id for t in open_todos if t.kind == "RETURN_DOC" and t.ref_type == "event"}
    for e in db.scalars(select(Event).where(Event.id.in_(ev_ids))) if ev_ids else []:
        if e.id in reversed_ids or e.status != "EFFECTIVE" or D(e.qty) - _allocated(db, event_id=e.id) <= 0:
            close_todos(db, "event", e.id, ("RETURN_DOC",))
    # 3. ERP 有单无明细：已补登、金蝶反审核 / 作废、或金蝶删了这一行（数量置 0）
    erp_ids = {t.ref_id for t in open_todos if t.kind == "ERP_UNBOUND" and t.ref_type == "erpline"}
    for ln, doc in (db.execute(select(ErpLine, ErpDoc).join(ErpDoc).where(ErpLine.id.in_(erp_ids))).all()
                    if erp_ids else []):
        bound = db.scalar(select(LedgerLine.id).where(LedgerLine.erp_line_id == ln.id).limit(1)) is not None
        if bound or not doc.posted or ln.qty <= 0:
            close_todos(db, "erpline", ln.id, ("ERP_UNBOUND",))
    # 4. 离职清零：名下已清零、没有待他确认的转交、没有未开完单的申请（下次通讯录同步时停用账号）
    from .wecom import offboard_blockers

    for uid in {t.ref_id for t in open_todos if t.kind == "OFFBOARD" and t.ref_type == "user"}:
        if not any(offboard_blockers(db, uid).values()):
            close_todos(db, "user", uid, ("OFFBOARD",))
    # 5. 待开单：已开够申请数量（含金蝶自动绑定）或申请已撤回
    from .requisition import close_issue_if_done

    req_ids = {t.ref_id for t in open_todos if t.kind == "ISSUE" and t.ref_type == "req"}
    for r in db.scalars(select(Requisition).where(Requisition.id.in_(req_ids))) if req_ids else []:
        close_issue_if_done(db, r)
    db.flush()

