"""第二轮代码检查（2026-11-25）发现的问题的回归测试：
报废批次含已冲销事件后无法挂单、关账后仍能复核通过冲销、补审被驳回的紧急行永远停在"待单据处置"、
锁定行上的转交无法拒绝、手工改已关账月份单据的过账状态、报废单据覆盖率把已冲销事件算进分母。"""
from datetime import datetime

from app import clock
from app import db as dbmod
from app.models import LedgerLine

from .helpers import erp_doc, issue, make_req


def _event(org, line_id, etype, who="a1"):
    return [e for e in org.ok(org.get(who, f"/api/lines/{line_id}"))["events"] if e["etype"] == etype]


def _reverse(org, event_id):
    rv = org.ok(org.post("keeper", "/api/reversals", {"event_id": event_id, "reason": "误登"}))
    org.ok(org.post("admin", f"/api/reversals/{rv['id']}/decide", {"approve": True}))


def _scrap(org, line_id, qty):
    org.ok(org.post("a1", f"/api/lines/{line_id}/scrap",
                    {"mode": "WHOLE", "qty": qty, "reason": "F01", "box": "BOX-1", "photos": [org.photo("a1")]}))


def test_scrap_batch_with_reversed_event_can_still_bind(org, ref):
    """批次里有一笔整件报废被冲销后，其余明细仍要能挂报废单核销；否则它们既不进新批次、也挂不了单，
    行永远停在 S6，"中台已报废、ERP 无单"差异永远平不掉，月份关不了账。"""
    l1 = issue(org, ref, qty=10)
    _scrap(org, l1["id"], 2)
    _scrap(org, l1["id"], 3)
    org.ok(org.post("keeper", "/api/scrap/batches/build"))
    batch = org.ok(org.get("keeper", "/api/scrap/batches"))[0]
    assert len(batch["items"]) == 2
    first = min(e["id"] for e in _event(org, l1["id"], "SCRAP"))
    _reverse(org, first)
    bf = erp_doc(org, "BF", [{"material_code": "M-CABLE", "qty": 3}])
    res = org.ok(org.post("keeper", f"/api/scrap/batches/{batch['id']}/bind?erp_doc_id={bf['id']}"))
    assert res["batch_rest"] == "0"
    assert org.ok(org.get("keeper", "/api/scrap/batches"))[0]["status"] == "BOUND"
    out = org.ok(org.get("a1", f"/api/lines/{l1['id']}"))
    assert out["scrap_pending"] == "0" and out["balance"] == "7"


def test_scrap_batch_all_reversed_is_rejected_clearly(org, ref):
    l1 = issue(org, ref, qty=10)
    _scrap(org, l1["id"], 2)
    org.ok(org.post("keeper", "/api/scrap/batches/build"))
    batch = org.ok(org.get("keeper", "/api/scrap/batches"))[0]
    _reverse(org, _event(org, l1["id"], "SCRAP")[0]["id"])
    bf = erp_doc(org, "BF", [{"material_code": "M-CABLE", "qty": 2}])
    r = org.post("keeper", f"/api/scrap/batches/{batch['id']}/bind?erp_doc_id={bf['id']}")
    assert r.status_code == 400 and "已全部冲销" in r.json()["detail"]


def test_pending_reversal_cannot_take_effect_after_period_closed(org, ref):
    """关账后不能冲销该月事件：冲销在关账前发起、关账后才复核的，复核时也必须拦住，只能驳回。"""
    l1 = issue(org, ref, qty=10)
    org.ok(org.post("a1", f"/api/lines/{l1['id']}/consume",
                    {"qty": 2, "dest": "D02", "photos": [org.photo("a1")]}))
    ev = _event(org, l1["id"], "CONSUME")[0]
    rv = org.ok(org.post("keeper", "/api/reversals", {"event_id": ev["id"], "reason": "误登"}))
    clock.set_now(datetime(2026, 12, 2, 9, 0))
    org.ok(org.post("fin", "/api/recon/close/2026-11"))
    r = org.post("admin", f"/api/reversals/{rv['id']}/decide", {"approve": True})
    assert r.status_code == 400 and "已关账" in r.json()["detail"]
    assert org.ok(org.get("a1", f"/api/lines/{l1['id']}"))["balance"] == "8"
    # 驳回仍然可以，待办随之关闭
    org.ok(org.post("admin", f"/api/reversals/{rv['id']}/decide", {"approve": False, "note": "已关账"}))
    assert not any(t["kind"] == "REVERSAL_REVIEW" for t in org.ok(org.get("admin", "/api/todos")))


