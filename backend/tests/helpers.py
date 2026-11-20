from datetime import date, timedelta

from app import clock

_doc_seq = [0]


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
        line = api.ok(api.post(who, f"/api/lines/{line['id']}/sign", {"photos": [api.photo(who)]}))
    line["_req"] = req
    line["_doc"] = doc
    return line
