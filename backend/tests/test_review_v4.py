"""第四轮代码检查（基于 cc2ed82）：
一、处置类待办在事情办完后不自动关闭（用户也不能手动关），48 小时升级直属上级、3 天进三级挂账通知研发副总；
二、周报"暂存超 30 天"被更新位置、季度确认、回填结论重新计时；
三、超期挂账清理率：非全局视角的分母含看不到的行；
四、关账月份格式未校验。"""
from datetime import datetime, timedelta

from app import clock

from .helpers import erp_doc, issue, make_req


def _todos(org, who, kind):
    return [t for t in org.ok(org.get(who, "/api/todos")) if t["kind"] == kind]


def _events(org, line_id, etype, who="a1"):
    return [e for e in org.ok(org.get(who, f"/api/lines/{line_id}"))["events"] if e["etype"] == etype]


def _job(org, name):
    return org.ok(org.post("admin", f"/api/admin/jobs/{name}"))


def _photo(org, who="a1"):
    return [org.photo(who)]


def _level3(org):
    return org.ok(org.get("vp", "/api/reports/weekly"))["level3"]


# ------------------------------------------------------------------ 一、超期待办：退完即关，不再升级
def test_overdue_todo_closes_after_line_cleared(org, ref):
    l1 = issue(org, ref, qty=10)
    clock.set_now(datetime(2026, 12, 3, 9, 0))  # 超过预计归还日（30 天）
    _job(org, "daily")
    assert _todos(org, "a1", "OVERDUE")
    org.ok(org.post("a1", f"/api/lines/{l1['id']}/return", {"qty": 10}))
    assert not _todos(org, "a1", "OVERDUE"), "已全部退库，超期待办应关闭"
    clock.set_now(datetime(2026, 12, 7, 9, 0))  # 4 天后巡检：不应再升级直属上级 / 三级挂账
    _job(org, "quarter_hour")
    assert not _todos(org, "lead_a", "OVERDUE")
    assert not [t for t in _level3(org) if "预计归还日" in t["title"]]  # 待开退料单 4 天没开，照常挂账


def test_overdue_todo_closes_after_due_date_extended(org, ref):
    l1 = issue(org, ref, qty=10)
    clock.set_now(datetime(2026, 12, 3, 9, 0))
    _job(org, "daily")
    new_date = (clock.today() + timedelta(days=30)).isoformat()
    org.ok(org.post("a1", f"/api/lines/{l1['id']}/due-change", {"new_date": new_date, "reason": "实验延期"}))
    ev = _events(org, l1["id"], "DUE_CHANGE")[0]
    org.ok(org.post("lead_a", f"/api/line-requests/{ev['id']}/decide", {"approve": True}))
    _job(org, "quarter_hour")
    assert not _todos(org, "a1", "OVERDUE"), "已批准新的归还日、不再超期，超期待办应关闭"


def test_overdue_todo_stays_while_still_overdue(org, ref):
    l1 = issue(org, ref, qty=10)
    clock.set_now(datetime(2026, 12, 3, 9, 0))
    _job(org, "daily")
    org.ok(org.post("a1", f"/api/lines/{l1['id']}/return", {"qty": 4}))  # 只退了一部分，仍超期
    _job(org, "quarter_hour")
    assert _todos(org, "a1", "OVERDUE")


# ------------------------------------------------------------------ 一、须全额退库：退完即关
def test_must_return_todo_closes_after_full_return(org, ref):
    req = make_req(org, ref, qty=10, urgent=True, urgent_note="组长口头同意")
    line = org.ok(org.post("keeper", f"/api/requisitions/{req['id']}/urgent-handover/{req['items'][0]['id']}"))
    org.ok(org.post("a1", f"/api/lines/{line['id']}/sign", {"photos": _photo(org)}))
    org.ok(org.post("lead_a", f"/api/requisitions/{req['id']}/decide", {"approve": False, "note": "不符合"}))
    assert _todos(org, "a1", "MUST_RETURN")
    org.ok(org.post("a1", f"/api/lines/{line['id']}/return", {"qty": 10}))
    assert not _todos(org, "a1", "MUST_RETURN")


