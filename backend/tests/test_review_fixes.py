"""代码检查（2026-11）发现的问题的回归测试：对账差异卡死、冲销已分摊事件、未过账单据核销、
报废批次部分核销、同一行并发扣料、锁行无法解锁。"""
import threading
import time
from datetime import datetime

from app import clock
from app import db as dbmod
from app.errors import BizError
from app.models import Event, LedgerLine, User
from app.services import lineops

from .helpers import erp_doc, issue, make_req


def _diffs(org):
    return org.ok(org.get("fin", "/api/recon/diffs"))


def _event(org, line_id, etype):
    return [e for e in org.ok(org.get("a1", f"/api/lines/{line_id}"))["events"] if e["etype"] == etype][0]


def _reverse(org, event_id):
    rv = org.ok(org.post("keeper", "/api/reversals", {"event_id": event_id, "reason": "误登"}))
    org.ok(org.post("admin", f"/api/reversals/{rv['id']}/decide", {"approve": True}))


def test_urgent_line_diff_clears_after_erp_bound(org, ref):
    """紧急交接 7 天未补单进差异池；补挂 ERP 单后这条差异必须自动平掉，否则月份永远关不了账。"""
    req = make_req(org, ref, urgent=True, urgent_note="口头同意")
    line = org.ok(org.post("keeper", f"/api/requisitions/{req['id']}/urgent-handover/{req['items'][0]['id']}"))
    org.ok(org.post("a1", f"/api/lines/{line['id']}/sign", {"photos": [org.photo("a1")]}))
    clock.set_now(datetime(2026, 11, 10, 9, 0))
    org.ok(org.post("keeper", "/api/recon/run"))
    assert [(d["kind"], d["state"]) for d in _diffs(org)] == [("LINE_NO_ERP", "OPEN")]
    org.ok(org.post("lead_a", f"/api/requisitions/{req['id']}/decide", {"approve": True}))
    doc = erp_doc(org, "LL", [{"material_code": "M-CABLE", "qty": 10}], remark=req["no"])
    org.ok(org.post("keeper", "/api/erp/bind", {"req_item_id": req["items"][0]["id"],
                                                "erp_line_id": doc["lines"][0]["id"]}))
    org.ok(org.post("keeper", "/api/recon/run"))
    assert all(d["diff"] == "0" and d["state"] == "RESOLVED" for d in _diffs(org))
    assert not any(t["kind"] == "RECON_CLAIM" for t in org.ok(org.get("keeper", "/api/todos")))
    clock.set_now(datetime(2026, 12, 2, 9, 0))
    org.ok(org.post("fin", "/api/recon/close/2026-11"))


def test_return_without_erp_diff_clears_after_reversal(org, ref):
    """退库登记 7 天未挂退料单进差异池；领料专员冲销这次误登后差异平掉，可以关账。"""
    l1 = issue(org, ref, qty=10)
    org.ok(org.post("a1", f"/api/lines/{l1['id']}/return", {"qty": 4}))
    clock.set_now(datetime(2026, 11, 10, 9, 0))
    org.ok(org.post("keeper", "/api/recon/run"))
    assert any(d["kind"] == "EVT_NO_ERP" and d["diff"] == "-4" for d in _diffs(org))
    _reverse(org, _event(org, l1["id"], "RETURN")["id"])
    org.ok(org.post("keeper", "/api/recon/run"))
    assert all(d["diff"] == "0" for d in _diffs(org))
    clock.set_now(datetime(2026, 12, 2, 9, 0))
    org.ok(org.post("fin", "/api/recon/close/2026-11"))


def test_reversing_allocated_return_surfaces_diff_and_frees_doc(org, ref):
    """已挂退料单的退库被冲销：ERP 上的 4 不再有中台对应，必须进差异池；单据行空出的数量可以重新分摊。"""
    l1 = issue(org, ref, qty=10)
    org.ok(org.post("a1", f"/api/lines/{l1['id']}/return", {"qty": 4}))
    ev = _event(org, l1["id"], "RETURN")
    t1 = erp_doc(org, "TL", [{"material_code": "M-CABLE", "qty": 4}])
    org.ok(org.post("keeper", "/api/returns/allocate", {"erp_doc_id": t1["id"], "event_ids": [ev["id"]]}))
    _reverse(org, ev["id"])
    org.ok(org.post("keeper", "/api/recon/run"))
    out = org.ok(org.get("a1", f"/api/lines/{l1['id']}"))
    assert out["balance"] == "10"
    tl = [d for d in _diffs(org) if d["erp_doc_no"] == t1["no"]]
    assert tl and tl[0]["kind"] == "TL_MISMATCH" and tl[0]["diff"] == "4"
    # 被冲销的事件不能再拿来分摊
    r = org.post("keeper", "/api/returns/allocate", {"erp_doc_id": t1["id"], "event_ids": [ev["id"]]})
    assert r.status_code == 400
    # 重新正确登记退库后，原退料单可以摊给新事件，差异自动平
    org.ok(org.post("a1", f"/api/lines/{l1['id']}/return", {"qty": 4}))
    ev2 = [e for e in org.ok(org.get("a1", f"/api/lines/{l1['id']}"))["events"]
           if e["etype"] == "RETURN" and e["id"] != ev["id"]][0]
    res = org.ok(org.post("keeper", "/api/returns/allocate", {"erp_doc_id": t1["id"], "event_ids": [ev2["id"]]}))
    assert res["allocations"] == 1 and not res["leftover"]
    tl = [d for d in _diffs(org) if d["erp_doc_no"] == t1["no"]]
    assert tl[0]["diff"] == "0" and tl[0]["state"] == "RESOLVED"


