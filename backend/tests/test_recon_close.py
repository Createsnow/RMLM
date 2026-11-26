"""关闭差异按蓝图表 3-7：领料专员第④步认领，财务 + IT 第⑤步关闭；认领人不能关闭自己认领的差异。"""
from sqlalchemy import select

from app import db as dbmod
from app.models import User

from .helpers import erp_doc


def _diff(org, doc_no):
    org.ok(org.post("keeper", "/api/recon/run"))
    return next(d for d in org.ok(org.get("fin", "/api/recon/diffs")) if d["erp_doc_no"] == doc_no)


def _todos(org, who, kind):
    return [t for t in org.ok(org.get(who, "/api/todos")) if t["kind"] == kind]


def _adjust(org, doc_no, qty):
    tz = erp_doc(org, "TZ", [{"material_code": "M-CABLE", "qty": qty, "ref_line_no": 1}], ref_doc_no=doc_no)
    org.ok(org.post("fin", f"/api/erp/docs/{tz['id']}/finance-approve"))
    return tz


def test_keeper_claims_finance_or_it_closes(org):
    ll = erp_doc(org, "LL", [{"material_code": "M-CABLE", "qty": 5}])  # ERP 有单、中台无明细 → 差 5
    d = _diff(org, ll["no"])
    body = {"reason_code": "C03", "method": "BIND"}

    # 领料专员只能认领，不能关闭
    r = org.post("keeper", f"/api/recon/diffs/{d['id']}/resolve", body)
    assert r.status_code == 403
    # 未认领的差异，财务 / IT 也不能关闭
    r = org.post("fin", f"/api/recon/diffs/{d['id']}/resolve", body)
    assert r.status_code == 400 and "认领" in r.json()["detail"]

    # 认领后给财务和 IT 派"关闭差异"待办，认领人自己没有
    org.ok(org.post("keeper", f"/api/recon/diffs/{d['id']}/claim"))
    for who in ("fin", "admin", "admin2"):
        assert len(_todos(org, who, "RECON_RESOLVE")) == 1
    assert not _todos(org, "keeper", "RECON_RESOLVE")

    # IT 可以关闭；差额不为 0 仍然不能关
    r = org.post("admin", f"/api/recon/diffs/{d['id']}/resolve", body)
    assert r.status_code == 400 and "差额" in r.json()["detail"]
    tz = _adjust(org, ll["no"], -5)
    out = org.ok(org.post("admin", f"/api/recon/diffs/{d['id']}/resolve",
                          {"reason_code": "C03", "method": "ADJUST", "ref": tz["no"]}))
    assert out["state"] == "RESOLVED"
    d = _diff(org, ll["no"])
    assert d["reason_code"] == "C03" and d["owner"] == "领料员"
    for who in ("fin", "admin", "admin2"):
        assert not _todos(org, who, "RECON_RESOLVE")


def test_claimer_cannot_close_own_diff(org):
    with dbmod.SessionLocal() as db:  # 同时兼任领料专员和财务
        db.scalar(select(User).where(User.username == "keeper")).roles = "USER,KEEPER,FINANCE"
        db.commit()
    ll = erp_doc(org, "LL", [{"material_code": "M-CABLE", "qty": 3}])
    d = _diff(org, ll["no"])
    org.ok(org.post("keeper", f"/api/recon/diffs/{d['id']}/claim"))
    tz = _adjust(org, ll["no"], -3)
    body = {"reason_code": "C03", "method": "ADJUST", "ref": tz["no"]}
    r = org.post("keeper", f"/api/recon/diffs/{d['id']}/resolve", body)
    assert r.status_code == 400 and "认领人不能关闭" in r.json()["detail"]
    org.ok(org.post("fin", f"/api/recon/diffs/{d['id']}/resolve", body))
