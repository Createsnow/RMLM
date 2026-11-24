"""物料码取自仓储系统：赋码清单同步 / 导入、签收绑定、补绑、拆分扫新标签、旧 RM 码补绑。"""
import io

from openpyxl import Workbook
from sqlalchemy import select

from app import db as dbmod
from app.models import LedgerLine, MaterialCode, WmsCode
from app.services import wms

from .helpers import blanks, erp_doc, issue, make_req, wms_code


def _pending_line(org, ref, qty=10, material="M-CABLE"):
    req = make_req(org, ref, material=material, qty=qty)
    org.ok(org.post("lead_a", f"/api/requisitions/{req['id']}/decide", {"approve": True}))
    doc = erp_doc(org, "LL", [{"material_code": material, "qty": qty}], remark=req["no"])
    return org.ok(org.post("keeper", "/api/erp/bind",
                           {"req_item_id": req["items"][0]["id"], "erp_line_id": doc["lines"][0]["id"]}))


def test_import_and_sync(org):
    wb = Workbook()
    ws = wb.active
    ws.append(["仓储码", "物料编码", "数量", "单位", "批次", "作废"])
    ws.append(["W-001", "M-CABLE", 45, "米", "B2611", "否"])
    ws.append(["W-B01", None, None, None, None, None])
    ws.append(["W-002", "M-MPO", 6, "套", None, "是"])
    buf = io.BytesIO()
    wb.save(buf)
    assert org.c.post("/api/wms/codes/import", headers=org._h("a1"),
                      files={"file": ("w.xlsx", buf.getvalue())}).status_code == 403
    st = org.ok(org.c.post("/api/wms/codes/import", headers=org._h("wh"), files={"file": ("w.xlsx", buf.getvalue())}))
    assert st == {"created": 3, "updated": 0}
    res = org.ok(org.get("keeper", "/api/wms/codes?state=free"))
    assert {r["code"] for r in res["rows"]} == {"W-001", "W-B01"}
    assert res["status"]["blank_free"] == 1 and not res["status"]["enabled"]
    assert org.post("keeper", "/api/wms/sync").status_code == 400  # 未配置接口

    class Fake(wms.WmsReader):
        def fetch_codes(self, since):
            return [{"code": "W-001", "material_code": "M-CABLE", "qty": 45, "void": True},
                    {"code": "W-003", "material_code": "M-MPO", "qty": 2}]

    with dbmod.SessionLocal() as db:
        assert wms.sync(db, Fake()) == {"created": 1, "updated": 1}
        db.commit()
        assert db.scalar(select(WmsCode).where(WmsCode.code == "W-001")).void


def test_sign_binds_warehouse_code(org, ref):
    line = _pending_line(org, ref, qty=10)
    assert line["code"].startswith("LN") and line["code_kind"] == "LINE"
    lid, p = line["id"], org.photo("a1")
    sign = lambda **kw: org.post("a1", f"/api/lines/{lid}/sign", {"photos": [p], **kw})  # noqa: E731
    assert "仓储码" in sign().json()["detail"]
    assert "不在仓储赋码清单" in sign(code="NOPE").json()["detail"]
    assert "不一致" in sign(code=wms_code("M-MPO", 10)).json()["detail"]
    assert "数量" in sign(code=wms_code("M-CABLE", 9)).json()["detail"]
    assert "空白码" in sign(code=wms_code(None)).json()["detail"]
    other = issue(org, ref, qty=10)
    assert "已绑定其他领用行" in sign(code=other["code"]).json()["detail"]
    good = wms_code("M-CABLE", 10)
    out = org.ok(sign(code=good))
    assert out["code"] == good and out["wms_bound"] and "待绑仓储码" not in out["flags"]
    assert out["codes"][0]["source"] == "WMS" and out["codes"][0]["state"] == "B2"
    assert org.ok(org.get("a1", f"/api/scan/{good}"))["line_id"] == lid
    gate = org.ok(org.get("head", "/api/reports/dashboard"))["gate"]
    assert gate["code_bind_rate"] == 100.0


