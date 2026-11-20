from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from .. import clock
from ..models import DictItem, Sequence, Todo, User


def D(v) -> Decimal:
    """统一数量精度（3 位小数）。"""
    return Decimal(str(v or 0)).quantize(Decimal("0.001"))


def next_seq(db: Session, name: str) -> int:
    row = db.execute(select(Sequence).where(Sequence.name == name).with_for_update()).scalar_one_or_none()
    if row is None:
        row = Sequence(name=name, value=0)
        db.add(row)
        db.flush()
    row.value += 1
    db.flush()
    return row.value


def new_code(db: Session) -> str:
    """系统临时物料码（条码上线前使用）。"""
    return f"RM{next_seq(db, 'code'):08d}"


def new_no(db: Session, prefix: str) -> str:
    day = clock.today().strftime("%Y%m%d")
    return f"{prefix}-{day}-{next_seq(db, prefix + day):04d}"


def period_of(d: date | datetime) -> str:
    return d.strftime("%Y-%m")


def quarter_of(d: date | datetime) -> str:
    return f"{d.year}-Q{(d.month - 1) // 3 + 1}"


def add_months(d: date, months: int) -> date:
    m = d.month - 1 + months
    y, m = d.year + m // 12, m % 12 + 1
    import calendar

    return date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def dict_codes(db: Session, dict_type: str, active_only: bool = True) -> dict[str, str]:
    q = select(DictItem).where(DictItem.dict_type == dict_type)
    if active_only:
        q = q.where(DictItem.active.is_(True))
    return {d.code: d.name for d in db.scalars(q)}


# ------------------------------------------------------------------ 待办与通知
def add_todo(
    db: Session,
    user_id: int,
    kind: str,
    title: str,
    ref_type: str | None = None,
    ref_id: int | None = None,
    due_hours: float | None = None,
    due_at: datetime | None = None,
    notice: bool = False,
    level: int = 1,
    parent_id: int | None = None,
    dedupe: bool = False,
) -> Todo | None:
    if dedupe:
        exists = db.scalar(
            select(Todo.id).where(
                Todo.user_id == user_id, Todo.kind == kind, Todo.ref_type == ref_type,
                Todo.ref_id == ref_id, Todo.state == "OPEN",
            ).limit(1)
        )
        if exists:
            return None
    if due_at is None and due_hours is not None:
        due_at = clock.now() + timedelta(hours=due_hours)
    t = Todo(
        user_id=user_id, kind=kind, title=title, ref_type=ref_type, ref_id=ref_id,
        due_at=due_at, is_notice=notice, level=level, parent_id=parent_id,
    )
    db.add(t)
    db.flush()
    return t


def notify(db: Session, user_id: int, title: str, ref_type: str | None = None, ref_id: int | None = None) -> None:
    add_todo(db, user_id, "NOTICE", title, ref_type, ref_id, notice=True)


def close_todos(db: Session, ref_type: str, ref_id: int, kinds: tuple[str, ...] | None = None) -> None:
    q = update(Todo).where(Todo.ref_type == ref_type, Todo.ref_id == ref_id, Todo.state == "OPEN")
    if kinds:
        q = q.where(Todo.kind.in_(kinds))
    db.execute(q.values(state="DONE", done_at=clock.now()))


def users_with_role(db: Session, role: str) -> list[User]:
    return [u for u in db.scalars(select(User).where(User.active.is_(True), User.roles.like(f"%{role}%")))
            if role in u.role_set]
