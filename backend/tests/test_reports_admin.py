import io
from datetime import timedelta

from openpyxl import Workbook

from app import clock

from .helpers import issue


def test_reports_smoke(org, ref):
    line = issue(org, ref, qty=10)
    p = org.photo("a1")
    org.ok(org.post("a1", f"/api/lines/{line['id']}/consume", {"qty": 2, "is_loss": True, "loss_reason": "L1", "photos": [p]}))
    org.ok(org.post("a1", f"/api/lines/{line['id']}/hold", {"location_id": ref.location("LOC-A3"), "photos": [p]}))
    clock.advance(timedelta(days=31))
    for who in ("head", "a1", "keeper"):
        d = org.ok(org.get(who, "/api/reports/dashboard"))
        assert "gate" in d and "observe" in d
    weekly = org.ok(org.get("head", "/api/reports/weekly"))
    assert len(weekly["overdue"]) == 1  # 超过预计归还日（30 天）
    proj = org.ok(org.get("head", "/api/reports/projects"))
    assert proj[0]["issued_amount"] == "25.00" and proj[0]["balance_amount"] == "20.00"
    loss = org.ok(org.get("head", "/api/reports/loss"))
    assert loss["by_material"][0]["rate"] == 20.0
    org.ok(org.get("head", "/api/reports/monthly?period=2026-11"))
    q = org.ok(org.get("head", "/api/reports/quarterly"))
    assert q["stocktake_sample"] and q["dict_usage"].get("R01") == 1
    tick = org.ok(org.post("admin", "/api/admin/jobs/tick"))
    assert "daily" in tick and "weekly" in tick
    assert org.ok(org.get("admin", "/api/audit"))


def test_split_reversal_voids_children(org, ref):
    line = issue(org, ref, qty=10)
    out = org.ok(org.post("a1", f"/api/lines/{line['id']}/split", {"pieces": [3, 2], "photos": [org.photo("a1")]}))
    ev = [e for e in out["events"] if e["etype"] == "SPLIT_OUT"][0]
    rev = org.ok(org.post("keeper", "/api/reversals", {"event_id": ev["id"], "reason": "拆错"}))
    assert org.post("keeper", f"/api/reversals/{rev['id']}/decide", {"approve": True}).status_code == 403
    org.ok(org.post("admin", f"/api/reversals/{rev['id']}/decide", {"approve": True}))
    out = org.ok(org.get("a1", f"/api/lines/{line['id']}"))
    assert out["balance"] == "10" and all(c["voided"] for c in out["children"])


def test_user_admin_and_import(org, ref):
    u = org.ok(org.post("admin", "/api/users", {"username": "new1", "name": "新人", "manager_id": ref.user("lead_a"),
                                               "roles": ["KEEPER"], "password": "Abcdefg1!"}))
    assert set(u["roles"]) == {"KEEPER", "USER"}
    assert org.post("a1", "/api/users", {"username": "x", "name": "x"}).status_code == 403
    # 不能把下属设为自己的上级（防环）
    body = {"username": "lead_a", "name": "组长甲", "manager_id": ref.user("a1"), "roles": ["USER"]}
    assert org.c.put(f"/api/users/{ref.user('lead_a')}", json=body, headers=org._h("admin")).status_code == 400
    wb = Workbook()
    ws = wb.active
    ws.append(["序号", "岗位", "姓名", "直属上级", "账号", "角色"])
    ws.append([1, "工程经理", "导入甲", "/", "imp_a", "DEPT_HEAD"])
    ws.append([2, "工程师", "导入乙", "导入甲", "imp_b", ""])
    buf = io.BytesIO()
    wb.save(buf)
    r = org.c.post("/api/users/import", headers=org._h("admin"),
                   files={"file": ("u.xlsx", buf.getvalue(), "application/octet-stream")})
    assert org.ok(r)["created"] == 2
    users = {x["username"]: x for x in org.ok(org.get("admin", "/api/users"))}
    assert users["imp_b"]["manager_name"] == "导入甲" and "DEPT_HEAD" in users["imp_a"]["roles"]


def test_password_change(org):
    assert org.post("a1", "/api/auth/password", {"old": "bad", "new": "Newpass123"}).status_code == 400
    org.ok(org.post("a1", "/api/auth/password", {"old": "Passw0rd!", "new": "Newpass123"}))
