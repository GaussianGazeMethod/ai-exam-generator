"""S6 统计服务 — 自动判卷结果的多维聚合与角色视图隔离。

依据 spec/核心模块架构设计.md 模块六与 spec/页面层级树与核心服务模块.md S6：
- 多维度统计：按考试全景 / 按章节 / 按部门 / 按个人 / 按题目
- 角色视图隔离：HR 看全公司、部门管理者看本部门、合规看全景、专家看题目质量
- 看板数据实时计算（T+0）
- 题目质量反馈：错误率超阈值的题目推送给 S3 标记「待复查」

实现要点：
- **避免 N+1 查询**：旧实现按答卷逐条查用户，N 份答卷就是 N 次查询。
  这里统一用 JOIN / 批量 IN 查询，一次取回所需关联数据后在内存聚合。
- 数据权限在服务层通过 `dept_scope` 参数收口，路由层负责按角色传入。
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Iterable, Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.answer import Answer, AnswerDetail
from app.models.exam import Exam, ExamStatus
from app.models.question import Question, QuestionStatus
from app.models.user import Department, User

logger = logging.getLogger(__name__)

# 题目质量预警阈值（错误率高于此值 → 建议专家复查题目表述）
DEFAULT_ERROR_THRESHOLD = 50.0

# 及格线（spec：≥60 分通过）
PASS_SCORE = 60.0


# ──────────────────────────────────────────────────────────
# 内部数据结构
# ──────────────────────────────────────────────────────────


@dataclass
class ExamStatsContext:
    """一次考试统计所需的全部原始数据（一次查询取齐，供各维度复用）。"""

    exam: Exam
    questions: dict[str, Question] = field(default_factory=dict)
    users: dict[str, User] = field(default_factory=dict)
    departments: dict[str, Department] = field(default_factory=dict)
    answers: list[Answer] = field(default_factory=list)
    details: list[AnswerDetail] = field(default_factory=list)

    @property
    def submitted(self) -> list[Answer]:
        return [a for a in self.answers if a.status == "submitted"]

    @property
    def max_score(self) -> float:
        """试卷满分（所有题目分值之和）。"""
        return float(sum((q.score or 0.0) for q in self.questions.values()))

    def score_percent(self, answer: Answer) -> float:
        """答卷得分率（0-100）。

        通过判定按**得分率**而非绝对分：一场 10 题、每题 1 分的考试满分只有 10 分，
        用固定 60 分及格线会永远不及格（此前实测 10/10 全对仍判未通过）。
        """
        total = self.max_score
        if total <= 0:
            return 0.0
        return round((answer.total_score or 0.0) / total * 100, 1)

    def is_passed(self, answer: Answer) -> bool:
        return self.score_percent(answer) >= PASS_SCORE

    def user_of(self, answer: Answer) -> User | None:
        return self.users.get(answer.user_id)

    def dept_name_of(self, user: User | None) -> str:
        if user is None or not user.department_id:
            return "未分配部门"
        dept = self.departments.get(user.department_id)
        return dept.name if dept else "未知部门"


async def load_exam_context(db: AsyncSession, exam_id: str) -> ExamStatsContext:
    """一次性加载某场考试统计所需的全部数据（4~5 次查询，与数据量无关）。"""
    exam = (await db.execute(select(Exam).where(Exam.id == exam_id))).scalar_one_or_none()
    if exam is None:
        raise ValueError("考试不存在")

    ctx = ExamStatsContext(exam=exam)

    # 1) 试卷题目
    try:
        question_ids = json.loads(exam.question_ids or "[]")
    except (json.JSONDecodeError, TypeError):
        question_ids = []
    if question_ids:
        rows = (
            await db.execute(select(Question).where(Question.id.in_(question_ids)))
        ).scalars().all()
        ctx.questions = {q.id: q for q in rows}

    # 2) 答卷
    ctx.answers = list(
        (await db.execute(select(Answer).where(Answer.exam_id == exam_id))).scalars().all()
    )

    # 3) 答题明细（一次取回，按答卷分组）
    answer_ids = [a.id for a in ctx.answers]
    if answer_ids:
        ctx.details = list(
            (
                await db.execute(
                    select(AnswerDetail).where(AnswerDetail.answer_id.in_(answer_ids))
                )
            ).scalars().all()
        )

    # 4) 涉及的用户（一次 IN 查询，避免 N+1）
    user_ids = {a.user_id for a in ctx.answers if a.user_id}
    if user_ids:
        rows = (await db.execute(select(User).where(User.id.in_(user_ids)))).scalars().all()
        ctx.users = {u.id: u for u in rows}

    # 5) 部门字典
    rows = (await db.execute(select(Department))).scalars().all()
    ctx.departments = {d.id: d for d in rows}

    return ctx


def _pct(numerator: float, denominator: float, digits: int = 1) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator * 100, digits)


def _avg(values: Sequence[float], digits: int = 1) -> float:
    vals = [v for v in values if v is not None]
    if not vals:
        return 0.0
    return round(sum(vals) / len(vals), digits)


# ──────────────────────────────────────────────────────────
# 1. 考试全景
# ──────────────────────────────────────────────────────────


def build_overview(ctx: ExamStatsContext, *, dept_scope: Iterable[str] | None = None) -> dict[str, Any]:
    """考试全景：应考/已考/未考、平均分、通过率、最高/最低分。

    Args:
        dept_scope: 限定部门 ID 集合（部门管理者视角）。None 表示全公司。
    """
    scope = set(dept_scope) if dept_scope else None

    submitted = ctx.submitted
    if scope is not None:
        submitted = [
            a
            for a in submitted
            if (u := ctx.user_of(a)) is not None and u.department_id in scope
        ]

    scores = [a.total_score for a in submitted if a.total_score is not None]
    # 通过判定统一走得分率（见 ExamStatsContext.score_percent），与交卷时写回的
    # is_passed 口径一致；历史数据 is_passed 可能按绝对分算，故这里重算。
    passed = sum(1 for a in submitted if ctx.is_passed(a))

    # 应考人数：全公司用考试配置值；部门视角按该部门实际人数
    if scope is None:
        candidate_count = ctx.exam.candidate_count or 0
    else:
        candidate_count = sum(
            1
            for u in ctx.users.values()
            if u.department_id in scope
        )
        # 部门视角下，未提交答卷的部门成员也计入应考
        member_ids = {
            u.id for u in ctx.users.values() if u.department_id in scope
        }
        candidate_count = max(candidate_count, len(member_ids))

    completed = len(submitted)

    return {
        "exam_id": ctx.exam.id,
        "exam_name": ctx.exam.name,
        "exam_status": ctx.exam.status.value
        if hasattr(ctx.exam.status, "value")
        else str(ctx.exam.status),
        "candidate_count": candidate_count,
        "completed_count": completed,
        "not_started_count": max(0, candidate_count - completed),
        "average_score": _avg(scores),
        "pass_rate": _pct(passed, completed),
        "highest_score": max(scores) if scores else 0.0,
        "lowest_score": min(scores) if scores else 0.0,
        "pass_score": PASS_SCORE,
        "max_score": ctx.max_score,
        "average_score_percent": _avg([ctx.score_percent(a) for a in submitted]),
        "passed_count": passed,
        "failed_count": completed - passed,
    }


# ──────────────────────────────────────────────────────────
# 2. 按章节
# ──────────────────────────────────────────────────────────


def build_chapter_stats(ctx: ExamStatsContext, *, dept_scope: Iterable[str] | None = None) -> list[dict]:
    """按章/节聚合正确率（spec：哪些条款/章节错误率高 → 定位制度传达薄弱点）。"""
    scope = set(dept_scope) if dept_scope else None

    # answer_id -> 是否在范围内
    allowed_answers: set[str] = set()
    for a in ctx.submitted:
        if scope is None:
            allowed_answers.add(a.id)
        else:
            u = ctx.user_of(a)
            if u is not None and u.department_id in scope:
                allowed_answers.add(a.id)

    # (chapter_no, section_no) -> [total, correct]
    bucket: dict[tuple[str, str], list[int]] = {}
    question_of = ctx.questions

    for detail in ctx.details:
        if detail.answer_id not in allowed_answers:
            continue
        q = question_of.get(detail.question_id)
        if q is None:
            continue
        key = (q.chapter_no or "未标注章节", q.section_no or "")
        entry = bucket.setdefault(key, [0, 0])
        entry[0] += 1
        if detail.is_correct:
            entry[1] += 1

    result = [
        {
            "chapter_no": ch,
            "section_no": sec,
            "total_answers": total,
            "correct_answers": correct,
            "correct_rate": _pct(correct, total),
        }
        for (ch, sec), (total, correct) in bucket.items()
    ]
    # 正确率升序 —— 最薄弱的章节排最前
    result.sort(key=lambda x: x["correct_rate"])
    return result


# ──────────────────────────────────────────────────────────
# 3. 按部门
# ──────────────────────────────────────────────────────────


def build_dept_stats(ctx: ExamStatsContext, *, dept_scope: Iterable[str] | None = None) -> list[dict]:
    """按部门聚合：应考 / 已考 / 平均分 / 通过率 / 排名。

    修正旧实现的两个问题：
    - 应考人数不再用已考数代填，按组织架构真实统计
    - 不再逐条答卷查用户
    """
    scope = set(dept_scope) if dept_scope else None

    # 部门 -> 成员
    dept_members: dict[str, list[str]] = {}
    for u in ctx.users.values():
        if scope is not None and u.department_id not in scope:
            continue
        dept_members.setdefault(u.department_id or "__none__", []).append(u.id)

    # 部门 -> [分数]
    dept_scores: dict[str, list[float]] = {}
    dept_passed: dict[str, int] = {}
    for a in ctx.submitted:
        u = ctx.user_of(a)
        if u is None:
            continue
        if scope is not None and u.department_id not in scope:
            continue
        key = u.department_id or "__none__"
        if a.total_score is not None:
            dept_scores.setdefault(key, []).append(a.total_score)
        if ctx.is_passed(a):
            dept_passed[key] = dept_passed.get(key, 0) + 1

    # 参加考试的部门并集（含只应考未参考的部门）
    all_keys = set(dept_members) | set(dept_scores)

    rows: list[dict] = []
    for key in all_keys:
        name = "未分配部门" if key == "__none__" else ctx.departments.get(key).name if key in ctx.departments else "未知部门"
        scores = dept_scores.get(key, [])
        completed = len(scores)
        candidate = len(dept_members.get(key, []))
        rows.append(
            {
                "dept_id": None if key == "__none__" else key,
                "dept_name": name,
                "candidate_count": candidate,
                "completed_count": completed,
                "not_started_count": max(0, candidate - completed),
                "average_score": _avg(scores),
                # 通过率按得分率口径（见 ExamStatsContext.is_passed），与概览一致
                "pass_rate": _pct(dept_passed.get(key, 0), completed),
            }
        )

    # 平均分降序 → 排名
    rows.sort(key=lambda x: x["average_score"], reverse=True)
    for i, row in enumerate(rows, start=1):
        row["rank"] = i
    return rows


# ──────────────────────────────────────────────────────────
# 4. 按个人
# ──────────────────────────────────────────────────────────


def build_person_stats(ctx: ExamStatsContext, *, dept_scope: Iterable[str] | None = None) -> list[dict]:
    """按个人聚合（spec：姓名、部门、得分、用时、提交时间）。"""
    scope = set(dept_scope) if dept_scope else None

    rows: list[dict] = []
    for a in ctx.submitted:
        u = ctx.user_of(a)
        if scope is not None and (u is None or u.department_id not in scope):
            continue
        rows.append(
            {
                "user_id": a.user_id,
                "name": u.name if u else "未知用户",
                "dept_id": u.department_id if u else None,
                "dept_name": ctx.dept_name_of(u),
                "score": a.total_score or 0.0,
                "score_percent": ctx.score_percent(a),
                "is_passed": ctx.is_passed(a),
                "correct_count": a.correct_count or 0,
                "wrong_count": a.wrong_count or 0,
                "duration_seconds": a.duration_seconds or 0,
                "submitted_at": a.submitted_at or "",
                "submit_type": a.submit_type.value
                if a.submit_type and hasattr(a.submit_type, "value")
                else (str(a.submit_type) if a.submit_type else None),
                "ip_address": a.ip_address,
            }
        )

    rows.sort(key=lambda x: x["score"], reverse=True)
    for i, row in enumerate(rows, start=1):
        row["rank"] = i
    return rows


# ──────────────────────────────────────────────────────────
# 5. 按题目（含质量反馈）
# ──────────────────────────────────────────────────────────


def build_question_stats(
    ctx: ExamStatsContext,
    *,
    error_threshold: float = DEFAULT_ERROR_THRESHOLD,
) -> list[dict]:
    """按题目聚合错误率，并标记超过阈值的题目（M6 → S3 闭环反馈）。"""
    # question_id -> [total, correct]
    bucket: dict[str, list[int]] = {}
    for detail in ctx.details:
        entry = bucket.setdefault(detail.question_id, [0, 0])
        entry[0] += 1
        if detail.is_correct:
            entry[1] += 1

    rows: list[dict] = []
    # 保持试卷顺序
    for idx, qid in enumerate(ctx.questions.keys(), start=1):
        q = ctx.questions[qid]
        total, correct = bucket.get(qid, [0, 0])
        error_rate = round(100 - _pct(correct, total), 1) if total else 0.0
        rows.append(
            {
                "order": idx,
                "question_id": qid,
                "question_type": q.question_type.value
                if hasattr(q.question_type, "value")
                else str(q.question_type),
                "content": q.content,
                "chapter_no": q.chapter_no,
                "clause_no": q.clause_no,
                "total_answers": total,
                "correct_answers": correct,
                "correct_rate": _pct(correct, total),
                "error_rate": error_rate,
                "usage_count": q.usage_count,
                # 专家在列表侧即可核对「题干 vs 选项/答案」是否匹配，
                # 无需逐题跳详情页（交接文档 2.2）
                "options": json.loads(q.options) if q.options else [],
                "answer": json.loads(q.answer) if q.answer else [],
                "score": q.score,
                # 错误率超阈值 → 疑似题目表述问题，建议专家复查
                "needs_review": bool(total > 0 and error_rate >= error_threshold),
            }
        )

    rows.sort(key=lambda x: x["error_rate"], reverse=True)
    return rows


async def push_quality_feedback(
    db: AsyncSession,
    question_stats: list[dict],
    *,
    error_threshold: float = DEFAULT_ERROR_THRESHOLD,
) -> list[str]:
    """把高错误率题目回写 Question.error_rate 并标记「待复查」。

    对应 spec 的闭环反馈：M6 错误率异常题目 → 反馈给 M3 供专家复查。
    Returns: 被标记的题目 ID 列表
    """
    flagged: list[str] = []
    for row in question_stats:
        if not row.get("needs_review"):
            continue
        qid = row["question_id"]
        q = (
            await db.execute(select(Question).where(Question.id == qid))
        ).scalar_one_or_none()
        if q is None:
            continue
        q.error_rate = row["error_rate"]
        q.usage_count = (q.usage_count or 0) + 1
        if q.status == QuestionStatus.APPROVED:
            q.status = QuestionStatus.FLAGGED  # 待复查
            flagged.append(qid)
    if flagged:
        await db.flush()
        logger.info("已标记 %s 道高错误率题目为待复查", len(flagged))
    return flagged


# ──────────────────────────────────────────────────────────
# 6. 合规全景
# ──────────────────────────────────────────────────────────


def _period_range(period: str | None, now: datetime | None = None) -> tuple[datetime, datetime] | None:
    """把 period（如 2026Q3 / 2026 / 或 None）解析成时间区间。"""
    if not period:
        return None
    now = now or datetime.now()
    text = period.strip().upper()

    # 形如 2026Q3
    if "Q" in text:
        try:
            year_s, quarter_s = text.split("Q")
            year, quarter = int(year_s), int(quarter_s)
            if not 1 <= quarter <= 4:
                return None
            start = datetime(year, (quarter - 1) * 3 + 1, 1)
            end = start + timedelta(days=92)
            end = datetime(end.year, end.month, 1)
            return start, end
        except (ValueError, TypeError):
            return None

    # 形如 2026
    try:
        year = int(text)
        return datetime(year, 1, 1), datetime(year + 1, 1, 1)
    except (ValueError, TypeError):
        return None


async def build_compliance_overview(
    db: AsyncSession,
    *,
    period: str | None = None,
) -> list[dict]:
    """合规全景：每项制度的考试场次、覆盖率、通过率、状态标识。

    对应 spec 合规全景页：制度传达验证一览表。
    """
    rng = _period_range(period)

    # 取所有已发布过的考试（进行中 / 已截止 / 已终止）
    exams = list(
        (
            await db.execute(
                select(Exam)
                .where(Exam.status.in_([ExamStatus.IN_PROGRESS, ExamStatus.FINISHED, ExamStatus.TERMINATED]))
                .order_by(Exam.created_at.desc())
            )
        ).scalars().all()
    )

    if rng:
        start, end = rng
        filtered: list[Exam] = []
        for e in exams:
            ts = e.published_at or ""
            try:
                dt = datetime.fromisoformat(ts)
            except (ValueError, TypeError):
                continue
            if start <= dt < end:
                filtered.append(e)
        exams = filtered

    # 批量取答卷，避免逐场考试查询
    exam_ids = [e.id for e in exams]
    answers_by_exam: dict[str, list[Answer]] = {}
    if exam_ids:
        rows = (
            await db.execute(select(Answer).where(Answer.exam_id.in_(exam_ids)))
        ).scalars().all()
        for a in rows:
            answers_by_exam.setdefault(a.exam_id, []).append(a)

    # 每场考试的满分（用于把绝对分换算成得分率，口径与看板一致）
    max_score_by_exam: dict[str, float] = {}
    for e in exams:
        try:
            qids = json.loads(e.question_ids or "[]")
        except (json.JSONDecodeError, TypeError):
            qids = []
        if not qids:
            max_score_by_exam[e.id] = 0.0
            continue
        q_rows = (
            await db.execute(select(Question).where(Question.id.in_(qids)))
        ).scalars().all()
        max_score_by_exam[e.id] = float(sum((q.score or 0.0) for q in q_rows))

    # 制度名称字典
    doc_ids = set()
    for e in exams:
        try:
            doc_ids.update(json.loads(e.document_ids or "[]"))
        except (json.JSONDecodeError, TypeError):
            pass

    doc_names: dict[str, str] = {}
    if doc_ids:
        from app.models.document import Document

        rows = (
            await db.execute(select(Document).where(Document.id.in_(doc_ids)))
        ).scalars().all()
        doc_names = {d.id: d.name for d in rows}

    # 按制度聚合
    by_doc: dict[str, dict[str, Any]] = {}
    for exam in exams:
        try:
            ids = json.loads(exam.document_ids or "[]") or ["__all__"]
        except (json.JSONDecodeError, TypeError):
            ids = ["__all__"]

        answers = [a for a in answers_by_exam.get(exam.id, []) if a.status == "submitted"]
        completed = len(answers)
        candidate = exam.candidate_count or 0
        exam_max = max_score_by_exam.get(exam.id, 0.0)

        def _passed(a: Answer) -> bool:
            if exam_max <= 0:
                return bool(a.is_passed)
            return (a.total_score or 0.0) / exam_max * 100 >= PASS_SCORE

        passed = sum(1 for a in answers if _passed(a))

        for did in ids:
            entry = by_doc.setdefault(
                did,
                {
                    "document_id": None if did == "__all__" else did,
                    "document_name": doc_names.get(did, "全部制度" if did == "__all__" else "未知制度"),
                    "exam_count": 0,
                    # exams 带 ID，供前端「查看统计」下钻；exam_names 保留为派生字段，
                    # 兼容只想拿名字的调用方（避免一次性破坏既有契约）。
                    "exams": [],
                    "exam_names": [],
                    "_completed": 0,
                    "_candidate": 0,
                    "_passed": 0,
                    "latest_status": "",
                },
            )
            entry["exam_count"] += 1
            entry["exams"].append({"exam_id": exam.id, "name": exam.name})
            entry["exam_names"].append(exam.name)
            entry["_completed"] += completed
            entry["_candidate"] += candidate
            entry["_passed"] += passed
            entry["latest_status"] = (
                exam.status.value if hasattr(exam.status, "value") else str(exam.status)
            )

    result: list[dict] = []
    for entry in by_doc.values():
        completed = entry.pop("_completed")
        candidate = entry.pop("_candidate")
        passed = entry.pop("_passed")
        coverage = _pct(completed, candidate)

        if coverage >= 100:
            status, label = "complete", "已完成"
        elif coverage > 0:
            status, label = "partial", f"{max(0, candidate - completed)}人未考"
        else:
            status, label = "pending", "未开始"

        entry.update(
            {
                # 对外暴露真实计数（原先只用于内部计算后被 pop 掉，
                # 导致合规报告导出拿不到每项制度的应考/已考数）
                "candidate_count": candidate,
                "completed_count": completed,
                "not_started_count": max(0, candidate - completed),
                "passed_count": passed,
                "coverage_rate": coverage,
                "pass_rate": _pct(passed, completed),
                "status": status,
                "status_label": label,
            }
        )
        result.append(entry)

    result.sort(key=lambda x: x["coverage_rate"])
    return result


# ──────────────────────────────────────────────────────────
# 7. 个人答卷详情（下钻）
# ──────────────────────────────────────────────────────────


async def get_answer_detail(db: AsyncSession, answer_id: str) -> dict[str, Any] | None:
    """单份答卷的逐题明细（spec：从个人下钻到单题作答明细）。"""
    answer = (await db.execute(select(Answer).where(Answer.id == answer_id))).scalar_one_or_none()
    if answer is None:
        return None

    details = list(
        (await db.execute(select(AnswerDetail).where(AnswerDetail.answer_id == answer_id)))
        .scalars()
        .all()
    )
    qids = [d.question_id for d in details]
    questions: dict[str, Question] = {}
    if qids:
        rows = (await db.execute(select(Question).where(Question.id.in_(qids)))).scalars().all()
        questions = {q.id: q for q in rows}

    user = (await db.execute(select(User).where(User.id == answer.user_id))).scalar_one_or_none()
    dept = None
    if user and user.department_id:
        dept = (
            await db.execute(select(Department).where(Department.id == user.department_id))
        ).scalar_one_or_none()

    items = []
    for d in sorted(details, key=lambda x: x.question_order or 0):
        q = questions.get(d.question_id)
        items.append(
            {
                "order": d.question_order,
                "question_id": d.question_id,
                "question_type": q.question_type.value
                if q and hasattr(q.question_type, "value")
                else (str(q.question_type) if q else ""),
                "content": q.content if q else "(题目已删除)",
                "options": json.loads(q.options) if q and q.options else [],
                "correct_answer": json.loads(q.answer) if q and q.answer else [],
                "user_answer": json.loads(d.user_answer) if d.user_answer else [],
                "is_correct": bool(d.is_correct),
                "score": d.score,
                "chapter_no": q.chapter_no if q else None,
                "clause_no": q.clause_no if q else None,
            }
        )

    return {
        "answer_id": answer.id,
        "exam_id": answer.exam_id,
        "user_id": answer.user_id,
        "user_name": user.name if user else "未知用户",
        "dept_name": dept.name if dept else "未分配部门",
        "total_score": answer.total_score or 0.0,
        "is_passed": bool(answer.is_passed),
        "correct_count": answer.correct_count or 0,
        "wrong_count": answer.wrong_count or 0,
        "duration_seconds": answer.duration_seconds or 0,
        "submitted_at": answer.submitted_at,
        "submit_type": answer.submit_type.value
        if answer.submit_type and hasattr(answer.submit_type, "value")
        else None,
        "ip_address": answer.ip_address,
        "details": items,
    }