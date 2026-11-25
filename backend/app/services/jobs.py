"""定时任务。调度器每 15 分钟调用一次 tick()，按日/周/季/月的周期键判断是否需要执行。

不依赖 Redis：多实例部署时用 MySQL GET_LOCK 保证同一时刻只有一个实例在跑。
"""
import logging
from datetime import datetime, time, timedelta

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from .. import clock
from ..models import ErpDoc, ErpLine, Freeze, JobRun, LedgerLine, Project, Requisition, Todo, User
from ..perms import active_delegates, default_approver
from . import disposal, doc_sync, erp, ledger, master_sync, recon, wecom
from .common import add_todo, notify, period_of, quarter_of, users_with_role

log = logging.getLogger("rmlm.jobs")


def _manager_or_head(db: Session, user_id: int) -> int | None:
    u = db.get(User, user_id)
    if u and u.manager_id:
        return u.manager_id
    heads = users_with_role(db, "DEPT_HEAD") or users_with_role(db, "VP")
    return heads[0].id if heads and heads[0].id != user_id else None


# ------------------------------------------------------------------ 单步隔离
# 每一步在自己的保存点里执行：某一步出错只回滚这一步、给管理员派待办，其余步骤和每日 / 每周等执行标记照常
# （原来任何一步抛错，整次巡检全部回滚，连"今天已跑过"的标记也回滚，某一步持续出错时每日任务永远跑不成）。
STEPS = {
    "push": "企微推送补发", "doc_sync": "拉取金蝶单据", "auto_bound": "自动生成领用行", "approve": "审核提醒与升级",
    "urgent": "紧急领用 24 小时检查", "unbound": "ERP 有单无明细", "escalate_todo": "待办升级", "locked": "结存异常锁行",
    "recon": "每日对账", "material_sync": "金蝶物料同步", "contact_sync": "企微通讯录同步", "lab": "D02 实验结论预警",
    "overdue": "超期与冻结", "lapsed": "长周期季度确认过期", "weekly": "报废批次", "quarterly": "季度任务",
    "monthly": "关账提醒",
}


def _step(db: Session, key: str, fn, *args):
    try:
        with db.begin_nested():
            return fn(db, *args)
    except Exception as e:  # noqa: BLE001  单步失败不影响其他步骤
        log.exception("job step %s failed", key)
        msg = f"定时任务「{STEPS.get(key, key)}」出错：{str(e)[:150]}"
        for a in users_with_role(db, "ADMIN"):
            add_todo(db, a.id, "JOB_FAIL", msg, "jobstep", list(STEPS).index(key) if key in STEPS else 0, dedupe=True)
        return {"error": str(e)[:200]}


# ------------------------------------------------------------------ 每 15 分钟
def _approvals(db: Session) -> dict:
    """审核：4 小时提醒，24 小时升级上一级。"""
    now = clock.now()
    out = {"remind": 0, "escalate": 0}
    for req in db.scalars(select(Requisition).where(Requisition.status == "PENDING")):
        age = now - req.created_at
        if age >= timedelta(hours=4) and req.escalation == 0:
            req.escalation = 1
            for uid in [req.approver_id, *active_delegates(db, req.approver_id)]:
                notify(db, uid, f"领料申请 {req.no} 已超过 4 小时未审核，请尽快处理", "req", req.id)
            out["remind"] += 1
        if age >= timedelta(hours=24) and req.escalation < 2:
            req.escalation = 2
            approver = db.get(User, req.approver_id)
            up = None
            if approver.manager_id and approver.manager_id != req.user_id:
                up = approver.manager_id
            else:
                try:
                    cand = default_approver(db, approver)
                    up = cand.id if cand.id not in (req.user_id, approver.id) else None
                except Exception:
                    up = None
            if up:
                req.escalated_to_id = up
                add_todo(db, up, "APPROVE", f"【升级】领料申请 {req.no} 超 24 小时未审核", "req", req.id, level=2)
            out["escalate"] += 1
    db.flush()
    return out


