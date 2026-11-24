from datetime import datetime, timedelta

from app import clock

from .helpers import blanks, erp_doc, issue, make_req, wms_code


# ------------------------------------------------------------------ 申请与审核
def test_requisition_requires_remark_and_open_project(org, ref):
    body = {"project_id": ref.project("PDJ001"), "reason_code": "R01", "remark": " ",
            "expected_return": "2026-12-01", "items": [{"material_id": ref.material("M-CABLE"), "qty": 1}]}
    r = org.post("a1", "/api/requisitions", body)
    assert r.status_code == 400 and "备注" in r.json()["detail"]
    body.update(remark="x", project_id=ref.project("PDJ999"))
    assert "结项" in org.post("a1", "/api/requisitions", body).json()["detail"]


def test_approval_routing_and_separation(org, ref):
    req = make_req(org, ref, who="a1")
    assert req["approver"] == "组长甲"
    # 本人、同组、其他组长都不能审
    assert org.post("a1", f"/api/requisitions/{req['id']}/decide", {"approve": True}).status_code == 403
    assert org.post("a2", f"/api/requisitions/{req['id']}/decide", {"approve": True}).status_code == 403
    assert org.post("lead_b", f"/api/requisitions/{req['id']}/decide", {"approve": True}).status_code == 403
    # 驳回必须写原因
    assert org.post("lead_a", f"/api/requisitions/{req['id']}/decide", {"approve": False}).status_code == 400
    out = org.ok(org.post("lead_a", f"/api/requisitions/{req['id']}/decide", {"approve": True}))
    assert out["status"] == "APPROVED" and out["decided_as"] == "SELF"
    # 部门负责人的申请交给研发副总
    assert make_req(org, ref, who="head")["approver"] == "副总"


def test_delegate_rules(org, ref):
    now = clock.now()
    span = {"start_at": now.isoformat(), "end_at": (now + timedelta(days=3)).isoformat()}
    # 代理人只能是同级或上级：下属不行
    r = org.post("lead_a", "/api/delegates", {"agent_id": ref.user("a1"), **span})
    assert r.status_code == 400
    org.ok(org.post("lead_a", "/api/delegates", {"agent_id": ref.user("lead_b"), **span}))
    req = make_req(org, ref, who="a1")
    out = org.ok(org.post("lead_b", f"/api/requisitions/{req['id']}/decide", {"approve": True}))
    assert out["decided_as"] == "DELEGATE"
    # 下属不能当代理人（因此代理人永远不会审到自己的申请）
    assert org.post("head", "/api/delegates", {"agent_id": ref.user("keeper"), **span}).status_code == 400
    # 上级可以当代理人
    org.ok(org.post("lead_a", "/api/delegates", {"agent_id": ref.user("head"), **span}))


def test_bind_requires_approval_and_sign_rules(org, ref):
    req = make_req(org, ref, who="a1")
    doc = erp_doc(org, "LL", [{"material_code": "M-CABLE", "qty": 10}], posted=False, remark=req["no"])
    bind = {"req_item_id": req["items"][0]["id"], "erp_line_id": doc["lines"][0]["id"]}
    r = org.post("keeper", "/api/erp/bind", bind)
    assert r.status_code == 400 and "审核" in r.json()["detail"]
    org.ok(org.post("lead_a", f"/api/requisitions/{req['id']}/decide", {"approve": True}))
    sug = org.ok(org.get("keeper", "/api/erp/suggestions"))
    assert sug and sug[0]["req_no"] == req["no"]
    line = org.ok(org.post("keeper", "/api/erp/bind", bind))
    assert line["status"] == "S0"
    # 未过账不能签收；代签不允许；必须拍照
    assert "过账" in org.post("a1", f"/api/lines/{line['id']}/sign", {"photos": [org.photo("a1")]}).json()["detail"]
    org.ok(org.post("keeper", f"/api/erp/docs/{doc['id']}/posted?posted=true"))
    assert org.post("lead_a", f"/api/lines/{line['id']}/sign", {"photos": [org.photo("lead_a")]}).status_code == 403
    assert org.post("a1", f"/api/lines/{line['id']}/sign", {"photos": []}).status_code == 400
    out = org.ok(org.post("a1", f"/api/lines/{line['id']}/sign", {"photos": [org.photo("a1")], "code": wms_code(qty=10)}))
    assert out["status"] == "S1" and out["balance"] == "10"


