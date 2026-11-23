"""金蝶云星空 WebAPI 只读客户端，以及 ErpReader 的金蝶实现。

只用三个服务：
- LoginByAppSecret：第三方应用登录，返回会话 cookie（kdservice-sessionid）；
- ExecuteBillQuery：按字段批量查询，每页最多 2000 行；
- View：按编码或内码查一张完整单据 / 档案。

本模块只读，不调用任何保存、提交、审核类服务。连接参数全部来自环境变量（见 config.py），
密钥不得写进代码或仓库。
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime

import httpx

from ..config import settings

log = logging.getLogger("rmlm.k3")

LOGIN_SVC = "Kingdee.BOS.WebApi.ServicesStub.AuthService.LoginByAppSecret.common.kdsvc"
VIEW_SVC = "Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.View.common.kdsvc"
QUERY_SVC = "Kingdee.BOS.WebApi.ServicesStub.DynamicFormService.ExecuteBillQuery.common.kdsvc"
PAGE_MAX = 2000

# 物料档案 BD_MATERIAL 的查询字段（顺序即返回列顺序）
MATERIAL_FIELDS = (
    "FMaterialId",            # 内码
    "FNumber",                # 编码
    "FName",                  # 名称
    "FSpecification",         # 规格型号
    "FBaseUnitId.FName",      # 基本单位
    "FMaterialGroup.FName",   # 物料分组
    "FDocumentStatus",        # 数据状态：C 已审核
    "FForbidStatus",          # 禁用状态：A 否 / B 是
    "FModifyDate",            # 修改日期，增量同步用
)


class K3Error(RuntimeError):
    """金蝶接口调用失败（登录失败、权限不足、返回格式不对等）。"""


def _text(v) -> str:
    return "" if v is None else str(v).strip()


def _error_of(result) -> str | None:
    """ExecuteBillQuery 出错时返回 [[{Result: {ResponseStatus: …}}]] 或 {Result: …}。"""
    if isinstance(result, list):
        if result and isinstance(result[0], list) and result[0] and isinstance(result[0][0], dict):
            return _error_of(result[0][0])
        return _error_of(result[0]) if result and isinstance(result[0], dict) else None
    if isinstance(result, dict):
        status = (result.get("Result") or {}).get("ResponseStatus") or {}
        if status and status.get("IsSuccess") is False:
            msgs = [e.get("Message") for e in (status.get("Errors") or []) if e.get("Message")]
            return "; ".join(msgs) or json.dumps(status, ensure_ascii=False)
    return None


class K3Client:
    def __init__(self, server_url: str | None = None, acct_id: str | None = None, username: str | None = None,
                 app_id: str | None = None, app_secret: str | None = None, lcid: int | None = None,
                 timeout: int | None = None, transport: httpx.BaseTransport | None = None) -> None:
        self.base = (server_url or settings.k3_server_url).rstrip("/") + "/"
        self.acct_id = acct_id or settings.k3_acct_id
        self.username = username or settings.k3_username
        self.app_id = app_id or settings.k3_app_id
        self.app_secret = app_secret or settings.k3_app_secret
        self.lcid = lcid or settings.k3_lcid
        # trust_env=False：内网地址不走代理
        self.http = httpx.Client(timeout=timeout or settings.k3_timeout, trust_env=False, transport=transport)
        self.logged_in = False

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> "K3Client":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _post(self, service: str, *params) -> object:
        payload = {"format": 1, "useragent": "ApiClient", "rid": str(uuid.uuid4()), "parameters": list(params),
                   "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "v": "1.0"}
        try:
            resp = self.http.post(self.base + service, json=payload)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPError as e:
            raise K3Error(f"连接金蝶失败：{e}") from e
        except ValueError as e:
            raise K3Error("金蝶返回的不是 JSON") from e

    def login(self) -> None:
        result = self._post(LOGIN_SVC, self.acct_id, self.username, self.app_id, self.app_secret, self.lcid)
        if not isinstance(result, dict) or result.get("LoginResultType") != 1:
            msg = result.get("Message") if isinstance(result, dict) else None
            raise K3Error(f"金蝶登录失败：{msg or result}")
        sid = result.get("KDSVCSessionId")
        if sid:
            self.http.cookies.set("kdservice-sessionid", sid)
        self.logged_in = True

    def _ensure_login(self) -> None:
        if not self.logged_in:
            self.login()

    def query(self, form_id: str, fields: tuple[str, ...] | list[str], filter_string: str = "",
              order: str = "", page_size: int = PAGE_MAX) -> list[dict]:
        """ExecuteBillQuery 分页拉全量，每行按字段名转成 dict。"""
        self._ensure_login()
        keys = list(fields)
        limit = min(max(page_size, 1), PAGE_MAX)
        rows: list[dict] = []
        start = 0
        while True:
            data = {"FormId": form_id, "FieldKeys": ",".join(keys), "FilterString": filter_string,
                    "OrderString": order, "TopRowCount": 0, "StartRow": start, "Limit": limit}
            page = self._post(QUERY_SVC, json.dumps(data, ensure_ascii=False))
            err = _error_of(page)
            if err:
                raise K3Error(f"金蝶查询 {form_id} 失败：{err}")
            if not isinstance(page, list):
                raise K3Error(f"金蝶查询 {form_id} 返回格式不对：{type(page).__name__}")
            rows.extend({k: (r[i] if i < len(r) else None) for i, k in enumerate(keys)} for r in page)
            if len(page) < limit:
                return rows
            start += limit

    def view(self, form_id: str, number: str = "", bill_id: str = "") -> dict:
        """View 单查：编码与内码二选一，返回 Result.Result。"""
        self._ensure_login()
        data = {"CreateOrgId": 0, "Number": number, "Id": bill_id, "IsSortBySeq": False}
        result = self._post(VIEW_SVC, form_id, json.dumps(data, ensure_ascii=False))
        err = _error_of(result)
        if err:
            raise K3Error(f"金蝶查看 {form_id} {number or bill_id} 失败：{err}")
        return ((result or {}).get("Result") or {}).get("Result") or {}


def material_filter(use_org: str, since: datetime | None) -> str:
    """已审核 + 指定使用组织；增量时加修改日期。禁用的也要取回来，用来把本地物料置为停用。"""
    parts = ["FDocumentStatus='C'"]
    if use_org:
        parts.append(f"FUseOrgId.FNumber='{use_org}'")
    if since is not None:
        parts.append(f"FModifyDate>='{since.strftime('%Y-%m-%d %H:%M:%S')}'")
    return " AND ".join(parts)


def _parse_dt(v) -> datetime | None:
    s = _text(v)
    if not s:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s[:26], fmt)
        except ValueError:
            continue
    return None


def material_row(r: dict) -> dict:
    """把一行查询结果转成中台物料字段。"""
    return {
        "erp_id": int(r["FMaterialId"]) if _text(r.get("FMaterialId")).isdigit() else None,
        "code": _text(r.get("FNumber")),
        "name": _text(r.get("FName")),
        "spec": _text(r.get("FSpecification")) or None,
        "unit": _text(r.get("FBaseUnitId.FName")) or "个",
        "category": _text(r.get("FMaterialGroup.FName")) or None,
        "forbidden": _text(r.get("FForbidStatus")) == "B",
        "modified_at": _parse_dt(r.get("FModifyDate")),
    }


class K3CloudReader:
    """ErpReader 的金蝶云星空实现。目前只接物料档案；项目档案与单据的表单待业务确认。"""

    def __init__(self, client: K3Client | None = None, use_org: str | None = None) -> None:
        self.client = client or K3Client()
        self.use_org = settings.k3_use_org if use_org is None else use_org

    def close(self) -> None:
        self.client.close()

    def fetch_materials(self, since: datetime | None = None) -> list[dict]:
        rows = self.client.query("BD_MATERIAL", MATERIAL_FIELDS, material_filter(self.use_org, since),
                                 "FNumber ASC")
        return [m for m in (material_row(r) for r in rows) if m["code"]]

    def fetch_projects(self) -> list[dict]:
        raise NotImplementedError("项目档案在金蝶中的表单尚未确认，暂用 Excel 导入")

    def fetch_docs(self, since) -> list[dict]:
        raise NotImplementedError("领料 / 退料 / 报废单的表单尚未确认，暂用 Excel 导入")
