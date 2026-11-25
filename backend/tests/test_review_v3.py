"""第三轮（2026-11-25 业务答复后）：有待复核冲销不能关账、转交后续期 / 改归还日改派审核人，以及代码小问题：
损耗不记去向、手工绑定超申请数量、已撤回的转交挡住拆分冲销、过期的改归还日不能批准、重复续期 / 改归还日、
结存为 0 的季度确认、未过账单据不能确认实物、反审核后恢复"紧急·待补"、代登率与字典使用率排除已冲销事件。"""
from datetime import date, datetime, timedelta

from app import clock

from .helpers import erp_doc, issue, make_req


def _events(org, line_id, etype, who="a1"):
    return [e for e in org.ok(org.get(who, f"/api/lines/{line_id}"))["events"] if e["etype"] == etype]


def _todos(org, who, kind):
    return [t for t in org.ok(org.get(who, "/api/todos")) if t["kind"] == kind]


def _reverse(org, event_id):
    rv = org.ok(org.post("keeper", "/api/reversals", {"event_id": event_id, "reason": "误登"}))
    org.ok(org.post("admin", f"/api/reversals/{rv['id']}/decide", {"approve": True}))


def _consume(org, line_id, qty, who="a1", dest="D02"):
    org.ok(org.post(who, f"/api/lines/{line_id}/consume", {"qty": qty, "dest": dest, "photos": [org.photo(who)]}))


# ------------------------------------------------------------------ 一 3：有待复核冲销不能关账
def test_cannot_close_period_with_pending_reversal(org, ref):
    l1 = issue(org, ref, qty=10)
    _consume(org, l1["id"], 2)
    rv = org.ok(org.post("keeper", "/api/reversals", {"event_id": _events(org, l1["id"], "CONSUME")[0]["id"],
                                                     "reason": "误登"}))
    clock.set_now(datetime(2026, 12, 2, 9, 0))
    r = org.post("fin", "/api/recon/close/2026-11")
    assert r.status_code == 400 and "待复核的冲销" in r.json()["detail"]
    org.ok(org.post("admin", f"/api/reversals/{rv['id']}/decide", {"approve": True}))
    org.ok(org.post("fin", "/api/recon/close/2026-11"))


# ------------------------------------------------------------------ 一 6：转交后改派续期 / 改归还日的审核人
def test_transfer_reassigns_pending_due_change_to_new_approver(org, ref):
    l1 = issue(org, ref, qty=10)
    new_date = (clock.today() + timedelta(days=60)).isoformat()
    org.ok(org.post("a1", f"/api/lines/{l1['id']}/due-change", {"new_date": new_date, "reason": "实验延期"}))
    assert _todos(org, "lead_a", "LINE_APPROVE")
    org.ok(org.post("a1", f"/api/lines/{l1['id']}/transfer", {"to_user_id": ref.user("b1"), "reason": "换人做"}))
    req = _events(org, l1["id"], "TRANSFER_REQ")[0]
    org.ok(org.post("b1", f"/api/transfers/{req['id']}/decide?accept=true"))
    assert not _todos(org, "lead_a", "LINE_APPROVE")
    assert _todos(org, "lead_b", "LINE_APPROVE")
    ev = _events(org, l1["id"], "DUE_CHANGE", who="b1")[0]
    org.ok(org.post("lead_b", f"/api/line-requests/{ev['id']}/decide", {"approve": True}))
    assert org.ok(org.get("b1", f"/api/lines/{l1['id']}"))["due_date"] == new_date


# ------------------------------------------------------------------ 二 10：损耗不记去向
def test_loss_does_not_store_dest(org, ref):
    l1 = issue(org, ref, qty=10)
    org.ok(org.post("a1", f"/api/lines/{l1['id']}/consume", {"qty": 1, "dest": "D99", "is_loss": True,
                                                            "loss_reason": "L1", "photos": [org.photo("a1")]}))
    assert _events(org, l1["id"], "CONSUME")[0]["dest"] is None


