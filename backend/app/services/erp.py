"""金蝶单据镜像（只读，不回写金蝶）。

单据来源：手工录入 / Excel 导入（金蝶导出）/ 金蝶只读接口（ErpReader，金蝶实现见 k3cloud.py）。
主数据：物料已接金蝶 WebAPI（master_sync.py）；项目档案仍用 Excel 导入。
"""
import re
from datetime import date, datetime
from typing import Protocol

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import clock
from ..errors import BizError
from ..models import ErpDoc, ErpLine, LedgerLine, Material, Project, ReqItem, Requisition, User
from ..perms import require_role
from . import requisition as reqsvc
from .common import D, add_todo, close_todos, dict_codes

DOC_TYPES = {"LL", "AB", "TL", "BF", "TZ"}


class ErpReader(Protocol):
    """金蝶只读接口约定：由定时任务按天增量拉取。

    fetch_materials 每行：code, name, spec, unit, category, erp_id, forbidden(bool), modified_at(datetime|None)。
    """

    def fetch_docs(self, since: date) -> list[dict]: ...
    def fetch_projects(self) -> list[dict]: ...
    def fetch_materials(self, since: datetime | None = None) -> list[dict]: ...


def save_doc(db: Session, op: User | None, data: dict, source: str = "MANUAL") -> ErpDoc:
    if op is not None:
        require_role(op, "KEEPER", "ADMIN")
    no = (data.get("no") or "").strip()
    if not no:
        raise BizError("单据号必填")
    if db.scalar(select(ErpDoc.id).where(ErpDoc.no == no)):
        raise BizError(f"单据 {no} 已存在")
    dtype = data.get("doc_type")
    if dtype not in DOC_TYPES:
        raise BizError("单据类型不正确")
    ddate = date.fromisoformat(str(data.get("doc_date") or clock.today()))
    from .recon import period_closed

    if period_closed(db, ddate):
        raise BizError(f"{ddate:%Y-%m} 已关账，不能再录入该月单据")
    doc = ErpDoc(no=no, doc_type=dtype, doc_date=ddate, posted=bool(data.get("posted")),
                 remark=data.get("remark"), ref_doc_no=data.get("ref_doc_no"), source=source,
                 created_by=op.id if op else None)
    if dtype == "TZ":
        if not doc.ref_doc_no or not db.scalar(select(ErpDoc.id).where(ErpDoc.no == doc.ref_doc_no)):
            raise BizError("调整单必须填写被调整的原单号")
    lines = data.get("lines") or []
    if not lines:
        raise BizError("单据至少一行")
    seen = set()
    for i, ln in enumerate(lines, start=1):
        line_no = int(ln.get("line_no") or i)
        if line_no in seen:
            raise BizError(f"行号 {line_no} 重复")
        seen.add(line_no)
        m = db.scalar(select(Material).where(Material.code == str(ln.get("material_code") or "").strip()))
        if not m:
            raise BizError(f"第 {line_no} 行物料编码不存在：{ln.get('material_code')}")
        q = D(ln.get("qty"))
        if q == 0 or (q < 0 and dtype != "TZ"):
            raise BizError(f"第 {line_no} 行数量不正确")
        price = ln.get("unit_price")
        doc.lines.append(ErpLine(line_no=line_no, material_id=m.id, qty=q, project_code=ln.get("project_code"),
                                 unit_price=D(price) if price not in (None, "") else None,
                                 ref_line_no=int(ln["ref_line_no"]) if ln.get("ref_line_no") else None))
        if price not in (None, "") and dtype in ("LL", "AB"):
            m.unit_price = price  # 同步 ERP 单价
    db.add(doc)
    db.flush()
    after_doc_change(db, doc)
    return doc


def set_posted(db: Session, op: User, doc: ErpDoc, posted: bool) -> ErpDoc:
    require_role(op, "KEEPER", "ADMIN")
    doc.posted = posted
    after_doc_change(db, doc)
    return doc


def after_doc_change(db: Session, doc: ErpDoc) -> None:
    if doc.doc_type in ("LL", "AB"):
        for ln in doc.lines:
            line = db.scalar(select(LedgerLine).where(LedgerLine.erp_line_id == ln.id))
            if line is not None:
                reqsvc.refresh_urgent(db, line)
                if doc.posted and not line.signed:
                    add_todo(db, line.holder_id, "SIGN", f"请签收物料 {line.code}", "line", line.id, dedupe=True)


def finance_approve(db: Session, op: User, doc: ErpDoc) -> ErpDoc:
    require_role(op, "FINANCE")
    if doc.doc_type != "TZ":
        raise BizError("只有调整单需要财务审核")
    doc.finance_approved, doc.finance_by = True, op.id
    close_todos(db, "erpdoc", doc.id)
    return doc


def warehouse_receive(db: Session, op: User, doc: ErpDoc) -> ErpDoc:
    require_role(op, "WAREHOUSE")
    if doc.doc_type not in ("TL", "BF"):
        raise BizError("只有退料单 / 报废单需要仓库确认实物")
    doc.received, doc.received_by, doc.received_at = True, op.id, clock.now()
    return doc


REQ_NO = re.compile(r"REQ-\d{8}-\d{4}")


