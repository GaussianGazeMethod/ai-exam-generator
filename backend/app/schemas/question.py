"""题目 Schema。"""

from __future__ import annotations
from typing import Optional

from datetime import datetime

from pydantic import BaseModel


class QuestionBase(BaseModel):
    question_type: str
    content: str
    options: Optional[str] = None  # JSON
    answer: str  # JSON
    score: float = 1.0


class QuestionCreate(QuestionBase):
    document_id: Optional[str] = None
    chapter_no: Optional[str] = None
    section_no: Optional[str] = None
    clause_no: Optional[str] = None


class QuestionUpdate(BaseModel):
    content: Optional[str] = None
    options: Optional[str] = None
    answer: Optional[str] = None
    score: Optional[float] = None


class QuestionReview(BaseModel):
    """审核操作。"""
    action: str  # approve / edit_approve / reject
    content: Optional[str] = None
    options: Optional[str] = None
    answer: Optional[str] = None
    reject_reason: Optional[str] = None


class QuestionBatchReview(BaseModel):
    """批量审核。"""
    question_ids: list[str]
    action: str  # approve / reject


class QuestionResponse(BaseModel):
    id: str
    document_id: Optional[str] = None
    chapter_no: Optional[str] = None
    section_no: Optional[str] = None
    clause_no: Optional[str] = None
    question_type: str
    status: str
    content: str
    options: Optional[str] = None
    answer: str
    score: float = 1.0
    ai_raw_content: Optional[str] = None
    ai_raw_options: Optional[str] = None
    usage_count: int = 0
    error_rate: Optional[float] = None
    is_active: bool = True
    # AI 质量自查结论（M2-3 产出，供审核端参考）
    ai_quality_pass: Optional[bool] = None
    ai_quality_note: Optional[str] = None
    reject_reason: Optional[str] = None
    created_at: datetime | str
    updated_at: datetime | str

    model_config = {"from_attributes": True}


class QuestionListResponse(BaseModel):
    items: list[QuestionResponse]
    total: int