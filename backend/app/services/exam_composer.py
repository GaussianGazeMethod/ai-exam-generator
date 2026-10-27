"""M4-1 智能组卷服务 — 按题型配比从题库随机抽取题目。

依据 spec/核心模块架构设计.md 模块四：
- M4-1 智能组卷：按制度/章节范围筛选；按题型比例随机抽取
  （如单选 4 + 多选 3 + 判断 2 + 填空 1）；不满意的题可单独替换
- 组卷策略可配置：管理员可自定义题型比例，也可直接用系统默认比例
- 同一场考试的考生看到不同题目（随机抽取），防止作弊

依据 spec/页面层级树与核心服务模块.md 新建考试页：
- ② 智能组卷：题型配比设置（默认：单选4 + 多选3 + 判断2 + 填空1）
  / 系统自动抽题结果预览 / 每题旁有[换一题]按钮 / 题目总数：10
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.question import Question, QuestionStatus, QuestionType

logger = logging.getLogger(__name__)

# spec 规定的默认题型配比：单选4 + 多选3 + 判断2 + 填空1 = 10 题
DEFAULT_TYPE_RATIO: dict[str, int] = {
    "single_choice": 4,
    "multi_choice": 3,
    "true_false": 2,
    "fill_blank": 1,
}

TOTAL_QUESTIONS = 10


@dataclass
class CompositionResult:
    """组卷结果。"""

    question_ids: list[str] = field(default_factory=list)
    questions: list[Question] = field(default_factory=list)
    type_stats: dict[str, int] = field(default_factory=dict)
    # 各题型的缺口（题库该题型不足时记录，供前端提示管理员）
    shortages: dict[str, dict[str, int]] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)

    @property
    def is_complete(self) -> bool:
        return len(self.question_ids) == TOTAL_QUESTIONS

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_ids": self.question_ids,
            "total": len(self.question_ids),
            "type_stats": self.type_stats,
            "shortages": self.shortages,
            "warnings": self.warnings,
            "is_complete": self.is_complete,
        }


class RatioError(ValueError):
    """题型配比不合法。"""


def normalize_ratio(ratio: dict[str, int] | None) -> dict[str, int]:
    """校验并归一化题型配比。

    规则（依据 spec：需求澄清文档「题量：每场考试 10 题」）：
    - 缺省用 spec 默认配比（单选4 + 多选3 + 判断2 + 填空1，合计 10）
    - 非法题型剔除、负数归零
    - **合计必须等于 10**，否则抛 RatioError：
      配比之和即试卷题量，与「每场考试 10 题」的硬要求冲突时必须显式报错，
      而不是静默补足（静默补足会让管理员配的题型比例失真）
    """
    if not ratio:
        return dict(DEFAULT_TYPE_RATIO)

    valid = {t.value for t in QuestionType}
    normalized: dict[str, int] = {t: 0 for t in valid}
    for key, value in ratio.items():
        if key in valid:
            try:
                normalized[key] = max(0, int(value))
            except (TypeError, ValueError):
                normalized[key] = 0

    total = sum(normalized.values())
    if total != TOTAL_QUESTIONS:
        raise RatioError(
            f"题型配比合计必须为 {TOTAL_QUESTIONS} 题（当前 {total} 题）。"
            f"请调整各题型数量，例如默认配比：单选4 + 多选3 + 判断2 + 填空1。"
        )
    return normalized


async def compose_exam(
    db: AsyncSession,
    *,
    document_ids: list[str] | None = None,
    chapter_nos: list[str] | None = None,
    ratio: dict[str, int] | None = None,
    rng: random.Random | None = None,
) -> CompositionResult:
    """按题型配比从题库随机组卷。

    Args:
        document_ids: 制度范围（空表示不限制度）
        chapter_nos: 章节范围（空表示不限章节）
        ratio: 题型配比（合计必须为 10），缺省用 spec 默认配比
        rng: 随机源（便于测试注入固定种子）

    Returns:
        CompositionResult

    Raises:
        RatioError: 配比合计不等于 10
    """
    picker = rng or random.Random()
    wanted = normalize_ratio(ratio)
    result = CompositionResult()

    # 1) 按范围筛选可用题目（已通过 + 已上架）
    query = select(Question).where(
        Question.status == QuestionStatus.APPROVED,
        Question.is_active == True,  # noqa: E712
    )
    if document_ids:
        query = query.where(Question.document_id.in_(document_ids))
    if chapter_nos:
        query = query.where(Question.chapter_no.in_(chapter_nos))

    rows = (await db.execute(query)).scalars().all()

    # 2) 按题型分组
    pool: dict[str, list[Question]] = {t.value: [] for t in QuestionType}
    for q in rows:
        key = q.question_type.value if hasattr(q.question_type, "value") else str(q.question_type)
        pool.setdefault(key, []).append(q)

    # 3) 按配比抽取
    picked: list[Question] = []
    for qtype, count in wanted.items():
        if count <= 0:
            continue
        candidates = pool.get(qtype, [])
        take = min(count, len(candidates))
        if take < count:
            result.shortages[qtype] = {"wanted": count, "available": len(candidates)}
            result.warnings.append(
                f"{qtype} 题库不足：需要 {count} 道，可用 {len(candidates)} 道"
            )
        picked.extend(picker.sample(candidates, take))

    # 4) 不足 10 题时，用其他题型的剩余题目补足
    #    注意：这里只在「题库该题型不足」时补足（属客观约束，已在 shortages 记录），
    #    不会因为管理员配比写错而静默补足——配比合计校验由 normalize_ratio 负责。
    if len(picked) < TOTAL_QUESTIONS:
        picked_ids = {q.id for q in picked}
        extras = [q for q in rows if q.id not in picked_ids]
        need = TOTAL_QUESTIONS - len(picked)
        if extras:
            supplement = picker.sample(extras, min(need, len(extras)))
            picked.extend(supplement)
            result.warnings.append(
                f"题库题型不足，已用其他题型补足 {len(supplement)} 道以保证 10 题题量"
            )

    # 5) 打乱最终顺序（同一场考试不同考生顺序应不同）
    picker.shuffle(picked)

    result.questions = picked
    result.question_ids = [q.id for q in picked]
    stats: dict[str, int] = {}
    for q in picked:
        key = q.question_type.value if hasattr(q.question_type, "value") else str(q.question_type)
        stats[key] = stats.get(key, 0) + 1
    result.type_stats = stats

    return result


async def find_swap_candidate(
    db: AsyncSession,
    *,
    question_type: str,
    exclude_ids: list[str],
    document_ids: list[str] | None = None,
    chapter_nos: list[str] | None = None,
    allow_any_type: bool = True,
    rng: random.Random | None = None,
) -> tuple[Question | None, bool]:
    """为「换一题」寻找替换候选。

    spec：组卷预览时每题旁有 [换一题] 按钮，随机替换。

    优先同题型；同题型无可替换时，若 allow_any_type=True 则跨题型兜底
    （小型题库下同题型常常全部在卷中，此时跨题型是更实用的行为）。

    Returns:
        (候选题目或 None, 是否为跨题型替换)
    """
    picker = rng or random.Random()

    def _base_query():
        q = select(Question).where(
            Question.status == QuestionStatus.APPROVED,
            Question.is_active == True,  # noqa: E712
        )
        if document_ids:
            q = q.where(Question.document_id.in_(document_ids))
        if chapter_nos:
            q = q.where(Question.chapter_no.in_(chapter_nos))
        if exclude_ids:
            q = q.where(Question.id.notin_(exclude_ids))
        return q

    # 1) 优先同题型
    same = (
        await db.execute(_base_query().where(Question.question_type == question_type))
    ).scalars().all()
    if same:
        return picker.choice(list(same)), False

    # 2) 跨题型兜底
    if allow_any_type:
        others = (await db.execute(_base_query())).scalars().all()
        if others:
            return picker.choice(list(others)), True

    return None, False


def parse_question_ids(raw: str | None) -> list[str]:
    """安全解析 Exam.question_ids 字段。"""
    if not raw:
        return []
    try:
        value = json.loads(raw)
        return [str(x) for x in value] if isinstance(value, list) else []
    except (json.JSONDecodeError, TypeError):
        return []


def parse_document_ids(raw: str | None) -> list[str]:
    """安全解析 Exam.document_ids 字段。"""
    return parse_question_ids(raw)