# ------------------------------------------------------------------ 二 11：手工绑定不能超过申请数量
def test_manual_bind_cannot_exceed_requested_qty(org, ref):
    req = make_req(org, ref, qty=10)
    org.ok(org.post("lead_a", f"/api/requisitions/{req['id']}/decide", {"approve": True}))
    item = req["items"][0]["id"]
    over = erp_doc(org, "LL", [{"material_code": "M-CABLE", "qty": 12}])
    r = org.post("keeper", "/api/erp/bind", {"req_item_id": item, "erp_line_id": over["lines"][0]["id"]})
    assert r.status_code == 400 and "超过" in r.json()["detail"]
    part = erp_doc(org, "LL", [{"material_code": "M-CABLE", "qty": 6}])
    org.ok(org.post("keeper", "/api/erp/bind", {"req_item_id": item, "erp_line_id": part["lines"][0]["id"]}))
    rest = erp_doc(org, "LL", [{"material_code": "M-CABLE", "qty": 4}])
    org.ok(org.post("keeper", "/api/erp/bind", {"req_item_id": item, "erp_line_id": rest["lines"][0]["id"]}))
    more = erp_doc(org, "LL", [{"material_code": "M-CABLE", "qty": 1}])
    r = org.post("keeper", "/api/erp/bind", {"req_item_id": item, "erp_line_id": more["lines"][0]["id"]})
    assert r.status_code == 400 and "已开够" in r.json()["detail"]


def test_auto_bind_skips_doc_exceeding_requested_qty(org, ref):
    req = make_req(org, ref, qty=10)
    org.ok(org.post("lead_a", f"/api/requisitions/{req['id']}/decide", {"approve": True}))
    from app import db as dbmod
    from app.services import doc_sync

    with dbmod.SessionLocal() as db:
        stats = doc_sync.apply_docs(db, [{"no": "QTCK-OVER-1", "doc_type": "LL", "doc_date": "2026-11-02",
                                          "posted": True, "remark": req["no"], "lines": [
                                              {"line_no": 1, "material_code": "M-CABLE", "qty": 12}]}])
        from app.services import erp

        assert stats["created"] == 1 and erp.auto_bind(db) == 0
        db.commit()


# ------------------------------------------------------------------ 二 12：已撤回的转交不算子行"发生过业务"
def test_cancelled_transfer_does_not_block_split_reversal(org, ref):
    l1 = issue(org, ref, qty=10)
    org.ok(org.post("a1", f"/api/lines/{l1['id']}/split", {"pieces": [3], "photos": [org.photo("a1")]}))
    child = org.ok(org.get("a1", f"/api/lines/{l1['id']}"))["children"][0]
    org.ok(org.post("a1", f"/api/lines/{child['id']}/transfer", {"to_user_id": ref.user("a2"), "reason": "给甲二"}))
    req = _events(org, child["id"], "TRANSFER_REQ")[0]
    org.ok(org.post("a1", f"/api/transfers/{req['id']}/cancel"))
    _reverse(org, _events(org, l1["id"], "SPLIT_OUT")[0]["id"])
    assert org.ok(org.get("a1", f"/api/lines/{child['id']}"))["voided"] is True
    assert org.ok(org.get("a1", f"/api/lines/{l1['id']}"))["balance"] == "10"


# ------------------------------------------------------------------ 二 13：新归还日已过的申请不能批准
def test_due_change_cannot_be_approved_after_new_date_passed(org, ref):
    l1 = issue(org, ref, qty=10)
    org.ok(org.post("a1", f"/api/lines/{l1['id']}/due-change", {"new_date": "2026-11-10", "reason": "延期"}))
    ev = _events(org, l1["id"], "DUE_CHANGE")[0]
    clock.set_now(datetime(2026, 11, 12, 9, 0))
    r = org.post("lead_a", f"/api/line-requests/{ev['id']}/decide", {"approve": True})
    assert r.status_code == 400 and "已过" in r.json()["detail"]
    org.ok(org.post("lead_a", f"/api/line-requests/{ev['id']}/decide", {"approve": False, "note": "日期已过"}))


# ------------------------------------------------------------------ 二 14：重复申请、结存为 0 的季度确认
def test_no_duplicate_pending_renew_or_due_change(org, ref):
    l1 = issue(org, ref, qty=10, long_cycle=True, long_cycle_months=6)
    org.ok(org.post("a1", f"/api/lines/{l1['id']}/renew", {"months": 3, "reason": "继续验证"}))
    r = org.post("a1", f"/api/lines/{l1['id']}/renew", {"months": 3, "reason": "继续验证"})
    assert r.status_code == 400 and "待审核" in r.json()["detail"]
    new_date = (clock.today() + timedelta(days=20)).isoformat()
    org.ok(org.post("a1", f"/api/lines/{l1['id']}/due-change", {"new_date": new_date, "reason": "提前"}))
    r = org.post("a1", f"/api/lines/{l1['id']}/due-change", {"new_date": new_date, "reason": "提前"})
    assert r.status_code == 400 and "待审核" in r.json()["detail"]


