"""金蝶单据接入：研发领料（其他出库单中"研发测试其他出库单"类型）批量增量拉取；退料 / 报废单按单号取回。

金蝶不真正连接：用 httpx.MockTransport 模拟 WebAPI，返回列与《其他出库单》《其他入库单》文档里的全查格式一致。
"""
import json
from datetime import date, datetime

import httpx
import pytest

from app import clock
from app.config import settings
from app.services import doc_sync, k3cloud

from .helpers import issue, make_req


def _transport(by_form, seen):
    """by_form: {表单: [行…]}，按 StartRow / Limit 分页返回。"""
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "LoginByAppSecret" in request.url.path:
            return httpx.Response(200, json={"LoginResultType": 1, "KDSVCSessionId": "sid-1"})
        q = json.loads(body["parameters"][0])
        seen.append(q)
        rows = by_form.get(q["FormId"], [])
        if "FBillNo='" in q["FilterString"]:
            no = q["FilterString"].split("FBillNo='")[1].split("'")[0]
            rows = [r for r in rows if r[0] == no]
        return httpx.Response(200, json=rows[q["StartRow"]:q["StartRow"] + q["Limit"]])
    return httpx.MockTransport(handler)


def _out(no, seq, mat, base_qty, status="C", cancel="A", note="REQ-20261102-0001", price=0.0, proj=" ", head_proj=" ",
         modified="2026-11-20T10:00:00.123"):
    # 列顺序 = DOC_FIELDS[STK_MisDelivery]
    return [no, "2026-11-13T00:00:00", status, cancel, note, head_proj, modified, "研发测试其他出库单",
            1000 + seq, seq, mat, base_qty, price, proj]


def _in(no, seq, mat, base_qty, status="C"):
    # 列顺序 = DOC_FIELDS[STK_MISCELLANEOUS]
    return [no, "2026-11-20T00:00:00", status, "A", "研发退料", "2026-11-20T11:00:00", "标准其他入库单",
            2000 + seq, seq, mat, base_qty, 0.0, " "]


def _reader(by_form, seen):
    client = k3cloud.K3Client("http://k3.test/k3cloud/", "acct", "user", "app", "secret",
                              transport=_transport(by_form, seen))
    return k3cloud.K3CloudReader(client, use_org="100")


def test_fetch_ll_filters_rd_bill_type_merges_pages_and_maps_fields():
    seen = []
    rows = [_out("QTCK-ADT-26110001", 1, "M-CABLE", 80.0, price=2.5, proj="PDJ001"),
            _out("QTCK-ADT-26110001", 2, "M-MPO", 5.0, head_proj="PDJ002"),
            _out("QTCK-ADT-26110002", 1, "M-CABLE", 3.0, status="D"),
            _out("QTCK-ADT-26110003", 1, "M-CABLE", 1.0, cancel="B")]
    reader = _reader({"STK_MisDelivery": rows}, seen)
    reader.client.query = (lambda q: lambda *a, **k: q(*a, **{**k, "page_size": 2}))(reader.client.query)
    docs = reader.fetch_docs(datetime(2026, 11, 1))
    f = seen[0]["FilterString"]
    assert "FDocumentStatus IN ('C','D')" in f and "FStockOrgId.FNumber='100'" in f
    assert "FBillTypeID.FName IN ('研发测试其他出库单')" in f and "FModifyDate>='2026-11-01 00:00:00'" in f
    assert seen[0]["OrderString"] == "FBillNo ASC, FEntity_FEntryId ASC"
    assert [q["StartRow"] for q in seen] == [0, 2, 4]  # 第一张单的两行跨页也要合并成一张
    assert [d["no"] for d in docs] == ["QTCK-ADT-26110001", "QTCK-ADT-26110002", "QTCK-ADT-26110003"]
    d1 = docs[0]
    assert (d1["doc_type"], d1["doc_date"], d1["posted"], d1["remark"]) == ("LL", date(2026, 11, 13), True,
                                                                          "REQ-20261102-0001")
    assert d1["modified_at"] == datetime(2026, 11, 20, 10, 0, 0, 123000)
    assert d1["lines"] == [
        {"line_no": 1, "material_code": "M-CABLE", "qty": 80.0, "unit_price": 2.5, "project_code": "PDJ001",
         "ref_line_no": None},
        {"line_no": 2, "material_code": "M-MPO", "qty": 5.0, "unit_price": None, "project_code": "PDJ002",
         "ref_line_no": None}]
    assert docs[1]["posted"] is False  # 重新审核
    assert docs[2]["posted"] is False  # 已作废


