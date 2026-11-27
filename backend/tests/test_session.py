"""多页面保持登录：凭证滑动续期（响应头 X-New-Token）。"""
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
