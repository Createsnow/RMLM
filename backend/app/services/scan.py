"""扫码统一识别（蓝图 3.1：扫码为主、选单为备，两条路进同一套校验）。

一个入口依次识别：物料码 → ERP 单据号 → 库位码 → 申请号 → 仓储系统里的条码（实时查询，放最后，免得每次扫单号都调仓储）。
- 已绑定的物料码：直接带出该行；已拆分替换（B4）的旧码拒绝，提示扫新码。
- 中台还没有、仓储系统里有的条码：列出本人名下同物料、待签收的行（有出库单号时按单号对），点开后带着这个码签收。
- ERP 单据号（选单号路径）：列出该单各行对应的领用行；可逐行签收（/lines/{id}/sign），
  一人领整单时也可整单批量签收（/lines/batch-sign，2026-11 业务确认），两条路同一套校验。
- 库位码：列出放在该库位 / 工位 / 报废箱的在制行。
- 申请号：返回申请 id，由前端打开申请详情（详情接口自带权限校验）。
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..errors import BizError
from ..models import DocAlloc, ErpDoc, ErpLine, Event, LedgerLine, Location, Material, MaterialCode, Requisition, User
from ..perms import can_view_line
from . import ledger, wms
from .common import D, qs


def _brief(db: Session, viewer: User, line: LedgerLine, c) -> dict:
    from ..api.serial import line_out

    d = line_out(db, line, viewer, c)
    d["can_sign"] = (not line.signed) and line.holder_id == viewer.id and not line.locked and not line.voided
    return d


def resolve(db: Session, viewer: User, raw: str) -> dict:
    code = (raw or "").strip()
    if not code:
        raise BizError("请扫描或输入物料码 / 单据号")

    # 1. 物料码（当前有效码）
    line = db.scalar(select(LedgerLine).where(LedgerLine.code == code))
    if line is not None and db.scalar(select(MaterialCode.id).where(MaterialCode.code == code,
                                                                    MaterialCode.replaced.is_(True))):
        raise BizError(f"物料码 {code} 已拆分替换（B4），不能再扫；请扫新码")
    if line is not None:
        if not can_view_line(db, viewer, line):
            from ..errors import forbid

            raise forbid("无权查看该物料")
        return {"kind": "line", "code": code, "line_id": line.id}
    mc = db.scalar(select(MaterialCode).where(MaterialCode.code == code))
    if mc is not None:
        raise BizError(f"物料码 {code} 已拆分替换（B4），不能再扫；请扫新码")

    # 2. ERP 单据号（选单号）
    doc = db.scalar(select(ErpDoc).where(ErpDoc.no == code))
    if doc is not None:
        return _doc(db, viewer, doc)

    # 3. 库位码
    loc = db.scalar(select(Location).where(Location.code == code))
    if loc is not None:
        lines = [l for l in db.scalars(select(LedgerLine).where(LedgerLine.location_id == loc.id,
                                                                 LedgerLine.voided.is_(False)))
                 if can_view_line(db, viewer, l)]
        calcs = ledger.load(db, lines)
        rows = [_brief(db, viewer, l, calcs[l.id]) for l in lines if calcs[l.id].balance > 0]
        return {"kind": "location", "code": code,
                "location": {"id": loc.id, "code": loc.code, "name": loc.name, "kind": loc.kind}, "lines": rows}

    # 4. 申请号
    req = db.scalar(select(Requisition).where(Requisition.no == code))
    if req is not None:
        return {"kind": "req", "code": code, "req_id": req.id}

    # 5. 仓储系统里的条码（还没签收绑定）
    if wms.enabled():
        bc = wms.lookup(code)
        if bc is not None:
            return _wms(db, viewer, bc)

    raise BizError(f"未识别的码：{code}（不是物料码、ERP 单据号、库位码或申请号）")


def _doc(db: Session, viewer: User, doc: ErpDoc) -> dict:
    mats = {m.id: m for m in db.scalars(select(Material).where(
        Material.id.in_({ln.material_id for ln in doc.lines} or {0})))}
    rows, hidden = [], 0
    lines_by_erp: dict[int, list[LedgerLine]] = {}
    if doc.doc_type in ("LL", "AB"):
        for l in db.scalars(select(LedgerLine).where(LedgerLine.erp_line_id.in_([ln.id for ln in doc.lines] or [0]))):
            lines_by_erp.setdefault(l.erp_line_id, []).append(l)
    else:
        # 退料 / 报废单：通过分摊找到对应的领用行
        q = (select(DocAlloc.erp_line_id, Event.line_id).join(Event, Event.id == DocAlloc.event_id)
             .where(DocAlloc.erp_line_id.in_([ln.id for ln in doc.lines] or [0])))
        for erp_line_id, line_id in db.execute(q):
            l = db.get(LedgerLine, line_id)
            if l and l not in lines_by_erp.get(erp_line_id, []):
                lines_by_erp.setdefault(erp_line_id, []).append(l)
    all_lines = [l for ls in lines_by_erp.values() for l in ls]
    calcs = ledger.load(db, all_lines)
    for ln in sorted(doc.lines, key=lambda x: x.line_no):
        m = mats.get(ln.material_id)
        visible = []
        for l in lines_by_erp.get(ln.id, []):
            if can_view_line(db, viewer, l):
                visible.append(_brief(db, viewer, l, calcs[l.id]))
            else:
                hidden += 1
        rows.append({"line_no": ln.line_no, "material_code": m.code if m else None, "material": m.name if m else None,
                     "unit": m.unit if m else None, "qty": qs(ln.qty), "lines": visible})
    return {"kind": "doc", "code": doc.no,
            "doc": {"id": doc.id, "no": doc.no, "doc_type": doc.doc_type, "doc_date": doc.doc_date.isoformat(),
                    "posted": doc.posted},
            "rows": rows, "hidden": hidden, "sign_needs_codes": wms.enabled(),
            "to_sign": sum(1 for r in rows for l in r["lines"] if l["can_sign"])}


def _wms(db: Session, viewer: User, bc) -> dict:
    """仓储条码还没在中台签收：找本人名下同物料、待签收的行。"""
    m = db.scalar(select(Material).where(Material.code == bc.material_code))
    info = {**bc.as_dict(), "code": bc.barcode, "material": m.name if m else None}
    cands = []
    if m is not None and bc.status == wms.OUTBOUND:
        lines = list(db.scalars(select(LedgerLine).where(LedgerLine.material_id == m.id, LedgerLine.voided.is_(False),
                                                         LedgerLine.holder_id == viewer.id,
                                                         LedgerLine.signed.is_(False)).order_by(LedgerLine.id)))
        if bc.out_doc_no:
            docs = {l.id: db.scalar(select(ErpDoc.no).join(ErpLine).where(ErpLine.id == l.erp_line_id))
                    for l in lines if l.erp_line_id}
            lines = [l for l in lines if docs.get(l.id) == bc.out_doc_no or l.erp_line_id is None]
        calcs = ledger.load(db, lines)
        for l in lines:
            d = _brief(db, viewer, l, calcs[l.id])
            d["action"] = "sign"
            d["qty_match"] = D(bc.qty) == D(l.qty)
            cands.append(d)
    return {"kind": "wms", "code": bc.barcode, "wms": info, "candidates": cands}
