"""v1.1：按《业务蓝图（简版）V1.1》与《双端可操作原型》对齐的功能。"""
import io
from datetime import timedelta

from openpyxl import Workbook

from app import clock

from .helpers import erp_doc, issue, make_req


# ------------------------------------------------------------------ 去向附加要求（蓝图 4.1）
def test_dest_requirements(org, ref):
    line = issue(org, ref)
    lid, p = line["id"], org.photo("a1")
    r = org.post("a1", f"/api/lines/{lid}/consume", {"qty": 1, "dest": "D01", "photos": [p]})
    assert r.status_code == 400 and "出货单号" in r.json()["detail"]
    out = org.ok(org.post("a1", f"/api/lines/{lid}/consume",
                          {"qty": 1, "dest": "D01", "ship_no": "XSCK-2611-001", "photos": [p]}))
    assert out["events"][-1]["extra"]["ship_no"] == "XSCK-2611-001"

    r = org.post("a1", f"/api/lines/{lid}/consume", {"qty": 1, "dest": "D03", "photos": [p]})
    assert r.status_code == 400 and "工位" in r.json()["detail"]
    # 库位（非工位）不行
    r = org.post("a1", f"/api/lines/{lid}/consume",
                 {"qty": 1, "dest": "D03", "location_id": ref.location("LOC-A3"), "photos": [p]})
    assert r.status_code == 400
    out = org.ok(org.post("a1", f"/api/lines/{lid}/consume",
                          {"qty": 1, "dest": "D03", "location_id": ref.location("LOC-B4"), "photos": [p]}))
    assert out["events"][-1]["location"] == "B栋4F产线3号工位"
    assert out["balance"] == "8"


def test_lab_conclusion_backfill_and_warning(org, ref):
    line = issue(org, ref)
    lid = line["id"]
    out = org.ok(org.post("a1", f"/api/lines/{lid}/consume", {"qty": 2, "dest": "D02", "photos": [org.photo("a1")]}))
    eid = out["events"][-1]["id"]
    assert out["lab_pending"] == [eid]
    # 30 天内不预警
    clock.advance(timedelta(days=10))
    org.ok(org.post("admin", "/api/admin/jobs/daily"))
    assert not any(t["kind"] == "LAB_CONCLUSION" for t in org.ok(org.get("a1", "/api/todos")))
    clock.advance(timedelta(days=21))
    org.ok(org.post("admin", "/api/admin/jobs/daily"))
    assert any(t["kind"] == "LAB_CONCLUSION" for t in org.ok(org.get("a1", "/api/todos")))
    weekly = org.ok(org.get("head", "/api/reports/weekly"))
    assert [x["event_id"] for x in weekly["lab_pending"]] == [eid]
    # 其他组的人不能回填
    assert org.post("b1", f"/api/events/{eid}/conclusion", {"text": "合格"}).status_code == 403
    r = org.post("a1", f"/api/events/{eid}/conclusion", {"text": " "})
    assert r.status_code == 400
    out = org.ok(org.post("a1", f"/api/events/{eid}/conclusion", {"text": "高低温 500 次循环后插损合格"}))
    assert out["lab_pending"] == [] and out["events"][-1]["etype"] == "CONCLUSION"
    assert out["balance"] == "8"  # 回填结论不动结存
    assert not any(t["kind"] == "LAB_CONCLUSION" for t in org.ok(org.get("a1", "/api/todos")))
    assert org.post("a1", f"/api/events/{eid}/conclusion", {"text": "重复"}).status_code == 400
    # 非 D02 事件不需要回填
    out = org.ok(org.post("a1", f"/api/lines/{lid}/consume",
                          {"qty": 1, "dest": "D01", "ship_no": "XS-1", "photos": [org.photo("a1")]}))
    assert org.post("a1", f"/api/events/{out['events'][-1]['id']}/conclusion", {"text": "x"}).status_code == 400