def test_rejected_urgent_line_closes_after_full_return(org, ref):
    """紧急领用补审被驳回 → 全额退库；补齐领料单、退料单后行应结案（S7），"紧急·待补"标记和待办随之结束，
    而不是永远停在 S6、待办每天升级到副总。"""
    req = make_req(org, ref, urgent=True, urgent_note="组长口头同意")
    line = org.ok(org.post("keeper", f"/api/requisitions/{req['id']}/urgent-handover/{req['items'][0]['id']}"))
    org.ok(org.post("a1", f"/api/lines/{line['id']}/sign", {"photos": [org.photo("a1")]}))
    clock.set_now(datetime(2026, 11, 3, 10, 0))
    org.ok(org.post("admin", "/api/admin/jobs/quarter_hour"))
    assert any(t["kind"] == "URGENT_PENDING" for t in org.ok(org.get("a1", "/api/todos")))
    org.ok(org.post("lead_a", f"/api/requisitions/{req['id']}/decide", {"approve": False, "note": "不该领"}))
    org.ok(org.post("a1", f"/api/lines/{line['id']}/return", {"qty": 10}))
    ll = erp_doc(org, "LL", [{"material_code": "M-CABLE", "qty": 10}], remark=req["no"])
    org.ok(org.post("keeper", "/api/erp/bind", {"req_item_id": req["items"][0]["id"],
                                                "erp_line_id": ll["lines"][0]["id"]}))
    tl = erp_doc(org, "TL", [{"material_code": "M-CABLE", "qty": 10}])
    ev = _event(org, line["id"], "RETURN")[0]
    org.ok(org.post("keeper", "/api/returns/allocate", {"erp_doc_id": tl["id"], "event_ids": [ev["id"]]}))
    out = org.ok(org.get("a1", f"/api/lines/{line['id']}"))
    assert out["status"] == "S7", out["status"]
    assert "紧急·待补" not in out["flags"]
    assert not any(t["kind"] == "URGENT_PENDING" for t in org.ok(org.get("a1", "/api/todos")))


def test_rejected_urgent_without_erp_doc_stays_pending(org, ref):
    """驳回本身不等于补齐：还没补挂领料单的，仍是"紧急·待补"。"""
    req = make_req(org, ref, urgent=True, urgent_note="组长口头同意")
    line = org.ok(org.post("keeper", f"/api/requisitions/{req['id']}/urgent-handover/{req['items'][0]['id']}"))
    org.ok(org.post("a1", f"/api/lines/{line['id']}/sign", {"photos": [org.photo("a1")]}))
    org.ok(org.post("lead_a", f"/api/requisitions/{req['id']}/decide", {"approve": False, "note": "不该领"}))
    out = org.ok(org.get("a1", f"/api/lines/{line['id']}"))
    assert "紧急·待补" in out["flags"]


def test_receiver_can_reject_transfer_on_locked_line(org, ref):
    """行被锁定（结存异常）时，受让人仍要能拒绝待确认的转交，否则待办一直挂着、按规则升级。"""
    l1 = issue(org, ref, qty=10)
    org.ok(org.post("a1", f"/api/lines/{l1['id']}/transfer", {"to_user_id": ref.user("a2"), "reason": "换人做"}))
    req = [e for e in _event(org, l1["id"], "TRANSFER_REQ")][0]
    with dbmod.SessionLocal() as db:
        db.get(LedgerLine, l1["id"]).locked = True
        db.commit()
    r = org.post("a2", f"/api/transfers/{req['id']}/decide?accept=true")
    assert r.status_code == 400 and "锁定" in r.json()["detail"]
    org.ok(org.post("a2", f"/api/transfers/{req['id']}/decide?accept=false"))
    assert not any(t["kind"] == "TRANSFER_CONFIRM" for t in org.ok(org.get("a2", "/api/todos")))


def test_cannot_toggle_posted_on_closed_period_doc(org, ref):
    """关账后该月单据不能再变：手工改过账状态同样要拦住（否则已关账月份冒出新差异）。"""
    l1 = issue(org, ref, qty=10)
    clock.set_now(datetime(2026, 12, 2, 9, 0))
    org.ok(org.post("fin", "/api/recon/close/2026-11"))
    r = org.post("keeper", f"/api/erp/docs/{l1['_doc']['id']}/posted?posted=false")
    assert r.status_code == 400 and "已关账" in r.json()["detail"]


def test_scrap_doc_rate_ignores_reversed_events(org, ref):
    """报废单据覆盖率 = 整件报废已摊满报废单的占比；被冲销的报废不再需要报废单，不能算进分母。"""
    l1 = issue(org, ref, qty=10)
    _scrap(org, l1["id"], 2)
    _scrap(org, l1["id"], 3)
    evs = sorted(e["id"] for e in _event(org, l1["id"], "SCRAP"))
    bf = erp_doc(org, "BF", [{"material_code": "M-CABLE", "qty": 2}])
    org.ok(org.post("keeper", "/api/scrap/batches/build"))
    batch = org.ok(org.get("keeper", "/api/scrap/batches"))[0]
    org.ok(org.post("keeper", f"/api/scrap/batches/{batch['id']}/bind?erp_doc_id={bf['id']}"))
    _reverse(org, evs[1])
    obs = org.ok(org.get("vp", "/api/reports/dashboard"))["observe"]
    assert obs["scrap_doc_rate"] == 100.0
