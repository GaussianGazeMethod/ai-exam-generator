"""答卷模型 — M5 答题服务。"""

from __future__ import annotations
from typing import Optional

import enum

from sqlalchemy import Enum, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class SubmitType(str, enum.Enum):
    MANUAL = "manual"  # 手动提交
    AUTO = "auto"  # 倒计时自动提交


class Answer(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "answers"

    exam_id: Mapped[str] = mapped_column(String(36), index=True, comment="考试ID")
    user_id: Mapped[str] = mapped_column(String(36), index=True, comment="考生ID")

    started_at: Mapped[Optional[str]] = mapped_column(String(30), nullable=True, comment="开始答题时间 ISO")
    submitted_at: Mapped[Optional[str]] = mapped_column(String(30), nullable=True, comment="提交时间 ISO")
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="用时（秒）")

    total_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="总分")
    correct_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="正确题数")
    wrong_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, comment="错误题数")
    is_passed: Mapped[Optional[bool]] = mapped_column(nullable=True, comment="是否通过（≥60分）")

    submit_type: Mapped[Optional[SubmitType]] = mapped_column(
        Enum(SubmitType), nullable=True, comment="提交方式"
    )
    ip_address: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="IP地址")

    status: Mapped[str] = mapped_column(
        String(20), default="in_progress", comment="答卷状态: in_progress / submitted / graded"
    )


class AnswerDetail(Base, UUIDMixin, TimestampMixin):
    """每题作答明细。"""

    __tablename__ = "answer_details"

    answer_id: Mapped[str] = mapped_column(String(36), index=True, comment="答卷ID")
    question_id: Mapped[str] = mapped_column(String(36), index=True, comment="题目ID")

    # JSON: 考生答案
    user_answer: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="考生答案 JSON")

    is_correct: Mapped[Optional[bool]] = mapped_column(nullable=True, comment="是否正确")
    score: Mapped[float] = mapped_column(Float, default=0.0, comment="得分")

    question_order: Mapped[int] = mapped_column(Integer, default=0, comment="题目序号")