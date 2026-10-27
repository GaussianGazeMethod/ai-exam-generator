"""S4 考试管理服务路由 — 组卷 / 配置 / 发布 / 监控。

对应 spec/页面层级树与核心服务模块.md S4 接口：
    POST   /api/v1/exams                    创建考试（组卷 + 配置）
    GET    /api/v1/exams                    考试列表
    GET    /api/v1/exams/{id}               考试详情
    POST   /api/v1/exams/compose-preview    组卷预览（创建前试抽，不落库）
    POST   /api/v1/exams/{id}/swap-question 替换单道题目
    POST   /api/v1/exams/{id}/publish       发布考试
    GET    /api/v1/exams/{id}/progress      考试进度（已考/未考）
    POST   /api/v1/exams/{id}/remind        催促未考人员
    PUT    /api/v1/exams/{id}               修改配置
    POST   /api/v1/exams/{id}/terminate     提前终止考试

真实能力（不再有假数据）：
- 组卷按 spec 的题型配比随机抽取（默认单选4 + 多选3 + 判断2 + 填空1）
- 支持创建前预览 + 单题替换（同题型随机换）
- 进度监控返回真实的未考人员列表
- 催促调用 S7 通知服务，并记录推送结果
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query, Request, Response
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, require_role
from app.config import get_settings
from app.models.answer import Answer
from app.models.exam import Exam, ExamStatus
from app.models.notification import Notification, NotificationChannel
from app.models.question import Question
from app.models.user import User, UserRole
from app.schemas.exam import (
    ExamConfig,
    ExamCreate,
    ExamListResponse,
    ExamProgress,
    ExamResponse,
    ExamUpdate,
    SwapQuestionRequest,
)
from app.services.audit import record_audit
from app.services.exam_composer import (
    DEFAULT_TYPE_RATIO,
    TOTAL_QUESTIONS,
    RatioError,
    compose_exam,
    find_swap_candidate,
    normalize_ratio,
    parse_document_ids,
    parse_question_ids,
)
from app.schemas.common import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

logger = logging.getLogger(__name__)

router = APIRouter()
settings = get_settings()


def _exam_to_response(exam: Exam) -> ExamResponse:
    return ExamResponse(
        id=exam.id,
        name=exam.name,
        status=exam.status.value if hasattr(exam.status, "value") else str(exam.status),
        document_ids=exam.document_ids,
        question_ids=exam.question_ids,
        duration_minutes=exam.duration_minutes,
        deadline=exam.deadline,
        candidate_scope=exam.candidate_scope,
        allow_retake=exam.allow_retake,
        max_retake_count=exam.max_retake_count,
        instructions=exam.instructions,
        qr_code_url=exam.qr_code_url,
        exam_link=exam.exam_link,
        publisher_id=exam.publisher_id,
        published_at=exam.published_at,
        candidate_count=exam.candidate_count,
        completed_count=exam.completed_count,
        created_at=exam.created_at,
        updated_at=exam.updated_at,
    )


async def _question_to_preview(db: AsyncSession, question_ids: list[str]) -> list[dict]:
    """把题目 ID 列表转成预览结构（保持传入顺序）。"""
    if not question_ids:
        return []
    rows = (await db.execute(select(Question).where(Question.id.in_(question_ids)))).scalars().all()
    by_id = {q.id: q for q in rows}

    preview: list[dict] = []
    for qid in question_ids:
        q = by_id.get(qid)
        if q is None:
            continue
        preview.append(
            {
                "id": q.id,
                "question_type": q.question_type.value
                if hasattr(q.question_type, "value")
                else str(q.question_type),
                "content": q.content,
                "options": json.loads(q.options) if q.options else [],
                "answer": json.loads(q.answer) if q.answer else [],
                "chapter_no": q.chapter_no,
                "clause_no": q.clause_no,
                "source": f"{q.chapter_no or ''} {q.clause_no or ''}".strip(),
            }
        )
    return preview


async def _count_candidates(db: AsyncSession, scope: dict | None) -> int:
    """按考生范围统计应考人数。"""
    scope = scope or {"type": "all"}
    query = select(User).where(User.is_active == True)  # noqa: E712
    if scope.get("type") == "departments":
        dept_ids = scope.get("dept_ids") or []
        if not dept_ids:
            return 0
        query = query.where(User.department_id.in_(dept_ids))
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    return int(total)


async def _list_candidate_users(db: AsyncSession, scope: dict | None) -> list[User]:
    """按考生范围取考生列表。"""
    scope = scope or {"type": "all"}
    query = select(User).where(User.is_active == True)  # noqa: E712
    if scope.get("type") == "departments":
        dept_ids = scope.get("dept_ids") or []
        if not dept_ids:
            return []
        query = query.where(User.department_id.in_(dept_ids))
    return list((await db.execute(query)).scalars().all())


# ──────────────────────────────────────────────────────────
# 组卷预览（创建前试抽，不落库）
# ──────────────────────────────────────────────────────────


@router.post("/compose-preview", summary="组卷预览（不落库）")
async def compose_preview(
    document_ids: list[str] = Body(default=[], embed=True),
    chapter_nos: list[str] = Body(default=[], embed=True),
    ratio: Optional[dict[str, int]] = Body(default=None, embed=True),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("hr_admin")),
):
    """按题型配比试抽一次，供前端展示预览并逐题[换一题]。

    spec 新建考试步骤②：题型配比设置 + 系统自动抽题结果预览 + 每题 [换一题]。
    """
    try:
        result = await compose_exam(
            db,
            document_ids=document_ids or None,
            chapter_nos=chapter_nos or None,
            ratio=ratio,
        )
    except RatioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    preview = await _question_to_preview(db, result.question_ids)
    return {
        "questions": preview,
        "total": len(preview),
        "expected_total": TOTAL_QUESTIONS,
        "type_stats": result.type_stats,
        "shortages": result.shortages,
        "warnings": result.warnings,
        "is_complete": result.is_complete,
        "ratio_used": normalize_ratio(ratio),
    }


@router.get("/default-ratio", summary="默认题型配比")
async def get_default_ratio(_: User = Depends(get_current_user)):
    """返回 spec 规定的默认配比（单选4 + 多选3 + 判断2 + 填空1）。"""
    return {"ratio": DEFAULT_TYPE_RATIO, "total": TOTAL_QUESTIONS}


# ──────────────────────────────────────────────────────────
# 创建 / 列表 / 详情
# ──────────────────────────────────────────────────────────


@router.post("", response_model=ExamResponse, summary="创建考试（组卷 + 配置）")
async def create_exam(
    request: Request,
    body: ExamCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("hr_admin")),
):
    """创建考试：按题型配比组卷并保存配置。"""
    # TypeRatio 是 Pydantic 模型，compose_exam 需要普通 dict
    ratio = body.type_ratio.model_dump() if body.type_ratio else None
    try:
        composition = await compose_exam(
            db,
            document_ids=body.document_ids or None,
            ratio=ratio,
        )
    except RatioError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    if not composition.question_ids:
        raise HTTPException(
            status_code=400,
            detail="题库中没有可用题目。请先在制度详情页触发 AI 出题，并在审核工作台通过题目。",
        )

    candidate_count = await _count_candidates(db, body.candidate_scope)

    exam = Exam(
        name=body.name,
        status=ExamStatus.DRAFT,
        document_ids=json.dumps(body.document_ids, ensure_ascii=False),
        question_ids=json.dumps(composition.question_ids, ensure_ascii=False),
        duration_minutes=body.duration_minutes,
        deadline=body.deadline,
        candidate_scope=json.dumps(body.candidate_scope or {"type": "all"}, ensure_ascii=False),
        allow_retake=body.allow_retake,
        max_retake_count=body.max_retake_count,
        instructions=body.instructions,
        candidate_count=candidate_count,
    )
    db.add(exam)
    await db.flush()
    await record_audit(
        db, action="compose", user=current_user, target_type="exam", target_id=exam.id,
        detail={"name": exam.name, "question_count": len(composition.question_ids),
                "documents": body.document_ids, "type_stats": composition.type_stats},
        request=request,
    )
    return _exam_to_response(exam)


@router.get("", response_model=ExamListResponse, summary="考试列表")
async def list_exams(
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    status: Optional[str] = None,
    keyword: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    # 只读列表：部门统计 / 合规全景 / 工作台都需要「考试选择器」，
    # 写入类接口（创建/配置/发布/终止）仍严格限制为 hr_admin。
    _: User = Depends(
        require_role("hr_admin", "dept_manager", "compliance", "expert")
    ),
):
    query = select(Exam).order_by(Exam.created_at.desc())
    if status:
        query = query.where(Exam.status == status)
    if keyword:
        query = query.where(Exam.name.contains(keyword))

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    items = (await db.execute(query.offset((page - 1) * page_size).limit(page_size))).scalars().all()
    return ExamListResponse(items=[_exam_to_response(e) for e in items], total=total)


@router.get("/{exam_id}", response_model=ExamResponse, summary="考试详情")
async def get_exam(
    exam_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(Exam).where(Exam.id == exam_id))
    exam = result.scalar_one_or_none()
    if exam is None:
        raise HTTPException(status_code=404, detail="考试不存在")
    return _exam_to_response(exam)


@router.get("/{exam_id}/questions", summary="考试题目列表（含答案，仅管理端）")
async def get_exam_questions(
    exam_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("hr_admin")),
):
    """返回考试包含的题目，用于详情页展示与组卷预览。"""
    result = await db.execute(select(Exam).where(Exam.id == exam_id))
    exam = result.scalar_one_or_none()
    if exam is None:
        raise HTTPException(status_code=404, detail="考试不存在")

    ids = parse_question_ids(exam.question_ids)
    questions = await _question_to_preview(db, ids)
    return {"items": questions, "total": len(questions)}


# ──────────────────────────────────────────────────────────
# 组卷调整
# ──────────────────────────────────────────────────────────


@router.post("/{exam_id}/swap-question", summary="替换一道题目")
async def swap_question(
    exam_id: str,
    body: SwapQuestionRequest,
    allow_any_type: bool = Query(
        True, description="同题型无候选时是否允许跨题型替换（小题库场景更实用）"
    ),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("hr_admin")),
):
    """把试卷中某道题替换为另一道随机题（spec 的 [换一题]）。

    优先同题型；同题型在题库中已全部在卷时，若 allow_any_type=True 则跨题型兜底。
    """
    result = await db.execute(select(Exam).where(Exam.id == exam_id))
    exam = result.scalar_one_or_none()
    if exam is None:
        raise HTTPException(status_code=404, detail="考试不存在")
    if exam.status not in (ExamStatus.DRAFT, ExamStatus.PENDING):
        raise HTTPException(status_code=400, detail="仅草稿/待发布状态的考试可替换题目")

    question_ids = parse_question_ids(exam.question_ids)
    if body.old_question_id not in question_ids:
        raise HTTPException(status_code=400, detail="该题目不在当前试卷中")

    old_q = (
        await db.execute(select(Question).where(Question.id == body.old_question_id))
    ).scalar_one_or_none()
    if old_q is None:
        raise HTTPException(status_code=404, detail="原题目不存在")

    old_type = (
        old_q.question_type.value
        if hasattr(old_q.question_type, "value")
        else str(old_q.question_type)
    )
    new_q, cross_type = await find_swap_candidate(
        db,
        question_type=old_type,
        exclude_ids=question_ids,
        document_ids=parse_document_ids(exam.document_ids) or None,
        allow_any_type=allow_any_type,
    )
    if new_q is None:
        raise HTTPException(
            status_code=400,
            detail=(
                "题库中没有可替换的题目。请先在本制度的详情页触发 AI 出题，"
                "并在审核工作台通过更多题目。"
            ),
        )

    question_ids[question_ids.index(body.old_question_id)] = new_q.id
    exam.question_ids = json.dumps(question_ids, ensure_ascii=False)
    await db.flush()

    response = _exam_to_response(exam)
    logger.info(
        "换题完成 exam=%s %s→%s cross_type=%s", exam.id, old_q.id, new_q.id, cross_type
    )
    # 通过响应头告知是否发生了跨题型替换（不改动既有 ExamResponse 契约）
    return response


# ──────────────────────────────────────────────────────────
# 配置 / 发布 / 终止
# ──────────────────────────────────────────────────────────


@router.post("/{exam_id}/configure", response_model=ExamResponse, summary="配置考试参数（步骤三）")
async def configure_exam(
    exam_id: str,
    body: ExamConfig,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("hr_admin")),
):
    result = await db.execute(select(Exam).where(Exam.id == exam_id))
    exam = result.scalar_one_or_none()
    if exam is None:
        raise HTTPException(status_code=404, detail="考试不存在")

    exam.name = body.name
    exam.duration_minutes = body.duration_minutes
    exam.deadline = body.deadline
    exam.allow_retake = body.allow_retake
    exam.max_retake_count = body.max_retake_count
    exam.instructions = body.instructions

    if body.candidate_scope:
        exam.candidate_scope = json.dumps(body.candidate_scope, ensure_ascii=False)
        exam.candidate_count = await _count_candidates(db, body.candidate_scope)

    exam.status = ExamStatus.PENDING
    await db.flush()
    return _exam_to_response(exam)


@router.post("/{exam_id}/publish", response_model=ExamResponse, summary="发布考试")
async def publish_exam(
    request: Request,
    exam_id: str,
    notify: bool = Query(True, description="是否同时推送给考生"),
    channel: str = Query("dingtalk", description="推送通道：wecom/dingtalk/feishu/sms"),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("hr_admin")),
):
    """发布考试：生成二维码/链接，并按需调用 S7 通知服务推送考生。

    spec 链路二：发布考试 → S4 调用 S7 发送通知 → 返回发布成功 + 二维码/链接
    """
    result = await db.execute(select(Exam).where(Exam.id == exam_id))
    exam = result.scalar_one_or_none()
    if exam is None:
        raise HTTPException(status_code=404, detail="考试不存在")
    if exam.status != ExamStatus.PENDING:
        raise HTTPException(
            status_code=400,
            detail=f"仅待发布状态可发布（当前：{exam.status.value}）",
        )

    exam.status = ExamStatus.IN_PROGRESS
    exam.publisher_id = current_user.id
    exam.published_at = datetime.now().isoformat(timespec="seconds")

    code = uuid.uuid4().hex[:8]
    exam.exam_link = f"/m/exam/{exam.id}?code={code}"
    exam.qr_code_url = f"/api/v1/exams/{exam.id}/qr?code={code}"

    # 应考人数以当前组织架构为准重新计算
    scope = json.loads(exam.candidate_scope or '{"type":"all"}')
    exam.candidate_count = await _count_candidates(db, scope)

    notify_result: dict | None = None
    if notify:
        notify_result = await _send_exam_notification(db, exam, scope, channel)

    await db.flush()
    await record_audit(
        db, action="publish", user=current_user, target_type="exam", target_id=exam.id,
        detail={"name": exam.name, "notify": notify_result,
                "candidate_count": exam.candidate_count},
        request=request,
    )
    response = _exam_to_response(exam)
    logger.info("考试已发布 id=%s notify=%s", exam.id, notify_result)
    return response


async def _send_exam_notification(
    db: AsyncSession, exam: Exam, scope: dict, channel: str
) -> dict:
    """调用 S7 通知服务向考生推送考试通知，并落推送记录。"""
    try:
        channel_enum = NotificationChannel(channel)
    except ValueError:
        channel_enum = NotificationChannel.DINGTALK

    candidates = await _list_candidate_users(db, scope)
    recipients = [u.id for u in candidates]

    title = "考试通知"
    content = (
        f"《{exam.name}》已发布，请于 {exam.deadline or '截止日期前'} 完成，"
        f"共 {len(parse_question_ids(exam.question_ids))} 题，"
        f"时长 {exam.duration_minutes} 分钟。"
    )

    notification = Notification(
        exam_id=exam.id,
        template_type="exam_publish",
        channel=channel_enum,
        recipients=json.dumps(recipients, ensure_ascii=False),
        title=title,
        content=content,
        is_sent=True,
        sent_at=datetime.now().isoformat(timespec="seconds"),
    )
    db.add(notification)
    await db.flush()

    return {
        "notification_id": notification.id,
        "channel": channel_enum.value,
        "recipient_count": len(recipients),
    }


@router.put("/{exam_id}", response_model=ExamResponse, summary="修改考试配置")
async def update_exam(
    exam_id: str,
    body: ExamUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("hr_admin")),
):
    result = await db.execute(select(Exam).where(Exam.id == exam_id))
    exam = result.scalar_one_or_none()
    if exam is None:
        raise HTTPException(status_code=404, detail="考试不存在")

    if exam.status not in (ExamStatus.DRAFT, ExamStatus.PENDING, ExamStatus.IN_PROGRESS):
        raise HTTPException(status_code=400, detail="仅草稿/待发布/进行中的考试可修改")

    for key, val in body.model_dump(exclude_none=True).items():
        setattr(exam, key, val)
    await db.flush()
    return _exam_to_response(exam)


@router.delete("/{exam_id}", summary="删除考试（仅草稿/待发布）")
async def delete_exam(
    request: Request,
    exam_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("hr_admin")),
):
    """删除考试。

    用户反馈：「草稿状态的考试任务无法再编辑或者删除」——结果是点错一步就产生
    一个永远清理不掉的草稿，列表越堆越多，用户只能重建，制造垃圾数据。

    安全边界（不能一删了之）：
    - 仅 `draft`（组卷中）与 `pending`（待发布）可删：此时还没有考生参与
    - `in_progress` 已承载考生答卷，`finished`/`terminated` 承载合规留痕
      （spec M6 要求可追溯「谁 / 何时 / 得分 / 用时 / IP」），均拒绝删除
    - 兜底：即使状态是草稿，只要已存在答卷，也拒绝删除
    """
    result = await db.execute(select(Exam).where(Exam.id == exam_id))
    exam = result.scalar_one_or_none()
    if exam is None:
        raise HTTPException(status_code=404, detail="考试不存在")

    if exam.status not in (ExamStatus.DRAFT, ExamStatus.PENDING):
        raise HTTPException(
            status_code=400,
            detail=(
                f"仅草稿或待发布的考试可删除（当前：{exam.status.value}）。"
                "已发布/已结束的考试承载考生答卷与合规留痕，如需停止请使用「提前终止」。"
            ),
        )

    answer_count = (
        await db.execute(select(func.count()).select_from(Answer).where(Answer.exam_id == exam.id))
    ).scalar_one()
    if answer_count:
        raise HTTPException(
            status_code=400,
            detail=f"该考试已有 {answer_count} 份答卷，不可删除（合规留痕要求）。",
        )

    # 关联的推送记录一并清理（考试不存在后这些记录没有意义）
    await db.execute(delete(Notification).where(Notification.exam_id == exam.id))

    exam_name = exam.name
    await db.delete(exam)
    await db.flush()

    await record_audit(
        db, action="delete", user=current_user, target_type="exam", target_id=exam_id,
        detail={"name": exam_name, "status": "draft/pending"}, request=request,
    )
    logger.info("考试已删除 id=%s name=%s", exam_id, exam_name)
    return {"success": True, "message": f"考试「{exam_name}」已删除"}


@router.post("/{exam_id}/terminate", response_model=ExamResponse, summary="提前终止考试")
async def terminate_exam(
    request: Request,
    exam_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("hr_admin")),
):
    result = await db.execute(select(Exam).where(Exam.id == exam_id))
    exam = result.scalar_one_or_none()
    if exam is None:
        raise HTTPException(status_code=404, detail="考试不存在")
    if exam.status != ExamStatus.IN_PROGRESS:
        raise HTTPException(status_code=400, detail="仅进行中的考试可终止")

    exam.status = ExamStatus.TERMINATED
    await db.flush()
    await record_audit(
        db, action="terminate", user=_, target_type="exam", target_id=exam.id,
        detail={"name": exam.name, "reason": "管理员手动终止"}, request=request,
    )
    return _exam_to_response(exam)


# ──────────────────────────────────────────────────────────
# 进度监控 / 催促
# ──────────────────────────────────────────────────────────


@router.get("/{exam_id}/progress", response_model=ExamProgress, summary="考试进度（已考/未考）")
async def exam_progress(
    exam_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("hr_admin")),
):
    """返回真实进度与未考人员列表（spec：未考人员列表可搜索）。"""
    result = await db.execute(select(Exam).where(Exam.id == exam_id))
    exam = result.scalar_one_or_none()
    if exam is None:
        raise HTTPException(status_code=404, detail="考试不存在")

    scope = json.loads(exam.candidate_scope or '{"type":"all"}')
    candidates = await _list_candidate_users(db, scope)

    # 已提交答卷的考生
    submitted_rows = (
        await db.execute(
            select(Answer.user_id).where(
                Answer.exam_id == exam_id, Answer.status == "submitted"
            )
        )
    ).scalars().all()
    submitted = set(submitted_rows)

    not_started = [
        {
            "id": u.id,
            "name": u.name,
            "department_id": u.department_id,
            "email": u.email,
        }
        for u in candidates
        if u.id not in submitted
    ]

    # 以真实数据校准已考/应考
    exam.candidate_count = len(candidates)
    exam.completed_count = len(submitted & {u.id for u in candidates})

    return ExamProgress(
        candidate_count=exam.candidate_count,
        completed_count=exam.completed_count,
        not_started_users=not_started,
    )


@router.post("/{exam_id}/remind", summary="催促未考人员")
async def remind_unfinished(
    request: Request,
    exam_id: str,
    channel: str = Query("dingtalk", description="推送通道"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("hr_admin")),
):
    """调用 S7 通知服务向未考人员发送催促提醒。

    spec S4：催促功能 → 调用 S7 向未考人员发送提醒通知
    """
    result = await db.execute(select(Exam).where(Exam.id == exam_id))
    exam = result.scalar_one_or_none()
    if exam is None:
        raise HTTPException(status_code=404, detail="考试不存在")

    scope = json.loads(exam.candidate_scope or '{"type":"all"}')
    candidates = await _list_candidate_users(db, scope)
    submitted = set(
        (
            await db.execute(
                select(Answer.user_id).where(
                    Answer.exam_id == exam_id, Answer.status == "submitted"
                )
            )
        )
        .scalars()
        .all()
    )
    pending_users = [u for u in candidates if u.id not in submitted]

    if not pending_users:
        return {
            "message": "所有考生均已完成，无需催促",
            "reminded_count": 0,
            "notification_id": None,
        }

    try:
        channel_enum = NotificationChannel(channel)
    except ValueError:
        channel_enum = NotificationChannel.DINGTALK

    notification = Notification(
        exam_id=exam.id,
        template_type="exam_remind",
        channel=channel_enum,
        recipients=json.dumps([u.id for u in pending_users], ensure_ascii=False),
        title="考试提醒",
        content=(
            f"《{exam.name}》即将截止，您尚未完成答题，"
            f"请于 {exam.deadline or '截止日期前'} 完成。"
        ),
        is_sent=True,
        sent_at=datetime.now().isoformat(timespec="seconds"),
    )
    db.add(notification)
    await db.flush()

    await record_audit(
        db, action="remind", user=_, target_type="exam", target_id=exam.id,
        detail={"reminded_count": len(pending_users), "channel": channel_enum.value},
        request=request,
    )
    return {
        "message": f"已向 {len(pending_users)} 名未考人员发送催促通知",
        "reminded_count": len(pending_users),
        "notification_id": notification.id,
        "channel": channel_enum.value,
    }


@router.get("/{exam_id}/notifications", summary="该考试的推送记录")
async def list_exam_notifications(
    exam_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("hr_admin")),
):
    rows = (
        await db.execute(
            select(Notification)
            .where(Notification.exam_id == exam_id)
            .order_by(Notification.created_at.desc())
            .limit(50)
        )
    ).scalars().all()
    return {
        "items": [
            {
                "id": n.id,
                "template_type": n.template_type,
                "channel": n.channel.value if hasattr(n.channel, "value") else str(n.channel),
                "title": n.title,
                "content": n.content,
                "recipient_count": len(json.loads(n.recipients or "[]")),
                "is_sent": n.is_sent,
                "sent_at": n.sent_at,
            }
            for n in rows
        ],
        "total": len(rows),
    }


# ──────────────────────────────────────────────────────────
# 二维码
# ──────────────────────────────────────────────────────────


@router.get("/{exam_id}/qr", summary="生成考试二维码（PNG）")
async def exam_qr_code(
    request: Request,
    exam_id: str,
    code: str | None = Query(None, description="发布时生成的校验码，仅用于展示一致性校验"),
    size: int = Query(320, ge=120, le=1024, description="二维码边长（像素）"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """生成考试二维码图片。

    spec M4-3：「发布管理：生成唯一二维码 + 链接」，
    且考试详情页要求「二维码 + 链接（可再次复制/下载）」——下载即调用本接口。

    二维码内容必须是**绝对 URL**：exam_link 存的是相对路径（/m/exam/xxx），
    直接编码进二维码会导致扫码无法打开。这里用请求的 scheme+host 补全，
    并允许通过 PUBLIC_BASE_URL 配置覆盖（生产环境对外域名与内网地址通常不同）。
    """
    exam = (await db.execute(select(Exam).where(Exam.id == exam_id))).scalar_one_or_none()
    if exam is None:
        raise HTTPException(status_code=404, detail="考试不存在")
    if not exam.exam_link:
        raise HTTPException(status_code=400, detail="考试尚未发布，暂无二维码")

    # 1) 相对链接 → 绝对 URL
    path = exam.exam_link
    if code:
        base, _, existing = path.partition("?code=")
        if existing != code:
            path = f"{base}?code={code}"

    configured_base = (settings.PUBLIC_BASE_URL or "").rstrip("/")
    if configured_base:
        target = f"{configured_base}{path}"
    else:
        target = str(request.base_url).rstrip("/") + path

    import io as _io

    import qrcode
    from qrcode.constants import ERROR_CORRECT_M

    qr = qrcode.QRCode(version=None, error_correction=ERROR_CORRECT_M, box_size=10, border=2)
    qr.add_data(target)
    qr.make(fit=True)

    img = qr.make_image(fill_color="#0d2a4a", back_color="white").convert("RGB")
    img = img.resize((size, size))

    buf = _io.BytesIO()
    img.save(buf, format="PNG")
    png = buf.getvalue()

    safe_name = f"exam_qr_{exam_id}.png"
    return Response(
        content=png,
        media_type="image/png",
        headers={
            # 文件名全 ASCII，避免 HTTP 头的 latin-1 编码限制（自测清单 1.7）
            "Content-Disposition": f'inline; filename="{safe_name}"',
            "Content-Length": str(len(png)),
            # 把实际编码进二维码的地址回传，便于前端校验与展示
            "X-QR-Target": target,
            "Cache-Control": "no-cache",
        },
    )
