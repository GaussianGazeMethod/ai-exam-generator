"""通知记录模型 — S7 通知服务。"""

from __future__ import annotations
from typing import Optional

import enum

from sqlalchemy import Boolean, Enum, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class NotificationChannel(str, enum.Enum):
    WECOM = "wecom"  # 企业微信
    DINGTALK = "dingtalk"  # 钉钉
    FEISHU = "feishu"  # 飞书
    SMS = "sms"  # 短信


class Notification(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "notifications"

    exam_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True, comment="关联考试ID")
    template_type: Mapped[str] = mapped_column(
        String(30), comment="模板类型: exam_publish / exam_remind / report_ready"
    )
    channel: Mapped[NotificationChannel] = mapped_column(
        Enum(NotificationChannel), comment="推送通道"
    )

    recipients: Mapped[str] = mapped_column(Text, comment="接收人列表 JSON [user_id, ...]")
    title: Mapped[str] = mapped_column(String(200), comment="消息标题")
    content: Mapped[str] = mapped_column(Text, comment="消息正文")

    is_sent: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否已发送")
    sent_at: Mapped[Optional[str]] = mapped_column(String(30), nullable=True, comment="发送时间")
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="发送失败原因")