# ------------------------------------------------------------------ 使用、损耗、代登
def test_consume_loss_and_proxy(org, ref):
    line = issue(org, ref)
    lid = line["id"]
    p = org.photo("a1")
    assert org.post("a1", f"/api/lines/{lid}/consume", {"qty": 11, "dest": "D01", "ship_no": "XSCK-T001", "photos": [p]}).status_code == 400
    assert org.post("a1", f"/api/lines/{lid}/consume", {"qty": 1, "dest": "D05", "photos": [p]}).status_code == 400
    assert org.post("a1", f"/api/lines/{lid}/consume",
                    {"qty": 1, "is_loss": True, "loss_reason": "L5", "photos": [p]}).status_code == 400
    out = org.ok(org.post("a1", f"/api/lines/{lid}/consume", {"qty": 4, "dest": "D02", "photos": [p]}))
    assert out["status"] == "S2" and out["balance"] == "6"
    out = org.ok(org.post("a1", f"/api/lines/{lid}/consume",
                          {"qty": 0.7, "is_loss": True, "loss_reason": "L1", "photos": [p]}))
    assert out["loss"] == "0.7" and out["balance"] == "5.3"
    # 同组成员可代登并留痕通知；其他组成员不行
    out = org.ok(org.post("a2", f"/api/lines/{lid}/consume", {"qty": 1, "dest": "D01", "ship_no": "XSCK-T001", "photos": [org.photo("a2")]}))
    assert "代登" in out["flags"]
    todos = org.ok(org.get("a1", "/api/todos"))
    assert any("代您登记" in t["title"] for t in todos)
    assert org.post("b1", f"/api/lines/{lid}/consume",
                    {"qty": 1, "dest": "D01", "ship_no": "XSCK-T001", "photos": [org.photo("b1")]}).status_code == 403
    dash = org.ok(org.get("head", "/api/reports/dashboard"))
    assert dash["gate"]["proxy_rate"] > 0


# ------------------------------------------------------------------ 拆分换码
def test_split_conservation_and_codes(org, ref):
    line = issue(org, ref, qty=1)
    lid, old_code = line["id"], line["code"]
    p = org.photo("a1")
    b = blanks(6)
    r = org.post("a1", f"/api/lines/{lid}/split", {"pieces": [{"qty": 0.5, "code": b[0]}, {"qty": 0.4, "code": b[1]}],
                                                   "loss_qty": 0.2, "loss_reason": "L1", "photos": [p]})
    assert r.status_code == 400  # 0.5+0.4+0.2 > 1
    out = org.ok(org.post("a1", f"/api/lines/{lid}/split",
                          {"pieces": [{"qty": 0.4, "code": b[0]}], "loss_qty": 0.3, "loss_reason": "L2", "photos": [p],
                           "remainder_code": b[1]}))
    assert out["balance"] == "0.3" and out["code"] != old_code
    assert out["result"]["remainder"] == "0.300"
    child_code = out["result"]["children"][0]
    # 原码失效
    r = org.get("a1", f"/api/lines/by-code/{old_code}")
    assert r.status_code == 400 and "B4" in r.json()["detail"]
    child = org.ok(org.get("a1", f"/api/lines/by-code/{child_code}"))
    assert child["level"] == 2 and child["balance"] == "0.4" and child["status"] == "S1"
    # 第二层再拆一次 → 第三层；第三层不能再拆
    c2 = org.ok(org.post("a1", f"/api/lines/{child['id']}/split", {"pieces": [{"qty": 0.1, "code": b[2]}],
                                                                  "remainder_code": b[3], "photos": [p]}))
    grand = org.ok(org.get("a1", f"/api/lines/by-code/{c2['result']['children'][0]}"))
    assert grand["level"] == 2 and grand["codes"][0]["layer"] == 3
    r = org.post("a1", f"/api/lines/{grand['id']}/split", {"pieces": [{"qty": 0.05, "code": b[4]}],
                                                          "remainder_code": b[5], "photos": [p]})
    assert r.status_code == 400 and "第三层" in r.json()["detail"]