# ------------------------------------------------------------------ 一、项目结项清零：清零即关
def test_project_closed_todo_closes_after_cleared(org, ref):
    from app import db as dbmod
    from app.models import Project

    l1 = issue(org, ref, qty=10)
    with dbmod.SessionLocal() as db:
        db.get(Project, ref.project("PDJ001")).closed = True
        db.commit()
    _job(org, "daily")
    assert _todos(org, "a1", "PROJECT_CLOSED")
    org.ok(org.post("a1", f"/api/lines/{l1['id']}/consume", {"qty": 10, "dest": "D02", "photos": _photo(org)}))
    assert not _todos(org, "a1", "PROJECT_CLOSED")


# ------------------------------------------------------------------ 一、退库被冲销：待开退料单待办关闭
def test_return_doc_todo_closes_when_return_reversed(org, ref):
    l1 = issue(org, ref, qty=10)
    org.ok(org.post("a1", f"/api/lines/{l1['id']}/return", {"qty": 4}))
    assert _todos(org, "keeper", "RETURN_DOC")
    ev = _events(org, l1["id"], "RETURN")[0]
    rv = org.ok(org.post("keeper", "/api/reversals", {"event_id": ev["id"], "reason": "误登"}))
    org.ok(org.post("admin", f"/api/reversals/{rv['id']}/decide", {"approve": True}))
    assert not _todos(org, "keeper", "RETURN_DOC"), "退库已冲销，不再需要开退料单"


# ------------------------------------------------------------------ 一、D02 耗用被冲销：回填结论待办关闭
def test_lab_conclusion_todo_closes_when_consume_reversed(org, ref):
    l1 = issue(org, ref, qty=10)
    org.ok(org.post("a1", f"/api/lines/{l1['id']}/consume", {"qty": 2, "dest": "D02", "photos": _photo(org)}))
    clock.set_now(datetime(2026, 12, 5, 9, 0))
    _job(org, "daily")
    assert _todos(org, "a1", "LAB_CONCLUSION")
    ev = _events(org, l1["id"], "CONSUME")[0]
    rv = org.ok(org.post("keeper", "/api/reversals", {"event_id": ev["id"], "reason": "误登"}))
    org.ok(org.post("admin", f"/api/reversals/{rv['id']}/decide", {"approve": True}))
    assert not _todos(org, "a1", "LAB_CONCLUSION")


# ------------------------------------------------------------------ 一、ERP 有单无明细：金蝶反审核后关闭
def test_erp_unbound_todo_closes_when_doc_unposted(org, ref):
    doc = erp_doc(org, "LL", [{"material_code": "M-CABLE", "qty": 5}])
    clock.set_now(datetime(2026, 11, 3, 10, 0))
    _job(org, "quarter_hour")
    assert _todos(org, "keeper", "ERP_UNBOUND")
    org.ok(org.post("keeper", f"/api/erp/docs/{doc['id']}/posted?posted=false"))
    _job(org, "quarter_hour")
    assert not _todos(org, "keeper", "ERP_UNBOUND"), "单据已反审核，不再需要补登"


# ------------------------------------------------------------------ 二、暂存超 30 天按进入暂存的时间算
def test_hold_30d_counts_from_hold_not_from_move(org, ref):
    l1 = issue(org, ref, qty=10, expected_return="2027-01-31")
    org.ok(org.post("a1", f"/api/lines/{l1['id']}/hold",
                    {"location_id": ref.location("LOC-A3"), "photos": _photo(org)}))
    clock.set_now(datetime(2026, 11, 22, 9, 0))
    org.ok(org.post("a1", f"/api/lines/{l1['id']}/move", {"location_id": ref.location("LOC-B4")}))
    clock.set_now(datetime(2026, 11, 30, 9, 0))  # 暂存 28 天：还不到
    assert not org.ok(org.get("vp", "/api/reports/weekly"))["hold_30d"]
    clock.set_now(datetime(2026, 12, 1, 10, 0))  # 暂存 29 天多：还不到
    assert not org.ok(org.get("vp", "/api/reports/weekly"))["hold_30d"]
    clock.set_now(datetime(2026, 12, 3, 9, 0))  # 暂存 31 天（中间更新过位置，仍在暂存）
    hold = org.ok(org.get("vp", "/api/reports/weekly"))["hold_30d"]
    assert [b["code"] for b in hold] == [l1["code"]]


