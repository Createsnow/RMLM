"""第五轮（2026-11-25 业务答复"都按建议"）：登录与权限、紧急领用交接后不能驳回、定时任务单步失败不连坐、超量开单比例。"""
from datetime import datetime, timedelta

import pytest

from app import clock
from app import db as dbmod
from app.config import check_jwt_secret, settings
from app.models import Todo, User
from app.security import make_token
from app.services import erp, jobs

from .conftest import PW
from .helpers import erp_doc, issue, make_req


def _login(org, username, password):
    return org.c.post("/api/auth/login", json={"username": username, "password": password})


def _h(token):
    return {"Authorization": f"Bearer {token}"}


# ------------------------------------------------------------------ 6 不强制改密码（2026-11-26 业务确认），可随时自己改
def test_initial_or_reset_password_not_forced_to_change(org, ref):
    org.ok(org.post("admin", "/api/users", {"username": "newbie", "name": "新人", "manager_id": ref.user("lead_a")}))
    tok = org.ok(_login(org, "newbie", "Rmlm@123456"))["token"]
    assert "must_change_pw" not in org.c.get("/api/auth/me", headers=_h(tok)).json()
    assert org.c.get("/api/lines", headers=_h(tok)).status_code == 200  # 初始密码登录后各功能直接可用
    # 管理员重置密码后同样不强制
    body = {"username": "newbie", "name": "新人", "manager_id": ref.user("lead_a"), "roles": ["USER"],
            "password": "Reset#2026x"}
    org.ok(org.c.put(f"/api/users/{ref.user('newbie')}", json=body, headers=org._h("admin")))
    tok = org.ok(_login(org, "newbie", "Reset#2026x"))["token"]
    assert org.c.get("/api/lines", headers=_h(tok)).status_code == 200
    # 本人随时可改：改完换发新凭证，旧凭证失效
    r = org.c.post("/api/auth/password", headers=_h(tok), json={"old": "Reset#2026x", "new": "NewPassw0rd!"})
    new_tok = r.json()["token"]
    assert org.c.get("/api/lines", headers=_h(new_tok)).status_code == 200
    assert org.c.get("/api/lines", headers=_h(tok)).status_code == 401


# ------------------------------------------------------------------ 7 连续输错 5 次锁定 15 分钟
def test_login_locks_after_five_failures(org):
    for _ in range(5):
        assert _login(org, "a1", "wrong").status_code == 401
    r = _login(org, "a1", PW)
    assert r.status_code == 401 and "锁定" in r.json()["detail"]
    clock.set_now(clock.now() + timedelta(minutes=16))
    assert _login(org, "a1", PW).status_code == 200


def test_successful_login_resets_failure_count(org):
    for _ in range(4):
        _login(org, "a1", "wrong")
    assert _login(org, "a1", PW).status_code == 200
    for _ in range(4):
        _login(org, "a1", "wrong")
    assert _login(org, "a1", PW).status_code == 200


# ------------------------------------------------------------------ 8 照片：只能引用本人上传的；查看要有权限
def test_cannot_attach_someone_elses_photo(org, ref):
    l1 = issue(org, ref, who="a1", qty=10)
    p = org.photo("b1")
    r = org.post("a1", f"/api/lines/{l1['id']}/consume", {"qty": 1, "dest": "D02", "photos": [p]})
    assert r.status_code == 400 and "本人" in r.json()["detail"]


def test_photo_visible_only_to_uploader_or_line_viewers(org, ref):
    l1 = issue(org, ref, who="a1", qty=10)
    pid = org.ok(org.get("a1", f"/api/lines/{l1['id']}"))["events"][0]["photos"][0]
    for who, code in (("a1", 200), ("a2", 200), ("lead_a", 200), ("keeper", 200), ("b1", 403)):
        assert org.get(who, f"/api/photos/{pid}").status_code == code, who
    own = org.photo("b1")  # 还没挂到任何事件上的照片：只有上传人能看
    assert org.get("b1", f"/api/photos/{own}").status_code == 200
    assert org.get("a1", f"/api/photos/{own}").status_code == 403


# ------------------------------------------------------------------ 9 改密码 / 重置密码后旧凭证失效
def test_old_token_invalid_after_password_change_or_reset(org, ref):
    old = org.login("a1")
    r = org.c.post("/api/auth/password", headers=_h(old), json={"old": PW, "new": "Another0ne!"})
    new = r.json()["token"]
    assert org.c.get("/api/auth/me", headers=_h(old)).status_code == 401
    assert org.c.get("/api/auth/me", headers=_h(new)).status_code == 200
    b1_old = org.login("b1")
    b1 = org.ok(org.get("admin", f"/api/users?q=b1"))[0]
    org.ok(org.c.put(f"/api/users/{b1['id']}", headers=_h(org.login("admin")),
                     json={"username": "b1", "name": b1["name"], "manager_id": b1["manager_id"], "roles": b1["roles"],
                           "password": "Reset0000!", "active": True}))
    assert org.c.get("/api/auth/me", headers=_h(b1_old)).status_code == 401


