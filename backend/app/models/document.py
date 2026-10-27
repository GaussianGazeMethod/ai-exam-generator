"""制度文档模型 — M1 制度管理。"""

from __future__ import annotations
from typing import Optional

import enum

from sqlalchemy import Enum, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class DocStatus(str, enum.Enum):
    UPLOADED = "uploaded"  # 已上传
    PARSING = "parsing"  # AI 解析中
    PARSED = "parsed"  # 解析完成
    GENERATING = "generating"  # 题目生成中
    READY = "ready"  # 题目已生成，待审核


class Document(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "documents"

    name: Mapped[str] = mapped_column(String(200), comment="制度名称")
    version: Mapped[str] = mapped_column(String(50), default="1.0", comment="版本号")
    file_name: Mapped[str] = mapped_column(String(300), comment="原始文件名")
    file_path: Mapped[str] = mapped_column(String(500), comment="OSS/本地存储路径")
    file_size: Mapped[int] = mapped_column(Integer, default=0, comment="文件大小（字节）")
    page_count: Mapped[int] = mapped_column(Integer, default=0, comment="页数")
    file_type: Mapped[str] = mapped_column(String(10), comment="文件类型: docx / pdf")

    status: Mapped[DocStatus] = mapped_column(
        Enum(DocStatus),
        default=DocStatus.UPLOADED,
        comment="制度状态",
    )

    # JSON 字符串：章节树 [{chapter_no, title, clauses: [{clause_no, text}]}]
    structured_content: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="结构化文档 JSON")

    remark: Mapped[Optional[str]] = mapped_column(String(500), nullable=True, comment="备注")
    uploader_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, comment="上传者用户ID")

    question_count: Mapped[int] = mapped_column(Integer, default=0, comment="关联题目数")