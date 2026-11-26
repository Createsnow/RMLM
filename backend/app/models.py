"""数据模型。

原则：
- 事件表 event 只增不改（除"待审"类事件的审批结果字段），结存与状态全部由事件推导；
- 金蝶只读：erp_doc / erp_line 是从金蝶读取（或手工录入、Excel 导入）的单据镜像，中台不回写金蝶。
"""
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Index,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import clock
from .db import Base

Qty = Numeric(14, 3)
Money = Numeric(14, 4)


def _now() -> datetime:
    return clock.now()


# ---------------------------------------------------------------- 账号与组织
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(64))
    emp_no: Mapped[str | None] = mapped_column(String(32))
    title: Mapped[str | None] = mapped_column(String(64))
    manager_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    roles: Mapped[str] = mapped_column(String(255), default="USER")  # 逗号分隔
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    frozen: Mapped[bool] = mapped_column(Boolean, default=False)
    password_hash: Mapped[str] = mapped_column(String(128))
    must_change_pw: Mapped[bool] = mapped_column(Boolean, default=False)  # 已不使用：业务确认不强制改密码（保留列，兼容已有数据）
    wecom_userid: Mapped[str | None] = mapped_column(String(64))  # 企业微信 UserID，用于免密登录、通讯录同步、消息推送
    # failed_logins / locked_until：原密码输错锁定用，业务确认（2026-11-26）取消锁定后不再使用，保留列兼容已有数据
    failed_logins: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    locked_until: Mapped[datetime | None] = mapped_column(DateTime)
    # 改密码 / 重置密码时 token_version + 1，旧登录凭证随即失效
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    manager: Mapped["User | None"] = relationship(remote_side=[id])

    __table_args__ = (Index("ux_users_wecom_userid", "wecom_userid", unique=True),)

    @property
    def role_set(self) -> set[str]:
        return {r for r in (self.roles or "").split(",") if r}


class Freeze(Base):
    """冻结记录：超期连续 2 周无动作 → 暂停新领用，由直属上级解冻。"""
    __tablename__ = "freeze"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    line_id: Mapped[int | None] = mapped_column(ForeignKey("ledger_line.id"))
    reason: Mapped[str] = mapped_column(String(255))
    frozen_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    released_at: Mapped[datetime | None] = mapped_column(DateTime)
    released_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))


class Delegate(Base):
    """代理审核：代理人限被代理人的同级或上级。"""
    __tablename__ = "delegate"
    id: Mapped[int] = mapped_column(primary_key=True)
    principal_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    agent_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    start_at: Mapped[datetime] = mapped_column(DateTime)
    end_at: Mapped[datetime] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ---------------------------------------------------------------- 主数据
class Project(Base):
    __tablename__ = "project"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(255))
    customer_code: Mapped[str | None] = mapped_column(String(32))
    closed: Mapped[bool] = mapped_column(Boolean, default=False)  # ERP 结项状态
    source: Mapped[str] = mapped_column(String(16), default="ERP")  # ERP / LOCAL
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Material(Base):
    __tablename__ = "material"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    spec: Mapped[str | None] = mapped_column(String(255))
    unit: Mapped[str] = mapped_column(String(16))
    unit_price: Mapped[Decimal | None] = mapped_column(Money)  # 从 ERP 同步
    category: Mapped[str | None] = mapped_column(String(64))  # 金蝶物料分组
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")  # 金蝶已禁用的物料置 False：保留历史引用，不能再申请
    erp_id: Mapped[int | None] = mapped_column(Integer)  # 金蝶物料内码 FMaterialId
    synced_at: Mapped[datetime | None] = mapped_column(DateTime)  # 最近一次从金蝶同步的时间
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class Location(Base):
    """库位 / 工位 / 报废箱。"""
    __tablename__ = "location"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(16), default="STOCK")  # STOCK / STATION / SCRAP_BOX
    active: Mapped[bool] = mapped_column(Boolean, default=True)


class DictItem(Base):
    __tablename__ = "dict_item"
    __table_args__ = (UniqueConstraint("dict_type", "code"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    dict_type: Mapped[str] = mapped_column(String(32))
    code: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(64))
    meaning: Mapped[str | None] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)


class DictChange(Base):
    """字典变更：系统管理员提出，研发副总批准后生效。只能新增、停用、改名，不能删除。"""
    __tablename__ = "dict_change"
    id: Mapped[int] = mapped_column(primary_key=True)
    dict_type: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(16))  # ADD / DISABLE / RENAME
    code: Mapped[str] = mapped_column(String(16))
    name: Mapped[str | None] = mapped_column(String(64))
    meaning: Mapped[str | None] = mapped_column(String(255))
    reason: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(16), default="PENDING")
    requested_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)