def test_unposted_doc_cannot_be_allocated(org, ref):
    """未过账的退料单 / 报废单不能拿来核销，否则行会在金蝶还没过账时就结案。"""
    l1 = issue(org, ref, qty=4)
    org.ok(org.post("a1", f"/api/lines/{l1['id']}/return", {"qty": 4}))
    ev = _event(org, l1["id"], "RETURN")
    t1 = erp_doc(org, "TL", [{"material_code": "M-CABLE", "qty": 4}], posted=False)
    r = org.post("keeper", "/api/returns/allocate", {"erp_doc_id": t1["id"], "event_ids": [ev["id"]]})
    assert r.status_code == 400 and "过账" in r.json()["detail"]
    assert org.ok(org.get("a1", f"/api/lines/{l1['id']}"))["status"] == "S6"
    org.ok(org.post("keeper", f"/api/erp/docs/{t1['id']}/posted?posted=true"))
    org.ok(org.post("keeper", "/api/returns/allocate", {"erp_doc_id": t1["id"], "event_ids": [ev["id"]]}))
    assert org.ok(org.get("a1", f"/api/lines/{l1['id']}"))["status"] == "S7"


def test_scrap_batch_partial_bind_can_take_second_doc(org, ref):
    """报废单数量不够摊完批次：批次保持未核销，可再挂一张报废单，摊完才算已核销。"""
    p = org.photo("a1")
    l2 = issue(org, ref, qty=5)
    org.ok(org.post("a1", f"/api/lines/{l2['id']}/scrap",
                    {"mode": "WHOLE", "qty": 5, "reason": "F01", "box": "BOX-1", "photos": [p]}))
    org.ok(org.post("keeper", "/api/scrap/batches/build"))
    batch = org.ok(org.get("keeper", "/api/scrap/batches"))[0]
    bf = erp_doc(org, "BF", [{"material_code": "M-CABLE", "qty": 3}])
    res = org.ok(org.post("keeper", f"/api/scrap/batches/{batch['id']}/bind?erp_doc_id={bf['id']}"))
    assert res["batch_rest"] == "2"
    b = org.ok(org.get("keeper", "/api/scrap/batches"))[0]
    assert b["status"] == "OPEN" and b["erp_doc_no"] == bf["no"]
    assert org.ok(org.get("a1", f"/api/lines/{l2['id']}"))["status"] == "S6"
    bf2 = erp_doc(org, "BF", [{"material_code": "M-CABLE", "qty": 2}])
    res = org.ok(org.post("keeper", f"/api/scrap/batches/{batch['id']}/bind?erp_doc_id={bf2['id']}"))
    assert res["batch_rest"] == "0" and not res["leftover"]
    assert org.ok(org.get("keeper", "/api/scrap/batches"))[0]["status"] == "BOUND"
    assert org.ok(org.get("a1", f"/api/lines/{l2['id']}"))["status"] == "S7"


def test_concurrent_consume_cannot_overdraw(org, ref):
    """同一行两笔扣料同时提交：后到的一方要等前一方提交后再按新结存校验，不能把结存扣成负数。"""
    line = issue(org, ref, qty=10)
    photo = org.photo("a1")
    results: dict[str, str] = {}

    def consume(tag: str, hold: float) -> None:
        with dbmod.SessionLocal() as db:
            op = db.get(User, ref.user("a1"))
            ln = db.get(LedgerLine, line["id"])
            try:
                lineops.consume(db, ln, op, 8, "D02", False, None, None, [photo])
                time.sleep(hold)  # 持有行锁一段时间，让另一笔请求撞上
                db.commit()
                results[tag] = "ok"
            except BizError as e:
                db.rollback()
                results[tag] = str(e)

    t1 = threading.Thread(target=consume, args=("first", 1.0))
    t1.start()
    time.sleep(0.3)
    t2 = threading.Thread(target=consume, args=("second", 0))
    t2.start()
    t1.join()
    t2.join()
    assert results["first"] == "ok"
    assert "超过当前结存" in results["second"]
    assert org.ok(org.get("a1", f"/api/lines/{line['id']}"))["balance"] == "2"


def test_locked_line_unlocks_after_reversal_fix(org, ref):
    """结存为负被巡检锁行；IT 复核通过冲销把结存修回正常后自动解锁，行可以继续操作。"""
    line = issue(org, ref, qty=10)
    org.ok(org.post("a1", f"/api/lines/{line['id']}/consume",
                    {"qty": 6, "dest": "D02", "photos": [org.photo("a1")]}))
    with dbmod.SessionLocal() as db:  # 模拟历史并发留下的超扣事件
        db.add(Event(line_id=line["id"], code=line["code"], etype="CONSUME", qty=6, dest="D02",
                     operator_id=ref.user("a1"), holder_id=ref.user("a1")))
        db.commit()
    org.ok(org.post("admin", "/api/admin/jobs/quarter_hour"))
    out = org.ok(org.get("a1", f"/api/lines/{line['id']}"))
    assert out["locked"] and out["balance"] == "-2"
    bad = [e for e in out["events"] if e["etype"] == "CONSUME"][-1]
    _reverse(org, bad["id"])
    out = org.ok(org.get("a1", f"/api/lines/{line['id']}"))
    assert not out["locked"] and out["balance"] == "4"
    assert not any(t["kind"] == "LOCKED_LINE" for t in org.ok(org.get("admin", "/api/todos")))
    org.ok(org.post("a1", f"/api/lines/{line['id']}/consume", {"qty": 1, "dest": "D02", "photos": [org.photo("a1")]}))
