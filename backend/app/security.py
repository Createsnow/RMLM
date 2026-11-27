"""登录凭证：JWT。登录方式有两种：企业微信网页授权（手机端，见 services/wecom.py）和账号密码（电脑端 / 未配置企微时）。

滑动续期：凭证有效期 RMLM_JWT_HOURS 小时；只要还在用（任一页面发请求），签发满 RENEW_AFTER 秒后，
服务端在响应头 RENEW_HEADER 里返回新凭证，前端换上并同步给同一浏览器的其他页面。
所以一直在用就不会被退出，连续 RMLM_JWT_HOURS 小时没用才需要重新登录。旧凭证在到期前仍然有效，
多个页面同时续期、各拿到不同的新凭证也互不影响。
"""
import time

import bcrypt
import jwt
from fastapi import Depends, Header, Request
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .errors import BizError
from .models import User

RENEW_HEADER = "X-New-Token"
RENEW_AFTER = 600  # 凭证签发满 10 分钟后，下次请求时续期（避免每个请求都换新凭证）


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
    now = int(time.time())
    payload = {"sub": str(user.id), "iat": now, "exp": now + settings.jwt_hours * 3600,
               "v": user.token_version or 0, "m": method}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


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
    # 登录方式（pw 账号密码 / wx 企业微信）：本人改密码后换发新凭证时沿用。业务确认不强制改密码，只提供改密入口
    request.state.login_method = payload.get("m")
    # 滑动续期，沿用原登录方式；没有 iat 的是续期功能上线前签发的旧凭证，直接续期
    if time.time() - payload.get("iat", 0) >= RENEW_AFTER:
        request.state.new_token = make_token(user, payload.get("m") or "pw")
    return user