def _urgent(db: Session) -> int:
    """紧急领用 24 小时未补齐。"""
    now, n = clock.now(), 0
    for line in db.scalars(select(LedgerLine).where(LedgerLine.urgent.is_(True), LedgerLine.urgent_resolved.is_(False),
                                                    LedgerLine.urgent_overdue.is_(False))):
        if line.urgent_deadline and line.urgent_deadline < now:
            line.urgent_overdue = True
            add_todo(db, line.holder_id, "URGENT_PENDING", f"紧急领用 {line.code} 超 24 小时未补审核 / ERP 单，已进挂账",
                     "line", line.id)
            mgr = _manager_or_head(db, line.holder_id)
            if mgr:
                notify(db, mgr, f"紧急领用 {line.code} 超 24 小时未补齐", "line", line.id)
            for k in users_with_role(db, "KEEPER"):
                add_todo(db, k.id, "URGENT_PENDING", f"紧急领用 {line.code} 待补 ERP 单", "line", line.id, dedupe=True)
            n += 1
    db.flush()
    return n


def _unbound(db: Session) -> int:
    """ERP 有单无明细：24 小时内补登。"""
    now, n = clock.now(), 0
    for ln, doc in db.execute(select(ErpLine, ErpDoc).join(ErpDoc).where(ErpDoc.doc_type.in_(("LL", "AB")),
                                                                          ErpDoc.posted.is_(True), ErpLine.qty > 0)):
        if doc.created_at < now - timedelta(hours=24) and not db.scalar(
            select(LedgerLine.id).where(LedgerLine.erp_line_id == ln.id)
        ):
            for k in users_with_role(db, "KEEPER"):
                if add_todo(db, k.id, "ERP_UNBOUND", f"ERP 有单无明细：{doc.no} 第 {ln.line_no} 行，请补登",
                            "erpline", ln.id, dedupe=True):
                    n += 1
    db.flush()
    return n


def _escalate_todos(db: Session) -> dict:
    """通用升级：48 小时未处理 → 直属上级；连续 3 天未关闭 → 月度经营会挂账。"""
    now = clock.now()
    out = {"todo_l2": 0, "todo_l3": 0}
    for t in db.scalars(select(Todo).where(Todo.state == "OPEN", Todo.is_notice.is_(False), Todo.level == 1,
                                           Todo.kind != "APPROVE")):
        age = now - t.created_at
        if age >= timedelta(hours=48) and t.escalated == 0:
            t.escalated = 1
            mgr = _manager_or_head(db, t.user_id)
            if mgr:
                owner = db.get(User, t.user_id)
                add_todo(db, mgr, t.kind, f"【二级】{owner.name}：{t.title}", t.ref_type, t.ref_id, level=2, parent_id=t.id)
            out["todo_l2"] += 1
        if age >= timedelta(days=3) and t.escalated < 2:
            t.escalated = 2
            for vp in users_with_role(db, "VP"):
                notify(db, vp.id, f"【三级挂账】{t.title}", t.ref_type, t.ref_id)
            out["todo_l3"] += 1
    db.flush()
    return out


def _lock_abnormal(db: Session) -> int:
    """结存异常锁行。"""
    n = 0
    lines = list(db.scalars(select(LedgerLine).where(LedgerLine.locked.is_(False), LedgerLine.voided.is_(False))))
    calcs = ledger.load(db, lines)
    for line in lines:
        c = calcs[line.id]
        if c.balance < 0 or c.balance > line.qty:
            line.locked, line.lock_reason = True, f"结存异常（{c.balance}）"
            for a in users_with_role(db, "ADMIN"):
                add_todo(db, a.id, "LOCKED_LINE", f"结存异常已锁行：{line.code}，48 小时内修正", "line", line.id,
                         due_hours=48)
            n += 1
    db.flush()
    return n


