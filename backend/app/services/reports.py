"""看板与报表：验收门槛三项、观察指标、周报挂账、项目视图、损耗、月度与季度清单。"""
import random
from collections import defaultdict
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock
from ..config import settings
from ..models import (DictItem, DocAlloc, ErpDoc, ErpLine, Event, LedgerLine, Location, Material, MaterialCode,
                      OverdueSnapshot, Project, ReconDiff, Requisition, Todo, User)
from ..perms import visible_user_ids
from . import ledger
from .common import D, qs, quarter_of


def _pct(a, b) -> float | None:
    return round(float(a) / float(b) * 100, 1) if b else None


def _reversed_ids(db: Session) -> set[int]:
    return set(db.scalars(select(Event.reverses_id).where(Event.etype == "REVERSAL", Event.status == "EFFECTIVE")))


def scoped_lines(db: Session, user: User, include_void: bool = False) -> list[LedgerLine]:
    q = select(LedgerLine)
    if not include_void:
        q = q.where(LedgerLine.voided.is_(False))
    ids = visible_user_ids(db, user)
    if ids is not None:
        q = q.where((LedgerLine.holder_id.in_(ids)) | (LedgerLine.user_id.in_(ids)))
    return list(db.scalars(q.order_by(LedgerLine.id.desc())))


def dashboard(db: Session, user: User) -> dict:
    lines = scoped_lines(db, user)
    calcs = ledger.load(db, lines)
    today = clock.today()
    status_cnt: dict[str, int] = defaultdict(int)
    active = overdue = urgent = 0
    amount = Decimal(0)
    for l in lines:
        c = calcs[l.id]
        st = ledger.status_of(l, c, today)
        status_cnt[st] += 1
        if c.balance > 0 and l.signed:
            active += 1
            amount += c.balance * D(l.unit_price or 0)
        if st == "S5":
            overdue += 1
        if l.urgent and not l.urgent_resolved:
            urgent += 1
    line_ids = [l.id for l in lines]
    reversed_ids = _reversed_ids(db)
    # 代登率：已被冲销的事件不算（误登已更正）
    events = [e for e in db.scalars(select(Event).where(Event.line_id.in_(line_ids or [0]), Event.status == "EFFECTIVE",
                                                        Event.etype.notin_(("SIGN", "SPLIT_IN", "REVERSAL"))))
              if e.id not in reversed_ids]
    projects = {p.id: p for p in db.scalars(select(Project))}
    roots = [l for l in lines if l.level == 1]
    gate = {
        "project_link_rate": _pct(sum(1 for l in roots if projects[l.project_id].source == "ERP"), len(roots)),
        "code_bind_rate": _code_bind_rate(db, roots),
        "proxy_rate": _pct(sum(1 for e in events if e.is_proxy), len(events)),
    }
    gate["passed"] = (gate["project_link_rate"] in (None, 100.0)
                      and (gate["code_bind_rate"] is None or gate["code_bind_rate"] >= 95)
                      and (gate["proxy_rate"] is None or gate["proxy_rate"] < 10))
    users = {u.id: u for u in db.scalars(select(User))}
    mats = {m.id: m for m in db.scalars(select(Material))}
    # 原型「总览看板」：在制结存按项目、需要今天处理、最近流向事件
    by_project: dict[int, dict] = {}
    need_today, scrap_wait = [], 0
    for l in lines:
        c = calcs[l.id]
        st = ledger.status_of(l, c, today)
        if st == "S6" and c.scrap_pending > 0:
            scrap_wait += 1
        if c.balance > 0 and l.signed:
            p = projects[l.project_id]
            b = by_project.setdefault(l.project_id, {"project": p.code, "name": p.name, "lines": 0,
                                                     "balance_amount": Decimal(0)})
            b["lines"] += 1
            b["balance_amount"] += c.balance * D(l.unit_price or 0)
        if st == "S5" or (st == "S6" and c.scrap_pending > 0):
            need_today.append(line_brief(db, l, c, users, mats, projects))
    for b in by_project.values():
        b["balance_amount"] = str(b["balance_amount"].quantize(Decimal("0.01")))
    start_of_day = datetime.combine(today, datetime.min.time())
    today_events = db.scalar(select(func.count()).select_from(Event).where(
        Event.line_id.in_(line_ids or [0]), Event.created_at >= start_of_day,
        Event.etype.notin_(("SPLIT_IN", "REVERSAL"))))
    recon_open = db.scalar(select(func.count()).select_from(ReconDiff).where(ReconDiff.state != "RESOLVED",
                                                                             ReconDiff.diff != 0))
    return {
        "today": today.isoformat(),
        "counts": {"lines": len(lines), "active": active, "overdue": overdue, "urgent_pending": urgent,
                   "doc_pending": status_cnt.get("S6", 0), "to_sign": status_cnt.get("S0", 0),
                   "scrap_pending": scrap_wait, "recon_open": recon_open, "today_events": today_events,
                   "projects": len(by_project), "balance_amount": str(amount.quantize(Decimal("0.01")))},
        "status": dict(status_cnt),
        "gate": gate,
        "observe": observe(db, lines, calcs),
        "by_project": sorted(by_project.values(), key=lambda x: (-x["lines"], x["project"])),
        "need_today": need_today[:20],
        "recent_events": recent_events(db, lines, users, mats),
        "todos": db.scalar(select(func.count()).select_from(Todo).where(Todo.user_id == user.id, Todo.state == "OPEN")),
    }