# ------------------------------------------------------------------ 暂存与转交
def test_hold_move_and_transfer(org, ref):
    line = issue(org, ref)
    lid = line["id"]
    p = org.photo("a1")
    assert org.post("a1", f"/api/lines/{lid}/move", {"location_id": ref.location("LOC-A3")}).status_code == 400
    out = org.ok(org.post("a1", f"/api/lines/{lid}/hold", {"location_id": ref.location("LOC-A3"), "photos": [p]}))
    assert out["status"] == "S3" and out["balance"] == "10"
    assert org.post("a1", f"/api/lines/{lid}/hold",
                    {"location_id": ref.location("LOC-B4"), "photos": [p]}).status_code == 400
    out = org.ok(org.post("a1", f"/api/lines/{lid}/move", {"location_id": ref.location("LOC-B4")}))
    assert out["location"] == "B栋4F产线3号工位"
    # 转交：确认前不生效
    out = org.ok(org.post("a1", f"/api/lines/{lid}/transfer", {"to_user_id": ref.user("b1"), "reason": "交实验"}))
    assert out["holder"] == "组员甲一" and out["pending_transfer"]["to_user"] == "组员乙一"
    eid = out["pending_transfer"]["id"]
    assert org.post("a1", f"/api/transfers/{eid}/decide?accept=true").status_code == 403
    out = org.ok(org.post("b1", f"/api/transfers/{eid}/decide?accept=true"))
    assert out["holder"] == "组员乙一" and out["status"] == "S4"
    # 责任随新持有人：原持有人不能再操作（不同组）
    assert org.post("a1", f"/api/lines/{lid}/consume", {"qty": 1, "dest": "D01", "ship_no": "XSCK-T001", "photos": [p]}).status_code == 403


def test_overdue_blocks_hold(org, ref):
    line = issue(org, ref, expected_return=(clock.today() + timedelta(days=1)).isoformat())
    clock.advance(timedelta(days=3))
    out = org.ok(org.get("a1", f"/api/lines/{line['id']}"))
    assert out["status"] == "S5"
    r = org.post("a1", f"/api/lines/{line['id']}/hold", {"location_id": ref.location("LOC-A3"), "photos": [org.photo("a1")]})
    assert r.status_code == 400 and "超期" in r.json()["detail"]


# ------------------------------------------------------------------ 退库（多对多）与对账
def test_return_many_to_many_and_recon(org, ref):
    l1 = issue(org, ref, qty=10)
    l2 = issue(org, ref, qty=6)
    org.ok(org.post("a1", f"/api/lines/{l1['id']}/return", {"qty": 4}))
    org.ok(org.post("a1", f"/api/lines/{l1['id']}/return", {"qty": 6}))
    org.ok(org.post("a1", f"/api/lines/{l2['id']}/return", {"qty": 5}))
    out = org.ok(org.get("a1", f"/api/lines/{l1['id']}"))
    assert out["balance"] == "0" and out["status"] == "S6"  # 已退但未挂 ERP 单
    pending = org.ok(org.get("keeper", "/api/returns/pending"))
    assert len(pending) == 3
    ev_ids = [p["event_id"] for p in pending]
    # 第一张退料单 8 米：跨两张领料单，按时间先进先出
    t1 = erp_doc(org, "TL", [{"material_code": "M-CABLE", "qty": 8}])
    res = org.ok(org.post("keeper", "/api/returns/allocate", {"erp_doc_id": t1["id"], "event_ids": ev_ids}))
    assert res["cross_doc"] and not res["leftover"]
    # 第二张退料单 8 米，只剩 7 米可摊 → 差 1 进差异池
    t2 = erp_doc(org, "TL", [{"material_code": "M-CABLE", "qty": 8}])
    res = org.ok(org.post("keeper", "/api/returns/allocate", {"erp_doc_id": t2["id"], "event_ids": ev_ids}))
    assert res["leftover"] == [{"line_no": 1, "unallocated": "1.000"}]
    assert org.ok(org.get("a1", f"/api/lines/{l1['id']}"))["status"] == "S7"
    diffs = [d for d in org.ok(org.get("keeper", "/api/recon/diffs")) if d["erp_doc_no"] == t2["no"]]
    assert diffs and diffs[0]["diff"] == "1"
    d = diffs[0]
    # 差额不为 0 不能关闭；ERP 侧开调整单并经财务审核后才平
    r = org.post("keeper", f"/api/recon/diffs/{d['id']}/resolve", {"reason_code": "C03", "method": "BIND"})
    assert r.status_code == 400 and "差额" in r.json()["detail"]
    tz = erp_doc(org, "TZ", [{"material_code": "M-CABLE", "qty": -1, "ref_line_no": 1}], ref_doc_no=t2["no"])
    r = org.post("keeper", f"/api/recon/diffs/{d['id']}/resolve",
                 {"reason_code": "C03", "method": "ADJUST", "ref": tz["no"]})
    assert "财务" in r.json()["detail"]
    org.ok(org.post("fin", f"/api/erp/docs/{tz['id']}/finance-approve"))
    org.ok(org.post("keeper", f"/api/recon/diffs/{d['id']}/resolve",
                    {"reason_code": "C03", "method": "ADJUST", "ref": tz["no"]}))


