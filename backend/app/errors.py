from fastapi import HTTPException


class BizError(HTTPException):
    """业务规则拦截：前端直接把 detail 显示给用户。"""

    def __init__(self, msg: str, status: int = 400) -> None:
        super().__init__(status_code=status, detail=msg)


def forbid(msg: str = "无权限执行此操作") -> BizError:
    return BizError(msg, 403)


def not_found(what: str = "记录") -> BizError:
    return BizError(f"{what}不存在", 404)