def _code_bind_rate(db: Session, roots: list[LedgerLine]) -> float | None:
    """码绑定率 = 已绑定实物码的行 / 已签收行。接了仓储后只认仓储条码（签收时扫码绑定），本系统临时码不算。"""
    signed = [l for l in roots if l.signed]
    if not settings.wms_enabled:
        return _pct(sum(1 for l in signed if l.code), len(signed))
    ids = [l.id for l in signed]
    bound = set(db.scalars(select(MaterialCode.line_id).where(MaterialCode.line_id.in_(ids or [0]),
                                                              MaterialCode.layer == 1, MaterialCode.source == "WMS")))
    return _pct(sum(1 for l in signed if l.id in bound), len(signed))


def recent_events(db: Session, lines: list[LedgerLine], users: dict, mats: dict, limit: int = 10) -> list[dict]:
    """最近流向事件（只增不改，每条都有经手人与时间）。"""
    from .lineops import etype_name

    by_id = {l.id: l for l in lines}
    evs = list(db.scalars(select(Event).where(Event.line_id.in_(list(by_id) or [0]), Event.status == "EFFECTIVE",
                                              Event.etype.notin_(("SPLIT_IN", "REVERSAL")))
                          .order_by(Event.id.desc()).limit(limit)))
    allocs: dict[int, list[str]] = {}
    for eid, no in db.execute(select(DocAlloc.event_id, ErpDoc.no).join(ErpLine, ErpLine.id == DocAlloc.erp_line_id)
                              .join(ErpDoc, ErpDoc.id == ErpLine.doc_id)
                              .where(DocAlloc.event_id.in_([e.id for e in evs] or [0]))):
        allocs.setdefault(eid, [])
        if no not in allocs[eid]:
            allocs[eid].append(no)
    locs = {x.id: x.name for x in db.scalars(select(Location))}
    out = []
    for e in evs:
        l = by_id[e.line_id]
        m = mats[l.material_id]
        where = ((locs.get(e.location_id) if e.location_id else None) or (e.extra or {}).get("place")
                 or (users[e.to_user_id].name if e.to_user_id else None))
        erp = None
        if e.etype == "SIGN" and l.erp_line_id:
            erp = db.get(ErpLine, l.erp_line_id).doc.no
        elif e.etype in ("RETURN", "SCRAP"):
            erp = "、".join(allocs.get(e.id, [])) or None
        out.append({"id": e.id, "line_id": l.id, "code": e.code, "etype": e.etype, "etype_name": etype_name(e.etype),
                    "qty": qs(e.qty), "unit": m.unit, "material": m.name, "dest": e.dest, "where": where,
                    "operator": users[e.operator_id].name, "is_proxy": e.is_proxy, "erp_doc_no": erp,
                    "needs_doc": e.etype in ("RETURN", "SCRAP") and e.scrap_mode != "RESIDUE" and not erp,
                    "created_at": e.created_at.isoformat(sep=" ")})
    return out


def observe(db: Session, lines: list[LedgerLine], calcs) -> dict:
    today = clock.today()
    diffs = list(db.scalars(select(ReconDiff)))
    # 被冲销的整件报废不再需要报废单，不算进覆盖率分母
    reversed_ids = _reversed_ids(db)
    scrap_events = [e for e in db.scalars(select(Event).where(Event.etype == "SCRAP", Event.status == "EFFECTIVE",
                                                              Event.scrap_mode == "WHOLE"))
                    if e.id not in reversed_ids]
    alloc = dict(db.execute(select(DocAlloc.event_id, func.sum(DocAlloc.qty)).group_by(DocAlloc.event_id)).all())
    covered = sum(1 for e in scrap_events if D(alloc.get(e.id, 0)) >= D(e.qty))
    q = quarter_of(today)
    snap = set(db.scalars(select(OverdueSnapshot.line_id).where(OverdueSnapshot.quarter == q)))
    by_id = {l.id: l for l in lines}
    cleared = sum(1 for lid in snap if lid in by_id and not ledger.is_overdue(by_id[lid], calcs[lid], today))
    reqs = list(db.scalars(select(Requisition).where(Requisition.decided_at.is_not(None))))
    in24 = sum(1 for r in reqs if r.decided_at - r.created_at <= timedelta(hours=24))
    issued = sum(D(l.qty) for l in lines if l.level == 1)
    loss = sum(calcs[l.id].loss for l in lines)
    return {
        "recon_close_rate": _pct(sum(1 for d in diffs if d.diff == 0), len(diffs)),
        "scrap_doc_rate": _pct(covered, len(scrap_events)),
        "overdue_clear_rate": _pct(cleared, len(snap)),
        "approve_24h_rate": _pct(in24, len(reqs)),
        "loss_rate": _pct(loss, issued),
    }