def test_close_requires_balanced_books(org, ref):
    req = make_req(org, ref)
    org.ok(org.post("lead_a", f"/api/requisitions/{req['id']}/decide", {"approve": True}))
    erp_doc(org, "LL", [{"material_code": "M-CABLE", "qty": 5}])  # ERP 有单、中台无明细
    clock.set_now(datetime(2026, 12, 2, 10, 0))
    assert org.post("keeper", "/api/recon/close/2026-11").status_code == 403  # 仅财务
    r = org.post("fin", "/api/recon/close/2026-11")
    assert r.status_code == 400 and "账不平" in r.json()["detail"]
    diffs = org.ok(org.get("fin", "/api/recon/diffs?period=2026-11"))
    assert diffs[0]["kind"] == "LL_UNBOUND"
    # 补登后自动平账，才能关账
    docs = org.ok(org.get("keeper", "/api/erp/docs?doc_type=LL"))
    org.ok(org.post("keeper", "/api/erp/bind", {"req_item_id": req["items"][0]["id"], "erp_line_id": docs[0]["lines"][0]["id"]}))
    org.ok(org.post("fin", "/api/recon/close/2026-11"))
    # 已关账月份不能再录单
    r = org.post("keeper", "/api/erp/docs", {"no": "LL-X", "doc_type": "LL", "doc_date": "2026-11-20", "posted": True,
                                             "lines": [{"material_code": "M-CABLE", "qty": 1}]})
    assert "关账" in r.json()["detail"]


# ------------------------------------------------------------------ 报废两条路
def test_scrap_residue_and_whole(org, ref):
    l1 = issue(org, ref, qty=1)
    p = org.photo("a1")
    org.ok(org.post("a1", f"/api/lines/{l1['id']}/consume", {"qty": 0.7, "dest": "D01", "ship_no": "XSCK-T001", "photos": [p]}))
    out = org.ok(org.post("a1", f"/api/lines/{l1['id']}/scrap", {"mode": "RESIDUE", "photos": [p]}))
    assert out["status"] == "S7" and out["loss"] == "0.3"  # 残料不开报废单，直接结案
    assert out["codes"][0]["state"] == "B5"
    l2 = issue(org, ref, qty=5)
    r = org.post("a1", f"/api/lines/{l2['id']}/scrap", {"mode": "WHOLE", "qty": 5, "reason": "F01", "photos": [p]})
    assert "箱号" in r.json()["detail"]
    out = org.ok(org.post("a1", f"/api/lines/{l2['id']}/scrap",
                          {"mode": "WHOLE", "qty": 5, "reason": "F01", "box": "BOX-1", "photos": [p]}))
    assert out["status"] == "S6" and out["codes"][0]["state"] == "B7"
    built = org.ok(org.post("keeper", "/api/scrap/batches/build"))["created"]
    assert len(built) == 1
    batch = org.ok(org.get("keeper", "/api/scrap/batches"))[0]
    bf = erp_doc(org, "BF", [{"material_code": "M-CABLE", "qty": 5}])
    org.ok(org.post("keeper", f"/api/scrap/batches/{batch['id']}/bind?erp_doc_id={bf['id']}"))
    assert org.ok(org.get("a1", f"/api/lines/{l2['id']}"))["status"] == "S7"
    obs = org.ok(org.get("head", "/api/reports/dashboard"))["observe"]
    assert obs["scrap_doc_rate"] == 100.0


