"""待办问题收尾：金蝶反审核 / 作废进差异池并提醒领料专员；扫已拆分的原码提示扫新码；结存为 0 不能接收转交；
跨单标记按实际摊到的事件判断；分批到货时第二张领料单也能自动生成领用行。"""
from datetime import datetime

import pytest

from app import clock
from app import db as dbmod
from app.services import doc_sync

from .helpers import erp_doc, issue, make_req


class FakeDocReader:
    def __init__(self):
        self.rows = []

    def fetch_docs(self, since=None):
        return self.rows


@pytest.fixture
def reader(monkeypatch):
    r = FakeDocReader()
    monkeypatch.setattr(doc_sync, "make_reader", lambda: r)
    return r


def _ll(no, remark, qty, posted=True, modified=datetime(2026, 11, 2, 9, 30)):
    return {"no": no, "doc_type": "LL", "doc_date": clock.today(), "posted": posted, "remark": remark,
            "ref_doc_no": None, "modified_at": modified,
            "lines": [{"line_no": 1, "material_code": "M-CABLE", "qty": qty, "unit_price": None,
                       "project_code": "PDJ001", "ref_line_no": None}]}


def _sync(reader):
    with dbmod.SessionLocal() as db:
        out = doc_sync.sync_docs(db, reader=reader)
        db.commit()
    return out


def _diffs(org, no):
    return [d for d in org.ok(org.get("fin", "/api/recon/diffs")) if d["erp_doc_no"] == no]


def test_unposted_erp_doc_after_sign_goes_to_diff_pool_and_reposting_clears_it(org, ref, reader):
    req = make_req(org, ref, qty=10)
    org.ok(org.post("lead_a", f"/api/requisitions/{req['id']}/decide", {"approve": True}))
    reader.rows = [_ll("QTCK-1", req["no"], 10)]
    assert _sync(reader)["auto_bound"] == 1
    line = org.ok(org.get("a1", "/api/lines"))[0]
    org.ok(org.post("a1", f"/api/lines/{line['id']}/sign", {"photos": [org.photo("a1")]}))
    # 金蝶反审核
    reader.rows = [_ll("QTCK-1", req["no"], 10, posted=False, modified=datetime(2026, 11, 3, 9, 0))]
    _sync(reader)
    assert any(t["kind"] == "ERP_UNPOSTED" for t in org.ok(org.get("keeper", "/api/todos")))
    org.ok(org.post("keeper", "/api/recon/run"))
    d = _diffs(org, "QTCK-1")[0]
    assert (d["kind"], d["erp_qty"], d["detail_qty"], d["diff"]) == ("ERP_UNPOSTED", "0", "10", "-10")
    clock.set_now(datetime(2026, 12, 2, 9, 0))
    assert org.post("fin", "/api/recon/close/2026-11").status_code == 400
    # 重新审核后自动平，待办关闭，可以关账
    reader.rows = [_ll("QTCK-1", req["no"], 10, modified=datetime(2026, 11, 4, 9, 0))]
    _sync(reader)
    org.ok(org.post("keeper", "/api/recon/run"))
    d = _diffs(org, "QTCK-1")[0]
    assert d["diff"] == "0" and d["state"] == "RESOLVED"
    assert not any(t["kind"] == "ERP_UNPOSTED" for t in org.ok(org.get("keeper", "/api/todos")))
    org.ok(org.post("fin", "/api/recon/close/2026-11"))


def test_unposted_doc_never_used_makes_no_diff(org, ref):
    req = make_req(org, ref, qty=5)
    org.ok(org.post("lead_a", f"/api/requisitions/{req['id']}/decide", {"approve": True}))
    doc = erp_doc(org, "LL", [{"material_code": "M-CABLE", "qty": 5}], posted=False, remark=req["no"])
    org.ok(org.post("keeper", "/api/erp/bind", {"req_item_id": req["items"][0]["id"], "erp_line_id": doc["lines"][0]["id"]}))
    org.ok(org.post("keeper", "/api/recon/run"))
    assert _diffs(org, doc["no"]) == []  # 未过账、未签收：不算差异


