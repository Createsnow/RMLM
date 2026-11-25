"""2026-11 业务确认的两项细节需求：
1. 整单批量签收：一单 5 行仍是 5 条台账行、5 个码；一人领整单时可一次确认多行（同一套校验，整笔成功或整笔不生效）；
2. 去向增加 D08 委外加工、D09 展会 / 出差（不扣结存，只改位置，临时位置可填自定义文本）、D10 外部送样（扣结存）。
"""
from datetime import timedelta

from app import clock

from .helpers import erp_doc, issue, make_req
from .test_wms import fake  # noqa: F401  仓储模拟服务 fixture


def _req(org, ref, who, items):
    req = make_req(org, ref, who=who, items=[{"material_id": ref.material(m), "qty": q} for m, q in items])
    approver = "lead_a" if who in ("a1", "a2") else "lead_b"
    org.ok(org.post(approver, f"/api/requisitions/{req['id']}/decide", {"approve": True}))
    return req


def _issue_doc(org, ref, reqs):
    """一张领料单装下多份申请的全部明细（一物一行），逐行绑定，返回 (单据, [领用行 id...]，按单据行号排序)。"""
    items = [(r, it) for r in reqs for it in r["items"]]
    doc = erp_doc(org, "LL", [{"material_code": it["material_code"], "qty": float(it["qty"]), "unit_price": 1}
                              for _, it in items], remark=" ".join(r["no"] for r in reqs))
    ids = []
    for (_, it), ln in zip(items, doc["lines"]):
        ids.append(org.ok(org.post("keeper", "/api/erp/bind", {"req_item_id": it["id"], "erp_line_id": ln["id"]}))["id"])
    return doc, ids


def _batch(org, who, ids, codes=None):
    return org.post(who, "/api/lines/batch-sign", {"line_ids": ids, "photos": [org.photo(who)], "codes": codes or [],
                                                   "note": "整单交接"})


# ------------------------------------------------------------------ 整单批量签收
def test_batch_sign_whole_doc_without_wms(org, ref):
    req = _req(org, ref, "a1", [("M-CABLE", 10), ("M-MPO", 5), ("M-CABLE", 3)])
    doc, ids = _issue_doc(org, ref, [req])
    assert len(ids) == 3  # 一物一行：3 行单据 → 3 条台账行、3 个码
    scan = org.ok(org.get("a1", f"/api/scan/{doc['no']}"))
    assert scan["to_sign"] == 3 and scan["sign_needs_codes"] is False
    res = org.ok(_batch(org, "a1", ids))
    assert res["signed"] == 3
    for i in ids:
        line = org.ok(org.get("a1", f"/api/lines/{i}"))
        assert line["signed"] and line["status"] == "S1"
        sign = [e for e in line["events"] if e["etype"] == "SIGN"][0]
        assert sign["extra"] == {"batch_doc": doc["no"]} and sign["note"] == "整单交接" and not sign["is_proxy"]
    assert not any(t["kind"] == "SIGN" for t in org.ok(org.get("a1", "/api/todos")))


def test_batch_sign_is_all_or_nothing(org, ref):
    r1 = _req(org, ref, "a1", [("M-CABLE", 10)])
    r2 = _req(org, ref, "a2", [("M-MPO", 5)])
    doc, ids = _issue_doc(org, ref, [r1, r2])
    # 别人名下的行不能代签：整笔不生效
    r = _batch(org, "a1", ids)
    assert r.status_code == 403 and "本人" in r.json()["detail"]
    assert not org.ok(org.get("a1", f"/api/lines/{ids[0]}"))["signed"]
    # 不同单据上的行不能一起签
    r3 = _req(org, ref, "a1", [("M-MPO", 2)])
    _, ids3 = _issue_doc(org, ref, [r3])
    r = _batch(org, "a1", [ids[0], ids3[0]])
    assert r.status_code == 400 and "同一张" in r.json()["detail"]
    # 未过账的单据不能签
    r4 = _req(org, ref, "a1", [("M-CABLE", 1), ("M-MPO", 1)])
    d4 = erp_doc(org, "LL", [{"material_code": "M-CABLE", "qty": 1}, {"material_code": "M-MPO", "qty": 1}],
                 posted=False, remark=r4["no"])
    ids4 = [org.ok(org.post("keeper", "/api/erp/bind", {"req_item_id": it["id"], "erp_line_id": ln["id"]}))["id"]
            for it, ln in zip(r4["items"], d4["lines"])]
    r = _batch(org, "a1", ids4)
    assert r.status_code == 400 and "过账" in r.json()["detail"]
    # 没拍照不能签
    r = org.post("a1", "/api/lines/batch-sign", {"line_ids": [ids[0], ids3[0]], "photos": []})
    assert r.status_code == 400


def test_batch_sign_with_wms_assigns_codes_by_material(org, ref, fake):  # noqa: F811
    req = _req(org, ref, "a1", [("M-CABLE", 10), ("M-MPO", 5)])
    doc, ids = _issue_doc(org, ref, [req])
    fake.add("C-1", "M-CABLE", 6, out_doc_no=doc["no"])
    fake.add("C-2", "M-CABLE", 4, out_doc_no=doc["no"])
    fake.add("P-1", "M-MPO", 5, out_doc_no=doc["no"])
    fake.codes["P-1"]["out_line_no"] = 2
    assert org.ok(org.get("a1", f"/api/scan/{doc['no']}"))["sign_needs_codes"] is True
    # 少扫一行的码：整笔不生效
    r = _batch(org, "a1", ids, ["C-1", "C-2"])
    assert r.status_code == 400 and "还没扫" in r.json()["detail"]
    # 一行的码合计不对
    r = _batch(org, "a1", ids, ["C-1", "P-1"])
    assert r.status_code == 400 and "合计" in r.json()["detail"]
    res = org.ok(_batch(org, "a1", ids, ["C-1", "P-1", "C-2"]))
    assert res["signed"] == 3  # 光缆一行出了 2 个码 → 分成 2 条行
    assert sorted(l["code"] for l in res["lines"]) == ["C-1", "C-2", "P-1"]
    org.ok(org.post("keeper", "/api/recon/run"))
    assert all(d["diff"] == "0" for d in org.ok(org.get("keeper", "/api/recon/diffs")) if d["erp_doc_no"] == doc["no"])