# ------------------------------------------------------------------ 紧急领用
def test_urgent_handover_and_resolution(org, ref):
    req = make_req(org, ref, urgent=True, urgent_note="组长甲口头同意 11/2 9:00")
    line = org.ok(org.post("keeper", f"/api/requisitions/{req['id']}/urgent-handover/{req['items'][0]['id']}"))
    assert "紧急·待补" in line["flags"]
    out = org.ok(org.post("a1", f"/api/lines/{line['id']}/sign", {"photos": [org.photo("a1")], "code": wms_code(qty=10)}))
    assert out["status"] == "S1"  # 紧急领用无 ERP 单也能签收
    clock.advance(timedelta(hours=25))
    org.ok(org.post("admin", "/api/admin/jobs/quarter_hour"))
    assert any(t["kind"] == "URGENT_PENDING" for t in org.ok(org.get("a1", "/api/todos")))
    org.ok(org.post("lead_a", f"/api/requisitions/{req['id']}/decide", {"approve": True}))
    doc = erp_doc(org, "LL", [{"material_code": "M-CABLE", "qty": 10}], remark=req["no"])
    out = org.ok(org.post("keeper", "/api/erp/bind", {"req_item_id": req["items"][0]["id"],
                                                      "erp_line_id": doc["lines"][0]["id"]}))
    assert "紧急·待补" not in out["flags"]


def test_urgent_rejected_must_return(org, ref):
    req = make_req(org, ref, urgent=True, urgent_note="口头同意")
    line = org.ok(org.post("keeper", f"/api/requisitions/{req['id']}/urgent-handover/{req['items'][0]['id']}"))
    org.ok(org.post("lead_a", f"/api/requisitions/{req['id']}/decide", {"approve": False, "note": "不需要"}))
    assert any(t["kind"] == "MUST_RETURN" and t["ref_id"] == line["id"] for t in org.ok(org.get("a1", "/api/todos")))


# ------------------------------------------------------------------ 审核时限
def test_approval_reminder_and_escalation(org, ref):
    req = make_req(org, ref)
    clock.advance(timedelta(hours=4, minutes=1))
    org.ok(org.post("admin", "/api/admin/jobs/quarter_hour"))
    assert any("4 小时" in t["title"] for t in org.ok(org.get("lead_a", "/api/todos")))
    clock.advance(timedelta(hours=20))
    org.ok(org.post("admin", "/api/admin/jobs/quarter_hour"))
    assert any("升级" in t["title"] for t in org.ok(org.get("head", "/api/todos")))
    out = org.ok(org.post("head", f"/api/requisitions/{req['id']}/decide", {"approve": True}))
    assert out["decided_as"] == "ESCALATED"


# ------------------------------------------------------------------ 超期冻结与长周期
def test_freeze_and_unfreeze(org, ref):
    issue(org, ref, expected_return=(clock.today() + timedelta(days=1)).isoformat())
    clock.advance(timedelta(days=17))
    org.ok(org.post("admin", "/api/admin/jobs/daily"))
    me = org.ok(org.get("a1", "/api/auth/me"))
    assert me["frozen"]
    r = org.post("a1", "/api/requisitions", {"project_id": ref.project("PDJ001"), "reason_code": "R01", "remark": "x",
                                             "expected_return": "2027-01-30", "urgent": True, "urgent_note": "x",
                                             "items": [{"material_id": ref.material("M-CABLE"), "qty": 1}]})
    assert "冻结" in r.json()["detail"]
    assert org.post("lead_b", f"/api/users/{ref.user('a1')}/unfreeze").status_code == 403
    org.ok(org.post("lead_a", f"/api/users/{ref.user('a1')}/unfreeze"))
    org.ok(org.post("admin", "/api/admin/jobs/daily"))  # 刚解冻的 14 天内不重复冻结
    assert not org.ok(org.get("a1", "/api/auth/me"))["frozen"]