class Sequence(Base):
    __tablename__ = "seq"
    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    value: Mapped[int] = mapped_column(Integer, default=0)


# ---------------------------------------------------------------- 申请与审核
class Requisition(Base):
    __tablename__ = "requisition"
    id: Mapped[int] = mapped_column(primary_key=True)
    no: Mapped[str] = mapped_column(String(32), unique=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))  # 申请人 = 使用人
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))  # ≠ user_id 时为代申请（代登留痕）
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"))
    customer_code: Mapped[str | None] = mapped_column(String(32))
    reason_code: Mapped[str] = mapped_column(String(8))
    remark: Mapped[str] = mapped_column(Text)
    expected_return: Mapped[date] = mapped_column(Date)
    long_cycle: Mapped[bool] = mapped_column(Boolean, default=False)
    long_cycle_months: Mapped[int | None] = mapped_column(Integer)
    urgent: Mapped[bool] = mapped_column(Boolean, default=False)
    urgent_note: Mapped[str | None] = mapped_column(String(255))  # 口头同意说明
    status: Mapped[str] = mapped_column(String(16), default="PENDING")  # PENDING/APPROVED/REJECTED/CANCELLED
    approver_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    escalated_to_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    escalation: Mapped[int] = mapped_column(Integer, default=0)  # 0 无 / 1 已提醒 / 2 已升级
    decided_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    decided_as: Mapped[str | None] = mapped_column(String(16))  # SELF / DELEGATE / ESCALATED
    decided_at: Mapped[datetime | None] = mapped_column(DateTime)
    decision_note: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    items: Mapped[list["ReqItem"]] = relationship(back_populates="req", cascade="all, delete-orphan")


class ReqItem(Base):
    __tablename__ = "req_item"
    id: Mapped[int] = mapped_column(primary_key=True)
    req_id: Mapped[int] = mapped_column(ForeignKey("requisition.id"))
    material_id: Mapped[int] = mapped_column(ForeignKey("material.id"))
    qty: Mapped[Decimal] = mapped_column(Qty)
    req: Mapped[Requisition] = relationship(back_populates="items")


# ---------------------------------------------------------------- 金蝶单据镜像（只读）
class ErpDoc(Base):
    __tablename__ = "erp_doc"
    id: Mapped[int] = mapped_column(primary_key=True)
    no: Mapped[str] = mapped_column(String(64), unique=True)
    doc_type: Mapped[str] = mapped_column(String(8))  # LL 领料 / AB A转B / TL 退料 / BF 报废 / TZ 调整
    doc_date: Mapped[date] = mapped_column(Date)
    posted: Mapped[bool] = mapped_column(Boolean, default=False)
    remark: Mapped[str | None] = mapped_column(String(255))
    ref_doc_no: Mapped[str | None] = mapped_column(String(64))  # 调整单所调整的原单
    finance_approved: Mapped[bool] = mapped_column(Boolean, default=False)
    finance_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    received: Mapped[bool] = mapped_column(Boolean, default=False)  # 退料/报废：仓库确认实物
    received_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    received_at: Mapped[datetime | None] = mapped_column(DateTime)
    source: Mapped[str] = mapped_column(String(16), default="MANUAL")  # MANUAL / IMPORT / API
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    lines: Mapped[list["ErpLine"]] = relationship(back_populates="doc", cascade="all, delete-orphan")


