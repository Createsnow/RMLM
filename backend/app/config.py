"""运行配置：全部从环境变量读取，开发环境有默认值。"""
import os
from pathlib import Path


class Settings:
    def __init__(self) -> None:
        self.database_url: str = os.getenv(
            "RMLM_DATABASE_URL",
            "mysql+pymysql://rmlm:rmlm_dev@127.0.0.1:3306/rmlm?charset=utf8mb4",
        )
        self.jwt_secret: str = os.getenv("RMLM_JWT_SECRET", "dev-secret-change-me")
        self.jwt_hours: int = int(os.getenv("RMLM_JWT_HOURS", "12"))
        self.photo_dir: Path = Path(os.getenv("RMLM_PHOTO_DIR", "./data/photos")).resolve()
        self.photo_max_kb: int = int(os.getenv("RMLM_PHOTO_MAX_KB", "300"))
        self.watermark_font: str = os.getenv(
            "RMLM_WATERMARK_FONT", "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc"
        )
        self.static_dir: Path = Path(
            os.getenv("RMLM_STATIC_DIR", str(Path(__file__).resolve().parents[2] / "frontend" / "dist"))
        )
        self.enable_scheduler: bool = os.getenv("RMLM_SCHEDULER", "1") == "1"
        self.admin_username: str = os.getenv("RMLM_ADMIN_USER", "admin")
        self.admin_password: str = os.getenv("RMLM_ADMIN_PASSWORD", "admin123")
        # 对外访问地址（企微消息里的链接、网页授权回调都用它），例如 https://rmlm.example.com
        self.public_url: str = os.getenv("RMLM_PUBLIC_URL", "").rstrip("/")

        # 金蝶云星空 WebAPI（只读）。密钥只放 .env，不要写进代码或仓库。
        self.k3_server_url: str = os.getenv("K3_SERVER_URL", "").strip()
        self.k3_acct_id: str = os.getenv("K3_ACCT_ID", "").strip()
        self.k3_username: str = os.getenv("K3_USERNAME", "").strip()
        self.k3_app_id: str = os.getenv("K3_APP_ID", "").strip()
        self.k3_app_secret: str = os.getenv("K3_APP_SECRET", "").strip()
        self.k3_lcid: int = int(os.getenv("K3_LCID", "2052"))
        self.k3_use_org: str = os.getenv("K3_USE_ORG", "100").strip()  # 使用组织编码，100 = 深圳爱德泰
        self.k3_timeout: int = int(os.getenv("K3_TIMEOUT", "120"))

        # 企业微信自建应用。密钥只放 .env。
        self.wecom_corp_id: str = os.getenv("WECOM_CORP_ID", "").strip()
        self.wecom_agent_id: str = os.getenv("WECOM_AGENT_ID", "").strip()
        self.wecom_secret: str = os.getenv("WECOM_SECRET", "").strip()  # 应用 Secret：网页授权、发消息
        self.wecom_contact_secret: str = os.getenv("WECOM_CONTACT_SECRET", "").strip()  # 通讯录同步 Secret（只读）
        self.wecom_root_dept: int = int(os.getenv("WECOM_ROOT_DEPT", "1"))  # 同步哪个部门及其下级
        self.wecom_api_base: str = os.getenv("WECOM_API_BASE", "https://qyapi.weixin.qq.com").rstrip("/")

    @property
    def k3_enabled(self) -> bool:
        return all((self.k3_server_url, self.k3_acct_id, self.k3_username, self.k3_app_id, self.k3_app_secret))

    @property
    def wecom_enabled(self) -> bool:
        return all((self.wecom_corp_id, self.wecom_agent_id, self.wecom_secret))


settings = Settings()