def line_brief(db: Session, l: LedgerLine, c, users: dict, mats: dict, projects: dict) -> dict:
    return {"id": l.id, "code": l.code, "material": mats[l.material_id].name, "unit": mats[l.material_id].unit,
            "project": projects[l.project_id].code, "holder": users[l.holder_id].name,
            "balance": str(c.balance), "due_date": l.due_date.isoformat(), "status": ledger.status_of(l, c)}


def weekly_hanging(db: Session, user: User) -> dict:
    """周报挂账清单。"""
    lines = scoped_lines(db, user)
    calcs = ledger.load(db, lines)
    users = {u.id: u for u in db.scalars(select(User))}
    mats = {m.id: m for m in db.scalars(select(Material))}
    projects = {p.id: p for p in db.scalars(select(Project))}
    now = clock.now()
    out = {"overdue": [], "urgent": [], "hold_30d": [], "doc_pending": [], "lab_pending": [], "level3": []}
    for l in lines:
        c = calcs[l.id]
        st = ledger.status_of(l, c)
        b = line_brief(db, l, c, users, mats, projects)
        if st == "S5":
            out["overdue"].append(b)
        if l.urgent and l.urgent_overdue and not l.urgent_resolved:
            out["urgent"].append(b)
        if st == "S3" and c.last_action_at and c.last_action_at < now - timedelta(days=30):
            out["hold_30d"].append(b)
        if st == "S6":
            out["doc_pending"].append(b)
    from .lineops import LAB_CONCLUSION_DAYS, lab_pending

    by_id = {l.id: l for l in lines}
    for e in lab_pending(db, list(by_id)):
        if e.created_at < now - timedelta(days=LAB_CONCLUSION_DAYS):
            l = by_id[e.line_id]
            b = line_brief(db, l, calcs[l.id], users, mats, projects)
            b.update({"event_id": e.id, "sent_at": e.created_at.isoformat(sep=" "), "qty": qs(e.qty)})
            out["lab_pending"].append(b)
    for t in db.scalars(select(Todo).where(Todo.state == "OPEN", Todo.escalated >= 2)):
        out["level3"].append({"id": t.id, "title": t.title, "owner": users[t.user_id].name,
                              "created_at": t.created_at.isoformat(sep=" ")})
    return out


def project_view(db: Session, user: User, project_id: int | None = None) -> list[dict]:
    lines = scoped_lines(db, user)
    if project_id:
        lines = [l for l in lines if l.project_id == project_id]
    calcs = ledger.load(db, lines)
    projects = {p.id: p for p in db.scalars(select(Project))}
    agg: dict[int, dict] = {}
    for l in lines:
        c = calcs[l.id]
        price = D(l.unit_price or 0)
        a = agg.setdefault(l.project_id, {"project": projects[l.project_id].code, "name": projects[l.project_id].name,
                                          "closed": projects[l.project_id].closed, "lines": 0, "active": 0,
                                          "issued_amount": Decimal(0), "consumed_amount": Decimal(0),
                                          "balance_amount": Decimal(0)})
        a["lines"] += 1
        if l.level == 1:
            a["issued_amount"] += D(l.qty) * price
        a["consumed_amount"] += (c.consumed + c.loss) * price
        a["balance_amount"] += max(c.balance, D(0)) * price
        if c.balance > 0 and l.signed:
            a["active"] += 1
    res = []
    for a in agg.values():
        for k in ("issued_amount", "consumed_amount", "balance_amount"):
            a[k] = str(a[k].quantize(Decimal("0.01")))
        res.append(a)
    return sorted(res, key=lambda x: x["project"])