def test_sign_without_code_then_bind(org, ref):
    line = _pending_line(org, ref, qty=10)
    lid = line["id"]
    assert org.post("a1", f"/api/lines/{lid}/sign", {"photos": [org.photo("a1")], "no_code_reason": "随便"}).status_code == 400
    out = org.ok(org.post("a1", f"/api/lines/{lid}/sign", {"photos": [org.photo("a1")], "no_code_reason": "标签破损"}))
    assert "待绑仓储码" in out["flags"] and out["actions"]["bind_code"]
    assert out["events"][-1]["extra"] == {"no_code": "标签破损"}
    issue(org, ref)  # 另一行扫码签收
    d = org.ok(org.get("head", "/api/reports/dashboard"))
    assert d["gate"]["code_bind_rate"] == 50.0 and d["counts"]["to_bind"] == 1
    assert [x["id"] for x in org.ok(org.get("head", "/api/reports/weekly"))["to_bind"]] == [lid]
    # 扫到还没绑定的仓储码：带出本人可补绑的同物料行
    code = wms_code("M-CABLE", 10)
    res = org.ok(org.get("a1", f"/api/scan/{code}"))
    assert res["kind"] == "wms" and [(c["id"], c["action"]) for c in res["candidates"]] == [(lid, "bind")]
    assert org.post("b1", f"/api/lines/{lid}/bind-code", {"code": code}).status_code == 403
    out = org.ok(org.post("a1", f"/api/lines/{lid}/bind-code", {"code": code}))
    assert out["code"] == code and "待绑仓储码" not in out["flags"] and out["events"][-1]["etype"] == "BIND_CODE"
    assert org.post("a1", f"/api/lines/{lid}/bind-code", {"code": wms_code("M-CABLE", 10)}).status_code == 400


def test_scan_unbound_code_for_signing(org, ref):
    line = _pending_line(org, ref, qty=10)
    code = wms_code("M-CABLE", 10)
    res = org.ok(org.get("a1", f"/api/scan/{code}"))
    assert [(c["id"], c["action"], c["qty_match"]) for c in res["candidates"]] == [(line["id"], "sign", True)]
    assert org.ok(org.get("a2", f"/api/scan/{code}"))["candidates"] == []
    blank = wms_code(None)
    assert org.ok(org.get("a1", f"/api/scan/{blank}"))["wms"]["blank"]


def test_legacy_rm_code_rebind(org, ref):
    line = issue(org, ref, qty=10)
    # 模拟仓储条码上线前发出的 RM 码
    with dbmod.SessionLocal() as db:
        l = db.get(LedgerLine, line["id"])
        mc = db.scalar(select(MaterialCode).where(MaterialCode.code == l.code))
        w = db.scalar(select(WmsCode).where(WmsCode.code == l.code))
        w.line_id = None
        l.code = mc.code = "RM00000001"
        mc.source = "RM"
        db.commit()
    out = org.ok(org.get("a1", f"/api/lines/{line['id']}"))
    assert out["code_kind"] == "RM" and "待绑仓储码" in out["flags"]
    org.ok(org.post("a1", f"/api/lines/{line['id']}/consume", {"qty": 4, "dest": "D02", "photos": [org.photo("a1")]}))
    new = wms_code("M-CABLE", 10)  # 仓储码数量是原领用数量
    out = org.ok(org.post("keeper", f"/api/lines/{line['id']}/bind-code", {"code": new}))
    assert out["code"] == new and out["balance"] == "6"
    states = {c["code"]: (c["source"], c["state"]) for c in out["codes"]}
    assert states["RM00000001"] == ("RM", "RETIRED") and states[new] == ("WMS", "B3")
    r = org.get("a1", "/api/scan/RM00000001")
    assert r.status_code == 400 and new in r.json()["detail"]
    assert not org.ok(org.get("a1", f"/api/lines/{line['id']}"))["events"][-1]["is_proxy"]  # 领料专员补绑不算代登


def test_split_needs_new_warehouse_labels(org, ref):
    line = issue(org, ref, qty=10)
    lid, p = line["id"], org.photo("a1")
    b = blanks(3)
    split = lambda **kw: org.post("a1", f"/api/lines/{lid}/split", {"photos": [p], **kw})  # noqa: E731
    r = split(pieces=[{"qty": 4, "code": b[0]}])
    assert r.status_code == 400 and "余量" in r.json()["detail"]
    r = split(pieces=[{"qty": 4, "code": b[0]}], remainder_code=b[0])
    assert "两份" in r.json()["detail"]
    r = split(pieces=[{"qty": 4, "code": wms_code("M-MPO")}], remainder_code=b[1])
    assert "不能贴在" in r.json()["detail"]
    same = wms_code("M-CABLE")  # 同物料、不带数量的码也可以用
    out = org.ok(split(pieces=[{"qty": 4, "code": b[0]}, {"qty": 2, "code": same}], remainder_code=b[1]))
    assert out["code"] == b[1] and out["balance"] == "4"
    assert out["result"]["children"] == [b[0], same]
    child = org.ok(org.get("a1", f"/api/scan/{b[0]}"))
    assert org.ok(org.get("a1", f"/api/lines/{child['line_id']}"))["codes"][0]["parent_code"] == line["code"]
    assert "B4" in org.get("a1", f"/api/scan/{line['code']}").json()["detail"]
    # 全部拆完不需要余量标签
    out = org.ok(org.post("a1", f"/api/lines/{lid}/split", {"pieces": [{"qty": 4, "code": b[2]}], "photos": [p]}))
    assert out["balance"] == "0"