class ErpLine(Base):
    __tablename__ = "erp_line"
    __table_args__ = (UniqueConstraint("doc_id", "line_no"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    doc_id: Mapped[int] = mapped_column(ForeignKey("erp_doc.id"))
    line_no: Mapped[int] = mapped_column(Integer)
    material_id: Mapped[int] = mapped_column(ForeignKey("material.id"))
    qty: Mapped[Decimal] = mapped_column(Qty)  # 调整单可为负
    project_code: Mapped[str | None] = mapped_column(String(64))
    unit_price: Mapped[Decimal | None] = mapped_column(Money)
    ref_line_no: Mapped[int | None] = mapped_column(Integer)  # 调整单所调整的原单行
    doc: Mapped[ErpDoc] = relationship(back_populates="lines")


# ---------------------------------------------------------------- 领用台账
class LedgerLine(Base):
    __tablename__ = "ledger_line"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)  # 当前有效物料码（签收前为系统内部编号）
    root_id: Mapped[int | None] = mapped_column(Integer)  # 根行（父行）id，根行指向自己
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("ledger_line.id"))
    level: Mapped[int] = mapped_column(Integer, default=1)  # 1 父行 / 2 子行
    requisition_id: Mapped[int] = mapped_column(ForeignKey("requisition.id"))
    req_item_id: Mapped[int | None] = mapped_column(ForeignKey("req_item.id"))
    # 一张领料单行出了几个仓储条码，签收时就分成几条领用行（一个码只挂一行），所以这里不唯一
    erp_line_id: Mapped[int | None] = mapped_column(ForeignKey("erp_line.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("project.id"))
    material_id: Mapped[int] = mapped_column(ForeignKey("material.id"))
    qty: Mapped[Decimal] = mapped_column(Qty)  # 本行初始数量
    unit_price: Mapped[Decimal | None] = mapped_column(Money)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))  # 领用人
    holder_id: Mapped[int] = mapped_column(ForeignKey("users.id"))  # 当前持有人
    location_id: Mapped[int | None] = mapped_column(ForeignKey("location.id"))
    # 外出（D08 委外加工 / D09 展会·出差）时的临时位置，自定义文本；回到固定库位后清空
    location_text: Mapped[str | None] = mapped_column(String(128))
    due_date: Mapped[date] = mapped_column(Date)
    long_cycle: Mapped[bool] = mapped_column(Boolean, default=False)
    long_cycle_lapsed: Mapped[bool] = mapped_column(Boolean, default=False)  # 季度未确认 → 按标准类
    long_cycle_max: Mapped[date | None] = mapped_column(Date)  # 当前许可的最长期限
    signed: Mapped[bool] = mapped_column(Boolean, default=False)
    signed_at: Mapped[datetime | None] = mapped_column(DateTime)
    urgent: Mapped[bool] = mapped_column(Boolean, default=False)
    urgent_deadline: Mapped[datetime | None] = mapped_column(DateTime)
    urgent_resolved: Mapped[bool] = mapped_column(Boolean, default=True)
    urgent_overdue: Mapped[bool] = mapped_column(Boolean, default=False)
    locked: Mapped[bool] = mapped_column(Boolean, default=False)
    lock_reason: Mapped[str | None] = mapped_column(String(255))
    self_issue: Mapped[bool] = mapped_column(Boolean, default=False)  # 自领自开
    issued_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    split_event_id: Mapped[int | None] = mapped_column(Integer)  # 子行：由哪条拆分事件产生
    voided: Mapped[bool] = mapped_column(Boolean, default=False)  # 拆分被冲销后子行作废
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    events: Mapped[list["Event"]] = relationship(back_populates="line", order_by="Event.id")


class MaterialCode(Base):
    """物料码。正式环境由仓储系统赋码（签收时扫码绑定、拆分时向仓储申请新码）；
    未配置仓储接口时由本系统发 RM 开头的临时码。"""
    __tablename__ = "material_code"
    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(64), unique=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("ledger_line.id"))
    parent_code: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(8), default="RM", server_default="RM")  # WMS 仓储条码 / RM 本系统临时码
    layer: Mapped[int] = mapped_column(Integer, default=1)  # 最多 3 层
    qty_init: Mapped[Decimal] = mapped_column(Qty)
    replaced: Mapped[bool] = mapped_column(Boolean, default=False)  # B4 已拆分替换
    replaced_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class Event(Base):
    __tablename__ = "event"
    id: Mapped[int] = mapped_column(primary_key=True)
    line_id: Mapped[int] = mapped_column(ForeignKey("ledger_line.id"), index=True)
    code: Mapped[str | None] = mapped_column(String(64))
    etype: Mapped[str] = mapped_column(String(16))
    qty: Mapped[Decimal] = mapped_column(Qty, default=0)
    dest: Mapped[str | None] = mapped_column(String(8))
    is_loss: Mapped[bool] = mapped_column(Boolean, default=False)
    loss_reason: Mapped[str | None] = mapped_column(String(8))
    scrap_mode: Mapped[str | None] = mapped_column(String(8))  # RESIDUE / WHOLE
    scrap_reason: Mapped[str | None] = mapped_column(String(32))
    box: Mapped[str | None] = mapped_column(String(32))
    location_id: Mapped[int | None] = mapped_column(ForeignKey("location.id"))
    to_user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reason: Mapped[str | None] = mapped_column(String(255))
    note: Mapped[str | None] = mapped_column(Text)
    operator_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    holder_id: Mapped[int] = mapped_column(ForeignKey("users.id"))  # 发生时的持有人
    is_proxy: Mapped[bool] = mapped_column(Boolean, default=False)
    photos: Mapped[list | None] = mapped_column(JSON)
    reverses_id: Mapped[int | None] = mapped_column(ForeignKey("event.id"))
    status: Mapped[str] = mapped_column(String(16), default="EFFECTIVE")  # EFFECTIVE / PENDING / REJECTED
    reviewed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime)
    extra: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)

    line: Mapped[LedgerLine] = relationship(back_populates="events")