def test_batch_sign_with_wms_same_material_needs_line_no(org, ref, fake):  # noqa: F811
    req = _req(org, ref, "a1", [("M-CABLE", 10), ("M-CABLE", 3)])
    doc, ids = _issue_doc(org, ref, [req])
    fake.add("C-10", "M-CABLE", 10, out_doc_no=doc["no"])
    fake.add("C-3", "M-CABLE", 3, out_doc_no=doc["no"])
    for c in ("C-10", "C-3"):
        fake.codes[c]["out_line_no"] = None
    r = _batch(org, "a1", ids, ["C-10", "C-3"])
    assert r.status_code == 400 and "逐行签收" in r.json()["detail"]
    fake.codes["C-10"]["out_line_no"], fake.codes["C-3"]["out_line_no"] = 1, 2
    res = org.ok(_batch(org, "a1", ids, ["C-3", "C-10"]))
    assert {l["code"]: l["qty"] for l in res["lines"]} == {"C-10": "10", "C-3": "3"}


# ------------------------------------------------------------------ 新去向
def test_external_sample_d10_deducts_and_needs_receiver(org, ref):
    line = issue(org, ref, qty=10)
    p = org.photo("a1")
    r = org.post("a1", f"/api/lines/{line['id']}/consume", {"qty": 4, "dest": "D10", "photos": [p]})
    assert r.status_code == 400 and "收样方" in r.json()["detail"]
    out = org.ok(org.post("a1", f"/api/lines/{line['id']}/consume",
                          {"qty": 4, "dest": "D10", "note": "1195 客户 张工", "photos": [p]}))
    assert out["balance"] == "6" and out["status"] == "S2"
    # 委外加工、展会不走使用登记
    r = org.post("a1", f"/api/lines/{line['id']}/consume", {"qty": 1, "dest": "D09", "photos": [p]})
    assert r.status_code == 400 and "外出" in r.json()["detail"]


def test_outing_keeps_balance_and_takes_free_text_location(org, ref):
    line = issue(org, ref, qty=10)
    p = org.photo("a1")
    lid = line["id"]
    r = org.post("a1", f"/api/lines/{lid}/hold", {"dest": "D09", "location_text": " ", "photos": [p]})
    assert r.status_code == 400 and "临时位置" in r.json()["detail"]
    out = org.ok(org.post("a1", f"/api/lines/{lid}/hold",
                          {"dest": "D09", "location_text": "上海光博会 3 号馆 A12", "photos": [p]}))
    assert out["balance"] == "10" and out["status"] == "S3"
    assert out["location"] == "上海光博会 3 号馆 A12" and out["out_dest"] == "D09" and "外出·展会 / 出差" in out["flags"]
    # 外出期间换临时位置
    out = org.ok(org.post("a1", f"/api/lines/{lid}/move", {"location_text": "北京客户现场", "photos": []}))
    assert out["location"] == "北京客户现场" and out["out_dest"] == "D09"
    assert [e["location"] for e in out["events"] if e["etype"] in ("HOLD", "MOVE")] == ["上海光博会 3 号馆 A12", "北京客户现场"]
    # 外出中也能直接登记使用（扣结存）
    org.ok(org.post("a1", f"/api/lines/{lid}/consume", {"qty": 2, "dest": "D10", "note": "展会送客户", "photos": [p]}))
    # 回来：选回固定库位，外出标记消失
    out = org.ok(org.post("a1", f"/api/lines/{lid}/hold",
                          {"dest": "D04", "location_id": ref.location("LOC-A3"), "photos": [p]}))
    assert out["location"] == "A栋3楼工具柜3" and out["out_dest"] is None and out["location_text"] is None
    assert out["balance"] == "8"
    # 固定库位暂存不能填自定义文本
    r = org.post("a1", f"/api/lines/{lid}/move", {"location_text": "随便写个地方", "photos": []})
    assert r.status_code == 400 and "库位码" in r.json()["detail"]


def test_outing_from_hold_and_overdue_rules(org, ref):
    line = issue(org, ref, qty=5)
    p = org.photo("a1")
    lid = line["id"]
    org.ok(org.post("a1", f"/api/lines/{lid}/hold", {"location_id": ref.location("LOC-A3"), "photos": [p]}))
    # 暂存中可以带去委外加工
    out = org.ok(org.post("a1", f"/api/lines/{lid}/hold",
                          {"dest": "D08", "location_text": "东莞某加工厂", "photos": [p]}))
    assert out["out_dest"] == "D08" and out["location_id"] is None
    # 外出期间再次库位暂存 = 回来入库
    org.ok(org.post("a1", f"/api/lines/{lid}/hold", {"location_id": ref.location("LOC-A3"), "photos": [p]}))
    # 超期行不能外出（同暂存）
    clock.advance(timedelta(days=40))
    r = org.post("a1", f"/api/lines/{lid}/hold", {"dest": "D09", "location_text": "展会", "photos": [p]})
    assert r.status_code == 400 and "超期" in r.json()["detail"]