def loss_report(db: Session, user: User) -> dict:
    lines = scoped_lines(db, user)
    calcs = ledger.load(db, lines)
    users = {u.id: u.name for u in db.scalars(select(User))}
    mats = {m.id: m for m in db.scalars(select(Material))}
    by_user: dict[str, list] = defaultdict(lambda: [D(0), D(0)])
    by_mat: dict[str, list] = defaultdict(lambda: [D(0), D(0)])
    for l in lines:
        c = calcs[l.id]
        issued = D(l.qty) if l.level == 1 else D(0)
        by_user[users[l.user_id]][0] += issued
        by_user[users[l.user_id]][1] += c.loss
        key = f"{mats[l.material_id].code} {mats[l.material_id].name}"
        by_mat[key][0] += issued
        by_mat[key][1] += c.loss
    fmt = lambda d: [{"name": k, "issued": str(v[0]), "loss": str(v[1]), "rate": _pct(v[1], v[0])}
                     for k, v in sorted(d.items(), key=lambda kv: -(kv[1][1]))]
    return {"by_user": fmt(by_user), "by_material": fmt(by_mat)}


def monthly_lists(db: Session, period: str) -> dict:
    y, m = map(int, period.split("-"))
    start = datetime(y, m, 1)
    end = datetime(y + (m == 12), m % 12 + 1, 1)
    users = {u.id: u.name for u in db.scalars(select(User))}
    r06 = [{"no": r.no, "user": users[r.user_id], "remark": r.remark, "created_at": r.created_at.isoformat(sep=" ")}
           for r in db.scalars(select(Requisition).where(Requisition.reason_code == "R06",
                                                         Requisition.created_at >= start, Requisition.created_at < end))]
    self_issue = [{"code": l.code, "user": users[l.user_id], "created_at": l.created_at.isoformat(sep=" ")}
                  for l in db.scalars(select(LedgerLine).where(LedgerLine.self_issue.is_(True),
                                                               LedgerLine.created_at >= start, LedgerLine.created_at < end))]
    return {"period": period, "r06": r06, "self_issue": self_issue}


def quarterly_lists(db: Session, user: User) -> dict:
    """季度盘点抽样（每个项目抽 20% 在制行，至少 1 行）+ 字典使用复盘。"""
    today = clock.today()
    q = quarter_of(today)
    lines = [l for l in scoped_lines(db, user) if l.signed]
    calcs = ledger.load(db, lines)
    active = [l for l in lines if calcs[l.id].balance > 0]
    by_proj: dict[int, list] = defaultdict(list)
    for l in active:
        by_proj[l.project_id].append(l)
    rnd = random.Random(q)
    projects = {p.id: p for p in db.scalars(select(Project))}
    sample = []
    for pid, ls in sorted(by_proj.items()):
        k = max(1, round(len(ls) * 0.2))
        for l in rnd.sample(sorted(ls, key=lambda x: x.id), k):
            sample.append({"project": projects[pid].code, "code": l.code, "balance": str(calcs[l.id].balance),
                           "location_id": l.location_id})
    qs = date(today.year, 3 * ((today.month - 1) // 3) + 1, 1)
    usage: dict[str, int] = defaultdict(int)
    reversed_ids = _reversed_ids(db)
    for e in db.scalars(select(Event).where(Event.created_at >= qs, Event.status == "EFFECTIVE")):
        if e.id in reversed_ids:
            continue  # 已冲销的误登不算字典使用
        for code in (e.dest, e.loss_reason, e.scrap_reason):
            if code:
                usage[code] += 1
    for r in db.scalars(select(Requisition).where(Requisition.created_at >= qs)):
        usage[r.reason_code] += 1
    for d in db.scalars(select(ReconDiff).where(ReconDiff.resolved_at >= qs, ReconDiff.reason_code.is_not(None))):
        usage[d.reason_code] += 1
    return {"quarter": q, "stocktake_sample": sample, "dict_usage": dict(usage),
            "dict_review": dict_review(db, usage)}


REVIEW_DICTS = ("DEST", "REASON", "LOSS_REASON", "SCRAP_REASON", "RECON_REASON")


def dict_review(db: Session, usage: dict[str, int]) -> list[dict]:
    """蓝图 5.3：每季度复盘字典使用情况，整季零使用或使用率低于 1% 的枚举建议合并或停用。"""
    out = []
    for t in REVIEW_DICTS:
        items = list(db.scalars(select(DictItem).where(DictItem.dict_type == t, DictItem.active.is_(True))
                                .order_by(DictItem.sort)))
        total = sum(usage.get(i.code, 0) for i in items)
        for i in items:
            n = usage.get(i.code, 0)
            pct = round(n / total * 100, 1) if total else None
            out.append({"dict_type": t, "code": i.code, "name": i.name, "count": n, "pct": pct,
                        "suggest": bool(total) and (n == 0 or (pct is not None and pct < 1))})
    return out