class DocAlloc(Base):
    """退料单 / 报废单行 ↔ 中台事件 的分摊（多对多）。"""
    __tablename__ = "doc_alloc"
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(8))  # RET / SCRAP
    erp_line_id: Mapped[int] = mapped_column(ForeignKey("erp_line.id"), index=True)
    event_id: Mapped[int] = mapped_column(ForeignKey("event.id"), index=True)
    qty: Mapped[Decimal] = mapped_column(Qty)
    cross_doc: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ScrapBatch(Base):
    __tablename__ = "scrap_batch"
    id: Mapped[int] = mapped_column(primary_key=True)
    no: Mapped[str] = mapped_column(String(32), unique=True)
    box: Mapped[str] = mapped_column(String(32))
    week: Mapped[str] = mapped_column(String(10))
    status: Mapped[str] = mapped_column(String(16), default="OPEN")  # OPEN / BOUND
    erp_doc_id: Mapped[int | None] = mapped_column(ForeignKey("erp_doc.id"))
    event_ids: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


# ---------------------------------------------------------------- 对账与关账
class ReconDiff(Base):
    __tablename__ = "recon_diff"
    id: Mapped[int] = mapped_column(primary_key=True)
    key: Mapped[str] = mapped_column(String(64), unique=True)
    period: Mapped[str] = mapped_column(String(7), index=True)
    kind: Mapped[str] = mapped_column(String(16))
    erp_doc_no: Mapped[str | None] = mapped_column(String(64))
    erp_line_no: Mapped[int | None] = mapped_column(Integer)
    line_id: Mapped[int | None] = mapped_column(Integer)
    event_id: Mapped[int | None] = mapped_column(Integer)
    material_id: Mapped[int | None] = mapped_column(Integer)
    erp_qty: Mapped[Decimal] = mapped_column(Qty, default=0)
    detail_qty: Mapped[Decimal] = mapped_column(Qty, default=0)
    diff: Mapped[Decimal] = mapped_column(Qty, default=0)
    owner_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    claimed_at: Mapped[datetime | None] = mapped_column(DateTime)
    reason_code: Mapped[str | None] = mapped_column(String(8))
    method: Mapped[str | None] = mapped_column(String(16))  # REVERSAL / ADJUST
    method_ref: Mapped[str | None] = mapped_column(String(64))
    note: Mapped[str | None] = mapped_column(String(255))
    state: Mapped[str] = mapped_column(String(16), default="OPEN")  # OPEN / CLAIMED / RESOLVED
    escalation: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime)


class PeriodClose(Base):
    __tablename__ = "period_close"
    period: Mapped[str] = mapped_column(String(7), primary_key=True)
    closed: Mapped[bool] = mapped_column(Boolean, default=False)
    closed_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    closed_at: Mapped[datetime | None] = mapped_column(DateTime)


class OverdueSnapshot(Base):
    """季初超期挂账快照，用于"超期挂账清理率"。"""
    __tablename__ = "overdue_snapshot"
    id: Mapped[int] = mapped_column(primary_key=True)
    quarter: Mapped[str] = mapped_column(String(7), index=True)
    line_id: Mapped[int] = mapped_column(Integer)


# ---------------------------------------------------------------- 待办、照片、审计
class Todo(Base):
    __tablename__ = "todo"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    kind: Mapped[str] = mapped_column(String(24))
    title: Mapped[str] = mapped_column(String(255))
    ref_type: Mapped[str | None] = mapped_column(String(24))
    ref_id: Mapped[int | None] = mapped_column(Integer)
    level: Mapped[int] = mapped_column(Integer, default=1)  # 1 责任人 / 2 直属上级 / 3 月度经营会
    is_notice: Mapped[bool] = mapped_column(Boolean, default=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime)
    state: Mapped[str] = mapped_column(String(16), default="OPEN")  # OPEN / DONE / CANCELLED
    parent_id: Mapped[int | None] = mapped_column(Integer)
    escalated: Mapped[int] = mapped_column(Integer, default=0)
    # 企业微信推送：None 不推送（未启用或本人没有企微账号）/ PENDING 待发 / SENT 已发 / FAIL 多次失败放弃
    push_state: Mapped[str | None] = mapped_column(String(8), index=True)
    push_tries: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
    done_at: Mapped[datetime | None] = mapped_column(DateTime)