def quarter_hour(db: Session) -> dict:
    stats: dict = {
        "push": _step(db, "push", wecom.retry_pending),
        # 拉金蝶单据；按申请号自动生成领用行（含申请晚于单据审核通过的情况）
        "doc_sync": _step(db, "doc_sync", doc_sync.quarter_sync),
        "auto_bound": _step(db, "auto_bound", lambda d: erp.auto_bind(d)),
    }
    appr = _step(db, "approve", _approvals)
    stats.update(appr if "error" not in appr else {"remind": appr, "escalate": appr})
    stats["urgent"] = _step(db, "urgent", _urgent)
    stats["unbound"] = _step(db, "unbound", _unbound)
    esc = _step(db, "escalate_todo", _escalate_todos)
    stats.update(esc if "error" not in esc else {"todo_l2": esc, "todo_l3": esc})
    stats["locked"] = _step(db, "locked", _lock_abnormal)
    db.flush()
    return stats


# ------------------------------------------------------------------ 每天
def _lab(db: Session) -> int:
    """D02 可靠性实验：送实验室超过 30 天未回填结论 → 预警待办（48 小时未处理按通用规则升级直属上级）。"""
    from .lineops import LAB_CONCLUSION_DAYS, lab_pending

    now, n = clock.now(), 0
    for e in lab_pending(db):
        if e.created_at < now - timedelta(days=LAB_CONCLUSION_DAYS):
            line = db.get(LedgerLine, e.line_id)
            if line.voided:
                continue
            if add_todo(db, line.holder_id, "LAB_CONCLUSION",
                        f"{line.code} 送可靠性实验已超 {LAB_CONCLUSION_DAYS} 天未回填结论，请补填", "line", line.id,
                        dedupe=True):
                n += 1
    db.flush()
    return n


def _overdue(db: Session) -> dict:
    """超期待办与冻结、长周期到期提醒、项目结项检查。"""
    now, today = clock.now(), clock.today()
    out = {"overdue": 0, "frozen": 0, "renew": 0, "project": 0}
    lines = list(db.scalars(select(LedgerLine).where(LedgerLine.signed.is_(True), LedgerLine.voided.is_(False))))
    calcs = ledger.load(db, lines)
    for line in lines:
        c = calcs[line.id]
        if c.balance <= 0:
            continue
        if ledger.is_overdue(line, c, today):
            add_todo(db, line.holder_id, "OVERDUE", f"{line.code} 已超过预计归还日，请退库、报废或申请改归还日",
                     "line", line.id, dedupe=True)
            out["overdue"] += 1
            standard = (not line.long_cycle) or line.long_cycle_lapsed
            idle_since = c.last_action_at or line.created_at
            holder = db.get(User, line.holder_id)
            recently_released = db.scalar(select(Freeze.id).where(
                Freeze.user_id == holder.id, Freeze.released_at.is_not(None),
                Freeze.released_at > now - timedelta(days=14)).limit(1))
            if (standard and not holder.frozen and not recently_released
                    and (today - line.due_date).days > 14 and idle_since < now - timedelta(days=14)):
                holder.frozen = True
                db.add(Freeze(user_id=holder.id, line_id=line.id, reason=f"{line.code} 超期连续 2 周无动作"))
                notify(db, holder.id, f"因 {line.code} 超期连续 2 周无动作，您的新领用已被冻结", "line", line.id)
                if holder.manager_id:
                    add_todo(db, holder.manager_id, "UNFREEZE", f"{holder.name} 已被冻结新领用，请处理后解冻",
                             "user", holder.id, dedupe=True)
                out["frozen"] += 1
        if line.long_cycle and line.long_cycle_max and (line.long_cycle_max - today).days <= 30:
            if add_todo(db, line.holder_id, "RENEW_REMIND",
                        f"长周期物料 {line.code} 将于 {line.long_cycle_max} 到达最长期限，请续期或处置",
                        "line", line.id, dedupe=True):
                out["renew"] += 1
        project = db.get(Project, line.project_id)
        if project.closed:
            if add_todo(db, line.holder_id, "PROJECT_CLOSED", f"项目 {project.code} 已结项，{line.code} 须清零",
                        "line", line.id, dedupe=True):
                out["project"] += 1
    db.flush()
    return out


