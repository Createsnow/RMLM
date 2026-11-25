"""演示数据（全部为虚构人员与数据），用于试用和培训。

用法：
    cd backend && python -m scripts.demo_seed          # 往当前 RMLM_DATABASE_URL 写入演示数据
所有演示账号密码均为 Demo@1234。
"""
from datetime import timedelta

from sqlalchemy import select

from app import clock
from app.db import SessionLocal
from app.main import init_db
from app.models import Location, Material, Project, User
from app.security import hash_password
from app.services import erp, lineops, requisition
from app.services.photos import save_photo

PW = "Demo@1234"


def main() -> None:
    init_db()
    db = SessionLocal()
    if db.scalar(select(User.id).where(User.username == "demo_vp")):
        print("演示数据已存在，跳过")
        return

    def mk(username, name, title, roles="USER", manager=None):
        u = User(username=username, name=name, title=title, roles=roles, password_hash=hash_password(PW),
                 manager_id=manager.id if manager else None, must_change_pw=False)
        db.add(u)
        db.flush()
        return u

    vp = mk("demo_vp", "演示-副总", "研发副总", "USER,VP")
    head = mk("demo_head", "演示-经理", "工程高级经理", "USER,DEPT_HEAD", vp)
    lead = mk("demo_lead", "演示-组长", "工艺开发工程师", "USER", head)
    lab = mk("demo_lab", "演示-实验室组长", "可靠性工程师", "USER", head)
    eng1 = mk("demo_eng1", "演示-工程师甲", "助理工艺工程师", "USER", lead)
    eng2 = mk("demo_eng2", "演示-工程师乙", "助理工艺工程师", "USER", lead)
    tester = mk("demo_lab1", "演示-测试员", "实验室测试员", "USER", lab)
    keeper = mk("demo_keeper", "演示-领料专员", "助理工艺工程师", "USER,KEEPER", head)
    mk("demo_wh", "演示-仓管", "仓库管理员", "USER,WAREHOUSE")
    mk("demo_fin", "演示-财务", "成本会计", "USER,FINANCE")
    mk("demo_it", "演示-IT", "系统管理员", "USER,ADMIN")
    mk("demo_pm", "演示-项目经理", "项目管理工程师", "USER,PROJECT_VIEW", head)

    db.add_all([
        Project(code="PDJ20251101", name="1195 客户 3456F 返修方案验证", customer_code="1195"),
        Project(code="RD202519", name="实验室测试线 MPO/APC", customer_code="0832"),
        Project(code="PRC20251209", name="B 栋 4F shuffle 开新线", customer_code=None),
        Project(code="试制-1300", name="1300 项目试制（ERP 虚拟项目）", customer_code="1300"),
        Material(code="1231346", name="MPO 散件+插芯", spec="MPO 3.0 SM 公头 黄色外框", unit="套", unit_price=38.5),
        Material(code="1220987", name="空管", spec="SX 2.2 PVC 内 1.5 hytrel", unit="米", unit_price=1.2),
        Material(code="1000454", name="室内光缆", spec="SM G657A1 带纤 12 芯", unit="米", unit_price=4.8),
        Material(code="F9132074", name="光纤跳线", spec="1xMPO(F)/APC-16xLC", unit="条", unit_price=126),
        Location(code="A3-TC3", name="A 栋 3 楼工具柜 3", kind="STOCK"),
        Location(code="B3-ANA", name="B 栋 3 楼工程分析室", kind="STOCK"),
        Location(code="B2-LAB", name="B 栋 2 楼可靠性实验室", kind="STOCK"),
        Location(code="B4-WS3", name="B 栋 4F shuffle 产线 3 号工位", kind="STATION"),
        Location(code="BOX-1", name="研发报废暂存区 箱 1", kind="SCRAP_BOX"),
        Location(code="BOX-2", name="研发报废暂存区 箱 2", kind="SCRAP_BOX"),
    ])
    db.flush()
    proj = {p.code: p for p in db.scalars(select(Project))}
    mat = {m.code: m for m in db.scalars(select(Material))}

    def flow(user, approver, pcode, mcode, qty, sign=True, approve=True, **kw):
        req = requisition.create(db, user, {
            "project_id": proj[pcode].id, "reason_code": kw.pop("reason", "R01"), "remark": kw.pop("remark", "演示：验证明细"),
            "expected_return": (clock.today() + timedelta(days=kw.pop("days", 30))).isoformat(),
            "items": [{"material_id": mat[mcode].id, "qty": qty}], **kw})
        if not approve:
            return req, None
        requisition.decide(db, approver, req, True, None)
        doc = erp.save_doc(db, keeper, {"no": f"QTCK-DEMO-{req.id:04d}", "doc_type": "LL", "posted": True,
                                        "remark": req.no, "lines": [{"material_code": mcode, "qty": qty}]})
        line = erp.bind(db, keeper, req.items[0].id, doc.lines[0].id)
        if sign:
            lineops.sign(db, line, user, photo(user))  # 演示环境不接仓储，使用 RM 临时码
        return req, line

    def photo(u):
        return [save_photo(db, u, _img()).id]

    _, l1 = flow(eng1, lead, "PDJ20251101", "1220987", 20)
    lineops.consume(db, l1, eng1, 5, "D02", False, None, "3456F 返修验证", photo(eng1))
    lineops.consume(db, l1, eng1, 1.5, None, True, "L1", "裁剪损耗", photo(eng1))
    _, l2 = flow(eng1, lead, "PDJ20251101", "1231346", 6)
    lineops.transfer_request(db, l2, eng1, tester.id, "交实验室做可靠性测试")
    _, l3 = flow(eng2, lead, "RD202519", "1000454", 45, days=1)
    lineops.consume(db, l3, eng2, 20, "D01", False, None, "12PCS 测试线制作完成", photo(eng2), ship_no="XSCK-2601-0032")
    flow(eng2, lead, "PRC20251209", "F9132074", 2, sign=False)
    flow(eng1, lead, "RD202519", "1000454", 10, approve=False, remark="待审核的演示申请")
    flow(tester, lab, "试制-1300", "1231346", 4, long_cycle=True, long_cycle_months=6, days=150, reason="R03",
         remark="长周期可靠性老化测试")
    db.commit()
    print("演示数据已写入。演示账号（密码 Demo@1234）：")
    for u in db.scalars(select(User).where(User.username.like("demo_%"))):
        print(f"  {u.username:12s} {u.name}  {u.roles}")


def _img() -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (640, 480), (120, 140, 90)).save(buf, "JPEG")
    return buf.getvalue()


if __name__ == "__main__":
    main()
