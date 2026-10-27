"""审计日志模型 — M7 系统管理。"""

from __future__ import annotations
from typing import Optional

from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class AuditLog(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "audit_logs"

    user_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True, comment="操作者ID")
    user_name: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="操作者姓名")
    action: Mapped[str] = mapped_column(
        String(50),
        index=True,
        comment="操作类型: login/upload/review/compose/exam_take/export/config",
    )
    target_type: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, comment="操作对象: document/question/exam/answer/user"
    )
    target_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, comment="操作对象ID")
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="操作详情 JSON")
    ip_address: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="IP地址")