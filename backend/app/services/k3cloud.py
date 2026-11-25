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
import time
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


class K3NetworkError(K3Error):
    """没连上金蝶或金蝶没正常应答（超时、断连、5xx）：分页查询会按配置重试。"""


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
                 timeout: int | None = None, transport: httpx.BaseTransport | None = None,
                 page_retries: int | None = None, page_retry_seconds: float | None = None) -> None:
        self.base = (server_url or settings.k3_server_url).rstrip("/") + "/"
        self.acct_id = acct_id or settings.k3_acct_id
        self.username = username or settings.k3_username
        self.app_id = app_id or settings.k3_app_id
        self.app_secret = app_secret or settings.k3_app_secret
        self.lcid = lcid or settings.k3_lcid
        # 超时（秒）：传了 timeout 就三类请求统一用它，否则按 K3_LOGIN_TIMEOUT / K3_QUERY_TIMEOUT / K3_VIEW_TIMEOUT
        self.login_timeout = timeout or settings.k3_login_timeout
        self.query_timeout = timeout or settings.k3_query_timeout
        self.view_timeout = timeout or settings.k3_view_timeout
        self.page_retries = settings.k3_page_retries if page_retries is None else page_retries
        self.page_retry_seconds = settings.k3_page_retry_seconds if page_retry_seconds is None else page_retry_seconds
        # trust_env=False：内网地址不走代理
        self.http = httpx.Client(timeout=self.query_timeout, trust_env=False, transport=transport)
        self.logged_in = False

    def close(self) -> None:
        self.http.close()

    def __enter__(self) -> "K3Client":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _post(self, service: str, *params, timeout: float | None = None) -> object:
        payload = {"format": 1, "useragent": "ApiClient", "rid": str(uuid.uuid4()), "parameters": list(params),
                   "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"), "v": "1.0"}
        try:
            resp = self.http.post(self.base + service, json=payload, timeout=timeout or self.query_timeout)
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code >= 500:
                raise K3NetworkError(f"金蝶服务异常：{e}") from e
            raise K3Error(f"金蝶拒绝请求：{e}") from e
        except httpx.HTTPError as e:
            raise K3NetworkError(f"连接金蝶失败：{e}") from e
        except ValueError as e:
            raise K3Error("金蝶返回的不是 JSON") from e

    def login(self) -> None:
        result = self._post(LOGIN_SVC, self.acct_id, self.username, self.app_id, self.app_secret, self.lcid,
                            timeout=self.login_timeout)
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
            page = self._query_page(json.dumps(data, ensure_ascii=False), form_id, start)
            err = _error_of(page)
            if err:
                raise K3Error(f"金蝶查询 {form_id} 失败：{err}")
            if not isinstance(page, list):
                raise K3Error(f"金蝶查询 {form_id} 返回格式不对：{type(page).__name__}")
            rows.extend({k: (r[i] if i < len(r) else None) for i, k in enumerate(keys)} for r in page)
            if len(page) < limit:
                return rows
            start += limit

    def _query_page(self, data: str, form_id: str, start: int) -> object:
        """查一页；网络类失败按 K3_PAGE_RETRIES / K3_PAGE_RETRY_SECONDS 重试，业务错误（权限等）不重试。"""
        for attempt in range(self.page_retries + 1):
            try:
                return self._post(QUERY_SVC, data, timeout=self.query_timeout)
            except K3NetworkError as e:
                if attempt >= self.page_retries:
                    raise
                log.warning("金蝶查询 %s 第 %d 行起失败，%s 秒后第 %d 次重试：%s",
                            form_id, start, self.page_retry_seconds, attempt + 1, e)
                time.sleep(self.page_retry_seconds)
        raise AssertionError("unreachable")

    def view(self, form_id: str, number: str = "", bill_id: str = "") -> dict:
        """View 单查：编码与内码二选一，返回 Result.Result。"""
        self._ensure_login()
        data = {"CreateOrgId": 0, "Number": number, "Id": bill_id, "IsSortBySeq": False}
        result = self._post(VIEW_SVC, form_id, json.dumps(data, ensure_ascii=False), timeout=self.view_timeout)
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


# ------------------------------------------------------------------ 单据
# 研发领料 = 其他出库单 STK_MisDelivery 中单据类型为"研发测试其他出库单"（K3_LL_BILL_TYPES）的单据，定时批量拉；
# 退料 / 报废单不批量拉（全公司的其他入库、其他出库单太多，也不必区分类型）：领料专员在中台分摊时输入金蝶单号，
# 按单号取这一张（fetch_doc）。A 转 B 单、调整单一期不接，继续手工录入 / Excel 导入。
MIS_OUT = "STK_MisDelivery"  # 其他出库单，单号 QTCK 开头
MIS_IN = "STK_MISCELLANEOUS"  # 其他入库单，单号 QTRK 开头
DOC_FIELDS = {
    # 顺序即返回列顺序；两张表单的元数据字段名大小写不同，金蝶查询不区分大小写，这里按各自文档写
    MIS_OUT: ("FBillNo", "FDate", "FDocumentStatus", "FCancelStatus", "FNote", "F_ora_xmbm", "FModifyDate",
              "FBillTypeID.FName", "FEntity_FEntryId", "FEntity_FSeq", "FMaterialId.FNumber", "FBaseQty", "FPrice",
              "FProjectNo"),
    MIS_IN: ("FBillNo", "FDate", "FDocumentStatus", "FCANCELSTATUS", "FNOTE", "FModifyDate", "FBillTypeID.FName",
             "FEntity_FEntryId", "FEntity_FSeq", "FMATERIALID.FNumber", "FBaseQty", "FPrice", "FProjectNo"),
}
# 已审核 C 算过账；重新审核 D（反审核后在改）也要拉回来，用来把中台镜像置为未过账
DOC_STATUSES = ("C", "D")