# ------------------------------------------------------------------ 三、清理率分母按可见范围
def test_overdue_clear_rate_scoped(org, ref):
    issue(org, ref, who="a1", qty=10)
    issue(org, ref, who="b1", approver="lead_b", qty=10)
    clock.set_now(datetime(2027, 1, 1, 9, 0))  # 新季度第一天：两行都已超期，进季初快照
    _job(org, "quarterly")
    a1_line = org.ok(org.get("a1", "/api/lines?scope=mine"))[0]
    org.ok(org.post("a1", f"/api/lines/{a1_line['id']}/return", {"qty": 10}))
    obs = org.ok(org.get("lead_a", "/api/reports/dashboard"))["observe"]
    assert obs["overdue_clear_rate"] == 100.0  # 组长甲只看得到 a1 这一行，已清理
    assert org.ok(org.get("vp", "/api/reports/dashboard"))["observe"]["overdue_clear_rate"] == 50.0


# ------------------------------------------------------------------ 四、关账月份格式
def test_close_period_rejects_bad_format(org, ref):
    r = org.post("fin", "/api/recon/close/2026-1")
    assert r.status_code == 400 and "格式" in r.json()["detail"]


# ================================================================== 业务答复（2026-11-25）后的修改：M / N / O / P
def _put_user(org, ref, username, **kw):
    from app import db as dbmod
    from app.models import User

    with dbmod.SessionLocal() as db:
        u = db.get(User, ref.user(username))
        body = {"username": u.username, "name": u.name, "manager_id": u.manager_id,
                "roles": sorted(u.role_set), "active": u.active}
    body.update(kw)
    return org.c.put(f"/api/users/{ref.user(username)}", json=body, headers=org._h("admin"))


# ------------------------------------------------------------------ M：审核通过派"待开单"，开够即关
def test_issue_todo_after_approval_closes_when_fully_issued(org, ref):
    req = make_req(org, ref, qty=10)
    assert not _todos(org, "keeper", "ISSUE")
    org.ok(org.post("lead_a", f"/api/requisitions/{req['id']}/decide", {"approve": True}))
    assert _todos(org, "keeper", "ISSUE")
    item = req["items"][0]["id"]
    part = erp_doc(org, "LL", [{"material_code": "M-CABLE", "qty": 6}])
    org.ok(org.post("keeper", "/api/erp/bind", {"req_item_id": item, "erp_line_id": part["lines"][0]["id"]}))
    assert _todos(org, "keeper", "ISSUE"), "只开了 6 / 10，待开单仍在"
    rest = erp_doc(org, "LL", [{"material_code": "M-CABLE", "qty": 4}])
    org.ok(org.post("keeper", "/api/erp/bind", {"req_item_id": item, "erp_line_id": rest["lines"][0]["id"]}))
    assert not _todos(org, "keeper", "ISSUE")


def test_rejected_requisition_gets_no_issue_todo(org, ref):
    req = make_req(org, ref, qty=10)
    org.ok(org.post("lead_a", f"/api/requisitions/{req['id']}/decide", {"approve": False, "note": "不需要"}))
    assert not _todos(org, "keeper", "ISSUE")


# ------------------------------------------------------------------ N：离职检查看待确认转交和未开完单的申请
def test_clearance_checks_pending_transfer_and_open_requisitions(org, ref):
    l1 = issue(org, ref, who="a2", qty=10)
    org.ok(org.post("a2", f"/api/lines/{l1['id']}/transfer", {"to_user_id": ref.user("a1"), "reason": "交接"}))
    req = make_req(org, ref, who="a1", qty=5)
    org.ok(org.post("lead_a", f"/api/requisitions/{req['id']}/decide", {"approve": True}))
    c = org.ok(org.get("admin", f"/api/users/{ref.user('a1')}/clearance"))
    assert not c["clear"] and len(c["transfers"]) == 1 and [r["no"] for r in c["reqs"]] == [req["no"]]
    r = _put_user(org, ref, "a1", active=False)
    assert r.status_code == 400 and "转交" in r.json()["detail"] and req["no"] in r.json()["detail"]
    # 拒绝转交、撤回审核通过但还没开单的申请后可以停用
    tr = _events(org, l1["id"], "TRANSFER_REQ", who="a2")[0]
    org.ok(org.post("a1", f"/api/transfers/{tr['id']}/decide?accept=false"))
    assert org.ok(org.get("a1", f"/api/requisitions/{req['id']}"))["can_cancel"]
    org.ok(org.post("a1", f"/api/requisitions/{req['id']}/cancel"))
    assert not _todos(org, "keeper", "ISSUE")
    assert org.ok(org.get("admin", f"/api/users/{ref.user('a1')}/clearance"))["clear"]
    assert _put_user(org, ref, "a1", active=False).status_code == 200


