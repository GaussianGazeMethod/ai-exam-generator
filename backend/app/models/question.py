"""题目模型 — M3 题库服务。"""

from __future__ import annotations
from typing import Optional

import enum

from sqlalchemy import Boolean, Enum, Float, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class QuestionType(str, enum.Enum):
    SINGLE_CHOICE = "single_choice"  # 单选题
    MULTI_CHOICE = "multi_choice"  # 多选题
    TRUE_FALSE = "true_false"  # 判断题
    FILL_BLANK = "fill_blank"  # 填空题


class QuestionStatus(str, enum.Enum):
    PENDING = "pending"  # 待审核
    APPROVED = "approved"  # 已通过
    REJECTED = "rejected"  # 已弃用
    FLAGGED = "flagged"  # 待复查（M6 反馈）


class Question(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "questions"

    document_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, index=True, comment="来源制度ID")
    chapter_no: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="来源章编号")
    section_no: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="来源节编号")
    clause_no: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, comment="来源条款编号")

    question_type: Mapped[QuestionType] = mapped_column(Enum(QuestionType), comment="题型")
    status: Mapped[QuestionStatus] = mapped_column(
        Enum(QuestionStatus), default=QuestionStatus.PENDING, comment="题目状态"
    )

    content: Mapped[str] = mapped_column(Text, comment="题目正文")

    # JSON: ["选项A","选项B","选项C","选项D"]，判断题存 ["正确","错误"]
    options: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="选项 JSON 数组")

    # JSON: 正确答案，单选 ["A"]，多选 ["A","C"]，判断 ["正确"]，填空 ["密钥"]
    answer: Mapped[str] = mapped_column(Text, comment="正确答案 JSON")

    score: Mapped[float] = mapped_column(Float, default=1.0, comment="分值")

    # AI 原始版本 vs 专家版本
    ai_raw_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="AI 原始生成的题目")
    ai_raw_options: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="AI 原始选项")
    edit_history: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="修改历史 JSON")

    reviewer_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, comment="审核者ID")
    review_time: Mapped[Optional[str]] = mapped_column(String(30), nullable=True, comment="审核时间")
    reject_reason: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="弃用原因")

    usage_count: Mapped[int] = mapped_column(Integer, default=0, comment="使用次数")
    error_rate: Mapped[Optional[float]] = mapped_column(Float, nullable=True, comment="错误率（M6反馈）")

    # ── AI 质量自查结论（M2-3 → M3）──
    # M2-3 的校验结果必须传递给 M3 审核环节，否则专家在审核工作台
    # 看不到「AI 自查发现此题题干不自足」，这次大模型调用就白做了。
    ai_quality_pass: Mapped[Optional[bool]] = mapped_column(
        Boolean, nullable=True, comment="AI 质量自查是否通过（null=未校验）"
    )
    ai_quality_note: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="AI 质量自查说明（理由 + 问题清单）"
    )

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用（上下架）")