# ------------------------------------------------------------------ 11 登录凭证签名密钥不能是默认值 / 太短
@pytest.mark.parametrize("secret", ["", "dev-secret-change-me", "请改成一串随机字符", "short"])
def test_weak_jwt_secret_rejected(secret):
    with pytest.raises(RuntimeError):
        check_jwt_secret(secret)


def test_strong_jwt_secret_accepted():
    check_jwt_secret("k3Jx9vQ2mZr8LpT4wYb7NcF1")


# ------------------------------------------------------------------ 12 已凭口头同意交接的紧急申请只能通过
def test_cannot_reject_urgent_request_after_handover(org, ref):
    req = make_req(org, ref, urgent=True, urgent_note="组长口头同意")
    org.ok(org.post("keeper", f"/api/requisitions/{req['id']}/urgent-handover/{req['items'][0]['id']}"))
    r = org.post("lead_a", f"/api/requisitions/{req['id']}/decide", {"approve": False, "note": "不同意"})
    assert r.status_code == 400 and "只能审核通过" in r.json()["detail"]
    org.ok(org.post("lead_a", f"/api/requisitions/{req['id']}/decide", {"approve": True}))
    # 还没交接的紧急申请照常可以驳回
    req2 = make_req(org, ref, urgent=True, urgent_note="组长口头同意")
    org.ok(org.post("lead_a", f"/api/requisitions/{req2['id']}/decide", {"approve": False, "note": "不同意"}))


# ------------------------------------------------------------------ 13 定时任务：一步失败不影响其他步骤
def test_tick_step_failure_does_not_roll_back_other_steps(org, ref, monkeypatch):
    issue(org, ref, qty=10)
    req = make_req(org, ref)  # 待审核申请，4 小时后应提醒
    clock.set_now(clock.now() + timedelta(hours=5))

    def boom(db):
        raise RuntimeError("模拟自动绑定出错")

    monkeypatch.setattr(erp, "auto_bind", boom)
    with dbmod.SessionLocal() as db:
        out = jobs.tick(db)
        db.commit()
    assert "error" in str(out["quarter_hour"]["auto_bound"])
    assert out["quarter_hour"]["remind"] == 1 and "daily" in out
    with dbmod.SessionLocal() as db:
        from sqlalchemy import select

        fails = list(db.scalars(select(Todo).where(Todo.kind == "JOB_FAIL")))
        assert fails and "自动生成领用行" in fails[0].title
        from app.models import JobRun, Requisition

        assert db.get(JobRun, "daily").last_key == clock.today().isoformat()
        assert db.get(Requisition, req["id"]).escalation == 1


# ------------------------------------------------------------------ 14 超量开单比例（默认 0 = 不允许超出）
def test_over_issue_tolerance_configurable(org, ref, monkeypatch):
    monkeypatch.setattr(settings, "over_issue_pct", 10)
    req = make_req(org, ref, qty=10)
    org.ok(org.post("lead_a", f"/api/requisitions/{req['id']}/decide", {"approve": True}))
    item = req["items"][0]["id"]
    too_much = erp_doc(org, "LL", [{"material_code": "M-CABLE", "qty": 12}])
    r = org.post("keeper", "/api/erp/bind", {"req_item_id": item, "erp_line_id": too_much["lines"][0]["id"]})
    assert r.status_code == 400
    ok = erp_doc(org, "LL", [{"material_code": "M-CABLE", "qty": 11}])
    org.ok(org.post("keeper", "/api/erp/bind", {"req_item_id": item, "erp_line_id": ok["lines"][0]["id"]}))


def test_tokens_issued_before_upgrade_still_work(org):
    """升级前发出的凭证（没有版本号和登录方式）照常可用，也不会把企微开号的人逼去改密码。"""
    import time

    import jwt
    from sqlalchemy import select

    with dbmod.SessionLocal() as db:
        user = db.scalar(select(User).where(User.username == "a1"))
        user.must_change_pw = True
        db.commit()
        old = jwt.encode({"sub": str(user.id), "exp": int(time.time()) + 3600}, settings.jwt_secret, algorithm="HS256")
    assert org.c.get("/api/lines", headers=_h(old)).status_code == 200