def test_qconfirm_requires_balance_and_lapse_skips_cleared_lines(org, ref):
    l1 = issue(org, ref, qty=10, long_cycle=True, long_cycle_months=6)
    org.ok(org.post("admin", "/api/admin/jobs/quarterly"))
    assert _todos(org, "a1", "QCONFIRM")
    _consume(org, l1["id"], 10)
    r = org.post("a1", f"/api/lines/{l1['id']}/qconfirm", {"photos": []})
    assert r.status_code == 400 and "结存为 0" in r.json()["detail"]
    clock.set_now(clock.now() + timedelta(days=8))
    org.ok(org.post("admin", "/api/admin/jobs/daily"))
    assert not _todos(org, "a1", "QCONFIRM")
    assert "已按标准类" not in "".join(org.ok(org.get("a1", f"/api/lines/{l1['id']}"))["flags"])


# ------------------------------------------------------------------ 二 15：未过账单据不能确认实物
def test_warehouse_cannot_receive_unposted_doc(org, ref):
    tl = erp_doc(org, "TL", [{"material_code": "M-CABLE", "qty": 4}], posted=False)
    r = org.post("wh", f"/api/erp/docs/{tl['id']}/receive")
    assert r.status_code == 400 and "过账" in r.json()["detail"]


# ------------------------------------------------------------------ 二 16：反审核后恢复"紧急·待补"
def test_urgent_pending_restored_when_doc_unposted(org, ref):
    req = make_req(org, ref, urgent=True, urgent_note="组长口头同意")
    line = org.ok(org.post("keeper", f"/api/requisitions/{req['id']}/urgent-handover/{req['items'][0]['id']}"))
    org.ok(org.post("a1", f"/api/lines/{line['id']}/sign", {"photos": [org.photo("a1")]}))
    org.ok(org.post("a1", f"/api/lines/{line['id']}/split", {"pieces": [3], "photos": [org.photo("a1")]}))
    org.ok(org.post("lead_a", f"/api/requisitions/{req['id']}/decide", {"approve": True}))
    ll = erp_doc(org, "LL", [{"material_code": "M-CABLE", "qty": 10}], remark=req["no"])
    org.ok(org.post("keeper", "/api/erp/bind", {"req_item_id": req["items"][0]["id"],
                                                "erp_line_id": ll["lines"][0]["id"]}))
    assert "紧急·待补" not in org.ok(org.get("a1", f"/api/lines/{line['id']}"))["flags"]
    org.ok(org.post("keeper", f"/api/erp/docs/{ll['id']}/posted?posted=false"))
    out = org.ok(org.get("a1", f"/api/lines/{line['id']}"))
    assert "紧急·待补" in out["flags"]
    child = out["children"][0]
    assert "紧急·待补" not in org.ok(org.get("a1", f"/api/lines/{child['id']}"))["flags"]  # 子行不受影响
    org.ok(org.post("keeper", f"/api/erp/docs/{ll['id']}/posted?posted=true"))
    assert "紧急·待补" not in org.ok(org.get("a1", f"/api/lines/{line['id']}"))["flags"]


# ------------------------------------------------------------------ 二 17：代登率、字典使用率排除已冲销事件
def test_proxy_rate_and_dict_usage_ignore_reversed_events(org, ref):
    l1 = issue(org, ref, qty=10)
    _consume(org, l1["id"], 1, who="a1", dest="D02")
    org.ok(org.post("a2", f"/api/lines/{l1['id']}/consume", {"qty": 1, "dest": "D10", "note": "客户甲",
                                                            "photos": [org.photo("a2")]}))
    proxy = [e for e in _events(org, l1["id"], "CONSUME") if e["is_proxy"]][0]
    _reverse(org, proxy["id"])
    assert org.ok(org.get("vp", "/api/reports/dashboard"))["gate"]["proxy_rate"] == 0.0
    usage = org.ok(org.get("vp", "/api/reports/quarterly"))["dict_usage"]
    assert usage.get("D02") == 1 and "D10" not in usage
