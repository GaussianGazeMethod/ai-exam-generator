"""答题 Schema。"""

from __future__ import annotations
from typing import Optional

from datetime import datetime

from pydantic import BaseModel


class ExamStartResponse(BaseModel):
    answer_id: str
    exam_name: str
    duration_minutes: int
    question_count: int
    questions: list[dict]  # 题目列表（含随机顺序）
    started_at: str
    # 该答卷已暂存的作答（刷新页面后用于回显，避免答案丢失）
    my_answers: dict[str, list[str]] = {}


class AnswerSubmit(BaseModel):
    question_id: str
    answer: list[str]  # JSON


class ExamSubmitResponse(BaseModel):
    answer_id: str
    total_score: float
    correct_count: int
    wrong_count: int
    is_passed: bool
    submitted_at: str
    details: list[dict]  # 每题对错 + 正确答案 + 制度条款
    # 满分与得分率：通过判定按得分率（≥ pass_score），前端需展示「82 分 / 100 分」这类信息
    max_score: float = 0.0
    score_percent: float = 0.0
    pass_score: float = 60.0


class AnswerResponse(BaseModel):
    id: str
    exam_id: str
    user_id: str
    started_at: Optional[str] = None
    submitted_at: Optional[str] = None
    duration_seconds: Optional[int] = None
    total_score: Optional[float] = None
    correct_count: Optional[int] = None
    wrong_count: Optional[int] = None
    is_passed: Optional[bool] = None
    status: str
    submit_type: Optional[str] = None


class SingleAnswerSave(BaseModel):
    """保存/更新单题作答（暂存到缓存 + 落库明细）。

    answer_id：移动端「开始考试」返回的答卷 ID。带上它才能精确定位答卷——
    此前实现遍历缓存取第一个 key，多人同时答题时会互相覆盖答案。
    """

    question_id: str
    answer: list[str]
    answer_id: Optional[str] = None

    model_config = {"from_attributes": True}