def _lapsed(db: Session) -> int:
    """长周期季度确认：季末 7 天内未确认 → 按标准类处理。"""
    now, n = clock.now(), 0
    for t in db.scalars(select(Todo).where(Todo.kind == "QCONFIRM", Todo.state == "OPEN", Todo.due_at < now)):
        line = db.get(LedgerLine, t.ref_id)
        if line and ledger.load_one(db, line).balance <= 0:
            # 待确认期间已用完 / 退库：无需再确认，关掉待办，也不按标准类处理
            t.state, t.done_at = "DONE", now
            continue
        if line and not line.long_cycle_lapsed:
            line.long_cycle_lapsed = True
            notify(db, line.holder_id, f"{line.code} 未按期做季度在用确认，已按标准类超期规则处理", "line", line.id)
            n += 1
    db.flush()
    return n


def daily(db: Session) -> dict:
    stats: dict = {
        "recon": _step(db, "recon", recon.run),
        "material_sync": _step(db, "material_sync", master_sync.daily_sync),
        "contact_sync": _step(db, "contact_sync", wecom.daily_sync),
        "lab": _step(db, "lab", _lab),
    }
    od = _step(db, "overdue", _overdue)
    stats.update(od if "error" not in od else {"overdue": od})
    stats["lapsed"] = _step(db, "lapsed", _lapsed)
    db.flush()
    return stats


def weekly(db: Session) -> dict:
    return {"batches": len(disposal.build_batches(db))}


def quarterly(db: Session) -> dict:
    """新季度第一天：发起长周期在用确认（7 天内完成）；记录季初超期快照。"""
    from ..models import OverdueSnapshot

    q = quarter_of(clock.today())
    lines = list(db.scalars(select(LedgerLine).where(LedgerLine.signed.is_(True), LedgerLine.voided.is_(False))))
    calcs = ledger.load(db, lines)
    n = 0
    for line in lines:
        c = calcs[line.id]
        if c.balance <= 0:
            continue
        if line.long_cycle:
            add_todo(db, line.holder_id, "QCONFIRM", f"长周期物料季度在用确认：{line.code}", "line", line.id,
                     due_at=clock.now() + timedelta(days=7), dedupe=True)
            n += 1
        if ledger.is_overdue(line, c):
            db.add(OverdueSnapshot(quarter=q, line_id=line.id))
    db.flush()
    return {"qconfirm": n}


def monthly(db: Session) -> dict:
    """每月初提醒财务：上月须在 3 日前完成中台关账。"""
    today = clock.today()
    prev = (today.replace(day=1) - timedelta(days=1))
    period = period_of(prev)
    due = datetime.combine(today.replace(day=3), time(23, 59))
    for f in users_with_role(db, "FINANCE"):
        add_todo(db, f.id, "CLOSE_REMIND", f"请在 3 日前完成 {period} 中台关账", "period",
                 int(period.replace("-", "")), due_at=due, dedupe=True)
    return {"period": period}


# ------------------------------------------------------------------ 调度入口
def _due(db: Session, name: str, key: str) -> bool:
    jr = db.get(JobRun, name)
    if jr is None:
        jr = JobRun(name=name)
        db.add(jr)
    if jr.last_key == key:
        return False
    jr.last_key, jr.last_run_at = key, clock.now()
    return True


def tick(db: Session) -> dict:
    out = {"quarter_hour": quarter_hour(db)}
    today = clock.today()
    if _due(db, "daily", today.isoformat()):
        out["daily"] = daily(db)
    y, w, _ = today.isocalendar()
    if _due(db, "weekly", f"{y}-W{w:02d}"):
        out["weekly"] = _step(db, "weekly", weekly)
    if _due(db, "quarterly", quarter_of(today)):
        out["quarterly"] = _step(db, "quarterly", quarterly)
    if _due(db, "monthly", period_of(today)):
        out["monthly"] = _step(db, "monthly", monthly)
    return out


def tick_locked(db: Session) -> dict | None:
    """生产环境调度入口：拿不到 MySQL 命名锁说明另一实例在跑，直接跳过。"""
    if db.bind.dialect.name == "mysql":
        got = db.execute(text("SELECT GET_LOCK('rmlm_tick', 0)")).scalar()
        if not got:
            return None
        try:
            return tick(db)
        finally:
            db.execute(text("SELECT RELEASE_LOCK('rmlm_tick')"))
    return tick(db)
