"""历史基线（蓝图 5.2）。

旧表不做全量迁移：有效明细导入为历史基线，只用于查询与总量核对。
- 缺项目号的行，先按 ERP 出库单号回查回填（ERP 单据上本来带项目号）；补不到的挂"试制-未立项"虚拟项目；
- 去向 / 现状原文按转换规则归一到 D01–D07，位置与持有人分开；命中不了的保留原文、去向留空；
- 基线不生成领用行、不进差异池、不计入任何验收指标。
"""
import secrets
from collections import defaultdict
from datetime import date, datetime
from decimal import Decimal

from openpyxl import load_workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock
from ..errors import BizError
from ..models import ErpDoc, ErpLine, HistoryLine, Material, User
from ..perms import require_role
from .common import D, qs

VIRTUAL_PROJECT = "试制-未立项"
COLUMNS = ["领料单号", "日期", "项目号", "客户代码", "物料编码", "物料名称", "规格", "单位", "领用数量", "领用人",
           "去向 / 现状（原文）", "备注"]

# 转换规则：按顺序匹配，第一条命中的生效。(规则名, 关键词任一命中, 归一去向, 说明)
# 与原型「字典与规则 → 现有台账 → 新系统的关键转换规则」一致。
RULES: list[tuple[str, tuple[str, ...], str | None, str]] = [
    ("报废", ("报废", "尸体"), "D05", "报废退仓库 / 报废退仓 / 尸体已给仓库 → 报废；需补 ERP 报废单号"),
    ("退库", ("退仓", "退回仓", "已退", "仓库"), "D06", "已退仓库 / 已退回仓库 / 已退仓 / 仓库 → 退库"),
    ("转交", ("转交", "交给", "给了", "借给"), "D07", "转交 / 交给某人 → 转交他人"),
    ("位置与持有人分离", ("存放", "手上", "手中", "分析室", "暂存", "柜"), "D04",
     "存放于工程分析室 / 本人手上 / 在本人手中 → WIP 暂存；位置与持有人分开记录"),
    ("可靠性实验", ("实验", "可靠性", "测试"), "D02", "送实验室 / 可靠性测试 → 可靠性实验"),
    ("产线试用", ("产线", "试产", "工位"), "D03", "产线 / 试产 / 工位 → 产线试用"),
    ("项目成品", ("成品", "出货", "样品", "客户", "展会"), "D01", "做成样品 / 出货 / 展会 → 项目成品"),
]


def rules() -> list[dict]:
    out = [{"name": n, "keywords": list(k), "dest": d, "handling": h} for n, k, d, h in RULES]
    out.append({"name": "项目号回填", "keywords": [], "dest": None,
                "handling": f"项目号为空：按 ERP 出库单号回查回填；补不到的挂“{VIRTUAL_PROJECT}”虚拟项目"})
    out.append({"name": "退料单号一号多行", "keywords": [], "dest": None,
                "handling": "保留原文，仅作查询；新系统起按“行 + 实退数量”分摊"})
    return out


def map_dest(raw: str | None) -> tuple[str | None, str | None]:
    text = (raw or "").strip()
    if not text:
        return None, None
    for name, kws, dest, _ in RULES:
        if any(k in text for k in kws):
            return dest, name
    return None, None


def split_location(raw: str | None, user_name: str | None) -> tuple[str | None, str | None]:
    """把"存放于 B3 工程分析室 / 本人手上"拆成 (位置, 持有人)。"""
    text = (raw or "").strip()
    if not text:
        return None, None
    holder = user_name if any(k in text for k in ("本人", "手上", "手中")) else None
    loc = None
    for k in ("存放于", "存放在", "放在", "存放"):
        if k in text:
            loc = text.split(k, 1)[1].strip(" ：:，,。") or None
            break
    if loc is None and any(k in text for k in ("分析室", "柜", "实验室", "仓")):
        loc = text
    return loc, holder


def _cell(row, i):
    return row[i] if len(row) > i and row[i] not in (None, "") else None


def _date(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v).strip()[:10])
    except ValueError:
        return None


def _backfill_project(db: Session, doc_no: str | None, material_code: str | None) -> str | None:
    if not doc_no:
        return None
    doc = db.scalar(select(ErpDoc).where(ErpDoc.no == doc_no))
    if doc is None:
        return None
    mat = db.scalar(select(Material.id).where(Material.code == material_code)) if material_code else None
    lines = sorted(doc.lines, key=lambda x: (x.material_id != mat, x.line_no))
    for ln in lines:
        if ln.project_code:
            return ln.project_code
    return None


