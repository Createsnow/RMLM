"""运行配置：全部从环境变量读取，开发环境有默认值。"""
import os
from pathlib import Path

from sqlalchemy.engine import URL


def _env(name: str, default: str = "") -> str:
    """读取环境变量；未设置或为空时取默认值。值原样返回（密码可含 # = @ 等字符）。"""
    v = os.getenv(name)
    return default if v is None or v == "" else v


def _int(name: str, default: int) -> int:
    v = _env(name).strip()
    return int(v) if v else default


def _database_url() -> str:
    """优先用完整连接串 RMLM_DATABASE_URL（兼容旧配置 / 测试）；否则由 DB_HOST 等分项拼出。"""
    full = _env("RMLM_DATABASE_URL")
    if full:
        return full
    url = URL.create(
        "mysql+pymysql",
        username=_env("DB_USER", "rmlm"),
        password=_env("DB_PASSWORD", "rmlm_dev"),  # URL.create 会处理密码里的特殊字符
        host=_env("DB_HOST", "127.0.0.1"),
        port=_int("DB_PORT", 3306),
        database=_env("DB_NAME", "rmlm"),
        query={"charset": "utf8mb4"},
    )
    return url.render_as_string(hide_password=False)


class Settings:
    def __init__(self) -> None:
        # ---------- 数据库（MySQL 8）
        self.database_url: str = _database_url()

        # ---------- 安全与登录
        self.jwt_secret: str = _env("RMLM_JWT_SECRET", "dev-secret-change-me")
        self.jwt_hours: int = _int("RMLM_JWT_HOURS", 12)

        # ---------- 照片 / 静态文件
        self.photo_dir: Path = Path(_env("RMLM_PHOTO_DIR", "./data/photos")).resolve()
        self.photo_max_kb: int = _int("RMLM_PHOTO_MAX_KB", 300)
        self.watermark_font: str = _env("RMLM_WATERMARK_FONT", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc")
        self.static_dir: Path = Path(
            _env("RMLM_STATIC_DIR", str(Path(__file__).resolve().parents[2] / "frontend" / "dist"))
        )
        # ---------- 定时任务 / 管理员
        self.enable_scheduler: bool = _env("RMLM_SCHEDULER", "1") == "1"
        self.admin_username: str = _env("RMLM_ADMIN_USER", "admin")
        self.admin_password: str = _env("RMLM_ADMIN_PASSWORD", "admin123")
        # 对外访问地址（企微消息里的链接、网页授权回调都用它），例如 https://rmlm.example.com
        self.public_url: str = _env("RMLM_PUBLIC_URL").strip().rstrip("/")

        # ---------- 金蝶云星空 WebAPI（只读）。密钥只放 .env，不要写进代码或仓库。
        self.k3_server_url: str = _env("K3_SERVER_URL").strip()
        self.k3_acct_id: str = _env("K3_ACCT_ID").strip()
        self.k3_username: str = _env("K3_USERNAME").strip()
        self.k3_app_id: str = _env("K3_APP_ID").strip()
        self.k3_app_secret: str = _env("K3_APP_SECRET").strip()
        self.k3_lcid: int = _int("K3_LCID", 2052)  # 2052 = 简体中文
        self.k3_use_org: str = _env("K3_USE_ORG", "100").strip()  # 使用组织编码，100 = 深圳爱德泰
        # 登录 / 查询 / 查看单据的超时（秒）；旧配置 K3_TIMEOUT 仍可用，作为查询超时的默认值
        self.k3_login_timeout: int = _int("K3_LOGIN_TIMEOUT", 30)
        self.k3_query_timeout: int = _int("K3_QUERY_TIMEOUT", _int("K3_TIMEOUT", 120))
        self.k3_view_timeout: int = _int("K3_VIEW_TIMEOUT", 60)
        # 分页查询失败（超时、断连、5xx）时的重试次数与间隔（秒）
        self.k3_page_retries: int = _int("K3_PAGE_RETRIES", 3)
        self.k3_page_retry_seconds: int = _int("K3_PAGE_RETRY_SECONDS", 10)

        # ---------- 企业微信自建应用。密钥只放 .env。
        self.wecom_corp_id: str = _env("WECOM_CORP_ID").strip()
        self.wecom_agent_id: str = _env("WECOM_AGENT_ID").strip()
        self.wecom_secret: str = _env("WECOM_SECRET").strip()  # 应用 Secret：网页授权、发消息
        self.wecom_contact_secret: str = _env("WECOM_CONTACT_SECRET").strip()  # 通讯录同步 Secret（只读）
        self.wecom_root_dept: int = _int("WECOM_ROOT_DEPT", 1)  # 同步哪个部门及其下级
        self.wecom_api_base: str = _env("WECOM_API_BASE", "https://qyapi.weixin.qq.com").rstrip("/")

        # ---------- 仓储系统（实物条码由仓储赋码；接口见 docs/仓储接口规范.md）。令牌只放 .env。
        self.wms_base_url: str = _env("WMS_BASE_URL").strip().rstrip("/")
        self.wms_token: str = _env("WMS_TOKEN").strip()
        self.wms_timeout: int = _int("WMS_TIMEOUT", 10)

    @property
    def wms_enabled(self) -> bool:
        """地址和令牌都填才启用；未启用时由本系统发 RM 临时码（仅供开发、测试、演示）。"""
        return bool(self.wms_base_url and self.wms_token)

    @property
    def k3_enabled(self) -> bool:
        """金蝶连接五项都填上才启用（未填全时物料用 Excel 导入）。"""
        return all((self.k3_server_url, self.k3_acct_id, self.k3_username, self.k3_app_id, self.k3_app_secret))

    @property
    def wecom_enabled(self) -> bool:
        return all((self.wecom_corp_id, self.wecom_agent_id, self.wecom_secret))


settings = Settings()
