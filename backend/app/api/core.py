"""登录、账号与组织、代理、主数据、字典、待办、照片、审计、定时任务手动触发。"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import FileResponse
from openpyxl import load_workbook
from pydantic import BaseModel
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from .. import clock
from ..config import settings
from ..db import get_db
from ..errors import BizError, forbid, not_found
from ..models import (AuditLog, Delegate, DictChange, DictItem, Freeze, LedgerLine, Location, Material, Photo, Project,
                      Todo, User)
from ..perms import (ROLES, ancestors, can_view_line, descendants, has_role, require_role, validate_delegate,
                     visible_user_ids)
from ..security import current_user, hash_password, make_token, must_change_now, verify_password
from ..services import erp as erpsvc
from ..services import jobs, ledger, master_sync, wecom
from ..services.k3cloud import K3Error
from ..services.common import close_todos, notify
from ..services.photos import save_photo
from .serial import get_or_404, s, user_out

router = APIRouter(prefix="/api")


# ------------------------------------------------------------------ 登录
class LoginIn(BaseModel):
    username: str
    password: str


@router.post("/auth/login")
def login(body: LoginIn, db: Session = Depends(get_db)):
    u = db.scalar(select(User).where(User.username == body.username.strip()))
    now = clock.now()
    if u is not None and u.locked_until and u.locked_until > now:
        mins = max(1, int((u.locked_until - now).total_seconds() // 60) + 1)
        raise BizError(f"密码连续输错次数过多，账号已锁定，请 {mins} 分钟后再试或联系管理员", 401)
    if not u or not u.active or not verify_password(body.password, u.password_hash):
        if u is not None:
            u.failed_logins = (u.failed_logins or 0) + 1
            if u.failed_logins >= settings.login_max_fails:
                u.failed_logins, u.locked_until = 0, now + timedelta(minutes=settings.login_lock_minutes)
            db.commit()  # 抛错会回滚，计数必须先提交
        raise BizError("账号或密码错误", 401)
    u.failed_logins, u.locked_until = 0, None
    return {"token": make_token(u, "pw"), "user": user_out(u, db)}


# ------------------------------------------------------------------ 企业微信登录（网页授权）
@router.get("/auth/config")
def auth_config():
    """登录页用：是否启用企业微信登录。"""
    return {"wecom": settings.wecom_enabled, "wms": settings.wms_enabled}


@router.get("/auth/wecom/url")
def wecom_login_url(request: Request, next: str = "/"):
    """企微内打开时，前端跳转到这个授权地址；授权后回到 /login?code=…"""
    return wecom.login_url(next, str(request.base_url))


class WecomLoginIn(BaseModel):
    code: str


@router.post("/auth/wecom")
def wecom_login(body: WecomLoginIn, db: Session = Depends(get_db)):
    u = wecom.login_by_code(db, body.code.strip())
    return {"token": make_token(u, "wx"), "user": user_out(u, db)}


@router.get("/auth/me")
def me(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    d = user_out(user, db)
    d["delegated_by"] = [p.name for p in db.scalars(
        select(User).join(Delegate, Delegate.principal_id == User.id).where(
            Delegate.agent_id == user.id, Delegate.start_at <= clock.now(), Delegate.end_at >= clock.now()))]
    d["must_change_pw"] = must_change_now(user, getattr(request.state, "login_method", None))
    return d


class PwIn(BaseModel):
    old: str
    new: str


@router.post("/auth/password")
def change_password(request: Request, body: PwIn, user: User = Depends(current_user)):
    if not verify_password(body.old, user.password_hash):
        raise BizError("原密码不正确")
    if len(body.new) < 8:
        raise BizError("新密码至少 8 位")
    if body.new == body.old:
        raise BizError("新密码不能与原密码相同")
    user.password_hash, user.must_change_pw = hash_password(body.new), False
    user.token_version = (user.token_version or 0) + 1  # 其他设备上的旧登录凭证随即失效
    # 本设备换发新凭证，免得刚改完密码就被踢出
    return {"ok": True, "token": make_token(user, getattr(request.state, "login_method", None) or "pw")}


# ------------------------------------------------------------------ 账号与组织（创建账号时勾选角色）
@router.get("/roles")
def roles():
    return [{"code": k, "name": v} for k, v in ROLES.items()]


@router.get("/users")
def list_users(q: str = "", include_inactive: bool = False, user: User = Depends(current_user),
               db: Session = Depends(get_db)):
    stmt = select(User).order_by(User.id)
    if not include_inactive:
        stmt = stmt.where(User.active.is_(True))
    if q:
        stmt = stmt.where(or_(User.name.like(f"%{q}%"), User.username.like(f"%{q}%")))
    return [user_out(u, db) for u in db.scalars(stmt)]


class UserIn(BaseModel):
    username: str
    name: str
    title: str | None = None
    emp_no: str | None = None
    manager_id: int | None = None
    roles: list[str] = ["USER"]
    password: str | None = None
    active: bool = True
    wecom_userid: str | None = None


def _apply_user(db: Session, u: User, body: UserIn) -> None:
    bad = [r for r in body.roles if r not in ROLES]
    if bad:
        raise BizError(f"角色不存在：{bad}")
    roles = set(body.roles) | {"USER"}
    if body.manager_id:
        mgr = db.get(User, body.manager_id)
        if not mgr:
            raise BizError("直属上级不存在")
        if u.id and (body.manager_id == u.id or body.manager_id in descendants(db, u.id)):
            raise BizError("直属上级不能是本人或其下属")
    wx = (body.wecom_userid or "").strip() or None
    if wx and db.scalar(select(User.id).where(User.wecom_userid == wx, User.id != (u.id or 0))):
        raise BizError(f"企业微信账号 {wx} 已绑定到其他账号")
    u.name, u.title, u.emp_no, u.manager_id = body.name.strip(), body.title, body.emp_no, body.manager_id
    u.wecom_userid = wx
    u.roles = ",".join(sorted(roles))


@router.post("/users")
def create_user(body: UserIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_role(user, "ADMIN")
    if db.scalar(select(User.id).where(User.username == body.username.strip())):
        raise BizError("账号已存在")
    u = User(username=body.username.strip(), password_hash=hash_password(body.password or "Rmlm@123456"),
             must_change_pw=True)
    _apply_user(db, u, body)
    db.add(u)
    db.flush()
    return user_out(u, db)


@router.put("/users/{uid}")
def update_user(uid: int, body: UserIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_role(user, "ADMIN")
    u = get_or_404(db, User, uid, "账号")
    _apply_user(db, u, body)
    if body.password:
        u.password_hash, u.must_change_pw = hash_password(body.password), True
        u.token_version = (u.token_version or 0) + 1  # 重置密码：此前发出的登录凭证全部失效
        u.failed_logins, u.locked_until = 0, None  # 顺带解除锁定
    if not body.active and u.active:
        _check_clearance(db, u)
    u.active = body.active
    return user_out(u, db)


def _clearance(db: Session, u: User) -> list[dict]:
    left = wecom.open_lines(db, u.id)
    calcs = ledger.load(db, left)
    return [{"id": l.id, "code": l.code, "balance": s(calcs[l.id].balance)} for l in left]


def _check_clearance(db: Session, u: User) -> None:
    left = _clearance(db, u)
    if left:
        raise BizError(f"{u.name} 名下还有 {len(left)} 行结存未清零，须先退库或转交后才能停用（离职 / 调岗卡点）")


@router.get("/users/{uid}/clearance")
def clearance(uid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """离职 / 调岗检查：名下结存必须清零。停用账号、企微通讯录同步离职人员时都会执行。"""
    u = get_or_404(db, User, uid, "账号")
    if not (has_role(user, "ADMIN", "DEPT_HEAD", "VP") or u.manager_id == user.id or u.id == user.id):
        raise forbid()
    left = _clearance(db, u)
    return {"user": u.name, "clear": not left, "lines": left}


@router.post("/users/{uid}/unfreeze")
def unfreeze(uid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    u = get_or_404(db, User, uid, "账号")
    if u.manager_id != user.id:
        raise forbid("冻结后由直属上级确认解冻")
    if not u.frozen:
        raise BizError("该账号未冻结")
    u.frozen = False
    for f in db.scalars(select(Freeze).where(Freeze.user_id == u.id, Freeze.released_at.is_(None))):
        f.released_at, f.released_by = clock.now(), user.id
    close_todos(db, "user", u.id, ("UNFREEZE",))
    notify(db, u.id, f"{user.name} 已为您解冻新领用权限")
    return user_out(u, db)


@router.post("/users/import")
def import_users(file: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    """批量建号（可选）。列：序号 | 岗位 | 姓名 | 直属上级 | 账号 | 角色(逗号分隔代码) | 企微UserID。
    角色列为空时只开使用人；企微 UserID 可空，之后由通讯录同步按姓名自动绑定。"""
    require_role(user, "ADMIN")
    ws = load_workbook(file.file, read_only=True, data_only=True).active
    rows = [r for r in ws.iter_rows(min_row=2, values_only=True) if r and r[2]]
    created = 0
    by_name = {u.name: u for u in db.scalars(select(User))}
    for r in rows:
        name = str(r[2]).strip()
        if name in by_name:
            continue
        username = str(r[4]).strip() if len(r) > 4 and r[4] else name
        roles = {x.strip() for x in str(r[5]).split(",")} if len(r) > 5 and r[5] else set()
        wx = str(r[6]).strip() if len(r) > 6 and r[6] else None
        if wx and db.scalar(select(User.id).where(User.wecom_userid == wx)):
            wx = None
        u = User(username=username, name=name, title=str(r[1] or ""), password_hash=hash_password("Rmlm@123456"),
                 roles=",".join(sorted((roles & set(ROLES)) | {"USER"})), must_change_pw=True, wecom_userid=wx)
        db.add(u)
        by_name[name] = u
        created += 1
    db.flush()
    for r in rows:
        mgr = str(r[3] or "").strip()
        if mgr and mgr != "/" and mgr in by_name:
            by_name[str(r[2]).strip()].manager_id = by_name[mgr].id
    return {"created": created, "initial_password": "Rmlm@123456"}


@router.get("/org/team")
def my_team(user: User = Depends(current_user), db: Session = Depends(get_db)):
    sub = descendants(db, user.id)
    return {"subordinates": [user_out(db.get(User, i)) for i in sorted(sub)],
            "chain": [user_out(a) for a in ancestors(db, user)]}


# ------------------------------------------------------------------ 代理审核
class DelegateIn(BaseModel):
    agent_id: int
    start_at: datetime
    end_at: datetime


@router.get("/delegates")
def list_delegates(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(Delegate).where(or_(Delegate.principal_id == user.id, Delegate.agent_id == user.id))
                      .order_by(Delegate.id.desc()))
    return [{"id": d.id, "principal": db.get(User, d.principal_id).name, "agent": db.get(User, d.agent_id).name,
             "start_at": s(d.start_at), "end_at": s(d.end_at),
             "active": d.start_at <= clock.now() <= d.end_at} for d in rows]


@router.post("/delegates")
def add_delegate(body: DelegateIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    agent = get_or_404(db, User, body.agent_id, "代理人")
    validate_delegate(db, user, agent)
    if body.end_at <= body.start_at:
        raise BizError("结束时间须晚于开始时间")
    d = Delegate(principal_id=user.id, agent_id=agent.id, start_at=body.start_at, end_at=body.end_at)
    db.add(d)
    db.flush()
    notify(db, agent.id, f"{user.name} 设置您为代理审核人（{body.start_at:%m-%d %H:%M} 至 {body.end_at:%m-%d %H:%M}）")
    return {"id": d.id}


@router.delete("/delegates/{did}")
def end_delegate(did: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    d = get_or_404(db, Delegate, did)
    if d.principal_id != user.id:
        raise forbid()
    d.end_at = min(d.end_at, clock.now())
    return {"ok": True}


# ------------------------------------------------------------------ 主数据
@router.get("/projects")
def projects(q: str = "", include_closed: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    stmt = select(Project).order_by(Project.code)
    if q:
        stmt = stmt.where(or_(Project.code.like(f"%{q}%"), Project.name.like(f"%{q}%")))
    if not include_closed:
        stmt = stmt.where(Project.closed.is_(False))
    return [{"id": p.id, "code": p.code, "name": p.name, "customer_code": p.customer_code, "closed": p.closed,
             "source": p.source} for p in db.scalars(stmt.limit(200))]


class ProjectIn(BaseModel):
    code: str
    name: str
    customer_code: str | None = None
    closed: bool = False


@router.post("/projects")
def save_project(body: ProjectIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """ERP 项目档案镜像（只读同步 / 导入 / 手工维护）。"""
    require_role(user, "KEEPER", "ADMIN")
    p = db.scalar(select(Project).where(Project.code == body.code.strip())) or Project(code=body.code.strip())
    p.name, p.customer_code, p.closed = body.name, body.customer_code, body.closed
    db.add(p)
    db.flush()
    return {"id": p.id}


@router.get("/materials")
def materials(q: str = "", include_inactive: bool = False, user: User = Depends(current_user),
              db: Session = Depends(get_db)):
    stmt = select(Material).order_by(Material.code)
    if q:
        stmt = stmt.where(or_(Material.code.like(f"%{q}%"), Material.name.like(f"%{q}%"), Material.spec.like(f"%{q}%")))
    if not include_inactive:
        stmt = stmt.where(Material.active.is_(True))
    return [{"id": m.id, "code": m.code, "name": m.name, "spec": m.spec, "unit": m.unit,
             "unit_price": s(m.unit_price), "category": m.category, "active": m.active,
             "erp_id": m.erp_id, "synced_at": s(m.synced_at)} for m in db.scalars(stmt.limit(200))]


class MaterialIn(BaseModel):
    code: str
    name: str
    spec: str | None = None
    unit: str
    unit_price: float | None = None
    category: str | None = None


@router.post("/materials")
def save_material(body: MaterialIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_role(user, "KEEPER", "ADMIN")
    m = db.scalar(select(Material).where(Material.code == body.code.strip())) or Material(code=body.code.strip())
    m.name, m.spec, m.unit, m.unit_price, m.category = body.name, body.spec, body.unit, body.unit_price, body.category
    db.add(m)
    db.flush()
    return {"id": m.id}


@router.post("/master/import/{kind}")
def import_master(kind: str, file: UploadFile = File(...), user: User = Depends(current_user),
                  db: Session = Depends(get_db)):
    if kind not in ("project", "material"):
        raise not_found()
    return {"count": erpsvc.import_master_xlsx(db, user, kind, file.file)}


@router.get("/master/sync/status")
def master_sync_status(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """金蝶物料同步状态：是否已配置接口、最近同步时间与结果。"""
    return master_sync.status(db)


@router.post("/master/sync/material")
def master_sync_material(full: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """立即从金蝶同步物料。默认增量（按修改日期），full=true 全量。"""
    require_role(user, "KEEPER", "ADMIN")
    try:
        return master_sync.sync_materials(db, full=full)
    except K3Error as e:
        raise BizError(str(e), 502)


@router.get("/locations")
def locations(kind: str | None = None, user: User = Depends(current_user), db: Session = Depends(get_db)):
    stmt = select(Location).where(Location.active.is_(True)).order_by(Location.kind, Location.code)
    if kind:
        stmt = stmt.where(Location.kind == kind)
    return [{"id": l.id, "code": l.code, "name": l.name, "kind": l.kind} for l in db.scalars(stmt)]


class LocationIn(BaseModel):
    code: str
    name: str
    kind: str = "STOCK"
    active: bool = True


@router.post("/locations")
def save_location(body: LocationIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_role(user, "ADMIN", "WAREHOUSE", "KEEPER")
    if body.kind not in ("STOCK", "STATION", "SCRAP_BOX"):
        raise BizError("位置类型只能是库位 / 工位 / 报废箱")
    loc = db.scalar(select(Location).where(Location.code == body.code.strip())) or Location(code=body.code.strip())
    loc.name, loc.kind, loc.active = body.name, body.kind, body.active
    db.add(loc)
    db.flush()
    return {"id": loc.id}


# ------------------------------------------------------------------ 字典（变更需研发副总批准）
@router.get("/dicts")
def dicts(user: User = Depends(current_user), db: Session = Depends(get_db)):
    out: dict[str, list] = {}
    for d in db.scalars(select(DictItem).order_by(DictItem.dict_type, DictItem.sort, DictItem.code)):
        out.setdefault(d.dict_type, []).append({"code": d.code, "name": d.name, "meaning": d.meaning,
                                                "active": d.active, "version": d.version})
    return out


class DictChangeIn(BaseModel):
    dict_type: str
    action: str
    code: str
    name: str | None = None
    meaning: str | None = None
    reason: str


@router.get("/dict-changes")
def dict_changes(user: User = Depends(current_user), db: Session = Depends(get_db)):
    return [{"id": c.id, "dict_type": c.dict_type, "action": c.action, "code": c.code, "name": c.name,
             "reason": c.reason, "status": c.status, "requested_by": db.get(User, c.requested_by).name,
             "created_at": s(c.created_at)} for c in db.scalars(select(DictChange).order_by(DictChange.id.desc()))]


@router.post("/dict-changes")
def request_dict_change(body: DictChangeIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_role(user, "ADMIN")
    if body.action not in ("ADD", "DISABLE", "RENAME"):
        raise BizError("只能新增、停用或改名，不允许删除")
    if not body.reason.strip():
        raise BizError("请填写变更原因")
    exists = db.scalar(select(DictItem).where(DictItem.dict_type == body.dict_type, DictItem.code == body.code))
    if body.action == "ADD" and exists:
        raise BizError("编码已存在")
    if body.action != "ADD" and not exists:
        raise BizError("编码不存在")
    c = DictChange(**body.model_dump(), requested_by=user.id)
    db.add(c)
    db.flush()
    from ..services.common import add_todo, users_with_role

    for vp in users_with_role(db, "VP"):
        add_todo(db, vp.id, "DICT_APPROVE", f"字典变更待批准：{body.dict_type} {body.action} {body.code}", "dictchange", c.id)
    return {"id": c.id}


@router.post("/dict-changes/{cid}/decide")
def decide_dict_change(cid: int, approve: bool, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_role(user, "VP")
    c = get_or_404(db, DictChange, cid)
    if c.status != "PENDING":
        raise BizError("已处理")
    c.status, c.decided_by, c.decided_at = ("APPROVED" if approve else "REJECTED"), user.id, clock.now()
    close_todos(db, "dictchange", c.id)
    if approve:
        item = db.scalar(select(DictItem).where(DictItem.dict_type == c.dict_type, DictItem.code == c.code))
        if c.action == "ADD":
            db.add(DictItem(dict_type=c.dict_type, code=c.code, name=c.name or c.code, meaning=c.meaning, sort=99))
        elif c.action == "DISABLE":
            item.active, item.version = False, item.version + 1
        else:
            item.name, item.version = c.name or item.name, item.version + 1
    return {"ok": True}


# ------------------------------------------------------------------ 待办
@router.get("/todos")
def todos(state: str = "OPEN", user: User = Depends(current_user), db: Session = Depends(get_db)):
    stmt = select(Todo).where(Todo.user_id == user.id).order_by(Todo.id.desc()).limit(300)
    if state != "ALL":
        stmt = stmt.where(Todo.state == state)
    return [{"id": t.id, "kind": t.kind, "title": t.title, "ref_type": t.ref_type, "ref_id": t.ref_id, "level": t.level,
             "is_notice": t.is_notice, "due_at": s(t.due_at), "state": t.state, "created_at": s(t.created_at),
             "overdue": bool(t.due_at and t.due_at < clock.now())} for t in db.scalars(stmt)]


@router.get("/todos/count")
def todo_count(user: User = Depends(current_user), db: Session = Depends(get_db)):
    q = select(func.count()).select_from(Todo).where(Todo.user_id == user.id, Todo.state == "OPEN")
    return {"tasks": db.scalar(q.where(Todo.is_notice.is_(False))), "notices": db.scalar(q.where(Todo.is_notice.is_(True)))}


@router.post("/todos/{tid}/read")
def read_notice(tid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    t = get_or_404(db, Todo, tid)
    if t.user_id != user.id:
        raise forbid()
    if not t.is_notice:
        raise BizError("待办事项需完成对应操作后自动关闭")
    t.state, t.done_at = "DONE", clock.now()
    return {"ok": True}


# ------------------------------------------------------------------ 照片
@router.post("/photos")
async def upload_photo(file: UploadFile = File(...), user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = save_photo(db, user, await file.read())
    return {"id": p.id, "size": p.size}


@router.get("/photos/{pid}")
def get_photo(pid: int, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p = get_or_404(db, Photo, pid, "照片")
    if not _can_view_photo(db, user, p):
        raise forbid("无权查看该照片")
    return FileResponse(settings.photo_dir / p.path, media_type="image/jpeg")


def _can_view_photo(db: Session, user: User, p: Photo) -> bool:
    """上传人本人、全局可见角色，或能看到引用这张照片的领用行的人。"""
    if p.uploader_id == user.id or visible_user_ids(db, user) is None:
        return True
    from ..models import Event

    # JSON_CONTAINS(照片列表, '5')：MySQL 8 与 MariaDB 都支持，候选值以 JSON 文本传入
    line_ids = set(db.scalars(select(Event.line_id).where(func.json_contains(Event.photos, str(p.id)) == 1)))
    return any(can_view_line(db, user, l) for l in db.scalars(select(LedgerLine).where(LedgerLine.id.in_(line_ids or {0}))))


# ------------------------------------------------------------------ 审计与任务
@router.get("/audit")
def audit(limit: int = 200, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_role(user, "ADMIN")
    names = {u.id: u.name for u in db.scalars(select(User))}
    return [{"id": a.id, "user": names.get(a.user_id), "method": a.method, "path": a.path, "status": a.status,
             "body": a.body, "ip": a.ip, "at": s(a.at)}
            for a in db.scalars(select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 1000)))]


@router.get("/admin/wecom/status")
def wecom_status(user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_role(user, "ADMIN")
    return wecom.status(db)


@router.post("/admin/wecom/sync-contacts")
def wecom_sync_contacts(user: User = Depends(current_user), db: Session = Depends(get_db)):
    """立即从企业微信同步通讯录（人员、职务、直属上级、离职）。"""
    require_role(user, "ADMIN")
    try:
        return wecom.run_sync(db)
    except wecom.WeComError as e:
        raise BizError(str(e), 502)


@router.post("/admin/jobs/{name}")
def run_job(name: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    require_role(user, "ADMIN")
    fn = {"tick": jobs.tick, "quarter_hour": jobs.quarter_hour, "daily": jobs.daily, "weekly": jobs.weekly,
          "quarterly": jobs.quarterly, "monthly": jobs.monthly}.get(name)
    if not fn:
        raise not_found("任务")
    return fn(db)