def test_long_cycle_quarterly_confirm_and_renew(org, ref):
    line = issue(org, ref, long_cycle=True, long_cycle_months=3,
                 expected_return=(clock.today() + timedelta(days=60)).isoformat())
    assert "长周期" in line["flags"][0]
    # 超期也不触发 2 周冻结
    clock.advance(timedelta(days=80))
    org.ok(org.post("admin", "/api/admin/jobs/daily"))
    assert not org.ok(org.get("a1", "/api/auth/me"))["frozen"]
    # 季度确认任务，7 天内未确认 → 按标准类
    org.ok(org.post("admin", "/api/admin/jobs/quarterly"))
    assert any(t["kind"] == "QCONFIRM" for t in org.ok(org.get("a1", "/api/todos")))
    clock.advance(timedelta(days=8))
    org.ok(org.post("admin", "/api/admin/jobs/daily"))
    assert "已按标准类" in org.ok(org.get("a1", f"/api/lines/{line['id']}"))["flags"][0]
    # 续期：直属上级审核
    org.ok(org.post("a1", f"/api/lines/{line['id']}/renew", {"months": 6, "reason": "项目延期"}))
    reqs = org.ok(org.get("lead_a", "/api/line-requests"))
    assert reqs and reqs[0]["etype"] == "RENEW"
    assert org.post("a1", f"/api/line-requests/{reqs[0]['id']}/decide", {"approve": True}).status_code == 403
    org.ok(org.post("lead_a", f"/api/line-requests/{reqs[0]['id']}/decide", {"approve": True}))
    out = org.ok(org.get("a1", f"/api/lines/{line['id']}"))
    assert out["status"] != "S5" and out["due_date"] > clock.today().isoformat()


# ------------------------------------------------------------------ 冲销（两人）
def test_reversal_two_person(org, ref):
    line = issue(org, ref)
    out = org.ok(org.post("a1", f"/api/lines/{line['id']}/consume", {"qty": 3, "dest": "D01", "ship_no": "XSCK-T001", "photos": [org.photo("a1")]}))
    ev = [e for e in out["events"] if e["etype"] == "CONSUME"][0]
    assert org.post("a1", "/api/reversals", {"event_id": ev["id"], "reason": "数量错"}).status_code == 403
    rev = org.ok(org.post("keeper", "/api/reversals", {"event_id": ev["id"], "reason": "数量错登"}))
    assert org.ok(org.get("a1", f"/api/lines/{line['id']}"))["balance"] == "7"  # 待复核不生效
    org.ok(org.post("admin", f"/api/reversals/{rev['id']}/decide", {"approve": True}))
    assert org.ok(org.get("a1", f"/api/lines/{line['id']}"))["balance"] == "10"


# ------------------------------------------------------------------ 离职卡点、字典变更、权限
def test_deactivate_requires_clearance(org, ref):
    issue(org, ref)
    body = {"username": "a1", "name": "组员甲一", "manager_id": ref.user("lead_a"), "roles": ["USER"], "active": False}
    r = org.c.put(f"/api/users/{ref.user('a1')}", json=body, headers=org._h("admin"))
    assert r.status_code == 400 and "清零" in r.json()["detail"]
    assert not org.ok(org.get("lead_a", f"/api/users/{ref.user('a1')}/clearance"))["clear"]


def test_dict_change_needs_vp(org):
    c = org.ok(org.post("admin", "/api/dict-changes", {"dict_type": "LOSS_REASON", "action": "ADD", "code": "L6",
                                                       "name": "运输损坏", "reason": "季度复盘新增"}))
    assert org.post("admin", f"/api/dict-changes/{c['id']}/decide?approve=true").status_code == 403
    org.ok(org.post("vp", f"/api/dict-changes/{c['id']}/decide?approve=true"))
    assert any(d["code"] == "L6" for d in org.ok(org.get("a1", "/api/dicts"))["LOSS_REASON"])


def test_visibility_scope(org, ref):
    line = issue(org, ref)
    assert org.get("a2", f"/api/lines/{line['id']}").status_code == 200  # 本组只读
    assert org.get("b1", f"/api/lines/{line['id']}").status_code == 403  # 其他组
    assert org.get("head", f"/api/lines/{line['id']}").status_code == 200  # 下属逐级
    assert len(org.ok(org.get("lead_b", "/api/lines"))) == 0
