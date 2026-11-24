"""仓储系统物料码（蓝图 4.3：条码由仓储系统统一赋码，本系统不复刻一套编码）。

- 赋码清单只读镜像在 wms_code：每 15 分钟按接口增量同步；接口未通时由领料专员 / 仓库导入 Excel；
- 签收时扫实物上的仓储码绑定到领用行（B1 → B2）；拆分时逐张扫仓储预发的新标签（空白码）；
- 扫到清单里没有、已作废或已挂别的行的码，一律拦下（"异常编码拦下"）。

接口约定（字段待仓储确认，确认后只需改 WmsReader.fetch_codes）：
GET {WMS_API_URL}?since=<ISO 时间>，Authorization: Bearer {WMS_API_TOKEN}，返回
[{"code": "...", "material_code": "..." | null, "qty": 45, "unit": "米", "batch": "...", "void": false}, ...]
"""
import logging
from datetime import timedelta

import httpx
from openpyxl import load_workbook
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import clock
from ..config import settings
from ..errors import BizError
from ..models import JobRun, LedgerLine, Material, MaterialCode, User, WmsCode
from ..perms import require_role
from .common import D, add_todo, qs, users_with_role

log = logging.getLogger(__name__)

# 签收不扫码时可选的原因（蓝图 3.1：标签破损、未赋码、批量交接时选单号）
NO_CODE_REASONS = ("标签破损", "未赋码", "批量交接")


class WmsReader:
    def fetch_codes(self, since) -> list[dict]:
        headers = {"Authorization": f"Bearer {settings.wms_api_token}"} if settings.wms_api_token else {}
        params = {"since": since.isoformat(sep=" ")} if since else {}
        r = httpx.get(settings.wms_api_url, params=params, headers=headers, timeout=settings.wms_timeout)
        r.raise_for_status()
        data = r.json()
        if isinstance(data, dict):
            data = data.get("data") or data.get("rows") or []
        return list(data)


def upsert(db: Session, rows: list[dict], source: str) -> dict:
    """按码对齐：有则更新物料、数量、批次、作废标记；没有就新建。已绑定的行关系不动。"""
    stats = {"created": 0, "updated": 0}
    for r in rows:
        code = str(r.get("code") or "").strip()
        if not code:
            continue
        w = db.scalar(select(WmsCode).where(WmsCode.code == code))
        if w is None:
            w = WmsCode(code=code)
            db.add(w)
            stats["created"] += 1
        else:
            stats["updated"] += 1
        mc = r.get("material_code")
        w.material_code = str(mc).strip() if mc not in (None, "") else None
        w.qty = D(r["qty"]) if r.get("qty") not in (None, "") else None
        w.unit = str(r["unit"]) if r.get("unit") else None
        w.batch = str(r["batch"]) if r.get("batch") else None
        w.void = bool(r.get("void"))
        w.source = source
        w.synced_at = clock.now()
    db.flush()
    return stats


def sync(db: Session, reader: WmsReader | None = None) -> dict:
    jr = db.get(JobRun, "wms_codes") or JobRun(name="wms_codes")
    db.add(jr)
    since = jr.last_run_at - timedelta(minutes=10) if jr.last_run_at else None
    started = clock.now()
    stats = upsert(db, (reader or WmsReader()).fetch_codes(since), "API")
    jr.last_run_at = started
    jr.last_result = str(stats)
    return stats


def quarter_sync(db: Session) -> dict | None:
    if not settings.wms_enabled:
        return None
    try:
        with db.begin_nested():
            return sync(db)
    except Exception as e:  # noqa: BLE001  接口失败只记录，不中断巡检
        log.exception("wms sync failed")
        for a in users_with_role(db, "ADMIN"):
            add_todo(db, a.id, "SYNC_FAIL", f"仓储码同步失败：{str(e)[:180]}", "job", 2, dedupe=True)
        return {"error": str(e)[:200]}


def import_xlsx(db: Session, op: User, fileobj) -> dict:
    """列：仓储码 | 物料编码（空白码留空） | 数量 | 单位 | 批次 | 作废(是/否)"""
    require_role(op, "KEEPER", "WAREHOUSE", "ADMIN")
    ws = load_workbook(fileobj, read_only=True, data_only=True).active
    rows = []
    for row in ws.iter_rows(min_row=2, values_only=True):
        if not row or not row[0]:
            continue
        cell = lambda i: row[i] if len(row) > i and row[i] not in (None, "") else None  # noqa: E731
        rows.append({"code": row[0], "material_code": cell(1), "qty": cell(2), "unit": cell(3), "batch": cell(4),
                     "void": str(cell(5) or "").strip() in ("是", "1", "Y", "y", "True", "true")})
    if not rows:
        raise BizError("没有可导入的仓储码")
    return upsert(db, rows, "IMPORT")


