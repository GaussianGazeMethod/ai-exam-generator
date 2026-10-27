"""S5 在线答题服务路由 — 开始 / 暂存 / 提交。"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.schemas.common import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE
from app.config import get_settings
from app.models.answer import Answer, AnswerDetail, SubmitType
from app.models.exam import Exam, ExamStatus
from app.models.question import Question
from app.models.user import User
from app.services.audit import record_audit
from app.services.scheduler import is_answer_expired, is_exam_expired
from app.schemas.answer import ExamStartResponse, ExamSubmitResponse, SingleAnswerSave

router = APIRouter()
logger = logging.getLogger(__name__)
settings = get_settings()

# 简易内存缓存：{answer_id: {question_id: answer}}（生产用 Redis）
_answer_cache: dict[str, dict[str, list[str]]] = {}


@router.post("/{exam_id}/start", response_model=ExamStartResponse, summary="开始考试")
async def start_exam(
    request: Request,
    exam_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 验证考试存在且进行中
    result = await db.execute(select(Exam).where(Exam.id == exam_id))
    exam = result.scalar_one_or_none()
    if exam is None:
        raise HTTPException(status_code=404, detail="考试不存在")
    if exam.status != ExamStatus.IN_PROGRESS:
        raise HTTPException(status_code=400, detail="考试不在进行中")
    # 服务端截止时间校验：调度器最多 30s 才把考试置为已截止，
    # 这期间若放行会让考生答一份交不上的卷子
    if is_exam_expired(exam):
        raise HTTPException(status_code=400, detail="考试已过截止时间，无法开始答题")

    # 验证考生范围
    scope = json.loads(exam.candidate_scope)
    if scope.get("type") == "departments":
        dept_ids = scope.get("dept_ids", [])
        if current_user.department_id not in dept_ids:
            raise HTTPException(status_code=403, detail="您不在本次考试范围内")

    # ── 取卷：优先复用已有的进行中答卷 ──
    #
    # spec M5-2 明确要求「断网暂存、刷新不丢失」。
    # 考生刷新答题页会重新调用本接口，此时**必须返回同一份卷子与已作答内容**，
    # 而不是报「您已有一份进行中的答卷」——那会让刷新等于被迫中断考试。
    # 因此：有进行中答卷就复用它；只有在不允许重考且**已提交过**时才拒绝。
    existing = await db.execute(
        select(Answer)
        .where(Answer.exam_id == exam_id, Answer.user_id == current_user.id)
        .order_by(Answer.created_at.desc())
    )
    all_answers = list(existing.scalars().all())
    in_progress = next((a for a in all_answers if a.status == "in_progress"), None)
    finished = [a for a in all_answers if a.status in ("submitted", "graded", "expired")]

    if in_progress is not None:
        # 刷新 / 断线重连 → 复用原答卷（保留 started_at，倒计时不重置）
        answer = in_progress
        logger.info("复用进行中答卷 answer=%s user=%s", answer.id, current_user.id)
    elif finished and not exam.allow_retake:
        raise HTTPException(
            status_code=400,
            detail="您已完成本场考试，且该考试不允许重考",
        )
    elif finished and exam.allow_retake:
        used = len(finished)
        if exam.max_retake_count and used >= exam.max_retake_count + 1:
            raise HTTPException(
                status_code=400,
                detail=f"重考次数已用完（最多 {exam.max_retake_count} 次）",
            )
        answer = Answer(
            exam_id=exam_id,
            user_id=current_user.id,
            started_at=datetime.now().isoformat(),
            status="in_progress",
        )
        db.add(answer)
        await db.flush()
    else:
        answer = Answer(
            exam_id=exam_id,
            user_id=current_user.id,
            started_at=datetime.now().isoformat(),
            status="in_progress",
        )
        db.add(answer)
        await db.flush()

    # 下发题目（随机顺序）
    question_ids = json.loads(exam.question_ids)
    result_q = await db.execute(select(Question).where(Question.id.in_(question_ids)))
    questions = result_q.scalars().all()

    # 随机打乱 + 选项随机
    import random

    random.shuffle(questions)
    q_list = []
    for i, q in enumerate(questions):
        q_dict = {
            "order": i + 1,
            "id": q.id,
            "question_type": q.question_type.value,
            "content": q.content,
            "options": json.loads(q.options) if q.options else [],
            "score": q.score,
            "source": f"{q.chapter_no or ''} {q.clause_no or ''}".strip(),
        }
        # 随机选项顺序（判断题保持不变）
        if q_dict["options"] and q.question_type.value in ("single_choice", "multi_choice"):
            random.shuffle(q_dict["options"])
        q_list.append(q_dict)

    # 初始化缓存；若该答卷此前已暂存过答案（刷新后重新开卷/重新取卷），一并回填，
    # 否则考生刷新一次页面就会看到「答案全空」，接着提交即 0 分。
    saved = (
        await db.execute(select(AnswerDetail).where(AnswerDetail.answer_id == answer.id))
    ).scalars().all()
    restored: dict[str, list[str]] = {}
    for row in saved:
        if not row.user_answer:
            continue
        try:
            restored[row.question_id] = json.loads(row.user_answer)
        except (TypeError, ValueError):
            restored[row.question_id] = [row.user_answer]
    _answer_cache[answer.id] = dict(restored)

    await record_audit(
        db, action="exam_take", user=current_user, target_type="exam", target_id=exam_id,
        detail={"phase": "start", "answer_id": answer.id, "question_count": len(q_list)},
        request=request,
    )
    return ExamStartResponse(
        answer_id=answer.id,
        exam_name=exam.name,
        duration_minutes=exam.duration_minutes,
        question_count=len(q_list),
        questions=q_list,
        started_at=answer.started_at or "",
        my_answers=restored,
    )


@router.post("/{exam_id}/answer", summary="保存/更新单题作答（暂存）")
async def save_answer(
    exam_id: str,
    body: SingleAnswerSave,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """保存单题作答。

    两处落点：
    1. 内存缓存（快速交卷判卷路径）；
    2. AnswerDetail 明细行（持久化）。

    此前只写内存缓存，且 key 靠遍历取第一个答卷——多人同时答题会互相覆盖，
    服务重启/换 worker 后答案全丢，提交时被判 0 分。现在按 answer_id 精确定位，
    并把作答落库，提交时即使缓存缺失也能从明细行恢复。
    """
    target_id = body.answer_id
    if target_id:
        result = await db.execute(select(Answer).where(Answer.id == target_id))
        answer = result.scalar_one_or_none()
        if answer is None:
            raise HTTPException(status_code=404, detail="答卷不存在")
        if answer.user_id != current_user.id:
            raise HTTPException(status_code=403, detail="无权修改他人答卷")
        if answer.exam_id != exam_id:
            raise HTTPException(status_code=400, detail="答卷与考试不匹配")
    else:
        result = await db.execute(
            select(Answer)
            .where(
                Answer.exam_id == exam_id,
                Answer.user_id == current_user.id,
                Answer.status == "in_progress",
            )
            .order_by(Answer.created_at.desc())
            .limit(1)
        )
        answer = result.scalar_one_or_none()
        if answer is None:
            raise HTTPException(status_code=400, detail="请先开始考试")

    if answer.status != "in_progress":
        raise HTTPException(status_code=400, detail="答卷已提交，不能继续作答")

    # 1) 内存缓存
    _answer_cache.setdefault(answer.id, {})[body.question_id] = body.answer

    # 2) 落库明细（同一题重复作答时更新原行，不新增）
    existing = (
        await db.execute(
            select(AnswerDetail).where(
                AnswerDetail.answer_id == answer.id,
                AnswerDetail.question_id == body.question_id,
            )
        )
    ).scalar_one_or_none()

    if existing is None:
        db.add(
            AnswerDetail(
                answer_id=answer.id,
                question_id=body.question_id,
                user_answer=json.dumps(body.answer, ensure_ascii=False),
                is_correct=None,
                score=0.0,
                question_order=0,
            )
        )
    else:
        existing.user_answer = json.dumps(body.answer, ensure_ascii=False)

    await db.flush()

    return {
        "message": "已保存",
        "answer_id": answer.id,
        "question_id": body.question_id,
        "saved_count": len(_answer_cache.get(answer.id, {})),
    }


@router.post("/{exam_id}/submit", response_model=ExamSubmitResponse, summary="提交答卷")
async def submit_exam(
    request: Request,
    exam_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # 找到当前答卷
    result = await db.execute(
        select(Answer).where(
            Answer.exam_id == exam_id,
            Answer.user_id == current_user.id,
            Answer.status == "in_progress",
        ).order_by(Answer.created_at.desc()).limit(1)
    )
    answer = result.scalar_one_or_none()
    if answer is None:
        raise HTTPException(status_code=400, detail="未找到进行中的答卷")

    # 获取考试题目
    exam_result = await db.execute(select(Exam).where(Exam.id == exam_id))
    exam = exam_result.scalar_one_or_none()

    question_ids = json.loads(exam.question_ids) if exam else []
    result_q = await db.execute(select(Question).where(Question.id.in_(question_ids)))
    questions = {q.id: q for q in result_q.scalars().all()}

    # 从缓存读取答案；缓存缺失（服务重启/多 worker/答案只落了库）时从
    # AnswerDetail 明细行恢复——否则会导致「已作答却判 0 分」。
    cached = dict(_answer_cache.get(answer.id, {}))
    if not cached:
        saved_rows = (
            await db.execute(select(AnswerDetail).where(AnswerDetail.answer_id == answer.id))
        ).scalars().all()
        for row in saved_rows:
            if row.user_answer:
                try:
                    cached[row.question_id] = json.loads(row.user_answer)
                except (TypeError, ValueError):
                    cached[row.question_id] = [row.user_answer]

    # 判卷前先清掉本次作答的旧明细行，避免重复行（重复提交/重新判卷时明细翻倍）
    await db.execute(delete(AnswerDetail).where(AnswerDetail.answer_id == answer.id))

    # 自动判卷
    now = datetime.now()
    started = datetime.fromisoformat(answer.started_at) if answer.started_at else now
    duration = int((now - started).total_seconds())

    # ── 服务端超时判定（spec M5：「服务端控制倒计时」）──
    # 前端倒计时只是展示；这里以「开始时间 + 考试时长」为唯一权威重新判定，
    # 并给 30 秒宽限以容忍网络往返与客户端时钟误差。
    # 超时提交仍然收卷（不丢答案），但记为 auto 提交并标注超时时长。
    expired, remaining = is_answer_expired(exam, answer.started_at, now=now, grace_seconds=30)
    if expired:
        logger.warning(
            "答卷超时提交 answer=%s 超出 %ss，仍收卷但标记为自动提交",
            answer.id,
            abs(remaining),
        )

    total_score = 0.0
    correct_count = 0
    details = []

    for q_id in question_ids:
        q = questions.get(q_id)
        if q is None:
            continue

        user_ans = cached.get(q_id, [])
        correct_ans = json.loads(q.answer) if q.answer else []

        is_correct = sorted(user_ans) == sorted(correct_ans)
        score = q.score if is_correct else 0.0

        if is_correct:
            correct_count += 1
        total_score += score

        # 保存答题明细
        detail = AnswerDetail(
            answer_id=answer.id,
            question_id=q_id,
            user_answer=json.dumps(user_ans, ensure_ascii=False),
            is_correct=is_correct,
            score=score,
            question_order=question_ids.index(q_id) + 1,
        )
        db.add(detail)

        details.append(
            {
                "question_id": q_id,
                "content": q.content,
                "user_answer": user_ans,
                "correct_answer": correct_ans,
                "is_correct": is_correct,
                "score": score,
                "source": f"{q.chapter_no or ''} {q.clause_no or ''}".strip(),
            }
        )

    wrong_count = len(question_ids) - correct_count
    max_score = sum(q.score for q in questions.values())
    pass_score = settings.DEFAULT_PASS_SCORE
    # 通过判定按「得分率 ≥ 及格线」而非绝对分：
    # 一场 10 题、每题 1 分的考试满分仅 10 分，按固定 60 分判定会永远不及格
    # （实测全对 10/10 仍判未通过）。得分率口径与 S6 统计看板保持一致。
    score_percent = round(total_score / max_score * 100, 1) if max_score > 0 else 0.0
    is_passed = score_percent >= pass_score

    # 更新答卷
    answer.status = "submitted"
    answer.submitted_at = now.isoformat()
    answer.duration_seconds = duration
    answer.total_score = total_score
    answer.correct_count = correct_count
    answer.wrong_count = wrong_count
    answer.is_passed = is_passed
    # 超时收卷记为自动提交，便于统计「有多少人是被系统强制收卷的」
    answer.submit_type = SubmitType.AUTO if expired else SubmitType.MANUAL

    # 更新考试完成人数
    if exam:
        exam.completed_count = (exam.completed_count or 0) + 1

    await db.flush()

    # 清理缓存
    _answer_cache.pop(answer.id, None)

    # spec M7-4：答题属于必须留痕的操作（谁/何时/得分/用时/IP）
    await record_audit(
        db,
        action="exam_take",
        user=current_user,
        target_type="answer",
        target_id=answer.id,
        detail={
            "phase": "submit",
            "exam_id": exam_id,
            "score": total_score,
            "score_percent": score_percent,
            "is_passed": is_passed,
            "correct": correct_count,
            "wrong": wrong_count,
            "duration_seconds": duration,
        },
        request=request,
    )

    return ExamSubmitResponse(
        answer_id=answer.id,
        total_score=total_score,
        correct_count=correct_count,
        wrong_count=wrong_count,
        is_passed=is_passed,
        submitted_at=now.isoformat(),
        details=details,
        max_score=max_score,
        score_percent=score_percent,
        pass_score=pass_score,
    )


@router.get("/my-exams", summary="我的考试中心（员工待考 / 已考）")
async def my_exams(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """员工端考试中心：进行中/已截止的考试 + 本人在每场考试的状态。

    spec 移动端由扫码/链接进入；Web 端员工也需要一个入口看到「待考 / 已考」。
    数据范围：只返回本人答卷状态，不泄露他人成绩。
    """
    exam_rows = (
        await db.execute(
            select(Exam)
            .where(Exam.status.in_([ExamStatus.IN_PROGRESS, ExamStatus.FINISHED]))
            .order_by(Exam.created_at.desc())
        )
    ).scalars().all()

    answer_rows = (
        await db.execute(select(Answer).where(Answer.user_id == current_user.id))
    ).scalars().all()
    by_exam: dict[str, Answer] = {}
    for a in answer_rows:
        prev = by_exam.get(a.exam_id)
        # 同一场考试可能有多条答卷（重考），保留最近提交的一条
        if prev is None or (a.submitted_at or "") > (prev.submitted_at or ""):
            by_exam[a.exam_id] = a

    # 每场考试的满分（用于把绝对分换算为得分率，通过判定口径统一）
    max_score_by_exam: dict[str, float] = {}
    for e in exam_rows:
        try:
            qids = json.loads(e.question_ids or "[]")
        except (json.JSONDecodeError, TypeError):
            qids = []
        if qids:
            rows = (await db.execute(select(Question).where(Question.id.in_(qids)))).scalars().all()
            max_score_by_exam[e.id] = float(sum((q.score or 0.0) for q in rows))
        else:
            max_score_by_exam[e.id] = 0.0

    items = []
    for e in exam_rows:
        a = by_exam.get(e.id)
        exam_max = max_score_by_exam.get(e.id, 0.0)
        passed = (
            (a.total_score or 0.0) / exam_max * 100 >= settings.DEFAULT_PASS_SCORE
            if (a and exam_max > 0)
            else bool(a.is_passed if a else False)
        )
        items.append(
            {
                "exam_id": e.id,
                "name": e.name,
                "status": e.status.value,
                "duration_minutes": e.duration_minutes,
                "deadline": e.deadline,
                "instructions": e.instructions,
                "allow_retake": e.allow_retake,
                "max_retake_count": e.max_retake_count,
                # 本人答卷状态：not_started / in_progress / submitted
                "my_status": a.status if a else "not_started",
                "my_answer_id": a.id if a else None,
                "my_score": a.total_score if a else None,
                "my_max_score": exam_max,
                "my_score_percent": (
                    round((a.total_score or 0.0) / exam_max * 100, 1) if (a and exam_max > 0) else None
                ),
                "my_is_passed": passed if a else None,
                "my_submitted_at": a.submitted_at if a else None,
            }
        )

    return {"items": items, "total": len(items)}


@router.get("/records", summary="我的考试记录（个人中心 / 我的考试）")
async def my_exam_records(
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    user_id: Optional[str] = Query(None, description="管理员查看指定人员记录，默认本人"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """员工的「我的考试」与个人中心的成绩记录。

    spec 个人中心要求「我的考试记录（员工查看自己的历史成绩）」；此前只有
    /{exam_id}/status（单场考试），员工无法查看自己的全部记录，故补此只读接口。

    数据范围：默认仅返回本人答卷；hr_admin / it_admin 可通过 user_id 查看他人。
    """
    target_user = current_user.id
    if user_id and user_id != current_user.id:
        if current_user.role.value not in ("hr_admin", "it_admin"):
            raise HTTPException(status_code=403, detail="仅管理员可查看他人考试记录")
        target_user = user_id

    base = select(Answer).where(Answer.user_id == target_user)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar() or 0

    rows = (
        await db.execute(
            base.order_by(Answer.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).scalars().all()

    exam_ids = {a.exam_id for a in rows}
    exams: dict[str, Exam] = {}
    if exam_ids:
        exam_rows = (
            await db.execute(select(Exam).where(Exam.id.in_(exam_ids)))
        ).scalars().all()
        exams = {e.id: e for e in exam_rows}

    items = []
    for a in rows:
        exam = exams.get(a.exam_id)
        items.append(
            {
                "answer_id": a.id,
                "exam_id": a.exam_id,
                "exam_name": exam.name if exam else "（考试已删除）",
                "exam_status": exam.status.value if exam else None,
                "status": a.status,
                "total_score": a.total_score,
                "correct_count": a.correct_count,
                "wrong_count": a.wrong_count,
                "is_passed": a.is_passed,
                "duration_seconds": a.duration_seconds,
                "started_at": a.started_at,
                "submitted_at": a.submitted_at,
                "deadline": exam.deadline if exam else None,
            }
        )

    return {"items": items, "total": total}


@router.get("/{exam_id}/result/{answer_id}", summary="答卷成绩与逐题结果（移动端结果页）")
async def get_exam_result(
    exam_id: str,
    answer_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """移动端「考试结果」页数据：分数、通过状态、错题回顾所需逐题明细。

    判卷时 AnswerDetail 已持久化，这里直接读库：
    刷新页面、换设备、服务重启都不会丢结果（此前结果页是写死的假数据）。
    数据范围：仅本人答卷；hr_admin / it_admin 可查看他人。
    """
    result = await db.execute(select(Answer).where(Answer.id == answer_id))
    answer = result.scalar_one_or_none()
    if answer is None or answer.exam_id != exam_id:
        raise HTTPException(status_code=404, detail="答卷不存在")

    if answer.user_id != current_user.id and current_user.role.value not in (
        "hr_admin",
        "it_admin",
    ):
        raise HTTPException(status_code=403, detail="无权查看他人答卷")

    exam = (await db.execute(select(Exam).where(Exam.id == exam_id))).scalar_one_or_none()

    rows = (
        await db.execute(
            select(AnswerDetail)
            .where(AnswerDetail.answer_id == answer_id)
            .order_by(AnswerDetail.question_order)
        )
    ).scalars().all()

    q_ids = [r.question_id for r in rows]
    questions: dict[str, Question] = {}
    if q_ids:
        q_rows = (await db.execute(select(Question).where(Question.id.in_(q_ids)))).scalars().all()
        questions = {q.id: q for q in q_rows}

    details = []
    for idx, row in enumerate(rows, start=1):
        q = questions.get(row.question_id)
        details.append(
            {
                "order": row.question_order or idx,
                "question_id": row.question_id,
                "question_type": q.question_type.value if q else None,
                "content": q.content if q else "（题目已删除）",
                "user_answer": json.loads(row.user_answer) if row.user_answer else [],
                "correct_answer": json.loads(q.answer) if q and q.answer else [],
                "is_correct": bool(row.is_correct),
                "score": row.score,
                "source": f"{q.chapter_no or ''} {q.clause_no or ''}".strip() if q else "",
            }
        )

    max_score = float(sum((q.score or 0.0) for q in questions.values()))
    total = answer.total_score or 0.0
    score_percent = round(total / max_score * 100, 1) if max_score > 0 else 0.0

    return {
        "answer_id": answer.id,
        "exam_id": exam_id,
        "exam_name": exam.name if exam else "",
        "status": answer.status,
        "total_score": total,
        "max_score": max_score,
        "score_percent": score_percent,
        "correct_count": answer.correct_count or 0,
        "wrong_count": answer.wrong_count or 0,
        # 按得分率重算，避免历史答卷沿用旧的「绝对分」判定
        "is_passed": score_percent >= settings.DEFAULT_PASS_SCORE,
        "duration_seconds": answer.duration_seconds or 0,
        "submitted_at": answer.submitted_at,
        "pass_score": settings.DEFAULT_PASS_SCORE,
        "details": details,
    }


@router.get("/{exam_id}/status", summary="查询当前答卷状态")
async def get_exam_status(
    exam_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    result = await db.execute(
        select(Answer).where(
            Answer.exam_id == exam_id,
            Answer.user_id == current_user.id,
        ).order_by(Answer.created_at.desc()).limit(1)
    )
    answer = result.scalar_one_or_none()
    if answer is None:
        return {"status": "not_started"}

    return {
        "status": answer.status,
        "answer_id": answer.id,
        "total_score": answer.total_score,
        "is_passed": answer.is_passed,
        "submitted_at": answer.submitted_at,
    }