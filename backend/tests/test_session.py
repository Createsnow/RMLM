"""多页签登录：凭证滑动续期（响应头 X-New-Token）；同一浏览器多个页签同时登录不同账号。"""
import time

import jwt

from app import db as dbmod
from app.config import settings
from app.models import User
from app.security import RENEW_AFTER, RENEW_HEADER
from tests.conftest import PW


def _uid(name):
    with dbmod.SessionLocal() as db:
        return db.query(User).filter_by(username=name).one().id


def _token(uid, age, method="pw", version=0, iat=True):
    now = int(time.time()) - age
    payload = {"sub": str(uid), "exp": now + settings.jwt_hours * 3600, "v": version, "m": method}
    if iat:
        payload["iat"] = now
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def _me(api, token):
    return api.c.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})


def test_fresh_token_not_renewed(api, org):
    r = _me(api, api.login("a1"))
    assert r.status_code == 200 and RENEW_HEADER not in r.headers


def test_old_token_renewed_and_both_valid(api, org):
    uid = _uid("a1")
    old = _token(uid, RENEW_AFTER + 5, method="wx")
    r = _me(api, old)
    assert r.status_code == 200
    new = r.headers[RENEW_HEADER]
    p = jwt.decode(new, settings.jwt_secret, algorithms=["HS256"])
    assert p["sub"] == str(uid) and p["m"] == "wx"  # 沿用登录方式
    assert p["exp"] >= int(time.time()) + settings.jwt_hours * 3600 - 5  # 有效期从现在重新算
    # 其他页面还拿着旧凭证：到期前仍然可用
    assert _me(api, old).status_code == 200
    r = _me(api, new)
    assert r.status_code == 200 and RENEW_HEADER not in r.headers


def test_legacy_token_without_iat_renewed(api, org):
    r = _me(api, _token(_uid("a1"), 0, iat=False))
    assert r.status_code == 200 and RENEW_HEADER in r.headers


def test_renew_header_on_file_response(api, org):
    pid = api.photo("a1")
    r = api.c.get(f"/api/photos/{pid}", headers={"Authorization": f"Bearer {_token(_uid('a1'), RENEW_AFTER + 5)}"})
    assert r.status_code == 200 and RENEW_HEADER in r.headers


def test_expired_or_disabled_not_renewed(api, org):
    uid = _uid("a1")
    r = _me(api, _token(uid, settings.jwt_hours * 3600 + 5))
    assert r.status_code == 401 and RENEW_HEADER not in r.headers
    with dbmod.SessionLocal() as db:
        db.get(User, uid).active = False
        db.commit()
    r = _me(api, _token(uid, RENEW_AFTER + 5))
    assert r.status_code == 401 and RENEW_HEADER not in r.headers


def test_password_change_renew_header_is_new_version(api, org):
    uid = _uid("a1")
    old = _token(uid, RENEW_AFTER + 5)
    r = api.c.post("/api/auth/password", headers={"Authorization": f"Bearer {old}"},
                   json={"old": PW, "new": "NewPassw0rd!"})
    assert r.status_code == 200, r.text
    assert r.headers[RENEW_HEADER] == r.json()["token"]
    assert _me(api, r.json()["token"]).status_code == 200
    assert _me(api, old).status_code == 401  # 改密码后旧凭证失效


def _login(api, name):
    r = api.c.post("/api/auth/login", json={"username": name, "password": PW})
    assert r.status_code == 200, r.text
    return r.json()["token"]


def test_multiple_accounts_same_browser(api, org):
    """同一浏览器多个页签同时登录不同账号：各自的凭证互不影响。"""
    t_a1, t_lead = _login(api, "a1"), _login(api, "lead_a")
    # 后登录的账号不会让先登录的失效，两个账号的请求各按自己的身份
    assert _me(api, t_a1).json()["username"] == "a1"
    assert _me(api, t_lead).json()["username"] == "lead_a"
    # 同一账号在两个页签分别登录，两份凭证都有效
    t_a1_2 = _login(api, "a1")
    assert _me(api, t_a1).status_code == 200 and _me(api, t_a1_2).status_code == 200
    # 一个账号改密码，只让这个账号的旧凭证失效，另一个账号照常使用
    r = api.c.post("/api/auth/password", headers={"Authorization": f"Bearer {t_a1}"},
                   json={"old": PW, "new": "NewPassw0rd!"})
    assert r.status_code == 200, r.text
    assert _me(api, t_a1_2).status_code == 401
    assert _me(api, r.json()["token"]).json()["username"] == "a1"
    assert _me(api, t_lead).json()["username"] == "lead_a"


def test_wrong_password_with_other_account_token_is_plain_401(api, org):
    """切换账号时输错密码：登录接口只回 401，不影响请求里带的（本页签当前账号的）凭证。"""
    t_lead = _login(api, "lead_a")
    r = api.c.post("/api/auth/login", headers={"Authorization": f"Bearer {t_lead}"},
                   json={"username": "a1", "password": "wrong-password"})
    assert r.status_code == 401 and RENEW_HEADER not in r.headers
    assert _me(api, t_lead).json()["username"] == "lead_a"
