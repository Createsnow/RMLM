import json
import logging
from contextlib import asynccontextmanager

import jwt
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import String, UniqueConstraint, inspect, select, text
from sqlalchemy.schema import CreateColumn

from . import db as dbmod
from .api import biz, core
from .config import check_jwt_secret, settings
from .dicts import SEED
from .models import AuditLog, DictItem, User
from .security import hash_password

log = logging.getLogger("rmlm")


def upgrade_schema() -> None:
    """轻量升级：给已有表补上新增的列和索引；字符串列只加长不缩短；
    模型里已不再唯一的单列唯一索引（先建好普通索引）去掉唯一约束。不删列、不改其他类型。"""
    insp = inspect(dbmod.engine)
    existing = set(insp.get_table_names())
    with dbmod.engine.begin() as conn:
        for table in dbmod.Base.metadata.sorted_tables:
            if table.name not in existing:
                continue
            cols = {c["name"]: c for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name not in cols:
                    ddl = CreateColumn(col).compile(dialect=dbmod.engine.dialect)
                    conn.execute(text(f"ALTER TABLE `{table.name}` ADD COLUMN {ddl}"))
                    log.info("schema: %s.%s added", table.name, col.name)
                    continue
                want, have_len = getattr(col.type, "length", None), getattr(cols[col.name]["type"], "length", None)
                if isinstance(col.type, String) and want and have_len and have_len < want:
                    null = "NULL" if col.nullable else "NOT NULL"
                    conn.execute(text(f"ALTER TABLE `{table.name}` MODIFY `{col.name}` VARCHAR({want}) {null}"))
                    log.info("schema: %s.%s widened to %s", table.name, col.name, want)
            have_idx = {i["name"] for i in insp.get_indexes(table.name)}
            for idx in table.indexes:
                if idx.name not in have_idx:
                    idx.create(conn)
                    log.info("schema: index %s added", idx.name)
            model_unique = {c.name for c in table.columns if c.unique} | {
                i.columns.keys()[0] for i in table.indexes if i.unique and len(i.columns) == 1}
            for cons in table.constraints:
                if isinstance(cons, UniqueConstraint) and len(cons.columns) == 1:
                    model_unique.add(cons.columns.keys()[0])
            for i in insp.get_indexes(table.name):
                cols_ = i["column_names"]
                if i.get("unique") and len(cols_) == 1 and cols_[0] not in model_unique and table.c.get(cols_[0]) is not None:
                    conn.execute(text(f"ALTER TABLE `{table.name}` DROP INDEX `{i['name']}`"))
                    log.info("schema: unique index %s.%s dropped", table.name, i["name"])


def init_db() -> None:
    dbmod.Base.metadata.create_all(dbmod.engine)
    upgrade_schema()
    with dbmod.SessionLocal() as db:
        for dtype, items in SEED.items():
            have = set(db.scalars(select(DictItem.code).where(DictItem.dict_type == dtype)))
            for i, (code, name, meaning) in enumerate(items):
                if code not in have:
                    db.add(DictItem(dict_type=dtype, code=code, name=name, meaning=meaning or None, sort=i))
        if not db.scalar(select(User.id).limit(1)):
            db.add(User(username=settings.admin_username, name="系统管理员", roles="ADMIN,USER",
                        password_hash=hash_password(settings.admin_password), must_change_pw=True))
        db.commit()


@asynccontextmanager
async def lifespan(_app):
    check_jwt_secret(settings.jwt_secret)
    init_db()
    if settings.wecom_enabled:
        from .services.wecom import install_push_hook

        install_push_hook()
    if settings.enable_scheduler:
        from .scheduler import start

        start()
    yield


app = FastAPI(title="研发物料管家 RMLM", version="1.0.0", lifespan=lifespan)
app.include_router(core.router)
app.include_router(biz.router)

SENSITIVE = {"password", "old", "new", "code"}


@app.middleware("http")
async def audit(request: Request, call_next):
    body_text = None
    if request.method in ("POST", "PUT", "DELETE", "PATCH") and request.url.path.startswith("/api/"):
        ctype = request.headers.get("content-type", "")
        if ctype.startswith("application/json"):
            raw = await request.body()
            try:
                data = json.loads(raw or b"{}")
                if isinstance(data, dict):
                    data = {k: ("***" if k in SENSITIVE else v) for k, v in data.items()}
                body_text = json.dumps(data, ensure_ascii=False)[:4000]
            except ValueError:
                body_text = raw[:4000].decode(errors="replace")
        elif ctype.startswith("multipart/"):
            body_text = "(文件上传)"
    response = await call_next(request)
    if request.method in ("POST", "PUT", "DELETE", "PATCH") and request.url.path.startswith("/api/"):
        uid = None
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            try:
                uid = int(jwt.decode(auth[7:], settings.jwt_secret, algorithms=["HS256"])["sub"])
            except Exception:
                uid = None
        try:
            with dbmod.SessionLocal() as db:
                db.add(AuditLog(user_id=uid, method=request.method, path=str(request.url.path)[:255],
                                status=response.status_code, body=body_text,
                                ip=request.client.host if request.client else None))
                db.commit()
        except Exception:  # 审计失败不影响业务
            log.exception("audit log failed")
    return response


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("unhandled error")
    return JSONResponse({"detail": "系统内部错误，请联系管理员"}, status_code=500)


@app.get("/api/health")
def health():
    return {"ok": True}


# 前端（构建产物）
if settings.static_dir.exists():
    app.mount("/assets", StaticFiles(directory=settings.static_dir / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        f = settings.static_dir / path
        if path and f.is_file() and settings.static_dir in f.resolve().parents:
            return FileResponse(f)
        return FileResponse(settings.static_dir / "index.html")