# ------------------------------------------------------------------ 扫码为主、选单为备（蓝图 3.1）
def test_scan_resolves_codes_docs_locations(org, ref):
    req = make_req(org, ref, qty=10)
    org.ok(org.post("lead_a", f"/api/requisitions/{req['id']}/decide", {"approve": True}))
    doc = erp_doc(org, "LL", [{"material_code": "M-CABLE", "qty": 10, "unit_price": 2.5}], remark=req["no"])
    line = org.ok(org.post("keeper", "/api/erp/bind",
                           {"req_item_id": req["items"][0]["id"], "erp_line_id": doc["lines"][0]["id"]}))
    # 选单号：列出该单各行，本人可签收的标记出来
    res = org.ok(org.get("a1", f"/api/scan/{doc['no']}"))
    assert res["kind"] == "doc" and res["to_sign"] == 1
    assert res["rows"][0]["lines"][0]["id"] == line["id"] and res["rows"][0]["lines"][0]["can_sign"]
    # 别组的人看得到单据但看不到行
    other = org.ok(org.get("b1", f"/api/scan/{doc['no']}"))
    assert other["rows"][0]["lines"] == [] and other["hidden"] == 1
    # 签收仍逐行进行（同一套校验：本人、拍照）
    assert org.post("a2", f"/api/lines/{line['id']}/sign", {"photos": [org.photo("a2")]}).status_code == 403
    org.ok(org.post("a1", f"/api/lines/{line['id']}/sign", {"photos": [org.photo("a1")]}))
    assert org.ok(org.get("a1", f"/api/scan/{doc['no']}"))["to_sign"] == 0
    line = org.ok(org.get("a1", f"/api/lines/{line['id']}"))
    # 物料码
    assert org.ok(org.get("a1", f"/api/scan/{line['code']}")) == {"kind": "line", "code": line["code"],
                                                                  "line_id": line["id"]}
    # 拆分后旧码失效
    out = org.ok(org.post("a1", f"/api/lines/{line['id']}/split", {"pieces": [3], "photos": [org.photo("a1")]}))
    r = org.get("a1", f"/api/scan/{line['code']}")
    assert r.status_code == 400 and "B4" in r.json()["detail"]
    assert org.ok(org.get("a1", f"/api/scan/{out['code']}"))["line_id"] == line["id"]
    # 库位码
    org.ok(org.post("a1", f"/api/lines/{line['id']}/hold", {"location_id": ref.location("LOC-A3"),
                                                          "photos": [org.photo("a1")]}))
    loc = org.ok(org.get("a1", "/api/scan/LOC-A3"))
    assert loc["kind"] == "location" and [x["id"] for x in loc["lines"]] == [line["id"]]
    # 申请号
    assert org.ok(org.get("a1", f"/api/scan/{req['no']}")) == {"kind": "req", "code": req["no"], "req_id": req["id"]}
    assert org.get("a1", "/api/scan/NOPE-404").status_code == 400


# ------------------------------------------------------------------ 外部受让待认领（蓝图 3.3）
def test_external_transfer_assign_and_cancel(org, ref):
    line = issue(org, ref)
    lid = line["id"]
    out = org.ok(org.post("a1", f"/api/lines/{lid}/transfer", {"external_name": "王工（供应商驻场）", "reason": "外协测试"}))
    assert "外部·待认领" in out["flags"] and out["holder"] == "组员甲一"
    assert out["pending_transfer"]["external"] == "王工（供应商驻场）"
    pend = org.ok(org.get("keeper", "/api/transfers/external"))
    assert len(pend) == 1 and pend[0]["line"]["id"] == lid
    eid = pend[0]["event_id"]
    assert any(t["kind"] == "TRANSFER_ASSIGN" for t in org.ok(org.get("keeper", "/api/todos")))
    # 只有领料专员能指派
    assert org.post("a1", f"/api/transfers/{eid}/assign?to_user_id={ref.user('b1')}").status_code == 403
    out = org.ok(org.post("keeper", f"/api/transfers/{eid}/assign?to_user_id={ref.user('b1')}"))
    assert out["holder"] == "组员甲一" and out["pending_transfer"]["to_user"] == "组员乙一"
    assert org.ok(org.get("keeper", "/api/transfers/external")) == []
    new_eid = out["pending_transfer"]["id"]
    org.ok(org.post("b1", f"/api/transfers/{new_eid}/decide?accept=true"))
    assert org.ok(org.get("b1", f"/api/lines/{lid}"))["holder"] == "组员乙一"

    # 撤回
    line2 = issue(org, ref)
    out = org.ok(org.post("a1", f"/api/lines/{line2['id']}/transfer", {"to_user_id": ref.user("a2"), "reason": "换人"}))
    tid = out["pending_transfer"]["id"]
    assert org.post("b1", f"/api/transfers/{tid}/cancel").status_code == 403
    out = org.ok(org.post("a1", f"/api/transfers/{tid}/cancel"))
    assert out["pending_transfer"] is None
    assert org.post("a2", f"/api/transfers/{tid}/decide?accept=true").status_code == 400