def test_cancel_partially_issued_requisition_stops_further_binding(org, ref):
    req = make_req(org, ref, qty=10)
    org.ok(org.post("lead_a", f"/api/requisitions/{req['id']}/decide", {"approve": True}))
    part = erp_doc(org, "LL", [{"material_code": "M-CABLE", "qty": 6}])
    line = org.ok(org.post("keeper", "/api/erp/bind",
                           {"req_item_id": req["items"][0]["id"], "erp_line_id": part["lines"][0]["id"]}))
    assert org.post("b1", f"/api/requisitions/{req['id']}/cancel").status_code == 403
    org.ok(org.post("keeper", f"/api/requisitions/{req['id']}/cancel"))
    more = erp_doc(org, "LL", [{"material_code": "M-CABLE", "qty": 4}], remark=req["no"])
    r = org.post("keeper", "/api/erp/bind", {"req_item_id": req["items"][0]["id"], "erp_line_id": more["lines"][0]["id"]})
    assert r.status_code == 400 and "已撤回" in r.json()["detail"]
    assert not [x for x in org.ok(org.get("keeper", "/api/erp/suggestions")) if x["req_no"] == req["no"]]
    assert org.ok(org.get("a1", f"/api/lines/{line['id']}"))["qty"] == "6"  # 已开单的行照常


def test_cannot_cancel_fully_issued_or_urgent_unbound(org, ref):
    l1 = issue(org, ref, qty=10)
    r = org.post("a1", f"/api/requisitions/{l1['_req']['id']}/cancel")
    assert r.status_code == 400 and "已全部开单" in r.json()["detail"]
    req = make_req(org, ref, qty=5, urgent=True, urgent_note="组长口头同意")
    org.ok(org.post("keeper", f"/api/requisitions/{req['id']}/urgent-handover/{req['items'][0]['id']}"))
    org.ok(org.post("lead_a", f"/api/requisitions/{req['id']}/decide", {"approve": True}))
    r = org.post("keeper", f"/api/requisitions/{req['id']}/cancel")
    assert r.status_code == 400 and "紧急" in r.json()["detail"]


# ------------------------------------------------------------------ O：最上级不冻结
def test_top_level_holder_not_frozen(org, ref):
    from app import db as dbmod
    from app.models import User

    issue(org, ref, who="vp", approver="admin", qty=10)   # 研发副总没有直属上级
    issue(org, ref, who="a1", qty=10)
    clock.set_now(datetime(2026, 12, 20, 9, 0))  # 超期 18 天、18 天无动作
    _job(org, "daily")
    with dbmod.SessionLocal() as db:
        assert db.get(User, ref.user("vp")).frozen is False
        assert db.get(User, ref.user("a1")).frozen is True


# ------------------------------------------------------------------ P：改直属上级后改派续期 / 改归还日
def test_manager_change_reassigns_pending_line_requests(org, ref):
    l1 = issue(org, ref, qty=10)
    new_date = (clock.today() + timedelta(days=60)).isoformat()
    org.ok(org.post("a1", f"/api/lines/{l1['id']}/due-change", {"new_date": new_date, "reason": "实验延期"}))
    assert _todos(org, "lead_a", "LINE_APPROVE")
    org.ok(_put_user(org, ref, "a1", manager_id=ref.user("lead_b")))
    assert not _todos(org, "lead_a", "LINE_APPROVE")
    t = _todos(org, "lead_b", "LINE_APPROVE")
    assert t and "直属上级变更" in t[0]["title"]
    ev = _events(org, l1["id"], "DUE_CHANGE")[0]
    org.ok(org.post("lead_b", f"/api/line-requests/{ev['id']}/decide", {"approve": True}))