class Photo(Base):
    __tablename__ = "photo"
    id: Mapped[int] = mapped_column(primary_key=True)
    path: Mapped[str] = mapped_column(String(255))
    uploader_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    size: Mapped[int] = mapped_column(Integer)
    sha1: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(Integer)
    method: Mapped[str] = mapped_column(String(8))
    path: Mapped[str] = mapped_column(String(255))
    status: Mapped[int] = mapped_column(Integer)
    body: Mapped[str | None] = mapped_column(Text)
    ip: Mapped[str | None] = mapped_column(String(64))
    at: Mapped[datetime] = mapped_column(DateTime, default=_now, index=True)


class JobRun(Base):
    __tablename__ = "job_run"
    name: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime)
    last_key: Mapped[str | None] = mapped_column(String(32))  # 已执行过的周期标识（周/季/月），或同步任务的增量水位
    last_result: Mapped[str | None] = mapped_column(Text)  # 最近一次执行结果（同步任务用，JSON 文本）


class SysSetting(Base):
    """网页上可改的系统设置（键值）。目前只有金蝶领料单自动同步的开关与间隔；新表，启动时自动建，不改已有表。"""
    __tablename__ = "sys_setting"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_by: Mapped[int | None] = mapped_column(Integer)
    updated_at: Mapped[datetime | None] = mapped_column(DateTime, default=_now, onupdate=_now)


# ---------------------------------------------------------------- 历史基线与双轨抽查（v1.1）
class HistoryLine(Base):
    """旧台账导入的历史基线（只读）。只用于查询与总量核对：不生成领用行、不参与对账与验收指标。"""
    __tablename__ = "history_line"
    id: Mapped[int] = mapped_column(primary_key=True)
    batch: Mapped[str] = mapped_column(String(32), index=True)  # 导入批次号
    row_no: Mapped[int] = mapped_column(Integer)  # Excel 行号
    doc_no: Mapped[str | None] = mapped_column(String(64), index=True)  # 金蝶出库单号
    doc_date: Mapped[date | None] = mapped_column(Date)
    project_code: Mapped[str] = mapped_column(String(64))
    project_source: Mapped[str] = mapped_column(String(16))  # SHEET 原表 / ERP 出库单回填 / VIRTUAL 试制-未立项
    customer_code: Mapped[str | None] = mapped_column(String(32))
    material_code: Mapped[str | None] = mapped_column(String(64))
    material_name: Mapped[str | None] = mapped_column(String(128))
    spec: Mapped[str | None] = mapped_column(String(255))
    unit: Mapped[str | None] = mapped_column(String(16))
    qty: Mapped[Decimal] = mapped_column(Qty, default=0)
    user_name: Mapped[str | None] = mapped_column(String(64))
    holder_name: Mapped[str | None] = mapped_column(String(64))
    location_text: Mapped[str | None] = mapped_column(String(128))
    dest_raw: Mapped[str | None] = mapped_column(String(255))  # 原表"去向 / 现状"原文
    dest_code: Mapped[str | None] = mapped_column(String(8))  # 按转换规则归一后的去向，未命中为空
    rule: Mapped[str | None] = mapped_column(String(64))  # 命中的转换规则
    remark: Mapped[str | None] = mapped_column(Text)
    imported_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    imported_at: Mapped[datetime] = mapped_column(DateTime, default=_now)


class ParallelCheck(Base):
    """双轨并行期抽查：每周抽 20 单，与旧表逐单比对。"""
    __tablename__ = "parallel_check"
    __table_args__ = (UniqueConstraint("week", "line_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    week: Mapped[str] = mapped_column(String(8), index=True)  # 2026-W45
    line_id: Mapped[int] = mapped_column(ForeignKey("ledger_line.id"))
    result: Mapped[str | None] = mapped_column(String(8))  # MATCH 一致 / DIFF 不一致；空为未比对
    note: Mapped[str | None] = mapped_column(String(255))
    checked_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now)
