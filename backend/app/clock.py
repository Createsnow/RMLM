"""统一的"当前时间"。业务一律调用 now()/today()，测试时可以拨动时钟验证各类时限。

系统按本地时间（服务器时区，默认部署为 Asia/Shanghai）存储不带时区的时间。
"""
from datetime import date, datetime, timedelta

_offset = timedelta(0)
_fixed: datetime | None = None


def now() -> datetime:
    if _fixed is not None:
        return _fixed
    return (datetime.now() + _offset).replace(microsecond=0)


def today() -> date:
    return now().date()


def set_now(value: datetime | None) -> None:
    """测试用：固定当前时间；传 None 恢复真实时间。"""
    global _fixed
    _fixed = value


def advance(delta: timedelta) -> None:
    """测试用：把固定时钟向后拨。"""
    global _fixed
    _fixed = (_fixed or now()) + delta