def test_scanning_replaced_parent_code_asks_for_new_code(org, ref):
    line = issue(org, ref, qty=10)
    old = line["code"]
    org.ok(org.post("a1", f"/api/lines/{line['id']}/split", {"pieces": [6, 4], "photos": [org.photo("a1")]}))
    for url in (f"/api/scan/{old}", f"/api/lines/by-code/{old}"):
        r = org.get("a1", url)
        assert r.status_code == 400 and "请扫新码" in r.json()["detail"]


def test_cannot_accept_transfer_of_used_up_line(org, ref):
    line = issue(org, ref, qty=3)
    org.ok(org.post("a1", f"/api/lines/{line['id']}/transfer", {"to_user_id": ref.user("a2"), "reason": "交接"}))
    org.ok(org.post("a1", f"/api/lines/{line['id']}/consume", {"qty": 3, "dest": "D02", "photos": [org.photo("a1")]}))
    pending = org.ok(org.get("a1", f"/api/lines/{line['id']}"))["pending_transfer"]
    r = org.post("a2", f"/api/transfers/{pending['id']}/decide?accept=true")
    assert r.status_code == 400 and "拒绝" in r.json()["detail"]
    org.ok(org.post("a2", f"/api/transfers/{pending['id']}/decide?accept=false"))


def test_cross_doc_flag_follows_actual_allocation(org, ref):
    l1 = issue(org, ref, qty=10)
    l2 = issue(org, ref, qty=10)
    for l in (l1, l2):
        org.ok(org.post("a1", f"/api/lines/{l['id']}/return", {"qty": 5}))
    evs = [p["event_id"] for p in org.ok(org.get("keeper", "/api/returns/pending"))]
    # 只摊得到第一条：单一来源，不算跨单
    t1 = erp_doc(org, "TL", [{"material_code": "M-CABLE", "qty": 5}])
    assert org.ok(org.post("keeper", "/api/returns/allocate", {"erp_doc_id": t1["id"], "event_ids": evs}))["cross_doc"] is False
    # 摊到两张领料单的事件：跨单
    t2 = erp_doc(org, "TL", [{"material_code": "M-CABLE", "qty": 5}])
    evs2 = [p["event_id"] for p in org.ok(org.get("keeper", "/api/returns/pending"))]
    assert org.ok(org.post("keeper", "/api/returns/allocate", {"erp_doc_id": t2["id"], "event_ids": evs2}))["cross_doc"] is False
    l3 = issue(org, ref, qty=10)
    l4 = issue(org, ref, qty=10)
    for l in (l3, l4):
        org.ok(org.post("a1", f"/api/lines/{l['id']}/return", {"qty": 2}))
    evs3 = [p["event_id"] for p in org.ok(org.get("keeper", "/api/returns/pending"))]
    t3 = erp_doc(org, "TL", [{"material_code": "M-CABLE", "qty": 4}])
    assert org.ok(org.post("keeper", "/api/returns/allocate", {"erp_doc_id": t3["id"], "event_ids": evs3}))["cross_doc"] is True


def test_second_delivery_auto_binds(org, ref, reader):
    req = make_req(org, ref, qty=10)
    org.ok(org.post("lead_a", f"/api/requisitions/{req['id']}/decide", {"approve": True}))
    reader.rows = [_ll("QTCK-A", req["no"], 6)]
    assert _sync(reader)["auto_bound"] == 1
    reader.rows = [_ll("QTCK-B", req["no"], 4, modified=datetime(2026, 11, 3, 9, 0))]
    assert _sync(reader)["auto_bound"] == 1  # 分批到货：第二张单补足申请数量
    lines = org.ok(org.get("a1", "/api/lines"))
    assert sorted((l["erp_doc_no"], l["qty"]) for l in lines) == [("QTCK-A", "6"), ("QTCK-B", "4")]
    # 已开够申请数量：第三张单不再自动绑定
    reader.rows = [_ll("QTCK-C", req["no"], 1, modified=datetime(2026, 11, 4, 9, 0))]
    assert _sync(reader)["auto_bound"] == 0
