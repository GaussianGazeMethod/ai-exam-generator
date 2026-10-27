"""所有模型的 UUID 主键混入 + 时间戳混入。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UUIDMixin:
    """UUID v4 主键。"""

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: uuid.uuid4().hex,
    )


class TimestampMixin:
    """创建时间 / 更新时间。

    注意：这里使用 **Python 侧默认值**（`default=datetime.now` /
    `onupdate=datetime.now`），而不是仅依赖 `server_default=func.now()`。

    原因：若只给 SQL 函数默认值，SQLAlchemy 在 flush 后无法得知数据库生成的值，
    会把该属性标记为 expired，后续访问会触发一次隐式属性重载；
    在异步会话中这种同步 IO 会直接抛 `MissingGreenlet`。
    保留 `server_default` 是为了让绕过 ORM 的原生 SQL 插入也有值。
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.now,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.now,
        onupdate=datetime.now,
        server_default=func.now(),
    )