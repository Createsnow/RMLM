"""仓储系统对接：实物条码由仓储统一赋码，本系统只引用（接口见 docs/仓储接口规范.md）。

两个接口，都由仓储系统实现、本系统调用：
1. 查询条码：签收时核对扫到的码（物料、数量、是否已出库、出库单号）；
2. 拆分申请新码：现场拆分时由仓储发新码，原码在仓储侧置为已拆分。

未配置仓储接口（WMS_BASE_URL、WMS_TOKEN）时，本系统自己发 RM 临时码，签收不需要扫码——仅供开发、测试、演示。
仓储接口出错不会写入半截数据：签收 / 拆分整笔不生效，提示稍后重试。
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from decimal import Decimal
from urllib.parse import quote

import httpx

from ..config import settings
from ..errors import BizError
from .common import D

log = logging.getLogger("rmlm.wms")

OUTBOUND = "OUTBOUND"
STATUS_NAMES = {"IN_STOCK": "在库", "OUTBOUND": "已出库", "SPLIT": "已拆分作废", "SCRAPPED": "已报废", "VOID": "已作废"}


@dataclass
class Barcode:
    barcode: str
    material_code: str
    qty: Decimal
    status: str
    unit: str | None = None
    kind: str | None = None
    out_doc_no: str | None = None
    out_line_no: int | None = None
    parent_barcode: str | None = None

    def as_dict(self) -> dict:
        q = format(self.qty.normalize(), "f")
        return {"barcode": self.barcode, "material_code": self.material_code, "qty": q,
                "status": self.status, "status_name": STATUS_NAMES.get(self.status, self.status), "unit": self.unit,
                "kind": self.kind, "out_doc_no": self.out_doc_no, "out_line_no": self.out_line_no,
                "parent_barcode": self.parent_barcode}


class WmsClient:
    def __init__(self, base: str | None = None, token: str | None = None, timeout: int | None = None,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.base = (base or settings.wms_base_url).rstrip("/")
        self.http = httpx.Client(timeout=timeout or settings.wms_timeout, transport=transport,
                                 headers={"Authorization": f"Bearer {token or settings.wms_token}"})

    def close(self) -> None:
        self.http.close()

    def _call(self, method: str, path: str, body: dict | None = None) -> dict | None:
        """返回 data；条码不存在返回 None；其他业务错误抛 BizError（message 原样给使用人看）。"""
        try:
            resp = self.http.request(method, self.base + path, json=body)
        except httpx.HTTPError as e:
            log.warning("wms %s %s failed: %s", method, path, e)
            raise BizError("仓储系统暂时连不上，请稍后重试")
        if resp.status_code == 404:
            return None
        if resp.status_code >= 500 or resp.status_code in (401, 403):
            log.warning("wms %s %s http %s: %s", method, path, resp.status_code, resp.text[:200])
            raise BizError("仓储系统暂时连不上，请稍后重试" if resp.status_code >= 500
                           else "仓储系统拒绝访问，请管理员检查 WMS_TOKEN")
        try:
            payload = resp.json()
        except ValueError:
            raise BizError("仓储系统返回格式不正确，请联系管理员")
        code = payload.get("code")
        if code == 404:
            return None
        if code not in (0, "0"):
            raise BizError(f"仓储系统：{payload.get('message') or '处理失败'}")
        return payload.get("data") or {}

    def get_barcode(self, barcode: str) -> Barcode | None:
        data = self._call("GET", f"/rmlm/v1/barcodes/{quote(barcode, safe='')}")
        if data is None:
            return None
        return parse_barcode(data)

    def split(self, request_id: str, parent_barcode: str, material_code: str, pieces: list[Decimal],
              operator: str, project_code: str | None) -> list[str]:
        data = self._call("POST", "/rmlm/v1/barcodes/split", {
            "request_id": request_id, "parent_barcode": parent_barcode, "material_code": material_code,
            "pieces": [float(p) for p in pieces], "operator": operator, "project_code": project_code,
        })
        codes = (data or {}).get("barcodes") or []
        if len(codes) != len(pieces):
            raise BizError("仓储系统返回的新码数量与拆分份数不一致，请联系管理员")
        out = []
        for want, c in zip(pieces, codes):
            if D(c.get("qty")) != D(want) or not c.get("barcode"):
                raise BizError("仓储系统返回的新码数量不符，请联系管理员")
            out.append(str(c["barcode"]).strip())
        return out


def parse_barcode(d: dict) -> Barcode:
    try:
        return Barcode(
            barcode=str(d["barcode"]).strip(), material_code=str(d["material_code"]).strip(), qty=D(d["qty"]),
            status=str(d.get("status") or "").upper(), unit=d.get("unit"), kind=d.get("kind"),
            out_doc_no=(str(d["out_doc_no"]).strip() if d.get("out_doc_no") else None),
            out_line_no=int(d["out_line_no"]) if d.get("out_line_no") not in (None, "") else None,
            parent_barcode=d.get("parent_barcode"),
        )
    except (KeyError, TypeError, ValueError):
        raise BizError("仓储系统返回的条码信息不完整，请联系管理员")


def enabled() -> bool:
    return settings.wms_enabled


def make_client() -> WmsClient:
    """返回仓储接口客户端。测试里可替换此函数。"""
    return WmsClient()


def lookup(barcode: str) -> Barcode | None:
    client = make_client()
    try:
        return client.get_barcode(barcode)
    finally:
        client.close()


def request_split(parent_barcode: str, material_code: str, pieces: list[Decimal], operator: str,
                  project_code: str | None) -> list[str]:
    """向仓储申请拆分新码。幂等键由原码 + 份数生成：同一次拆分重试拿到的是同一批码。"""
    sig = hashlib.sha1(",".join(str(D(p)) for p in pieces).encode()).hexdigest()[:8]
    client = make_client()
    try:
        return client.split(f"RMLM-{parent_barcode}-{sig}", parent_barcode, material_code, pieces, operator,
                            project_code)
    finally:
        client.close()
