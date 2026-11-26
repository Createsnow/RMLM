"""企业微信对接：身份登录、通讯录同步、消息推送。

按业务蓝图，企微承担四件事：
1. 身份登录：手机端用网页授权（snsapi_base）免密登录，经手人身份以企微 UserID 为准；
2. 入口：应用主页设为 {RMLM_PUBLIC_URL}/，挂在企微工作台；点开就是 RMLM 网页（手机 / 电脑企微都可用），前端按屏幕宽度自动选界面；
3. 通讯录：每天同步人员、职务、直属上级；企微里离职或停用的人在中台停用（名下有结存的先派待办）；
4. 消息推送：每条站内待办同时推送一条企微应用消息（文本卡片），点开直达对应页面。

推送不影响业务：站内待办是准；推送失败最多重试 3 次，之后放弃并标记 FAIL。
没有配置企微（WECOM_CORP_ID 等）时，以上功能全部关闭，系统按账号密码登录 + 站内待办运行。
"""
from __future__ import annotations

import json
import logging
import secrets
import threading
import time
from urllib.parse import quote

import httpx
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from .. import clock
from ..config import settings
from ..errors import BizError
from ..models import JobRun, LedgerLine, Todo, User
from . import ledger
from .common import add_todo, users_with_role

log = logging.getLogger("rmlm.wecom")

JOB = "wecom_contacts"
MAX_TRIES = 3
TOKEN_ERRORS = {40014, 42001}  # access_token 无效 / 过期
STATUS_ACTIVE, STATUS_DISABLED, STATUS_UNACTIVATED, STATUS_QUIT = 1, 2, 4, 5


class WeComError(RuntimeError):
    def __init__(self, msg: str, code: int | None = None) -> None:
        super().__init__(msg)
        self.code = code


