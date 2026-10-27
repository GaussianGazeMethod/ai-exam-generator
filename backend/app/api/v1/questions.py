"""S3 题库服务路由 — 审核 / CRUD / 上下架。"""

from __future__ import annotations
from typing import Optional

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, require_role
from app.models.question import Question, QuestionStatus
from app.models.user import User
from app.services.audit import record_audit
from app.schemas.question import (
    QuestionBatchReview,
    QuestionListResponse,
    QuestionResponse,
    QuestionReview,
    QuestionUpdate,
)
from app.schemas.common import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

router = APIRouter()


def _q_to_response(q: Question) -> QuestionResponse:
    return QuestionResponse(
        id=q.id,
        document_id=q.document_id,
        chapter_no=q.chapter_no,
        section_no=q.section_no,
        clause_no=q.clause_no,
        question_type=q.question_type.value,
        status=q.status.value,
        content=q.content,
        options=q.options,
        answer=q.answer,
        score=q.score,
        ai_raw_content=q.ai_raw_content,
        ai_raw_options=q.ai_raw_options,
        usage_count=q.usage_count,
        error_rate=q.error_rate,
        is_active=q.is_active,
        ai_quality_pass=getattr(q, "ai_quality_pass", None),
        ai_quality_note=getattr(q, "ai_quality_note", None),
        reject_reason=q.reject_reason,
        created_at=q.created_at,
        updated_at=q.updated_at,
    )


# ── 待审核 ──


@router.get("/pending", response_model=QuestionListResponse, summary="待审核题目列表")
async def list_pending(
    page: int = Query(1, ge=1),
    # 审核工作台需要一次取出整批题目以便上下题切换，故上限放宽到 500。
    # 此前限制为 100，而前端请求 200 会直接 422，导致工作台无法打开。
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    document_id: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("expert")),
):
    query = select(Question).where(Question.status == QuestionStatus.PENDING).order_by(Question.created_at.desc())
    if document_id:
        query = query.where(Question.document_id == document_id)

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    items = result.scalars().all()
    return QuestionListResponse(items=[_q_to_response(q) for q in items], total=total)


# ── 审核 ──


@router.post("/{question_id}/approve", response_model=QuestionResponse, summary="审核通过")
async def approve_question(
    request: Request,
    question_id: str,
    body: QuestionReview | None = None,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("expert")),
):
    result = await db.execute(select(Question).where(Question.id == question_id))
    q = result.scalar_one_or_none()
    if q is None:
        raise HTTPException(status_code=404, detail="题目不存在")

    if body and body.action == "edit_approve":
        # 编辑后通过
        if body.content is not None:
            q.content = body.content
        if body.options is not None:
            q.options = body.options
        if body.answer is not None:
            q.answer = body.answer

        # 记录修改历史
        history = json.loads(q.edit_history or "[]")
        history.append(
            {
                "reviewer_id": current_user.id,
                "time": datetime.now().isoformat(),
                "action": "edit_approve",
                "changes": {
                    "content": body.content is not None,
                    "options": body.options is not None,
                    "answer": body.answer is not None,
                },
            }
        )
        q.edit_history = json.dumps(history, ensure_ascii=False)

    q.status = QuestionStatus.APPROVED
    q.reviewer_id = current_user.id
    q.review_time = datetime.now().isoformat()
    await db.flush()
    await record_audit(
        db, action="review", user=current_user, target_type="question", target_id=q.id,
        detail={"result": "edit_approve" if (body and body.action == "edit_approve") else "approve",
                "question_type": q.question_type.value, "source": f"{q.chapter_no or ''} {q.clause_no or ''}".strip()},
        request=request,
    )
    return _q_to_response(q)


@router.post("/{question_id}/reject", response_model=QuestionResponse, summary="弃用题目")
async def reject_question(
    request: Request,
    question_id: str,
    reason: str = Query(""),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("expert")),
):
    result = await db.execute(select(Question).where(Question.id == question_id))
    q = result.scalar_one_or_none()
    if q is None:
        raise HTTPException(status_code=404, detail="题目不存在")

    q.status = QuestionStatus.REJECTED
    q.reviewer_id = current_user.id
    q.review_time = datetime.now().isoformat()
    q.reject_reason = reason or "未填写原因"
    await db.flush()
    await record_audit(
        db, action="review", user=current_user, target_type="question", target_id=q.id,
        detail={"result": "reject", "reason": q.reject_reason},
        request=request,
    )
    return _q_to_response(q)


@router.post("/batch-review", summary="批量审核")
async def batch_review(
    body: QuestionBatchReview,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("expert")),
):
    result = await db.execute(select(Question).where(Question.id.in_(body.question_ids)))
    questions = result.scalars().all()

    now = datetime.now().isoformat()
    for q in questions:
        if body.action == "approve":
            q.status = QuestionStatus.APPROVED
        elif body.action == "reject":
            q.status = QuestionStatus.REJECTED
        q.reviewer_id = current_user.id
        q.review_time = now

    await db.flush()
    return {"message": f"已处理 {len(questions)} 道题目", "action": body.action}


# ── 题库 CRUD ──


@router.get("", response_model=QuestionListResponse, summary="题库列表")
async def list_questions(
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    document_id: Optional[str] = None,
    question_type: Optional[str] = None,
    status: Optional[str] = None,
    keyword: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("expert")),
):
    query = select(Question).order_by(Question.created_at.desc())
    if document_id:
        query = query.where(Question.document_id == document_id)
    if question_type:
        query = query.where(Question.question_type == question_type)
    if status:
        query = query.where(Question.status == status)
    if keyword:
        query = query.where(Question.content.contains(keyword))

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    items = result.scalars().all()
    return QuestionListResponse(items=[_q_to_response(q) for q in items], total=total)


@router.get("/{question_id}", response_model=QuestionResponse, summary="题目详情")
async def get_question(
    question_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("expert")),
):
    result = await db.execute(select(Question).where(Question.id == question_id))
    q = result.scalar_one_or_none()
    if q is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    return _q_to_response(q)


@router.put("/{question_id}", response_model=QuestionResponse, summary="编辑题目")
async def update_question(
    question_id: str,
    body: QuestionUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("expert")),
):
    result = await db.execute(select(Question).where(Question.id == question_id))
    q = result.scalar_one_or_none()
    if q is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    for key, val in body.model_dump(exclude_none=True).items():
        setattr(q, key, val)
    await db.flush()
    return _q_to_response(q)


@router.put("/{question_id}/status", response_model=QuestionResponse, summary="上架/下架")
async def toggle_question_status(
    question_id: str,
    is_active: bool = Query(...),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("expert")),
):
    result = await db.execute(select(Question).where(Question.id == question_id))
    q = result.scalar_one_or_none()
    if q is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    q.is_active = is_active
    await db.flush()
    return _q_to_response(q)


@router.delete("/{question_id}", summary="删除题目")
async def delete_question(
    question_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("expert")),
):
    result = await db.execute(select(Question).where(Question.id == question_id))
    q = result.scalar_one_or_none()
    if q is None:
        raise HTTPException(status_code=404, detail="题目不存在")
    await db.delete(q)
    return {"message": "删除成功"}
