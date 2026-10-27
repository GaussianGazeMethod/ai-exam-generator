"""考试模型 — M4 考试管理。"""

from __future__ import annotations
from typing import Optional

import enum

from sqlalchemy import Boolean, Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class ExamStatus(str, enum.Enum):
    DRAFT = "draft"  # 组卷中
    PENDING = "pending"  # 待发布
    IN_PROGRESS = "in_progress"  # 进行中
    FINISHED = "finished"  # 已截止
    TERMINATED = "terminated"  # 已终止


class Exam(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "exams"

    name: Mapped[str] = mapped_column(String(200), comment="考试名称")
    status: Mapped[ExamStatus] = mapped_column(
        Enum(ExamStatus), default=ExamStatus.DRAFT, comment="考试状态"
    )

    # 制度范围：JSON 数组 ["doc_id_1", "doc_id_2"]
    document_ids: Mapped[str] = mapped_column(Text, default="[]", comment="制度范围 JSON")

    # 题目列表：JSON 数组 ["q_id_1", ...]（10 题）
    question_ids: Mapped[str] = mapped_column(Text, default="[]", comment="题目列表 JSON")

    duration_minutes: Mapped[int] = mapped_column(Integer, default=30, comment="考试时长（分钟）")
    deadline: Mapped[Optional[str]] = mapped_column(String(30), nullable=True, comment="截止日期时间 ISO")

    # 考生范围：JSON {"type":"all"|"departments","dept_ids":[...]}
    candidate_scope: Mapped[str] = mapped_column(Text, default='{"type":"all"}', comment="考生范围 JSON")

    allow_retake: Mapped[bool] = mapped_column(Boolean, default=False, comment="是否允许重考")
    max_retake_count: Mapped[int] = mapped_column(Integer, default=0, comment="最大重考次数")

    instructions: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="考试须知")

    qr_code_url: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="二维码URL")
    exam_link: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="考试链接")

    publisher_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, comment="发布者ID")
    published_at: Mapped[Optional[str]] = mapped_column(String(30), nullable=True, comment="发布时间")

    candidate_count: Mapped[int] = mapped_column(Integer, default=0, comment="应考人数")
    completed_count: Mapped[int] = mapped_column(Integer, default=0, comment="已考人数")