"""登录凭证：JWT。登录方式有两种：企业微信网页授权（手机端，见 services/wecom.py）和账号密码（电脑端 / 未配置企微时）。"""
import time

import bcrypt
import jwt
from fastapi import Depends, Header
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


def make_token(user: User) -> str:
    # 过期时间用真实时间戳（不受业务时钟影响）
    payload = {"sub": str(user.id), "exp": int(time.time()) + settings.jwt_hours * 3600}
    return jwt.encode(payload, settings.jwt_secret, algorithm="HS256")


def current_user(authorization: str | None = Header(default=None), db: Session = Depends(get_db)) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise BizError("请先登录", 401)
    try:
        payload = jwt.decode(authorization[7:], settings.jwt_secret, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise BizError("登录已过期，请重新登录", 401)
    user = db.get(User, int(payload["sub"]))
    if not user or not user.active:
        raise BizError("账号不可用", 401)
    return user