# ------------------------------------------------------------------ 历史基线（蓝图 5.2）
def _xlsx(rows):
    wb = Workbook()
    ws = wb.active
    ws.append(["领料单号", "日期", "项目号", "客户代码", "物料编码", "物料名称", "规格", "单位", "领用数量", "领用人",
               "去向 / 现状", "备注"])
    for r in rows:
        ws.append(r)
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def test_history_baseline_import_and_check(org, ref):
    doc_no = erp_doc(org, "LL", [{"material_code": "M-CABLE", "qty": 45, "project_code": "PDJ001"}])["no"]
    data = _xlsx([
        [doc_no, "2026-01-06", None, "0832", "M-CABLE", "室内光缆", "12芯", "米", 20, "张金兰", "已退仓库", None],
        [doc_no, "2026-01-06", None, "0832", "M-CABLE", "室内光缆", "12芯", "米", 25, "张金兰", "存放于B3工程分析室", None],
        ["QTCK-OLD-1", "2025-10-19", None, None, "M-MPO", "MPO 散件", None, "套", 80, "徐鹏程", "报废退仓库", None],
        ["QTCK-OLD-2", "2025-09-11", "RD202519", None, "M-MPO", "MPO 散件", None, "套", 5, "刘斌", "某种说不清的状态", None],
        [None, None, None, None, None, "空行", None, None, None, None, None, None],
    ])
    assert org.post("a1", "/api/history/import", files={"file": ("h.xlsx", data)}).status_code == 403
    r = org.c.post("/api/history/import", headers=org._h("keeper"), files={"file": ("h.xlsx", data)})
    st = org.ok(r)
    assert st["count"] == 4 and st["skipped"] == 1
    assert st["backfilled"] == 2 and st["virtual"] == 1 and st["unmapped"] == 1
    rows = org.ok(org.get("keeper", "/api/history"))["rows"]
    by_raw = {x["dest_raw"]: x for x in rows}
    assert by_raw["已退仓库"]["dest_code"] == "D06" and by_raw["已退仓库"]["project_code"] == "PDJ001"
    assert by_raw["已退仓库"]["project_source"] == "ERP"
    wip = by_raw["存放于B3工程分析室"]
    assert wip["dest_code"] == "D04" and wip["location_text"] == "B3工程分析室" and wip["holder_name"] == "张金兰"
    assert by_raw["报废退仓库"]["dest_code"] == "D05" and by_raw["报废退仓库"]["project_code"] == "试制-未立项"
    assert by_raw["某种说不清的状态"]["dest_code"] is None and by_raw["某种说不清的状态"]["project_source"] == "SHEET"
    assert len(org.ok(org.get("keeper", "/api/history?dest=NONE"))["rows"]) == 1
    # 总量核对：20 + 25 = ERP 45
    chk = org.ok(org.get("keeper", "/api/history/check"))
    assert chk["summary"] == {"OK": 1, "DIFF": 0, "NO_DOC": 2}
    # 基线不进台账、不影响验收指标
    assert org.ok(org.get("keeper", "/api/lines")) == []
    rules = org.ok(org.get("a1", "/api/history/rules"))
    assert rules["virtual_project"] == "试制-未立项" and any(x["dest"] == "D05" for x in rules["rules"])