# ------------------------------------------------------------------ 绑定校验
def _free(db: Session, code: str) -> WmsCode:
    code = (code or "").strip()
    if not code:
        raise BizError("请扫描仓储码")
    w = db.scalar(select(WmsCode).where(WmsCode.code == code))
    if w is None:
        raise BizError(f"仓储码 {code} 不在仓储赋码清单里，请核对标签或联系仓库")
    if w.void:
        raise BizError(f"仓储码 {code} 已在仓储系统作废")
    if w.line_id is not None:
        raise BizError(f"仓储码 {code} 已绑定其他领用行，一个码只能挂一行")
    return w


def check_for_line(db: Session, code: str, line: LedgerLine, qty=None) -> WmsCode:
    """签收或补绑：带物料的仓储码，物料和数量都要与领用行一致；空白码不能用于签收。"""
    w = _free(db, code)
    m = db.get(Material, line.material_id)
    if not w.material_code:
        raise BizError(f"{w.code} 是仓储预发的空白码，只能用于拆分换码")
    if w.material_code != m.code:
        raise BizError(f"仓储码 {w.code} 对应物料 {w.material_code}，与本行物料 {m.code} 不一致")
    expect = D(qty if qty is not None else line.qty)
    if w.qty is not None and D(w.qty) != expect:
        raise BizError(f"仓储码 {w.code} 数量 {qs(w.qty)}，与本行 {qs(expect)} {m.unit} 不一致")
    return w


def check_for_split(db: Session, code: str, line: LedgerLine) -> WmsCode:
    """拆分：用空白码，或物料相同的码。"""
    w = _free(db, code)
    m = db.get(Material, line.material_id)
    if w.material_code and w.material_code != m.code:
        raise BizError(f"仓储码 {w.code} 对应物料 {w.material_code}，不能贴在 {m.code} 上")
    return w


def bind(db: Session, w: WmsCode, line: LedgerLine, qty, layer: int = 1, parent_code: str | None = None) -> MaterialCode:
    w.line_id, w.bound_at = line.id, clock.now()
    mc = MaterialCode(code=w.code, line_id=line.id, parent_code=parent_code, layer=layer, qty_init=D(qty),
                      source="WMS")
    db.add(mc)
    db.flush()
    return mc


def bound_codes(db: Session, codes: list[str]) -> set[str]:
    """哪些码是仓储码（用于码绑定率和"待绑仓储码"标记）。"""
    if not codes:
        return set()
    return set(db.scalars(select(MaterialCode.code).where(MaterialCode.code.in_(codes), MaterialCode.source == "WMS")))


def status(db: Session) -> dict:
    jr = db.get(JobRun, "wms_codes")
    total = db.scalar(select(func.count()).select_from(WmsCode))
    free = db.scalar(select(func.count()).select_from(WmsCode).where(WmsCode.line_id.is_(None), WmsCode.void.is_(False)))
    blank = db.scalar(select(func.count()).select_from(WmsCode).where(
        WmsCode.line_id.is_(None), WmsCode.void.is_(False), WmsCode.material_code.is_(None)))
    return {"enabled": settings.wms_enabled, "last_run_at": jr.last_run_at.isoformat(sep=" ") if jr and jr.last_run_at else None,
            "last_result": jr.last_result if jr else None, "total": total, "free": free, "blank_free": blank}


def out(db: Session, w: WmsCode) -> dict:
    line = db.get(LedgerLine, w.line_id) if w.line_id else None
    return {"id": w.id, "code": w.code, "material_code": w.material_code, "qty": qs(w.qty) if w.qty is not None else None,
            "unit": w.unit, "batch": w.batch, "void": w.void, "source": w.source,
            "line_id": w.line_id, "line_code": line.code if line else None,
            "bound_at": w.bound_at.isoformat(sep=" ") if w.bound_at else None,
            "state": "VOID" if w.void else ("B1" if w.line_id is None else "BOUND")}
