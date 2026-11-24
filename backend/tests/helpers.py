from datetime import date, timedelta

from app import clock

_doc_seq = [0]
_wms_seq = [0]


def wms_code(material: str | None = "M-CABLE", qty=None) -> str:
    """往仓储赋码清单里放一张码（material=None 为仓储预发的空白码），返回码。"""
    from app import db as dbmod
    from app.services import wms

    _wms_seq[0] += 1
    code = f"WM{_wms_seq[0]:06d}"
    with dbmod.SessionLocal() as db:
        wms.upsert(db, [{"code": code, "material_code": material, "qty": qty}], "IMPORT")
        db.commit()
    return code


def blanks(n: int) -> list[str]:
    return [wms_code(None) for _ in range(n)]


def make_req(api, ref, who="a1", material="M-CABLE", qty=10, **kw):
    body = {
        "project_id": ref.project("PDJ001"), "reason_code": "R01", "remark": "压接可靠性验证",
        "expected_return": (clock.today() + timedelta(days=30)).isoformat(),
        "items": [{"material_id": ref.material(material), "qty": qty}],
    }
    body.update(kw)
    return api.ok(api.post(who, "/api/requisitions", body))


def erp_doc(api, doc_type, lines, posted=True, remark=None, ref_doc_no=None, who="keeper", doc_date=None):
    _doc_seq[0] += 1
    no = f"{doc_type}-T{_doc_seq[0]:05d}"
    body = {"no": no, "doc_type": doc_type, "doc_date": (doc_date or clock.today()).isoformat(), "posted": posted,
            "remark": remark, "ref_doc_no": ref_doc_no,
            "lines": [{"line_no": i + 1, **ln} for i, ln in enumerate(lines)]}
    return api.ok(api.post(who, "/api/erp/docs", body))


def issue(api, ref, who="a1", approver="lead_a", material="M-CABLE", qty=10, sign=True, **kw):
    """申请 → 直属上级审核 → 领料专员开金蝶单并绑定 → 使用人签收。返回行详情。"""
    req = make_req(api, ref, who=who, material=material, qty=qty, **kw)
    api.ok(api.post(approver, f"/api/requisitions/{req['id']}/decide", {"approve": True}))
    doc = erp_doc(api, "LL", [{"material_code": material, "qty": qty, "unit_price": 2.5}], remark=req["no"])
    line = api.ok(api.post("keeper", "/api/erp/bind",
                           {"req_item_id": req["items"][0]["id"], "erp_line_id": doc["lines"][0]["id"]}))
    if sign:
        code = wms_code(material, qty)
        line = api.ok(api.post(who, f"/api/lines/{line['id']}/sign", {"photos": [api.photo(who)], "code": code}))
    line["_req"] = req
    line["_doc"] = doc
    return line