def import_xlsx(db: Session, op: User, fileobj) -> dict:
    require_role(op, "KEEPER", "ADMIN")
    ws = load_workbook(fileobj, read_only=True, data_only=True).active
    batch = f"HB{clock.now().strftime('%Y%m%d%H%M')}{secrets.token_hex(2).upper()}"
    stats = {"batch": batch, "count": 0, "mapped": 0, "unmapped": 0, "backfilled": 0, "virtual": 0, "skipped": 0}
    for idx, row in enumerate(ws.iter_rows(min_row=2, values_only=True), start=2):
        if not row or not any(v not in (None, "") for v in row):
            continue
        doc_no = str(_cell(row, 0)).strip() if _cell(row, 0) else None
        mat_code = str(_cell(row, 4)).strip() if _cell(row, 4) else None
        qty = _cell(row, 8)
        if not (doc_no or mat_code) or qty is None:
            stats["skipped"] += 1  # 既没单号也没物料编码、或没有数量的行不算有效明细
            continue
        try:
            q = D(qty)
        except Exception:
            stats["skipped"] += 1
            continue
        user_name = str(_cell(row, 9)).strip() if _cell(row, 9) else None
        dest_raw = str(_cell(row, 10)).strip() if _cell(row, 10) else None
        project = str(_cell(row, 2)).strip() if _cell(row, 2) else None
        source = "SHEET"
        if not project:
            project = _backfill_project(db, doc_no, mat_code)
            source = "ERP" if project else "VIRTUAL"
            project = project or VIRTUAL_PROJECT
            stats["backfilled" if source == "ERP" else "virtual"] += 1
        dest, rule = map_dest(dest_raw)
        loc, holder = split_location(dest_raw, user_name) if dest in ("D04", None) else (None, None)
        if dest == "D07":
            holder = None
        stats["mapped" if dest else "unmapped"] += 1
        db.add(HistoryLine(
            batch=batch, row_no=idx, doc_no=doc_no, doc_date=_date(_cell(row, 1)), project_code=project,
            project_source=source, customer_code=str(_cell(row, 3)) if _cell(row, 3) else None,
            material_code=mat_code, material_name=str(_cell(row, 5)) if _cell(row, 5) else None,
            spec=str(_cell(row, 6)) if _cell(row, 6) else None, unit=str(_cell(row, 7)) if _cell(row, 7) else None,
            qty=q, user_name=user_name, holder_name=holder or (user_name if dest == "D04" else None),
            location_text=loc, dest_raw=dest_raw, dest_code=dest, rule=rule,
            remark=str(_cell(row, 11)) if _cell(row, 11) else None, imported_by=op.id,
        ))
        stats["count"] += 1
    if not stats["count"]:
        raise BizError("没有可导入的有效明细（每行至少要有领料单号或物料编码，以及领用数量）")
    db.flush()
    return stats


def out(h: HistoryLine) -> dict:
    return {"id": h.id, "batch": h.batch, "row_no": h.row_no, "doc_no": h.doc_no,
            "doc_date": h.doc_date.isoformat() if h.doc_date else None, "project_code": h.project_code,
            "project_source": h.project_source, "customer_code": h.customer_code, "material_code": h.material_code,
            "material_name": h.material_name, "spec": h.spec, "unit": h.unit, "qty": qs(h.qty),
            "user_name": h.user_name, "holder_name": h.holder_name, "location_text": h.location_text,
            "dest_raw": h.dest_raw, "dest_code": h.dest_code, "rule": h.rule, "remark": h.remark}


def query(db: Session, q: str = "", project: str = "", dest: str = "", batch: str = "", limit: int = 500) -> dict:
    stmt = select(HistoryLine)
    if batch:
        stmt = stmt.where(HistoryLine.batch == batch)
    if project:
        stmt = stmt.where(HistoryLine.project_code == project)
    if dest == "NONE":
        stmt = stmt.where(HistoryLine.dest_code.is_(None))
    elif dest:
        stmt = stmt.where(HistoryLine.dest_code == dest)
    if q:
        like = f"%{q}%"
        stmt = stmt.where(HistoryLine.doc_no.like(like) | HistoryLine.material_code.like(like)
                          | HistoryLine.material_name.like(like) | HistoryLine.user_name.like(like))
    total = db.scalar(select(func.count()).select_from(stmt.subquery()))
    rows = [out(h) for h in db.scalars(stmt.order_by(HistoryLine.id.desc()).limit(limit))]
    batches = [{"batch": b, "count": n, "imported_at": t.isoformat(sep=" ") if t else None}
               for b, n, t in db.execute(select(HistoryLine.batch, func.count(), func.max(HistoryLine.imported_at))
                                         .group_by(HistoryLine.batch).order_by(func.max(HistoryLine.imported_at).desc()))]
    return {"total": total, "rows": rows, "batches": batches}


def check(db: Session, batch: str = "") -> dict:
    """总量核对：按"单号 + 物料"汇总基线数量，与 ERP 单据镜像的数量比较。"""
    stmt = select(HistoryLine).where(HistoryLine.doc_no.is_not(None))
    if batch:
        stmt = stmt.where(HistoryLine.batch == batch)
    base: dict[tuple[str, str | None], Decimal] = defaultdict(Decimal)
    for h in db.scalars(stmt):
        base[(h.doc_no, h.material_code)] += D(h.qty)
    mats = {m.id: m.code for m in db.scalars(select(Material))}
    rows = []
    for (doc_no, mcode), qty in sorted(base.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")):
        doc = db.scalar(select(ErpDoc).where(ErpDoc.no == doc_no))
        if doc is None:
            rows.append({"doc_no": doc_no, "material_code": mcode, "history_qty": qs(qty), "erp_qty": None,
                         "diff": None, "state": "NO_DOC"})
            continue
        erp_qty = sum((D(ln.qty) for ln in doc.lines if mcode is None or mats.get(ln.material_id) == mcode), D(0))
        diff = qty - erp_qty
        rows.append({"doc_no": doc_no, "material_code": mcode, "history_qty": qs(qty), "erp_qty": qs(erp_qty),
                     "diff": qs(diff), "state": "OK" if diff == 0 else "DIFF"})
    summary = {k: sum(1 for r in rows if r["state"] == k) for k in ("OK", "DIFF", "NO_DOC")}
    return {"rows": rows, "summary": summary}
