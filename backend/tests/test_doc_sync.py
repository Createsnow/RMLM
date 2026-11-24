"""金蝶单据自动拉取：开单过账后系统自动生成领用行，使用人直接签收，不需要人工导入。

金蝶不真正连接：用假读取器返回 ErpReader.fetch_docs 格式的单据。
"""
from datetime import datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from app import clock
from app import db as dbmod
from app.config import settings
from app.models import ErpDoc, ErpLine, LedgerLine, Todo, User
from app.services import doc_sync

from .helpers import erp_doc, make_req


class FakeDocReader:
    def __init__(self, rows=None, error=None):
        self.rows, self.error, self.calls = rows or [], error, []

    def fetch_docs(self, since=None):
        self.calls.append(since)
        if self.error:
            raise self.error
        return self.rows


def _ll(no, remark, lines, posted=True, modified=datetime(2026, 11, 2, 9, 30), doc_date=None):
    return {"no": no, "doc_type": "LL", "doc_date": doc_date or clock.today(), "posted": posted, "remark": remark,
            "ref_doc_no": None, "modified_at": modified,
            "lines": [{"line_no": i + 1, "project_code": "PDJ001", "ref_line_no": None, **ln}
                      for i, ln in enumerate(lines)]}


@pytest.fixture
def reader(monkeypatch):
    r = FakeDocReader()
    monkeypatch.setattr(doc_sync, "make_reader", lambda: r)
    return r


@pytest.fixture
def k3_on(monkeypatch):
    for k in ("k3_server_url", "k3_acct_id", "k3_username", "k3_app_id", "k3_app_secret"):
        monkeypatch.setattr(settings, k, "x")


def _line_of(item_id):
    with dbmod.SessionLocal() as db:
        return db.scalar(select(LedgerLine).where(LedgerLine.req_item_id == item_id))


def _approved_req(api, ref, **kw):
    req = make_req(api, ref, **kw)
    api.ok(api.post("lead_a", f"/api/requisitions/{req['id']}/decide", {"approve": True}))
    return req


def test_pulled_doc_creates_line_and_user_signs_directly(org, ref, reader):
    req = _approved_req(org, ref)
    reader.rows = [_ll("QTCK-0001", f"研发领料 {req['no']}", [{"material_code": "M-CABLE", "qty": 10, "unit_price": 2.5}])]

    assert org.post("a1", "/api/erp/docs/sync").status_code == 403
    st = org.ok(org.post("keeper", "/api/erp/docs/sync"))
    assert (st["created"], st["auto_bound"], st["mode"]) == (1, 1, "full")

    line = _line_of(req["items"][0]["id"])
    assert line is not None and line.qty == Decimal("10") and line.unit_price == Decimal("2.5")
    assert line.issued_by is None and line.self_issue is False  # 系统生成，没有经手的领料专员
    with dbmod.SessionLocal() as db:
        assert db.scalar(select(ErpDoc).where(ErpDoc.no == "QTCK-0001")).source == "API"
    assert any(t["kind"] == "SIGN" for t in org.ok(org.get("a1", "/api/todos")))
    out = org.ok(org.post("a1", f"/api/lines/{line.id}/sign", {"photos": [org.photo("a1")], "no_code_reason": "标签破损"}))
    assert out["status"] == "S1"

    # 再拉一次：增量、没有变化、不重复生成
    st = org.ok(org.post("keeper", "/api/erp/docs/sync"))
    assert (st["unchanged"], st["auto_bound"], st["mode"]) == (1, 0, "incremental")
    assert reader.calls[1] == datetime(2026, 11, 2, 9, 20)  # 水位往前回退 10 分钟
    st = org.ok(org.get("keeper", "/api/erp/docs/sync/status"))
    assert st["watermark"] == "2026-11-02T09:30:00"


def test_unposted_then_posted_and_changed_in_erp(org, ref, reader):
    req = _approved_req(org, ref)
    reader.rows = [_ll("QTCK-0002", req["no"], [{"material_code": "M-CABLE", "qty": 10, "unit_price": 2.5}],
                       posted=False)]
    st = org.ok(org.post("keeper", "/api/erp/docs/sync"))
    assert (st["created"], st["auto_bound"]) == (1, 0)  # 未过账不生成领用行
    assert _line_of(req["items"][0]["id"]) is None

    # 金蝶审核过账，数量改成 8
    reader.rows = [_ll("QTCK-0002", req["no"], [{"material_code": "M-CABLE", "qty": 8, "unit_price": 2.5}])]
    st = org.ok(org.post("keeper", "/api/erp/docs/sync"))
    assert (st["updated"], st["auto_bound"]) == (1, 1)
    line = _line_of(req["items"][0]["id"])
    assert line.qty == Decimal("8")

    # 生成领用行后金蝶又改数量：镜像跟着改，对账出差异
    reader.rows = [_ll("QTCK-0002", req["no"], [{"material_code": "M-CABLE", "qty": 9, "unit_price": 2.5}])]
    org.ok(org.post("keeper", "/api/erp/docs/sync"))
    diffs = org.ok(org.get("fin", "/api/recon/diffs?period=2026-11"))
    assert [(d["kind"], d["diff"]) for d in diffs] == [("LL_MISMATCH", "1")]

    # 金蝶删掉这一行：镜像数量置 0，对账出差异
    reader.rows = [_ll("QTCK-0002", req["no"], [])]
    st = org.ok(org.post("keeper", "/api/erp/docs/sync"))
    assert st["updated"] == 1
    with dbmod.SessionLocal() as db:
        assert db.scalar(select(ErpLine).where(ErpLine.id == line.erp_line_id)).qty == 0