def test_fetch_doc_by_number_picks_form_by_prefix():
    seen = []
    reader = _reader({"STK_MISCELLANEOUS": [_in("QTRK2611000001", 1, "M-CABLE", 4.0)],
                      "STK_MisDelivery": [_out("XX-1", 1, "M-CABLE", -2.0)]}, seen)
    d = reader.fetch_doc("QTRK2611000001", "TL")
    assert seen[0]["FormId"] == "STK_MISCELLANEOUS" and "FBillNo='QTRK2611000001'" in seen[0]["FilterString"]
    assert "FBillTypeID" not in seen[0]["FilterString"]  # 退料 / 报废单不限单据类型
    assert (d["doc_type"], d["posted"], d["lines"][0]["qty"]) == ("TL", True, 4.0)
    # 单号认不出前缀：两张表单都查；退货方向的负数量取绝对值
    seen.clear()
    d = reader.fetch_doc("XX-1", "BF")
    assert [q["FormId"] for q in seen] == ["STK_MISCELLANEOUS", "STK_MisDelivery"]
    assert d["lines"][0]["qty"] == 2.0
    assert reader.fetch_doc("QTRK-NOPE", "TL") is None


class FakeReader:
    def __init__(self, docs):
        self.docs = docs

    def fetch_doc(self, no, doc_type):
        d = self.docs.get(no)
        return {**d, "doc_type": doc_type} if d else None


@pytest.fixture
def k3(monkeypatch):
    for k in ("k3_server_url", "k3_acct_id", "k3_username", "k3_app_id", "k3_app_secret"):
        monkeypatch.setattr(settings, k, "x")
    docs = {}
    monkeypatch.setattr(doc_sync, "make_reader", lambda: FakeReader(docs))
    return docs


def _tl(no, qty, posted=True):
    return {"no": no, "doc_date": clock.today(), "posted": posted, "remark": "研发退料", "ref_doc_no": None,
            "modified_at": None, "lines": [{"line_no": 1, "material_code": "M-CABLE", "qty": qty,
                                            "unit_price": None, "project_code": None, "ref_line_no": None}]}


def test_keeper_fetches_return_doc_by_number_then_allocates(org, ref, k3):
    line = issue(org, ref, qty=10)
    org.ok(org.post("a1", f"/api/lines/{line['id']}/return", {"qty": 4}))
    ev = [e for e in org.ok(org.get("a1", f"/api/lines/{line['id']}"))["events"] if e["etype"] == "RETURN"][0]
    r = org.post("keeper", "/api/erp/docs/fetch", {"no": "QTRK2611000009", "doc_type": "TL"})
    assert r.status_code == 400 and "查不到" in r.json()["detail"]
    assert org.post("a1", "/api/erp/docs/fetch", {"no": "QTRK1", "doc_type": "TL"}).status_code == 403
    k3["QTRK2611000009"] = _tl("QTRK2611000009", 4, posted=False)
    doc = org.ok(org.post("keeper", "/api/erp/docs/fetch", {"no": "QTRK2611000009", "doc_type": "TL"}))
    assert doc["doc_type"] == "TL" and doc["source"] == "API" and not doc["posted"]
    r = org.post("keeper", "/api/returns/allocate", {"erp_doc_id": doc["id"], "event_ids": [ev["id"]]})
    assert r.status_code == 400 and "过账" in r.json()["detail"]
    # 金蝶审核后再取一次：按最新内容更新同一张单
    k3["QTRK2611000009"] = _tl("QTRK2611000009", 4)
    doc2 = org.ok(org.post("keeper", "/api/erp/docs/fetch", {"no": "QTRK2611000009", "doc_type": "TL"}))
    assert doc2["id"] == doc["id"] and doc2["posted"]
    org.ok(org.post("keeper", "/api/returns/allocate", {"erp_doc_id": doc["id"], "event_ids": [ev["id"]]}))
    assert org.ok(org.get("a1", f"/api/lines/{line['id']}"))["returned"] == "4"
    # 同一单号不能再按报废单取
    r = org.post("keeper", "/api/erp/docs/fetch", {"no": "QTRK2611000009", "doc_type": "BF"})
    assert r.status_code == 400 and "其他类型" in r.json()["detail"]


def test_fetch_requires_k3_config(org):
    r = org.post("keeper", "/api/erp/docs/fetch", {"no": "QTRK1", "doc_type": "TL"})
    assert r.status_code == 400 and "未配置金蝶" in r.json()["detail"]


def test_pulled_ll_with_req_no_creates_line(org, ref, monkeypatch):
    """端到端：金蝶其他出库单（研发类型）→ 单据镜像 → 按备注申请号自动生成领用行。"""
    req = make_req(org, ref, qty=80)
    org.ok(org.post("lead_a", f"/api/requisitions/{req['id']}/decide", {"approve": True}))
    seen = []
    reader = _reader({"STK_MisDelivery": [_out("QTCK-ADT-26110001", 1, "M-CABLE", 80.0, note=f"工程验证 {req['no']}",
                                                modified="2026-11-02T08:00:00")]}, seen)
    from app import db as dbmod

    with dbmod.SessionLocal() as db:
        stats = doc_sync.sync_docs(db, reader=reader)
        db.commit()
    assert (stats["created"], stats["auto_bound"]) == (1, 1)
    lines = org.ok(org.get("a1", "/api/lines"))
    assert lines[0]["erp_doc_no"] == "QTCK-ADT-26110001" and lines[0]["status"] == "S0"
