"""双轨并行期抽查（蓝图 表 5-1）。

第 1 月旧表与系统双记：每周从当周签收的领用行里随机抽 20 单（不足全抽），由领料专员对照旧表登记
一致 / 不一致；连续两周一致率 ≥ 95% 即满足"并行 → 收口"的退出条件。
"""
import random
from datetime import date, datetime, time, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..errors import BizError
from ..models import LedgerLine, Material, ParallelCheck, Project, User
from ..perms import require_role
from .common import qs

SAMPLE_SIZE = 20
PASS_RATE = 95.0


def week_of(d: date) -> str:
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


def week_range(week: str) -> tuple[datetime, datetime]:
    try:
        y, w = week.split("-W")
        start = date.fromisocalendar(int(y), int(w), 1)
    except (ValueError, TypeError):
        raise BizError("周格式应为 2026-W45")
    s = datetime.combine(start, time.min)
    return s, s + timedelta(days=7)


def sample(db: Session, op: User, week: str | None = None) -> list[ParallelCheck]:
    require_role(op, "KEEPER", "ADMIN")
    week = week or week_of(clock.today())
    start, end = week_range(week)
    have = list(db.scalars(select(ParallelCheck).where(ParallelCheck.week == week)))
    if have:
        return have
    pool = list(db.scalars(select(LedgerLine).where(
        LedgerLine.level == 1, LedgerLine.voided.is_(False), LedgerLine.signed.is_(True),
        LedgerLine.signed_at >= start, LedgerLine.signed_at < end).order_by(LedgerLine.id)))
    if not pool:
        raise BizError(f"{week} 没有新签收的领用行，无需抽查")
    picked = random.Random(week).sample(pool, min(SAMPLE_SIZE, len(pool)))
    rows = [ParallelCheck(week=week, line_id=l.id) for l in sorted(picked, key=lambda x: x.id)]
    db.add_all(rows)
    db.flush()
    return rows


def mark(db: Session, op: User, pc: ParallelCheck, result: str, note: str | None) -> ParallelCheck:
    require_role(op, "KEEPER", "ADMIN")
    if result not in ("MATCH", "DIFF"):
        raise BizError("比对结果只能是一致或不一致")
    if result == "DIFF" and not (note or "").strip():
        raise BizError("不一致时请写明差在哪里")
    pc.result, pc.note, pc.checked_by, pc.checked_at = result, (note or "").strip() or None, op.id, clock.now()
    db.flush()
    return pc


def rows_out(db: Session, rows: list[ParallelCheck]) -> list[dict]:
    users = {u.id: u.name for u in db.scalars(select(User))}
    out = []
    for r in rows:
        l = db.get(LedgerLine, r.line_id)
        m, p = db.get(Material, l.material_id), db.get(Project, l.project_id)
        out.append({"id": r.id, "week": r.week, "line_id": l.id, "code": l.code, "material": m.name,
                    "material_code": m.code, "qty": qs(l.qty), "unit": m.unit, "project": p.code,
                    "user": users.get(l.user_id), "signed_at": l.signed_at.isoformat(sep=" ") if l.signed_at else None,
                    "result": r.result, "note": r.note, "checked_by": users.get(r.checked_by),
                    "checked_at": r.checked_at.isoformat(sep=" ") if r.checked_at else None})
    return out


def summary(db: Session) -> dict:
    weeks: dict[str, dict] = {}
    for r in db.scalars(select(ParallelCheck).order_by(ParallelCheck.week)):
        w = weeks.setdefault(r.week, {"week": r.week, "sampled": 0, "checked": 0, "match": 0})
        w["sampled"] += 1
        if r.result:
            w["checked"] += 1
            w["match"] += r.result == "MATCH"
    out = []
    for w in weeks.values():
        w["rate"] = round(w["match"] / w["checked"] * 100, 1) if w["checked"] else None
        w["done"] = w["checked"] == w["sampled"]
        w["pass"] = w["done"] and w["rate"] is not None and w["rate"] >= PASS_RATE
        out.append(w)
    # 退出条件：最近两个完成比对的周连续达标，且这两周相邻
    done = [w for w in out if w["done"]]
    exit_ok = False
    if len(done) >= 2:
        a, b = done[-2], done[-1]
        sa, _ = week_range(a["week"])
        sb, _ = week_range(b["week"])
        exit_ok = a["pass"] and b["pass"] and (sb - sa).days == 7
    return {"weeks": out, "exit_ok": exit_ok, "target": PASS_RATE, "sample_size": SAMPLE_SIZE}