def test_waits_for_approval_and_skips_ambiguous(org, ref, reader):
    pending = make_req(org, ref)
    reader.rows = [_ll("QTCK-0003", pending["no"], [{"material_code": "M-CABLE", "qty": 10}])]
    assert org.ok(org.post("keeper", "/api/erp/docs/sync"))["auto_bound"] == 0  # 申请还没审核
    org.ok(org.post("lead_a", f"/api/requisitions/{pending['id']}/decide", {"approve": True}))
    out = org.ok(org.post("admin", "/api/admin/jobs/quarter_hour"))
    assert out["auto_bound"] == 1  # 审核通过后，下一次巡检自动生成
    assert _line_of(pending["items"][0]["id"]) is not None

    # 同一申请里同一物料两条明细：对不上唯一一条，留给领料专员手工绑定
    two = _approved_req(org, ref, items=[{"material_id": ref.material("M-CABLE"), "qty": 5},
                                         {"material_id": ref.material("M-CABLE"), "qty": 5}])
    reader.rows = [_ll("QTCK-0004", two["no"], [{"material_code": "M-CABLE", "qty": 5}])]
    assert org.ok(org.post("keeper", "/api/erp/docs/sync"))["auto_bound"] == 0
    sug = org.ok(org.get("keeper", "/api/erp/suggestions"))
    assert {s["req_item_id"] for s in sug} == {it["id"] for it in two["items"]}


def test_manual_doc_is_not_auto_bound(org, ref):
    req = _approved_req(org, ref)
    erp_doc(org, "LL", [{"material_code": "M-CABLE", "qty": 10}], remark=req["no"])  # 手工录入
    assert org.ok(org.post("admin", "/api/admin/jobs/quarter_hour"))["auto_bound"] == 0
    assert _line_of(req["items"][0]["id"]) is None


def test_urgent_line_gets_pulled_doc_attached(org, ref, reader):
    req = make_req(org, ref, urgent=True, urgent_note="组长甲口头同意")
    line = org.ok(org.post("keeper", f"/api/requisitions/{req['id']}/urgent-handover/{req['items'][0]['id']}"))
    org.ok(org.post("lead_a", f"/api/requisitions/{req['id']}/decide", {"approve": True}))
    reader.rows = [_ll("QTCK-0005", req["no"], [{"material_code": "M-CABLE", "qty": 10}])]
    assert org.ok(org.post("keeper", "/api/erp/docs/sync"))["auto_bound"] == 1
    assert "紧急·待补" not in org.ok(org.get("a1", f"/api/lines/{line['id']}"))["flags"]


def test_quarter_sync_skips_until_forms_confirmed_and_reports_errors(org, ref, reader, k3_on):
    def admin_fail_todos():
        with dbmod.SessionLocal() as db:
            admin = db.scalar(select(User).where(User.username == "admin"))
            return list(db.scalars(select(Todo).where(Todo.user_id == admin.id, Todo.kind == "SYNC_FAIL")))

    reader.error = NotImplementedError("金蝶单据表单尚未确认，暂不能自动拉取单据")
    out = org.ok(org.post("admin", "/api/admin/jobs/quarter_hour"))
    assert "尚未确认" in out["doc_sync"]["skipped"] and not admin_fail_todos()
    r = org.post("keeper", "/api/erp/docs/sync")
    assert r.status_code == 400 and "尚未确认" in r.json()["detail"]

    reader.error = RuntimeError("连接超时")
    out = org.ok(org.post("admin", "/api/admin/jobs/quarter_hour"))
    assert "连接超时" in out["doc_sync"]["error"] and len(admin_fail_todos()) == 1

    # 单张单据有问题（物料编码不存在）：跳过这张、其他照常，派待办
    req = _approved_req(org, ref)
    reader.error = None
    reader.rows = [_ll("QTCK-0006", "", [{"material_code": "NOPE", "qty": 1}]),
                   _ll("QTCK-0007", req["no"], [{"material_code": "M-CABLE", "qty": 10}])]
    out = org.ok(org.post("admin", "/api/admin/jobs/quarter_hour"))
    assert out["doc_sync"]["created"] == 1 and out["doc_sync"]["auto_bound"] == 1
    assert "QTCK-0006" in out["doc_sync"]["errors"][0]
    assert any("QTCK-0006" in t["title"] for t in org.ok(org.get("keeper", "/api/todos")))


def test_closed_month_change_is_not_applied(org, ref, reader):
    reader.rows = [_ll("QTCK-0008", "", [{"material_code": "M-CABLE", "qty": 5}])]
    org.ok(org.post("keeper", "/api/erp/docs/sync"))
    with dbmod.SessionLocal() as db:  # 让这张单据平账，好关 11 月
        ln = db.scalar(select(ErpLine).join(ErpDoc).where(ErpDoc.no == "QTCK-0008"))
        ln.qty = 0
        db.commit()
    clock.set_now(datetime(2026, 12, 2, 10, 0))
    org.ok(org.post("fin", "/api/recon/close/2026-11"))
    reader.rows = [_ll("QTCK-0008", "", [{"material_code": "M-CABLE", "qty": 7}], modified=clock.now())]
    st = org.ok(org.post("keeper", "/api/erp/docs/sync"))
    assert st["updated"] == 0 and "已关账" in st["errors"][0]
