"""统计 Schema。"""

from __future__ import annotations

from pydantic import BaseModel


class ExamOverview(BaseModel):
    exam_id: str
    exam_name: str
    candidate_count: int
    completed_count: int
    not_started_count: int
    average_score: float
    pass_rate: float  # ≥60 分比例
    highest_score: float
    lowest_score: float


class ChapterStats(BaseModel):
    chapters: list[dict]  # [{chapter_no, title, correct_rate}]


class DepartmentStats(BaseModel):
    departments: list[dict]  # [{dept_id, dept_name, avg_score, completed_count, total_count}]


class PersonStats(BaseModel):
    persons: list[dict]  # [{user_id, name, dept_name, score, duration_seconds, submitted_at}]


class QuestionStats(BaseModel):
    questions: list[dict]  # [{question_id, content, type, correct_rate, usage_count}]


class ComplianceOverview(BaseModel):
    """合规全景视图。"""
    items: list[dict]  # [{document_name, exam_count, coverage_rate, pass_rate, status}]