def _quote(v: str) -> str:
    return "'" + v.replace("'", "''") + "'"


def doc_filter(org: str, bill_types: list[str], since: datetime | None, bill_no: str = "") -> str:
    parts = ["FDocumentStatus IN ('C','D')"]
    if org:
        parts.append(f"FStockOrgId.FNumber={_quote(org)}")
    if bill_types:
        names = ",".join(_quote(t) for t in bill_types)
        parts.append(f"(FBillTypeID.FName IN ({names}) OR FBillTypeID.FNumber IN ({names}))")
    if since is not None:
        parts.append(f"FModifyDate>='{since.strftime('%Y-%m-%d %H:%M:%S')}'")
    if bill_no:
        parts.append(f"FBillNo={_quote(bill_no)}")
    return " AND ".join(parts)


def _num(v):
    s = _text(v)
    try:
        return float(s) if s else 0.0
    except ValueError:
        return 0.0


def merge_doc_rows(rows: list[dict], doc_type: str) -> list[dict]:
    """全查是一行一条明细：所有页取完后按单据号合并成单（ErpReader.fetch_docs 的格式）。
    数量取基本单位数量（中台物料单位就是金蝶基本单位）；成本价为 0 视为没有单价；项目号先取行上的项目编号，
    没有再取表头项目编码；作废的单据视为未过账。"""
    docs: dict[str, dict] = {}
    for r in rows:
        no = _text(r.get("FBillNo"))
        if not no:
            continue
        status = _text(r.get("FDocumentStatus"))
        cancelled = _text(r.get("FCancelStatus") or r.get("FCANCELSTATUS")) == "B"
        d = docs.get(no)
        if d is None:
            dt = _parse_dt(r.get("FDate"))
            d = docs[no] = {
                "no": no, "doc_type": doc_type, "doc_date": dt.date() if dt else None,
                "posted": status == "C" and not cancelled,
                "remark": _text(r.get("FNote") or r.get("FNOTE")) or None, "ref_doc_no": None,
                "modified_at": _parse_dt(r.get("FModifyDate")), "bill_type": _text(r.get("FBillTypeID.FName")),
                "lines": [],
            }
        seq = _text(r.get("FEntity_FSeq"))
        if not seq.isdigit() or int(seq) <= 0:
            continue  # 没有明细的单据：全查返回的明细列为空
        price = _num(r.get("FPrice"))
        project = _text(r.get("FProjectNo")) or _text(r.get("F_ora_xmbm")) or None
        d["lines"].append({
            "line_no": int(seq),
            "material_code": _text(r.get("FMaterialId.FNumber") or r.get("FMATERIALID.FNumber")),
            # 退货方向的其他出库单数量可能为负，中台单据行只记绝对值
            "qty": abs(_num(r.get("FBaseQty"))), "unit_price": price if price > 0 else None,
            "project_code": project, "ref_line_no": None,
        })
    return list(docs.values())


class K3CloudReader:
    """ErpReader 的金蝶云星空实现：物料档案、研发领料单（批量）、退料 / 报废单（按单号）。项目档案的表单待确认。"""

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

    def fetch_docs(self, since: datetime | None = None) -> list[dict]:
        """研发领料：其他出库单里单据类型为研发领料的（已审核 / 重新审核），按修改日期增量。"""
        if not settings.k3_ll_bill_types:
            raise NotImplementedError("未配置研发领料的单据类型（K3_LL_BILL_TYPES），不能自动拉取领料单")
        rows = self.client.query(MIS_OUT, DOC_FIELDS[MIS_OUT],
                                 doc_filter(self.use_org, settings.k3_ll_bill_types, since),
                                 "FBillNo ASC, FEntity_FEntryId ASC")
        return merge_doc_rows(rows, "LL")

    def fetch_doc(self, bill_no: str, doc_type: str) -> dict | None:
        """按单号取一张退料（TL）/ 报废（BF）单，不限单据类型：QTRK 开头查其他入库单、QTCK 开头查其他出库单，
        都不是就两张表单各查一次。没找到返回 None。"""
        no = bill_no.strip()
        forms = ([MIS_IN] if no.upper().startswith("QTRK") else [MIS_OUT] if no.upper().startswith("QTCK")
                 else [MIS_IN, MIS_OUT])
        for form in forms:
            rows = self.client.query(form, DOC_FIELDS[form], doc_filter(self.use_org, [], None, no),
                                     "FEntity_FEntryId ASC")
            docs = merge_doc_rows(rows, doc_type)
            if docs:
                return docs[0]
        return None
