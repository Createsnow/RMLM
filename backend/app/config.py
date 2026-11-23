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

        # ---------- 金蝶 K3 Cloud（只读）
        self.k3_server_url: str = _env("K3_SERVER_URL").strip()
        self.k3_acct_id: str = _env("K3_ACCT_ID").strip()
        self.k3_app_id: str = _env("K3_APP_ID").strip()
        self.k3_app_secret: str = _env("K3_APP_SECRET")
        self.k3_username: str = _env("K3_USERNAME").strip()
        self.k3_lcid: int = _int("K3_LCID", 2052)  # 2052 = 简体中文
        self.k3_login_timeout: int = _int("K3_LOGIN_TIMEOUT", 30)
        self.k3_query_timeout: int = _int("K3_QUERY_TIMEOUT", 120)
        self.k3_view_timeout: int = _int("K3_VIEW_TIMEOUT", 60)
        self.k3_page_retries: int = _int("K3_PAGE_RETRIES", 3)
        self.k3_page_retry_seconds: int = _int("K3_PAGE_RETRY_SECONDS", 10)

    @property
    def k3_enabled(self) -> bool:
        """金蝶连接参数是否填全（未填全时只走手工录入 / Excel 导入）。"""
        return all((self.k3_server_url, self.k3_acct_id, self.k3_app_id, self.k3_app_secret, self.k3_username))


settings = Settings()
