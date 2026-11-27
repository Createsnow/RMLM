"""测试使用独立的 MySQL 库（默认 rmlm_test），每个用例前清空数据。

组织（全部为虚构人员）：
    研发副总 vp
    └─ 部门负责人 head
       ├─ 组长 lead_a ── 组员 a1, a2
       ├─ 组长 lead_b ── 组员 b1
       └─ 领料专员 keeper
    仓库 wh、财务 fin、系统管理员 admin、admin2
"""
import io
import os
import tempfile
from datetime import datetime

os.environ.setdefault("RMLM_DATABASE_URL",
                      "mysql+pymysql://rmlm:rmlm_dev@127.0.0.1:3306/rmlm_test?charset=utf8mb4")
os.environ["RMLM_SCHEDULER"] = "0"
os.environ.setdefault("RMLM_JWT_SECRET", "test-only-secret-0123456789abcdef")
os.environ["RMLM_PHOTO_DIR"] = tempfile.mkdtemp(prefix="rmlm-photos-")

import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import text

from app import clock
from app import db as dbmod
from app.main import app, init_db
from app.models import Location, Material, Project, User
from app.security import hash_password

PW = "Passw0rd!"


@pytest.fixture(scope="session", autouse=True)
def _schema():
    dbmod.Base.metadata.drop_all(dbmod.engine)
    init_db()
    yield


@pytest.fixture(autouse=True)
def _clean():
    with dbmod.engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for t in reversed(dbmod.Base.metadata.sorted_tables):
            if t.name != "dict_item":
                conn.execute(text(f"TRUNCATE TABLE `{t.name}`"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    clock.set_now(datetime(2026, 11, 2, 9, 0, 0))
    yield
    clock.set_now(None)


class Api:
    def __init__(self, client: TestClient):
        self.c = client
        self.tokens: dict[str, str] = {}

    def login(self, username: str) -> str:
        if username not in self.tokens:
            r = self.c.post("/api/auth/login", json={"username": username, "password": PW})
            assert r.status_code == 200, r.text
            self.tokens[username] = r.json()["token"]
        return self.tokens[username]

    def _h(self, who):
        return {"Authorization": f"Bearer {self.login(who)}"}

    def get(self, who, url, **kw):
        return self.c.get(url, headers=self._h(who), **kw)

    def post(self, who, url, json=None, **kw):
        return self.c.post(url, headers=self._h(who), json=json, **kw)

    def ok(self, resp):
        assert resp.status_code == 200, resp.text
        return resp.json()

    def photo(self, who) -> int:
        buf = io.BytesIO()
        Image.new("RGB", (64, 48), (200, 80, 80)).save(buf, "PNG")
        r = self.c.post("/api/photos", headers=self._h(who), files={"file": ("p.png", buf.getvalue(), "image/png")})
        return self.ok(r)["id"]


@pytest.fixture
def api():
    with TestClient(app) as client:
        yield Api(client)


@pytest.fixture
def org(api):
    with dbmod.SessionLocal() as db:
        db.execute(text("DELETE FROM users"))

        def mk(username, name, roles="USER", manager=None):
            u = User(username=username, name=name, roles=roles, password_hash=hash_password(PW),
                     manager_id=manager.id if manager else None, must_change_pw=False)
            db.add(u)
            db.flush()
            return u

        vp = mk("vp", "副总", "USER,VP")
        head = mk("head", "负责人", "USER,DEPT_HEAD", vp)
        lead_a = mk("lead_a", "组长甲", "USER", head)
        lead_b = mk("lead_b", "组长乙", "USER", head)
        a1 = mk("a1", "组员甲一", "USER", lead_a)
        a2 = mk("a2", "组员甲二", "USER", lead_a)
        b1 = mk("b1", "组员乙一", "USER", lead_b)
        keeper = mk("keeper", "领料员", "USER,KEEPER", head)
        mk("wh", "仓管", "USER,WAREHOUSE")
        mk("fin", "财务", "USER,FINANCE")
        mk("admin", "管理员", "USER,ADMIN")
        mk("admin2", "管理员二", "USER,ADMIN")
        db.add_all([
            Project(code="PDJ001", name="测试项目一", customer_code="1195"),
            Project(code="PDJ999", name="已结项项目", customer_code="0001", closed=True),
            Material(code="M-CABLE", name="室内光缆", spec="12芯", unit="米", unit_price=2.5),
            Material(code="M-MPO", name="MPO 散件", spec="母头", unit="套", unit_price=30),
            Location(code="LOC-A3", name="A栋3楼工具柜3", kind="STOCK"),
            Location(code="LOC-B4", name="B栋4F产线3号工位", kind="STATION"),
            Location(code="BOX-1", name="报废箱1", kind="SCRAP_BOX"),
        ])
        db.commit()
    return api


def ids(db_model, **filters):
    with dbmod.SessionLocal() as db:
        from sqlalchemy import select

        q = select(db_model)
        for k, v in filters.items():
            q = q.where(getattr(db_model, k) == v)
        return db.scalar(q)


@pytest.fixture
def ref():
    """按编码 / 账号取 id 的小工具。"""
    class R:
        def user(self, username):
            return ids(User, username=username).id

        def project(self, code):
            return ids(Project, code=code).id

        def material(self, code):
            return ids(Material, code=code).id

        def location(self, code):
            return ids(Location, code=code).id

    return R()
