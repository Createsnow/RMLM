"""外部接口：金蝶物料同步、企业微信（登录 / 通讯录 / 推送）、表结构补列。

金蝶与企微都不真正连接：金蝶用 httpx.MockTransport 模拟 WebAPI，企微用假客户端。
"""
import json
from datetime import datetime

import httpx
import pytest
from sqlalchemy import inspect, select, text

from app import db as dbmod
from app.config import settings
from app.main import upgrade_schema
from app.models import Material, Todo, User
from app.services import k3cloud, master_sync, wecom

from .helpers import issue, make_req


# ------------------------------------------------------------------ 金蝶客户端
def _k3_transport(pages, seen):
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "LoginByAppSecret" in request.url.path:
            seen["login"] = body["parameters"]
            return httpx.Response(200, json={"LoginResultType": 1, "KDSVCSessionId": "sid-1"})
        assert request.headers.get("cookie", "").find("kdservice-sessionid=sid-1") >= 0
        q = json.loads(body["parameters"][0])
        seen.setdefault("queries", []).append(q)
        return httpx.Response(200, json=pages[q["StartRow"] // q["Limit"]])
    return httpx.MockTransport(handler)


def _row(no, name, forbid="A", mid=1, modified="2026-11-20T10:00:00.123"):
    return [mid, no, name, "规格" + no, "米", "光缆（室内）", "C", forbid, modified]


def test_k3_client_pages_and_maps_rows():
    seen = {}
    pages = [[_row("1000000", "单模光纤", mid=304646), _row("1000001", "室内光缆", forbid="B", mid=2)],
             [_row("1000002", "MPO 散件", mid=3)]]
    client = k3cloud.K3Client("http://k3.test/k3cloud/", "acct", "user", "app", "secret",
                              transport=_k3_transport(pages, seen))
    reader = k3cloud.K3CloudReader(client, use_org="100")
    # 每页 2 行，第二页不满一页即停止
    rows = [k3cloud.material_row(r) for r in client.query(
        "BD_MATERIAL", k3cloud.MATERIAL_FIELDS, k3cloud.material_filter("100", datetime(2026, 11, 1)), page_size=2)]
    assert [r["code"] for r in rows] == ["1000000", "1000001", "1000002"]
    assert rows[0] == {"erp_id": 304646, "code": "1000000", "name": "单模光纤", "spec": "规格1000000", "unit": "米",
                       "category": "光缆（室内）", "forbidden": False,
                       "modified_at": datetime(2026, 11, 20, 10, 0, 0, 123000)}
    assert rows[1]["forbidden"] is True
    assert seen["login"] == ["acct", "user", "app", "secret", 2052]
    f = seen["queries"][0]["FilterString"]
    assert "FDocumentStatus='C'" in f and "FUseOrgId.FNumber='100'" in f and "FModifyDate>='2026-11-01 00:00:00'" in f
    assert [q["StartRow"] for q in seen["queries"]] == [0, 2]
    reader.close()


def test_k3_client_reports_errors():
    def handler(request):
        if "LoginByAppSecret" in request.url.path:
            return httpx.Response(200, json={"LoginResultType": 1})
        return httpx.Response(200, json=[[{"Result": {"ResponseStatus": {
            "IsSuccess": False, "MsgCode": 2, "Errors": [{"Message": "没有权限"}]}}}]])
    client = k3cloud.K3Client("http://k3.test/", "a", "u", "i", "s", transport=httpx.MockTransport(handler))
    with pytest.raises(k3cloud.K3Error, match="没有权限"):
        client.query("BD_MATERIAL", ["FNumber"])

    bad = k3cloud.K3Client("http://k3.test/", "a", "u", "i", "s", transport=httpx.MockTransport(
        lambda r: httpx.Response(200, json={"LoginResultType": 0, "Message": "密钥错误"})))
    with pytest.raises(k3cloud.K3Error, match="密钥错误"):
        bad.login()



def test_k3_client_retries_pages_and_uses_timeouts():
    calls = {"query": 0, "timeouts": {}}

    def handler(request):
        kind = "login" if "LoginByAppSecret" in request.url.path else "query"
        calls["timeouts"][kind] = request.extensions["timeout"]["read"]
        if kind == "login":
            return httpx.Response(200, json={"LoginResultType": 1, "KDSVCSessionId": "sid"})
        calls["query"] += 1
        if calls["query"] <= 2:  # 前两次：一次断连、一次 502，都应重试
            if calls["query"] == 1:
                raise httpx.ConnectError("boom", request=request)
            return httpx.Response(502)
        return httpx.Response(200, json=[["1000000"]])

    client = k3cloud.K3Client("http://k3.test/", "a", "u", "i", "s", transport=httpx.MockTransport(handler),
                              page_retries=2, page_retry_seconds=0)
    client.login_timeout, client.query_timeout = 7, 9
    assert client.query("BD_MATERIAL", ["FNumber"]) == [{"FNumber": "1000000"}]
    assert calls["query"] == 3 and calls["timeouts"] == {"login": 7, "query": 9}

    # 重试用尽仍失败：报网络错误
    calls["query"] = -10
    client2 = k3cloud.K3Client("http://k3.test/", "a", "u", "i", "s", transport=httpx.MockTransport(handler),
                               page_retries=1, page_retry_seconds=0)
    with pytest.raises(k3cloud.K3NetworkError):
        client2.query("BD_MATERIAL", ["FNumber"])
    assert calls["query"] == -8


# ------------------------------------------------------------------ 物料同步
class FakeReader:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def fetch_materials(self, since=None):
        self.calls.append(since)
        return self.rows


def test_material_sync_creates_updates_and_disables(org, ref, monkeypatch):
    rows = [
        {"code": "M-CABLE", "name": "室内光缆 12 芯", "spec": "G657A1", "unit": "米", "category": "光缆（室内）",
         "erp_id": 11, "forbidden": False, "modified_at": datetime(2026, 11, 20, 8, 0)},
        {"code": "M-MPO", "name": "MPO 散件", "spec": "母头", "unit": "套", "category": "散件",
         "erp_id": 12, "forbidden": True, "modified_at": datetime(2026, 11, 21, 9, 30)},
        {"code": "1000000", "name": "单模光纤", "spec": None, "unit": "米", "category": "光缆（室内）",
         "erp_id": 304646, "forbidden": False, "modified_at": datetime(2026, 11, 19, 7, 0)},
        {"code": "9999999", "name": "从没用过的禁用料", "forbidden": True, "modified_at": None},
    ]
    reader = FakeReader(rows)
    monkeypatch.setattr(master_sync, "make_reader", lambda: reader)

    assert org.post("a1", "/api/master/sync/material").status_code == 403
    st = org.ok(org.post("keeper", "/api/master/sync/material"))
    assert st["mode"] == "full" and st["created"] == 1 and st["updated"] == 2 and st["disabled"] == 1
    assert reader.calls == [None]

    with dbmod.SessionLocal() as db:
        cable = db.scalar(select(Material).where(Material.code == "M-CABLE"))
        assert (cable.name, cable.spec, cable.erp_id, cable.active) == ("室内光缆 12 芯", "G657A1", 11, True)
        assert str(cable.unit_price) == "2.5000"  # 单价不被物料档案覆盖
        assert db.scalar(select(Material).where(Material.code == "9999999")) is None

    # 停用物料：列表默认不显示，也不能再申请
    codes = [m["code"] for m in org.ok(org.get("a1", "/api/materials"))]
    assert "M-MPO" not in codes and "1000000" in codes
    assert "M-MPO" in [m["code"] for m in org.ok(org.get("a1", "/api/materials?include_inactive=true"))]
    r = org.post("a1", "/api/requisitions", {
        "project_id": ref.project("PDJ001"), "reason_code": "R01", "remark": "验证",
        "expected_return": "2026-12-30", "items": [{"material_id": ref.material("M-MPO"), "qty": 1}]})
    assert r.status_code == 400 and "禁用" in r.json()["detail"]

    # 第二次是增量：从水位往前回退 10 分钟
    st2 = org.ok(org.post("keeper", "/api/master/sync/material"))
    assert st2["mode"] == "incremental" and reader.calls[1] == datetime(2026, 11, 21, 9, 20)
    status = org.ok(org.get("keeper", "/api/master/sync/status"))
    assert status["watermark"] == "2026-11-21T09:30:00" and status["last_result"]["mode"] == "incremental"
    assert status["configured"] is False  # 测试环境没有配置金蝶


def test_material_sync_requires_config(org):
    r = org.post("keeper", "/api/master/sync/material")
    assert r.status_code == 400 and "未配置金蝶" in r.json()["detail"]


# ------------------------------------------------------------------ 表结构补列
def test_upgrade_schema_adds_missing_columns(org):
    with dbmod.engine.begin() as conn:
        conn.execute(text("ALTER TABLE material DROP COLUMN active"))
        conn.execute(text("ALTER TABLE material DROP COLUMN synced_at"))
    upgrade_schema()
    cols = {c["name"] for c in inspect(dbmod.engine).get_columns("material")}
    assert {"active", "synced_at"} <= cols
    with dbmod.SessionLocal() as db:  # 已有物料补列后默认启用
        assert all(m.active for m in db.scalars(select(Material)))
    upgrade_schema()  # 重复执行无副作用


# ------------------------------------------------------------------ 企业微信
class FakeWeCom:
    def __init__(self, directory=None, code_map=None):
        self.directory = directory or {}
        self.code_map = code_map or {}
        self.sent, self.fail = [], False

    def department_ids(self, root):
        return {1, 2}

    def user_ids(self, depts):
        return list(self.directory)

    def get_user(self, uid):
        return self.directory[uid]

    def user_by_code(self, code):
        if code not in self.code_map:
            raise wecom.WeComError("不是本企业成员，无法登录")
        return self.code_map[code]

    def authorize_url(self, redirect, state):
        return f"https://open.weixin.qq.com/connect/oauth2/authorize?redirect_uri={redirect}&state={state}"

    def send_card(self, userids, title, desc, url):
        if self.fail:
            raise wecom.WeComError("企业微信接口错误 81013：user invalid")
        self.sent.append((userids, title, url))

    def close(self):
        pass


@pytest.fixture
def wx(monkeypatch):
    monkeypatch.setattr(settings, "wecom_corp_id", "ww-test")
    monkeypatch.setattr(settings, "wecom_agent_id", "1000002")
    monkeypatch.setattr(settings, "wecom_secret", "s")
    monkeypatch.setattr(settings, "public_url", "https://rmlm.example.com")
    fake = FakeWeCom()
    monkeypatch.setattr(wecom, "make_client", lambda: fake)
    return fake


def _person(name, status=1, leader=None, position=""):
    return {"name": name, "status": status, "position": position, "direct_leader": [leader] if leader else []}


def test_wecom_contact_sync(org, ref, wx):
    wx.directory = {
        "zhang.lead": _person("组长甲", position="工艺开发工程师"),
        "li.a1": _person("组员甲一", leader="zhang.lead", position="助理工艺工程师"),
        "new.guy": _person("新同事", leader="zhang.lead"),
        "b1.quit": _person("组员乙一", status=5),
    }
    st = org.ok(org.post("admin", "/api/admin/wecom/sync-contacts"))
    assert st["bound"] == 3 and st["created"] == 1 and st["deactivated"] == 1
    with dbmod.SessionLocal() as db:
        get = lambda wid: db.scalar(select(User).where(User.wecom_userid == wid))  # noqa: E731
        lead, a1, new = get("zhang.lead"), get("li.a1"), get("new.guy")
        assert lead.username == "lead_a" and lead.title == "工艺开发工程师"
        assert a1.username == "a1" and a1.manager_id == lead.id
        assert new.username == "new.guy" and new.manager_id == lead.id and new.roles == "USER"
        assert get("b1.quit").active is False

    # a1 名下有结存时在企微离职：不停用，派待办给直属上级
    issue(org, ref, who="a1", qty=5)
    wx.directory["li.a1"]["status"] = 5
    st = org.ok(org.post("admin", "/api/admin/wecom/sync-contacts"))
    assert st["blocked"] == 1 and st["deactivated"] == 0
    with dbmod.SessionLocal() as db:
        t = db.scalar(select(Todo).where(Todo.kind == "OFFBOARD"))
        assert t.user_id == ref.user("lead_a") and "1 行未清零" in t.title
    assert org.ok(org.get("admin", "/api/admin/wecom/status"))["last_result"]["blocked"] == 1


def test_wecom_login(org, ref, wx):
    assert org.c.get("/api/auth/config").json() == {"wecom": True, "wms": False}
    u = org.c.get("/api/auth/wecom/url", params={"next": "/m/req/5"}).json()
    assert "redirect_uri=https://rmlm.example.com/login?next=/m/req/5" in u["url"] and u["state"]
    wx.code_map = {"c-ok": "li.a1", "c-unbound": "nobody"}
    r = org.c.post("/api/auth/wecom", json={"code": "c-ok"})
    assert r.status_code == 401 and "尚未开通" in r.json()["detail"]
    with dbmod.SessionLocal() as db:
        db.get(User, ref.user("a1")).wecom_userid = "li.a1"
        db.commit()
    r = org.c.post("/api/auth/wecom", json={"code": "c-ok"})
    assert r.status_code == 200 and r.json()["user"]["username"] == "a1"
    assert org.c.post("/api/auth/wecom", json={"code": "c-bad"}).status_code == 401


def test_wecom_push_outbox(org, ref, wx):
    with dbmod.SessionLocal() as db:
        db.get(User, ref.user("lead_a")).wecom_userid = "zhang.lead"
        db.commit()
    req = make_req(org, ref, who="a1")
    with dbmod.SessionLocal() as db:
        t = db.scalar(select(Todo).where(Todo.kind == "APPROVE", Todo.ref_id == req["id"]))
        assert t.push_state == "PENDING"
        a1_todos = db.scalars(select(Todo).where(Todo.user_id == ref.user("a1"))).all()
        assert all(x.push_state is None for x in a1_todos)  # 没有企微账号的人不推送
        assert wecom.push_pending(db) == {"sent": 1, "failed": 0}
        db.commit()
        assert wx.sent == [(["zhang.lead"], t.title, "https://rmlm.example.com/m/approve")]
        assert db.get(Todo, t.id).push_state == "SENT"

    # 失败重试 3 次后放弃，站内待办不受影响
    org.ok(org.post("lead_a", f"/api/requisitions/{req['id']}/decide", {"approve": False, "note": "数量过多"}))
    with dbmod.SessionLocal() as db:
        db.get(User, ref.user("a1")).wecom_userid = "li.a1"
        db.commit()
    make_req(org, ref, who="a1", qty=3)
    wx.fail = True
    with dbmod.SessionLocal() as db:
        for _ in range(3):
            wecom.push_pending(db)
        db.commit()
        t = db.scalars(select(Todo).where(Todo.kind == "APPROVE").order_by(Todo.id.desc())).first()
        assert (t.push_state, t.push_tries, t.state) == ("FAIL", 3, "OPEN")
