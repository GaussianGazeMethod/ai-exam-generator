"""通用 Schema 与共享常量。"""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel

# ──────────────────────────────────────────────────────────
# 分页约束（全项目唯一来源）
# ──────────────────────────────────────────────────────────
#
# 所有列表接口必须使用这两个常量作为 Query 的 ge/le 约束，
# 不得各自硬编码数字。
#
# 背景：曾出现过「前端请求 page_size=200，后端某接口 le=100 → 422，
# 导致审核工作台整页打不开」的缺陷；且同类问题在多个接口重复出现。
# 统一常量可从根源上杜绝：改上限只需改一处，前端也可据此对齐。
MAX_PAGE_SIZE = 500
DEFAULT_PAGE_SIZE = 20


class PaginationParams(BaseModel):
    page: int = 1
    page_size: int = DEFAULT_PAGE_SIZE


class MessageResponse(BaseModel):
    """通用操作结果。

    ``detail`` 允许承载结构化补充信息（如新建对象的 id、统计计数），
    因此类型为 ``Any`` 而非 ``str``。

    背景：曾把它声明为 ``Optional[str]``，而多个接口传入 dict，
    导致 `POST /notifications/send` 等接口在运行期抛
    ``ValidationError: Input should be a valid string``（HTTP 500）——
    且因为只在被调用时才触发，静态检查与接口文档都看不出问题。
    """

    message: str
    detail: Optional[Any] = None