# ------------------------------------------------------------------ 双轨抽查（蓝图 表 5-1）
def test_parallel_check_weekly_sample(org, ref):
    assert org.post("keeper", "/api/parallel/sample").status_code == 400  # 本周还没有签收
    lines = [issue(org, ref, qty=1) for _ in range(3)]
    assert org.post("a1", "/api/parallel/sample").status_code == 403
    res = org.ok(org.post("keeper", "/api/parallel/sample"))
    assert res["week"] == "2026-W45" and len(res["rows"]) == 3
    assert {r["line_id"] for r in res["rows"]} == {l["id"] for l in lines}
    again = org.ok(org.post("keeper", "/api/parallel/sample"))  # 同一周不重抽
    assert [r["id"] for r in again["rows"]] == [r["id"] for r in res["rows"]]
    ids = [r["id"] for r in res["rows"]]
    assert org.post("keeper", f"/api/parallel/{ids[0]}", {"result": "DIFF"}).status_code == 400
    for i in ids:
        org.ok(org.post("keeper", f"/api/parallel/{i}", {"result": "MATCH"}))
    s = org.ok(org.get("head", "/api/parallel"))["summary"]
    assert s["weeks"][0]["rate"] == 100.0 and s["weeks"][0]["pass"] and not s["exit_ok"]
    # 下一周也达标 → 满足退出条件
    clock.advance(timedelta(days=7))
    issue(org, ref, qty=1)
    nxt = org.ok(org.post("keeper", "/api/parallel/sample"))
    assert nxt["week"] == "2026-W46"
    org.ok(org.post("keeper", f"/api/parallel/{nxt['rows'][0]['id']}", {"result": "MATCH"}))
    assert org.ok(org.get("keeper", "/api/parallel"))["summary"]["exit_ok"]


# ------------------------------------------------------------------ 看板、对账汇总、字典复盘（原型 + 蓝图 5.2/5.3）
def test_dashboard_recon_summary_and_dict_review(org, ref):
    line = issue(org, ref)
    org.ok(org.post("a1", f"/api/lines/{line['id']}/scrap",
                    {"mode": "WHOLE", "qty": 10, "reason": "F01", "box": "BOX-1", "photos": [org.photo("a1")]}))
    d = org.ok(org.get("head", "/api/reports/dashboard"))
    assert d["counts"]["scrap_pending"] == 1 and d["counts"]["today_events"] >= 2
    assert [x["code"] for x in d["need_today"]] == [line["code"]]
    ev = d["recent_events"][0]
    assert ev["etype"] == "SCRAP" and ev["needs_doc"] and ev["operator"] == "组员甲一"
    sign = next(e for e in d["recent_events"] if e["etype"] == "SIGN")
    assert sign["erp_doc_no"] == line["_doc"]["no"]
    line2 = issue(org, ref, qty=4)
    d = org.ok(org.get("head", "/api/reports/dashboard"))
    assert d["by_project"][0]["project"] == "PDJ001" and d["by_project"][0]["lines"] == 1
    assert line2["id"]

    erp_doc(org, "LL", [{"material_code": "M-CABLE", "qty": 3}])  # 有单无明细
    org.ok(org.post("keeper", "/api/recon/run"))
    s = org.ok(org.get("keeper", "/api/recon/summary"))
    assert s["docs"] == 3 and s["diff_docs"] == 1 and s["balanced"] == 2 and s["diff_rate"] == 33.3
    assert org.ok(org.get("head", "/api/reports/dashboard"))["counts"]["recon_open"] >= 1

    q = org.ok(org.get("head", "/api/reports/quarterly"))
    rev = {(x["dict_type"], x["code"]): x for x in q["dict_review"]}
    assert rev[("REASON", "R01")]["pct"] == 100.0 and not rev[("REASON", "R01")]["suggest"]
    assert rev[("REASON", "R02")]["count"] == 0 and rev[("REASON", "R02")]["suggest"]
