"""登录凭证：JWT。登录方式有两种：企业微信网页授权（手机端，见 services/wecom.py）和账号密码（电脑端 / 未配置企微时）。"""
import time

import bcrypt
import jwt
from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .errors import BizError
from .models import User


def hash_password(pw: str) -> str:
    return bcrypt.hashpw(pw.encode(), bcrypt.gensalt(rounds=10)).decode()


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(pw.encode(), hashed.encode())
    except ValueError:
        return False


def make_token(user: User, method: str = "pw") -> str:
    """method：pw 账号密码登录 / wx 企业微信网页授权登录。v = 用户的凭证版本，改密码后旧凭证失效。"""
    # 过期时间用真实时间戳（不受业务时钟影响）
    payload = {"sub": str(user.id), "exp": int(time.time()) + settings.jwt_hours * 3600,
               "v": user.token_version or 0, "m": method}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


# 用初始密码 / 被重置的密码登录后，改密码前只能调这些接口（后端拦截，不只靠前端跳转）
PW_CHANGE_ALLOWED = {("GET", "/api/auth/me"), ("POST", "/api/auth/password"), ("GET", "/api/auth/config"),
                     ("GET", "/api/dicts"), ("GET", "/api/todos/count")}
MUST_CHANGE_STATUS = 428  # 前端收到后直接跳到修改密码页


def must_change_now(user: User, method: str | None) -> bool:
    """企微登录的账号（通讯录自动开号，密码随机、本人不知道）不要求改密码。"""
    return bool(user.must_change_pw) and method == "pw"


def current_user(request: Request, authorization: str | None = Header(default=None),
                 db: Session = Depends(get_db)) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise BizError("请先登录", 401)
    try:
        payload = jwt.decode(authorization[7:], settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise BizError("登录已过期，请重新登录", 401)
    user = db.get(User, int(payload["sub"]))
    if not user or not user.active:
        raise BizError("账号不可用", 401)
    if int(payload.get("v", 0)) != (user.token_version or 0):
        raise BizError("密码已修改，请重新登录", 401)
    # 升级前发出的凭证没有登录方式：不确定是不是企微登录，不拦（最多 12 小时后过期，重新登录就带上了）
    method = payload.get("m")
    request.state.login_method = method
    if must_change_now(user, method) and (request.method, request.url.path) not in PW_CHANGE_ALLOWED:
        raise BizError("首次登录或密码已被重置，请先修改密码", MUST_CHANGE_STATUS)
    return user
