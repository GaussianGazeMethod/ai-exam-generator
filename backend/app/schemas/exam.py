"""考试 Schema。"""

from __future__ import annotations
from typing import Optional

from datetime import datetime

from pydantic import BaseModel


class TypeRatio(BaseModel):
    """题型配比。

    对应 spec 新建考试步骤②：题型配比设置（默认：单选4 + 多选3 + 判断2 + 填空1）。
    """

    single_choice: int = 4
    multi_choice: int = 3
    true_false: int = 2
    fill_blank: int = 1


class ExamCreate(BaseModel):
    name: str
    document_ids: list[str]
    duration_minutes: int = 30
    deadline: Optional[str] = None
    candidate_scope: dict | None = None  # {"type":"all"} or {"type":"departments","dept_ids":[...]}
    allow_retake: bool = False
    max_retake_count: int = 0
    instructions: Optional[str] = None
    # 组卷题型配比；不传则用 spec 默认配比（单选4 + 多选3 + 判断2 + 填空1）
    type_ratio: Optional[TypeRatio] = None


class ExamUpdate(BaseModel):
    name: Optional[str] = None
    duration_minutes: Optional[int] = None
    deadline: Optional[str] = None
    allow_retake: Optional[bool] = None
    instructions: Optional[str] = None


class ExamConfig(BaseModel):
    """步骤三：考试配置。"""
    name: str
    duration_minutes: int = 30
    deadline: Optional[str] = None
    candidate_scope: dict | None = None
    allow_retake: bool = False
    max_retake_count: int = 0
    instructions: Optional[str] = None


class SwapQuestionRequest(BaseModel):
    """替换题目。"""
    old_question_id: str


class ExamProgress(BaseModel):
    candidate_count: int
    completed_count: int
    not_started_users: list[dict] = []


class ExamResponse(BaseModel):
    id: str
    name: str
    status: str
    document_ids: str
    question_ids: str
    duration_minutes: int = 30
    deadline: Optional[str] = None
    candidate_scope: str = '{"type":"all"}'
    allow_retake: bool = False
    max_retake_count: int = 0
    instructions: Optional[str] = None
    qr_code_url: Optional[str] = None
    exam_link: Optional[str] = None
    publisher_id: Optional[str] = None
    published_at: Optional[str] = None
    candidate_count: int = 0
    completed_count: int = 0
    created_at: datetime | str
    updated_at: datetime | str

    model_config = {"from_attributes": True}


class ExamListResponse(BaseModel):
    items: list[ExamResponse]
    total: int