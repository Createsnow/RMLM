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


settings = Settings()
