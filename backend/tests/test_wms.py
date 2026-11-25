"""仓储系统对接：签收扫仓储条码（一个码一条领用行）、拆分向仓储申请新码、扫码找待签收行、码绑定率、表结构升级。

仓储系统不真正连接：用 httpx.MockTransport 模拟一个按《仓储接口规范》实现的仓储服务。
"""
import json
from decimal import Decimal

import httpx
import pytest
from sqlalchemy import inspect, select, text

from app import db as dbmod
from app.config import settings
from app.errors import BizError
from app.main import upgrade_schema
from app.models import LedgerLine, MaterialCode
from app.services import wms

from .helpers import erp_doc, issue, make_req


class FakeWms:
    """内存里的仓储系统。"""

    def __init__(self):
        self.codes: dict[str, dict] = {}
        self.splits: dict[str, list] = {}
        self.calls: list[tuple] = []
        self.seq = 0
        self.down = False

    def add(self, barcode, material, qty, status="OUTBOUND", out_doc_no=None, kind="PIECE"):
        self.codes[barcode] = {"barcode": barcode, "material_code": material, "qty": qty, "unit": "米",
                               "kind": kind, "status": status, "out_doc_no": out_doc_no, "out_line_no": 1}

    def handler(self, request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer tok-test"
        self.calls.append((request.method, request.url.path))
        if self.down:
            return httpx.Response(503)
        path = request.url.path
        if request.method == "GET" and path.startswith("/api/rmlm/v1/barcodes/"):
            bc = self.codes.get(path.rsplit("/", 1)[1])
            if bc is None:
                return httpx.Response(200, json={"code": 404, "message": "条码不存在"})
            return httpx.Response(200, json={"code": 0, "message": "ok", "data": bc})
        if request.method == "POST" and path == "/api/rmlm/v1/barcodes/split":
            body = json.loads(request.content)
            if body["request_id"] in self.splits:
                return httpx.Response(200, json={"code": 0, "data": {"barcodes": self.splits[body["request_id"]]}})
            parent = self.codes[body["parent_barcode"]]
            if parent["status"] == "SPLIT":
                return httpx.Response(200, json={"code": 409, "message": "该码已拆分"})
            out = []
            for q in body["pieces"]:
                self.seq += 1
                code = f"WMS-S{self.seq:04d}"
                self.add(code, parent["material_code"], q)
                self.codes[code]["parent_barcode"] = parent["barcode"]
                out.append({"barcode": code, "qty": q})
            parent["status"] = "SPLIT"
            self.splits[body["request_id"]] = out
            return httpx.Response(200, json={"code": 0, "message": "ok", "data": {"barcodes": out}})
        return httpx.Response(404)


@pytest.fixture
def fake(monkeypatch):
    f = FakeWms()
    monkeypatch.setattr(settings, "wms_base_url", "http://wms.test/api")
    monkeypatch.setattr(settings, "wms_token", "tok-test")
    monkeypatch.setattr(wms, "make_client", lambda: wms.WmsClient(transport=httpx.MockTransport(f.handler)))
    return f


def _sign(org, who, line_id, codes):
    return org.post(who, f"/api/lines/{line_id}/sign", {"photos": [org.photo(who)], "codes": codes})


def _err(resp):
    assert resp.status_code == 400, resp.text
    return resp.json()["detail"]


# ------------------------------------------------------------------ 客户端
def test_client_parses_and_maps_errors(fake):
    fake.add("WMS-1", "M-CABLE", 100, out_doc_no="LL-1")
    bc = wms.lookup("WMS-1")
    assert (bc.material_code, bc.qty, bc.status, bc.out_doc_no) == ("M-CABLE", Decimal("100.000"), "OUTBOUND", "LL-1")
    assert wms.lookup("NOPE") is None
    fake.down = True
    with pytest.raises(BizError, match="暂时连不上"):
        wms.lookup("WMS-1")


# ------------------------------------------------------------------ 签收
def test_sign_requires_wms_codes_and_splits_by_code(org, ref, fake):
    line = issue(org, ref, qty=200, sign=False)
    assert line["code"].startswith("LN")  # 签收前只是内部编号
    doc_no = line["_doc"]["no"]
    assert org.ok(org.get("a1", f"/api/lines/{line['id']}"))["actions"]["sign_needs_codes"] is True
    fake.add("WMS-A", "M-CABLE", 100, out_doc_no=doc_no)
    fake.add("WMS-B", "M-CABLE", 100, out_doc_no=doc_no)

    assert "扫描" in _err(_sign(org, "a1", line["id"], []))
    assert "合计 100.000" in _err(_sign(org, "a1", line["id"], ["WMS-A"]))
    res = org.ok(_sign(org, "a1", line["id"], ["WMS-A", "WMS-B"]))
    assert res["code"] == "WMS-A" and res["qty"] == "100" and res["signed"]
    assert [l["code"] for l in res["result"]["lines"]] == ["WMS-A", "WMS-B"]

    with dbmod.SessionLocal() as db:
        rows = list(db.scalars(select(LedgerLine).where(LedgerLine.requisition_id == line["_req"]["id"])
                               .order_by(LedgerLine.id)))
        assert [(l.code, l.qty, l.level, l.signed, l.erp_line_id) for l in rows] == [
            ("WMS-A", Decimal("100.000"), 1, True, line["_doc"]["lines"][0]["id"]),
            ("WMS-B", Decimal("100.000"), 1, True, line["_doc"]["lines"][0]["id"])]
        assert {c.source for c in db.scalars(select(MaterialCode))} == {"WMS"}
    # 对账：同一单据行的两条领用行合计 = 金蝶数量，不产生差异
    org.ok(org.post("keeper", "/api/recon/run"))
    diffs = [d for d in org.ok(org.get("keeper", "/api/recon/diffs")) if d["erp_doc_no"] == doc_no]
    assert all(d["diff"] == "0" and d["state"] == "RESOLVED" and d["detail_qty"] == "200" for d in diffs)
    assert org.ok(org.get("head", "/api/reports/dashboard"))["gate"]["code_bind_rate"] == 100.0
    # 扫码直接找到这条行
    assert org.ok(org.get("a1", "/api/lines/by-code/WMS-B"))["code"] == "WMS-B"


def test_sign_rejects_bad_codes(org, ref, fake):
    line = issue(org, ref, qty=100, sign=False)
    doc_no = line["_doc"]["no"]
    fake.add("IN-STOCK", "M-CABLE", 100, status="IN_STOCK", out_doc_no=doc_no)
    fake.add("OTHER-MAT", "M-MPO", 100, out_doc_no=doc_no)
    fake.add("OTHER-DOC", "M-CABLE", 100, out_doc_no="QTCK-OTHER")
    fake.add("GOOD", "M-CABLE", 100, out_doc_no=doc_no)
    assert "仍在库" in _err(_sign(org, "a1", line["id"], ["IN-STOCK"]))
    assert "不符" in _err(_sign(org, "a1", line["id"], ["OTHER-MAT"]))
    assert "QTCK-OTHER" in _err(_sign(org, "a1", line["id"], ["OTHER-DOC"]))
    assert "查不到" in _err(_sign(org, "a1", line["id"], ["NOPE"]))
    assert "两次" in _err(_sign(org, "a1", line["id"], ["GOOD", "GOOD"]))
    fake.down = True
    assert "连不上" in _err(_sign(org, "a1", line["id"], ["GOOD"]))
    fake.down = False
    org.ok(_sign(org, "a1", line["id"], ["GOOD"]))
    # 同一个码不能再挂到别的行
    other = issue(org, ref, qty=100, sign=False)
    assert "已被其他领用行使用" in _err(_sign(org, "a1", other["id"], ["GOOD"]))


def test_scan_unsigned_barcode_finds_pending_line(org, ref, fake):
    line = issue(org, ref, qty=50, sign=False)
    fake.add("WMS-P", "M-CABLE", 50, out_doc_no=line["_doc"]["no"])
    got = org.ok(org.get("a1", "/api/lines/by-code/WMS-P"))
    assert got["id"] == line["id"] and got["sign_code"] == "WMS-P"
    # 不是自己名下待签收的物料
    r = org.get("a2", "/api/lines/by-code/WMS-P")
    assert "不在你名下" in _err(r)


def test_urgent_handover_signs_with_codes_then_binds_all_lines(org, ref, fake):
    req = make_req(org, ref, qty=20, urgent=True, urgent_note="组长口头同意")
    line = org.ok(org.post("keeper", f"/api/requisitions/{req['id']}/urgent-handover/{req['items'][0]['id']}"))
    fake.add("U-1", "M-CABLE", 12)
    fake.add("U-2", "M-CABLE", 8)
    org.ok(_sign(org, "a1", line["id"], ["U-1", "U-2"]))  # 紧急领用还没开单，不核对出库单号
    org.ok(org.post("lead_a", f"/api/requisitions/{req['id']}/decide", {"approve": True}))
    doc = erp_doc(org, "LL", [{"material_code": "M-CABLE", "qty": 20}], remark=req["no"])
    org.ok(org.post("keeper", "/api/erp/bind", {"req_item_id": req["items"][0]["id"],
                                                "erp_line_id": doc["lines"][0]["id"]}))
    with dbmod.SessionLocal() as db:
        rows = list(db.scalars(select(LedgerLine).where(LedgerLine.requisition_id == req["id"])))
        assert len(rows) == 2 and all(l.erp_line_id == doc["lines"][0]["id"] and l.urgent_resolved for l in rows)


# ------------------------------------------------------------------ 拆分
def test_split_requests_codes_from_wms(org, ref, fake):
    line = issue(org, ref, qty=100, sign=False)
    fake.add("WMS-R", "M-CABLE", 100, out_doc_no=line["_doc"]["no"])
    org.ok(_sign(org, "a1", line["id"], ["WMS-R"]))
    res = org.ok(org.post("a1", f"/api/lines/{line['id']}/split",
                          {"pieces": [40, 25], "loss_qty": 5, "loss_reason": "L1", "photos": [org.photo("a1")]}))
    r = res["result"]
    assert r["children"] == ["WMS-S0001", "WMS-S0002"] and r["remainder_code"] == "WMS-S0003" and r["remainder"] == "30.000"
    assert fake.codes["WMS-R"]["status"] == "SPLIT"
    assert [c["qty"] for c in fake.splits[next(iter(fake.splits))]] == [40, 25, 30]  # 余量排最后
    with dbmod.SessionLocal() as db:
        codes = {c.code: c for c in db.scalars(select(MaterialCode))}
        assert codes["WMS-R"].replaced and codes["WMS-S0003"].parent_code == "WMS-R"
        assert all(codes[c].source == "WMS" and codes[c].layer == 2 for c in ("WMS-S0001", "WMS-S0002", "WMS-S0003"))
    assert "B4" in _err(org.get("a1", "/api/lines/by-code/WMS-R"))
    # 仓储出错时整笔不生效
    fake.down = True
    r = org.post("a1", f"/api/lines/{line['id']}/split", {"pieces": [10], "photos": [org.photo("a1")]})
    assert "连不上" in _err(r)
    assert org.ok(org.get("a1", f"/api/lines/{line['id']}"))["balance"] == "30"


def test_split_request_is_idempotent():
    f = FakeWms()
    f.add("P", "M", 10)
    client = wms.WmsClient(base="http://wms.test/api", token="tok-test", transport=httpx.MockTransport(f.handler))
    a = client.split("RMLM-P-x", "P", "M", [Decimal(4), Decimal(6)], "甲", None)
    b = client.split("RMLM-P-x", "P", "M", [Decimal(4), Decimal(6)], "甲", None)
    assert a == b and len(f.codes) == 3
    with pytest.raises(BizError, match="已拆分"):
        client.split("RMLM-P-y", "P", "M", [Decimal(10)], "甲", None)


def test_without_wms_keeps_rm_codes(org, ref):
    line = issue(org, ref, qty=10)
    assert line["code"].startswith("RM") and line["signed"]


# ------------------------------------------------------------------ 表结构升级
def test_upgrade_schema_relaxes_unique_and_widens_codes(org):
    with dbmod.engine.begin() as conn:
        conn.execute(text("ALTER TABLE ledger_line ADD UNIQUE INDEX erp_line_id (erp_line_id)"))
        conn.execute(text("ALTER TABLE material_code MODIFY code VARCHAR(32) NOT NULL"))
    upgrade_schema()
    insp = inspect(dbmod.engine)
    uniq = [i for i in insp.get_indexes("ledger_line") if i["unique"] and i["column_names"] == ["erp_line_id"]]
    assert uniq == []
    assert any(i["column_names"] == ["erp_line_id"] for i in insp.get_indexes("ledger_line"))
    assert {c["name"]: c for c in insp.get_columns("material_code")}["code"]["type"].length == 64
    # 其他唯一索引保留
    assert any(i["unique"] and i["column_names"] == ["code"] for i in insp.get_indexes("ledger_line"))
    upgrade_schema()


def test_unified_scan_finds_pending_line_for_wms_barcode(org, ref, fake):
    line = issue(org, ref, qty=30, sign=False)
    fake.add("WMS-U", "M-CABLE", 30, out_doc_no=line["_doc"]["no"])
    res = org.ok(org.get("a1", "/api/scan/WMS-U"))
    assert res["kind"] == "wms" and res["wms"]["qty"] == "30"
    assert [c["id"] for c in res["candidates"]] == [line["id"]]
    assert res["candidates"][0]["action"] == "sign" and res["candidates"][0]["qty_match"]
    # 单据号仍按单据识别，不去调仓储
    calls = len(fake.calls)
    assert org.ok(org.get("a1", f"/api/scan/{line['_doc']['no']}"))["kind"] == "doc"
    assert len(fake.calls) == calls