# ------------------------------------------------------------------ 接口客户端
class WeComClient:
    _tokens: dict[str, tuple[str, float]] = {}  # secret → (token, 过期时间)；进程内缓存，所有实例共用
    _lock = threading.Lock()

    def __init__(self, corp_id: str | None = None, agent_id: str | None = None, secret: str | None = None,
                 contact_secret: str | None = None, base: str | None = None,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.corp_id = corp_id or settings.wecom_corp_id
        self.agent_id = agent_id or settings.wecom_agent_id
        self.secret = secret or settings.wecom_secret
        self.contact_secret = contact_secret or settings.wecom_contact_secret or self.secret
        self.base = (base or settings.wecom_api_base).rstrip("/")
        self.http = httpx.Client(timeout=15, transport=transport)

    def close(self) -> None:
        self.http.close()

    # -- 基础 --
    def token(self, secret: str, refresh: bool = False) -> str:
        with self._lock:
            hit = self._tokens.get(secret)
            if hit and not refresh and hit[1] > time.time():
                return hit[0]
        data = self._raw("GET", "/cgi-bin/gettoken", params={"corpid": self.corp_id, "corpsecret": secret})
        tok = data["access_token"]
        with self._lock:
            self._tokens[secret] = (tok, time.time() + int(data.get("expires_in", 7200)) - 300)
        return tok

    def _raw(self, method: str, path: str, params: dict | None = None, body: dict | None = None) -> dict:
        try:
            resp = self.http.request(method, self.base + path, params=params, json=body)
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPError as e:
            raise WeComError(f"连接企业微信失败：{e}") from e
        except ValueError as e:
            raise WeComError("企业微信返回的不是 JSON") from e
        code = data.get("errcode", 0)
        if code:
            raise WeComError(f"企业微信接口错误 {code}：{data.get('errmsg')}", code)
        return data

    def call(self, method: str, path: str, secret: str, params: dict | None = None, body: dict | None = None) -> dict:
        """带 access_token 调用；token 失效时刷新重试一次。"""
        for attempt in (0, 1):
            p = dict(params or {}, access_token=self.token(secret, refresh=attempt == 1))
            try:
                return self._raw(method, path, params=p, body=body)
            except WeComError as e:
                if e.code in TOKEN_ERRORS and attempt == 0:
                    continue
                raise
        raise WeComError("企业微信 access_token 刷新失败")

    # -- 网页授权 --
    def authorize_url(self, redirect_uri: str, state: str) -> str:
        return ("https://open.weixin.qq.com/connect/oauth2/authorize"
                f"?appid={self.corp_id}&redirect_uri={quote(redirect_uri, safe='')}"
                f"&response_type=code&scope=snsapi_base&state={quote(state, safe='')}"
                f"&agentid={self.agent_id}#wechat_redirect")

    def user_by_code(self, code: str) -> str:
        data = self.call("GET", "/cgi-bin/auth/getuserinfo", self.secret, params={"code": code})
        uid = data.get("userid") or data.get("UserId")
        if not uid:
            raise WeComError("不是本企业成员，无法登录")
        return uid

    # -- 通讯录（用通讯录同步 Secret，只读）--
    def department_ids(self, root: int) -> set[int]:
        data = self.call("GET", "/cgi-bin/department/simplelist", self.contact_secret, params={"id": root})
        return {int(d["id"]) for d in data.get("department_id", [])} | {root}

    def user_ids(self, departments: set[int] | None = None) -> list[str]:
        """获取成员 ID 列表（分页游标）；给了部门集合时只保留属于这些部门的人。"""
        out, cursor = [], ""
        while True:
            body = {"limit": 10000}
            if cursor:
                body["cursor"] = cursor
            data = self.call("POST", "/cgi-bin/user/list_id", self.contact_secret, body=body)
            for row in data.get("dept_user", []):
                if departments is None or int(row.get("department", 0)) in departments:
                    out.append(row["userid"])
            cursor = data.get("next_cursor") or ""
            if not cursor:
                return list(dict.fromkeys(out))

    def get_user(self, userid: str) -> dict:
        return self.call("GET", "/cgi-bin/user/get", self.contact_secret, params={"userid": userid})

    # -- 消息 --
    def send_card(self, userids: list[str], title: str, description: str, url: str) -> dict:
        body = {"touser": "|".join(userids), "msgtype": "textcard", "agentid": int(self.agent_id),
                "textcard": {"title": title[:60], "description": description[:250], "url": url, "btntxt": "查看"}}
        data = self.call("POST", "/cgi-bin/message/send", self.secret, body=body)
        if data.get("invaliduser"):
            raise WeComError(f"企微账号无效：{data['invaliduser']}")
        return data


def make_client() -> WeComClient:
    """返回企微客户端；未配置时报错。测试里可替换此函数。"""
    if not settings.wecom_enabled:
        raise BizError("未配置企业微信（WECOM_CORP_ID 等环境变量）")
    return WeComClient()


# ------------------------------------------------------------------ 登录
def public_base(request_base: str = "") -> str:
    return settings.public_url or request_base.rstrip("/")


def login_url(next_path: str, request_base: str = "") -> dict:
    """生成企微网页授权地址。回调到前端 /login，由前端拿 code 换登录凭证。"""
    if not next_path.startswith("/") or next_path.startswith("//"):
        next_path = "/"
    state = secrets.token_urlsafe(12)
    redirect = f"{public_base(request_base)}/login?next={quote(next_path, safe='/')}"
    return {"url": make_client().authorize_url(redirect, state), "state": state}


def login_by_code(db: Session, code: str) -> User:
    client = make_client()
    try:
        userid = client.user_by_code(code)
    except WeComError as e:
        raise BizError(f"企业微信登录失败：{e}", 401)
    u = db.scalar(select(User).where(User.wecom_userid == userid))
    if not u or not u.active:
        raise BizError(f"企业微信账号 {userid} 尚未开通研发物料管家，请联系管理员", 401)
    return u


# ------------------------------------------------------------------ 消息推送
KIND_NAMES = {"APPROVE": "待审核", "SIGN": "待签收", "TRANSFER_CONFIRM": "待确认转交", "OVERDUE": "超期提醒",
              "NOTICE": "通知"}


def link_of(t: Todo) -> str:
    base = settings.public_url
    if t.kind == "APPROVE":
        path = "/m/approve"
    elif t.ref_type == "req" and t.ref_id:
        path = f"/m/req/{t.ref_id}"
    elif t.ref_type == "line" and t.ref_id:
        path = f"/m/line/{t.ref_id}"
    else:
        path = "/m/todos"
    return base + path


def card_of(t: Todo) -> tuple[str, str]:
    kind = "通知" if t.is_notice else KIND_NAMES.get(t.kind, "待办")
    lines = [f"{kind} · {t.created_at:%m-%d %H:%M}"]
    if t.due_at:
        lines.append(f"请在 {t.due_at:%m-%d %H:%M} 前处理")
    if t.level >= 2:
        lines.append("已升级")
    return t.title, "\n".join(lines)


def mark_pending(db: Session, t: Todo) -> None:
    """add_todo 调用：有企微账号的人，待办同时排入推送队列，事务提交后发送。"""
    if not settings.wecom_enabled:
        return
    u = db.get(User, t.user_id)
    if u is None or not u.wecom_userid or not u.active:
        return
    t.push_state = "PENDING"
    db.info.setdefault("wecom_push", []).append(t.id)


def push_pending(db: Session, ids: list[int] | None = None, client: WeComClient | None = None) -> dict:
    """发送待推送的待办。ids 为空时处理全部 PENDING（定时补发）。"""
    stmt = select(Todo).where(Todo.push_state == "PENDING").order_by(Todo.id).limit(500)
    if ids:
        stmt = stmt.where(Todo.id.in_(ids))
    todos = list(db.scalars(stmt))
    stats = {"sent": 0, "failed": 0}
    if not todos:
        return stats
    own = client is None
    client = client or make_client()
    try:
        for t in todos:
            u = db.get(User, t.user_id)
            if not u or not u.wecom_userid:
                t.push_state = None
                continue
            title, desc = card_of(t)
            try:
                client.send_card([u.wecom_userid], title, desc, link_of(t))
                t.push_state = "SENT"
                stats["sent"] += 1
            except WeComError as e:
                t.push_tries += 1
                if t.push_tries >= MAX_TRIES:
                    t.push_state = "FAIL"
                stats["failed"] += 1
                log.warning("push todo %s failed: %s", t.id, e)
    finally:
        if own:
            client.close()
    db.flush()
    return stats


def _push_after_commit(session: Session) -> None:
    ids = session.info.pop("wecom_push", None)
    if not ids or not settings.wecom_enabled:
        return

    def run() -> None:
        from ..db import SessionLocal

        with SessionLocal() as db:
            try:
                push_pending(db, ids)
                db.commit()
            except Exception:  # noqa: BLE001  推送失败留给定时任务补发
                db.rollback()
                log.exception("wecom push failed")

    threading.Thread(target=run, daemon=True, name="wecom-push").start()


def _drop_on_rollback(session: Session) -> None:
    session.info.pop("wecom_push", None)


_installed = False


def install_push_hook() -> None:
    """应用启动时调用：事务提交后再推送，避免推送了一条最终回滚的待办。"""
    global _installed
    if _installed:
        return
    from ..db import SessionLocal

    event.listen(SessionLocal, "after_commit", _push_after_commit)
    event.listen(SessionLocal, "after_soft_rollback", lambda s, _t: _drop_on_rollback(s))
    _installed = True


def retry_pending(db: Session) -> dict | None:
    """每 15 分钟补发：提交后线程没发出去的、发送失败待重试的。"""
    if not settings.wecom_enabled:
        return None
    try:
        return push_pending(db)
    except Exception as e:  # noqa: BLE001
        log.exception("wecom retry failed")
        return {"error": str(e)[:200]}


# ------------------------------------------------------------------ 通讯录同步
def open_lines(db: Session, user_id: int) -> list[LedgerLine]:
    """名下未清零的行（含未签收）。离职 / 调岗前必须清零。"""
    lines = list(db.scalars(select(LedgerLine).where(LedgerLine.holder_id == user_id, LedgerLine.voided.is_(False))))
    calcs = ledger.load(db, lines)
    return [l for l in lines if calcs[l.id].balance > 0 or not l.signed]


def offboard_blockers(db: Session, user_id: int) -> dict[str, list]:
    """离职 / 调岗前必须清理的事项（业务确认 2026-11-25）：
    lines 名下未清零的行；transfers 别人转给他、还没确认的转交（须由发起人撤回或他本人拒绝）；
    reqs 他本人还没开完单的申请（待审核，或审核通过未开够数量；须撤回，否则停用后金蝶单过来仍会给他生成领用行）。"""
    from ..models import Event, Requisition
    from .requisition import issue_done

    transfers = list(db.scalars(select(Event).where(Event.etype == "TRANSFER_REQ", Event.status == "PENDING",
                                                    Event.to_user_id == user_id)))
    reqs = [r for r in db.scalars(select(Requisition).where(Requisition.user_id == user_id,
                                                            Requisition.status.in_(("PENDING", "APPROVED"))))
            if r.status == "PENDING" or not issue_done(db, r)]
    return {"lines": open_lines(db, user_id), "transfers": transfers, "reqs": reqs}


def blockers_text(b: dict) -> str:
    parts = []
    if b["lines"]:
        parts.append(f"名下还有 {len(b['lines'])} 行未清零（须退库或转交）")
    if b["transfers"]:
        parts.append(f"有 {len(b['transfers'])} 笔转给他的转交待确认（须拒绝或由发起人撤回）")
    if b["reqs"]:
        parts.append(f"有 {len(b['reqs'])} 张申请未开完单（{'、'.join(r.no for r in b['reqs'])}，须撤回）")
    return "；".join(parts)


def _descendants(db: Session, uid: int) -> set[int]:
    from ..perms import descendants

    return descendants(db, uid)


def sync_contacts(db: Session, client: WeComClient | None = None) -> dict:
    """从企微通讯录同步人员与直属上级。

    - 按企微 UserID 对齐；中台已有同名、尚未绑定企微的账号（Excel 导入的）自动绑定；
    - 新人自动开号（只有使用人角色，用企微登录）；
    - 职务、姓名、直属上级以企微为准（企微没维护直属上级的，保留中台原值）；
    - 企微里离职 / 停用 / 移出同步范围的人：名下无结存直接停用，有结存派待办给直属上级，处理完下次同步再停用。
    """
    own = client is None
    client = client or make_client()
    stats = {"seen": 0, "created": 0, "bound": 0, "updated": 0, "manager_set": 0, "deactivated": 0, "blocked": 0}
    try:
        depts = client.department_ids(settings.wecom_root_dept)
        ids = client.user_ids(depts)
        infos = {}
        for uid in ids:
            info = client.get_user(uid)
            infos[uid] = info
    finally:
        if own:
            client.close()
    stats["seen"] = len(infos)

    users = list(db.scalars(select(User)))
    by_wx = {u.wecom_userid: u for u in users if u.wecom_userid}
    name_count: dict[str, int] = {}
    for u in users:
        if u.active and not u.wecom_userid:
            name_count[u.name] = name_count.get(u.name, 0) + 1
    by_name = {u.name: u for u in users if u.active and not u.wecom_userid and name_count[u.name] == 1}
    usernames = {u.username for u in users}

    from ..security import hash_password

    for uid, info in infos.items():
        status = int(info.get("status") or STATUS_ACTIVE)
        name = (info.get("name") or "").strip()
        u = by_wx.get(uid)
        if u is None and name and name in by_name:
            u = by_name.pop(name)
            u.wecom_userid = uid
            by_wx[uid] = u
            stats["bound"] += 1
        if u is None:
            if status not in (STATUS_ACTIVE, STATUS_UNACTIVATED):
                continue
            username = uid if uid not in usernames else f"wx_{uid}"
            u = User(username=username[:64], name=name or uid, title=info.get("position") or None,
                     roles="USER", wecom_userid=uid, must_change_pw=False,
                     password_hash=hash_password(secrets.token_urlsafe(16)))
            db.add(u)
            db.flush()
            usernames.add(u.username)
            by_wx[uid] = u
            stats["created"] += 1
            continue
        changed = False
        if name and u.name != name:
            u.name, changed = name, True
        pos = (info.get("position") or "").strip()
        if pos and u.title != pos:
            u.title, changed = pos, True
        stats["updated"] += int(changed)

    # 直属上级：取企微 direct_leader 的第一位；会形成循环的跳过
    changed_mgr: list[User] = []
    for uid, info in infos.items():
        u = by_wx.get(uid)
        leaders = [x for x in (info.get("direct_leader") or []) if x != uid]
        if not u or not leaders:
            continue
        leader = by_wx.get(leaders[0])
        if leader and leader.id != u.manager_id and leader.id not in _descendants(db, u.id) | {u.id}:
            u.manager_id = leader.id
            stats["manager_set"] += 1
            changed_mgr.append(u)
    db.flush()
    from .lineops import reassign_user_requests

    for u in changed_mgr:  # 直属上级变了：名下行待审的续期 / 改归还日改派给新上级
        reassign_user_requests(db, u)

    # 离职 / 停用 / 不在同步范围
    for wx, u in by_wx.items():
        info = infos.get(wx)
        gone = info is None or int(info.get("status") or STATUS_ACTIVE) in (STATUS_DISABLED, STATUS_QUIT)
        if not gone or not u.active:
            continue
        b = offboard_blockers(db, u.id)
        if not any(b.values()):
            u.active = False
            stats["deactivated"] += 1
            continue
        stats["blocked"] += 1
        target = [u.manager_id] if u.manager_id else [a.id for a in users_with_role(db, "ADMIN")]
        for t in target:
            add_todo(db, t, "OFFBOARD", f"{u.name} 已在企业微信离职或停用：{blockers_text(b)}"[:250],
                     "user", u.id, dedupe=True)
    db.flush()
    return stats


def status(db: Session) -> dict:
    jr = db.get(JobRun, JOB)
    return {"configured": settings.wecom_enabled,
            "last_run_at": jr.last_run_at.isoformat(sep=" ", timespec="seconds") if jr and jr.last_run_at else None,
            "last_result": json.loads(jr.last_result) if jr and jr.last_result else None}


def run_sync(db: Session, client: WeComClient | None = None) -> dict:
    stats = sync_contacts(db, client)
    jr = db.get(JobRun, JOB) or JobRun(name=JOB)
    db.add(jr)
    jr.last_run_at = clock.now()
    jr.last_result = json.dumps(stats, ensure_ascii=False)
    db.flush()
    return stats


def daily_sync(db: Session) -> dict | None:
    if not settings.wecom_enabled:
        return None
    try:
        with db.begin_nested():
            return run_sync(db)
    except Exception as e:  # noqa: BLE001
        log.exception("wecom contact sync failed")
        for a in users_with_role(db, "ADMIN"):
            add_todo(db, a.id, "SYNC_FAIL", f"企业微信通讯录同步失败：{str(e)[:160]}", "job", 0, dedupe=True)
        return {"error": str(e)[:200]}
