"""角色、数据范围与职责分离。

角色在创建账号时勾选；"直属上级"不是角色，而是由人员的上级关系自动得出。
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from . import clock
from .errors import BizError, forbid
from .models import Delegate, LedgerLine, User

ROLES = {
    "USER": "使用人",
    "KEEPER": "领料专员",
    "WAREHOUSE": "仓库",
    "FINANCE": "财务",
    "ADMIN": "系统管理员",
    "DEPT_HEAD": "部门负责人",
    "VP": "研发副总",
    "PROJECT_VIEW": "项目视图",
}
# 可查看业务范围内全部台账的角色（其中项目视图、财务为只读，写权限另行判断）
ALL_SCOPE_ROLES = {"DEPT_HEAD", "VP", "KEEPER", "FINANCE", "ADMIN", "PROJECT_VIEW", "WAREHOUSE"}


def has_role(user: User, *roles: str) -> bool:
    return bool(user.role_set & set(roles))


def require_role(user: User, *roles: str) -> None:
    if not has_role(user, *roles):
        names = "、".join(ROLES[r] for r in roles)
        raise forbid(f"仅{names}可执行此操作")


def direct_reports(db: Session, uid: int) -> list[User]:
    return list(db.scalars(select(User).where(User.manager_id == uid, User.active.is_(True))))


def descendants(db: Session, uid: int) -> set[int]:
    result: set[int] = set()
    frontier = [uid]
    while frontier:
        ids = list(db.scalars(select(User.id).where(User.manager_id.in_(frontier))))
        ids = [i for i in ids if i not in result]
        result.update(ids)
        frontier = ids
    return result


def ancestors(db: Session, user: User) -> list[User]:
    chain, seen = [], {user.id}
    cur = user.manager_id
    while cur and cur not in seen:
        u = db.get(User, cur)
        if not u:
            break
        chain.append(u)
        seen.add(cur)
        cur = u.manager_id
    return chain


def is_manager(db: Session, user: User) -> bool:
    return bool(db.scalar(select(User.id).where(User.manager_id == user.id).limit(1)))


def same_group(a: User, b: User) -> bool:
    return a.manager_id is not None and a.manager_id == b.manager_id


def visible_user_ids(db: Session, user: User) -> set[int] | None:
    """None 表示全部可见；否则为可见的人员集合（本人 + 本组同级只读 + 下属逐级）。"""
    if has_role(user, *ALL_SCOPE_ROLES):
        return None
    ids = {user.id} | descendants(db, user.id)
    if user.manager_id:
        ids |= set(db.scalars(select(User.id).where(User.manager_id == user.manager_id)))
    return ids


def can_view_line(db: Session, user: User, line: LedgerLine) -> bool:
    ids = visible_user_ids(db, user)
    return ids is None or line.holder_id in ids or line.user_id in ids


def proxy_check(db: Session, op: User, holder: User) -> bool:
    """代登校验。返回 True 表示本次为代登（需留痕并通知持有人）。

    代登范围：同组成员、持有人的直属上级、领料专员。
    """
    if op.id == holder.id:
        return False
    if holder.manager_id == op.id or same_group(op, holder) or has_role(op, "KEEPER"):
        return True
    raise forbid("只能操作本人持有的物料；代录仅限同组成员、直属上级或领料专员")


# ------------------------------------------------------------------ 审核路由
def default_approver(db: Session, applicant: User) -> User:
    if applicant.manager_id:
        mgr = db.get(User, applicant.manager_id)
        if mgr and mgr.active:
            return mgr
    for role in ("VP", "ADMIN"):
        for u in db.scalars(select(User).where(User.active.is_(True), User.roles.like(f"%{role}%"))):
            if role in u.role_set and u.id != applicant.id:
                return u
    raise BizError("找不到审核人：请先为该账号设置直属上级")


def active_delegates(db: Session, principal_id: int) -> list[int]:
    t = clock.now()
    return list(
        db.scalars(
            select(Delegate.agent_id).where(
                Delegate.principal_id == principal_id, Delegate.start_at <= t, Delegate.end_at >= t
            )
        )
    )


def validate_delegate(db: Session, principal: User, agent: User) -> None:
    if principal.id == agent.id:
        raise BizError("代理人不能是本人")
    if same_group(principal, agent):
        return
    if any(a.id == agent.id for a in ancestors(db, principal)):
        return
    raise BizError("代理审核人只能是同级或上级")