def suggestions(db: Session) -> list[dict]:
    """自动匹配：领料专员开金蝶单时在备注里写中台申请号，系统按申请号 + 物料给出绑定建议。"""
    out = []
    docs = db.scalars(select(ErpDoc).where(ErpDoc.doc_type.in_(("LL", "AB"))))
    for doc in docs:
        nos = REQ_NO.findall(doc.remark or "")
        if not nos:
            continue
        reqs = list(db.scalars(select(Requisition).where(Requisition.no.in_(nos))))
        for ln in doc.lines:
            if db.scalar(select(LedgerLine.id).where(LedgerLine.erp_line_id == ln.id)):
                continue
            for r in reqs:
                for it in r.items:
                    if it.material_id != ln.material_id:
                        continue
                    bound = db.scalar(select(LedgerLine.id).where(LedgerLine.req_item_id == it.id,
                                                                  LedgerLine.erp_line_id.is_not(None)).limit(1))
                    if bound:
                        continue
                    out.append({"erp_line_id": ln.id, "erp_doc_no": doc.no, "erp_line_no": ln.line_no,
                                "req_item_id": it.id, "req_no": r.no, "qty": str(ln.qty)})
    return out


def bind(db: Session, op: User, req_item_id: int, erp_line_id: int) -> LedgerLine:
    """把审核通过的申请明细绑定到金蝶领料单行（一物一行），生成领用行；紧急行则补挂单据。"""
    require_role(op, "KEEPER")
    item = db.get(ReqItem, req_item_id)
    erp = db.get(ErpLine, erp_line_id)
    if not item or not erp:
        raise BizError("申请明细或 ERP 单据行不存在")
    req = item.req
    if erp.doc.doc_type not in ("LL", "AB"):
        raise BizError("只能绑定领料出库单 / A 转 B 单")
    if erp.material_id != item.material_id:
        raise BizError("ERP 单据行的物料与申请不一致")
    if db.scalar(select(LedgerLine.id).where(LedgerLine.erp_line_id == erp.id)):
        raise BizError("该 ERP 单据行已绑定（一物一行）")
    urgent_line = db.scalar(select(LedgerLine).where(LedgerLine.req_item_id == item.id,
                                                     LedgerLine.erp_line_id.is_(None), LedgerLine.urgent.is_(True),
                                                     LedgerLine.level == 1))
    if urgent_line is not None:
        urgent_line.erp_line_id = erp.id
        if erp.unit_price is not None:
            urgent_line.unit_price = erp.unit_price
        reqsvc.refresh_urgent(db, urgent_line)
        close_todos(db, "erpline", erp.id)
        return urgent_line
    if req.status != "APPROVED":
        raise BizError("申请未经直属上级审核，不能开单")
    line = reqsvc._new_line(db, op, req, item, qty=erp.qty, erp_line=erp)
    close_todos(db, "erpline", erp.id)
    if erp.doc.posted:
        add_todo(db, line.holder_id, "SIGN", f"请签收物料 {line.code}", "line", line.id)
    return line


# ------------------------------------------------------------------ Excel 导入
def import_docs_xlsx(db: Session, op: User, fileobj) -> int:
    """列：单据号 | 类型(LL/AB/TL/BF/TZ) | 日期 | 已过账(是/否) | 备注 | 原单号 | 行号 | 物料编码 | 数量 | 单价 | 项目号 | 原单行号"""
    require_role(op, "KEEPER", "ADMIN")
    ws = load_workbook(fileobj, read_only=True, data_only=True).active
    docs: dict[str, dict] = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            continue
        no = str(row[0]).strip()
        d = docs.setdefault(no, {
            "no": no, "doc_type": str(row[1]).strip(),
            "doc_date": (row[2].date().isoformat() if hasattr(row[2], "date") else str(row[2])),
            "posted": str(row[3]).strip() in ("是", "1", "Y", "y", "True", "true"),
            "remark": row[4], "ref_doc_no": row[5], "lines": [],
        })
        d["lines"].append({"line_no": row[6], "material_code": row[7], "qty": row[8], "unit_price": row[9],
                           "project_code": row[10], "ref_line_no": row[11] if len(row) > 11 else None})
    for d in docs.values():
        save_doc(db, op, d, source="IMPORT")
    return len(docs)


def import_master_xlsx(db: Session, op: User, kind: str, fileobj) -> int:
    """项目：编码|名称|客户代码|已结项(是/否)；物料：编码|名称|规格|单位|单价|类别"""
    require_role(op, "KEEPER", "ADMIN")
    ws = load_workbook(fileobj, read_only=True, data_only=True).active
    n = 0
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            continue
        code = str(row[0]).strip()
        if kind == "project":
            p = db.scalar(select(Project).where(Project.code == code)) or Project(code=code)
            p.name, p.customer_code = str(row[1] or code), (str(row[2]) if row[2] else None)
            p.closed = str(row[3] or "").strip() in ("是", "1", "Y", "True")
            db.add(p)
        else:
            m = db.scalar(select(Material).where(Material.code == code)) or Material(code=code)
            m.name, m.spec, m.unit = str(row[1]), (str(row[2]) if row[2] else None), str(row[3] or "个")
            m.unit_price = row[4] if row[4] not in (None, "") else m.unit_price
            m.category = str(row[5]) if len(row) > 5 and row[5] else None
            db.add(m)
        n += 1
    db.flush()
    return n


def valid_doc_type(db: Session, t: str) -> bool:
    return t in dict_codes(db, "DOC_TYPE")
