"""通用 Pydantic 模型。"""

from __future__ import annotations

from typing import Optional,  Any, Generic, TypeVar

from pydantic import BaseModel

T = TypeVar("T")


class PaginatedResponse(BaseModel, Generic[T]):
    items: list[T]
    total: int
    page: int = 1
    page_size: int = 20


class MessageResponse(BaseModel):
    message: str
    detail: Any | None = None