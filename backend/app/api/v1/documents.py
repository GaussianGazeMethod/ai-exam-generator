"""S1 制度文档服务路由 — 上传 / 解析 / 列表 / 详情 / 删除。

对应 spec/页面层级树与核心服务模块.md S1 接口：
    POST   /api/v1/documents            上传制度文档
    GET    /api/v1/documents            制度列表（支持筛选）
    GET    /api/v1/documents/{id}       制度详情（含章节树）
    POST   /api/v1/documents/{id}/parse 触发/重新解析
    DELETE /api/v1/documents/{id}       删除制度

真实能力（不再有假数据）：
- 上传后调用 DocumentParser 解析出真实章节树（python-docx / PyMuPDF + 编号规则引擎）
- 解析后把条款写入向量库，供 S2 出题时语义检索关联条款
- 删除制度时同步清理向量索引，避免残留
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, require_role
from app.config import get_settings
from app.models.document import DocStatus, Document
from app.models.question import Question
from app.models.user import User
from app.schemas.common import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE, MessageResponse
from app.schemas.document import DocumentListResponse, DocumentResponse
from app.services.audit import record_audit
from app.services.document_parser import get_document_parser
from app.services.generation_workflow import (
    parse_and_index_document,
    remove_document_vectors,
)

logger = logging.getLogger(__name__)

router = APIRouter()
settings = get_settings()


def _doc_to_response(doc: Document) -> DocumentResponse:
    return DocumentResponse(
        id=doc.id,
        name=doc.name,
        version=doc.version,
        file_name=doc.file_name,
        file_type=doc.file_type,
        file_size=doc.file_size,
        page_count=doc.page_count,
        status=doc.status.value if hasattr(doc.status, "value") else str(doc.status),
        structured_content=doc.structured_content,
        remark=doc.remark,
        question_count=doc.question_count,
        uploader_id=doc.uploader_id,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


@router.post("", response_model=DocumentResponse, summary="上传制度文档")
async def upload_document(
    request: Request,
    file: UploadFile,
    name: str = Query(..., description="制度名称"),
    version: str = Query("1.0"),
    remark: str = Query(""),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("expert")),
):
    """上传制度文档并立即解析出章节结构。

    依据 spec M1：保存文件 → 调用解析引擎 → 识别章/节/条款层级树。
    解析失败不阻塞上传（文档仍以「已上传」状态保留，可稍后重新解析）。
    """
    # 校验文件类型
    ext = os.path.splitext(file.filename or "")[1].lower()
    allowed = [e.strip() for e in settings.ALLOWED_UPLOAD_EXTENSIONS.split(",")]
    if ext not in allowed:
        raise HTTPException(status_code=400, detail=f"不支持的文件类型: {ext}，支持: {allowed}")

    content = await file.read()
    file_size = len(content)
    if file_size == 0:
        raise HTTPException(status_code=400, detail="上传文件为空")
    if file_size > settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024:
        raise HTTPException(
            status_code=400, detail=f"文件超过 {settings.MAX_UPLOAD_SIZE_MB}MB 限制"
        )

    # 保存文件（开发阶段存本地 ./uploads，生产切 OSS）
    os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
    safe_name = f"{uuid.uuid4().hex}{ext}"
    file_path = os.path.join(settings.UPLOAD_DIR, safe_name)
    with open(file_path, "wb") as fh:
        fh.write(content)

    doc = Document(
        name=name,
        version=version,
        file_name=file.filename or safe_name,
        file_path=file_path,
        file_size=file_size,
        file_type=ext.lstrip("."),
        remark=remark,
        uploader_id=current_user.id,
        status=DocStatus.UPLOADED,
    )
    db.add(doc)
    await db.flush()
    doc_id = doc.id
    await db.commit()

    # 真实解析（含向量索引）；失败则保持「已上传」状态，前端可手动重新解析
    ok, message = await parse_and_index_document(doc_id)
    if not ok:
        logger.warning("制度上传后自动解析失败 doc_id=%s：%s", doc_id, message)

    # 解析在独立会话中完成并提交，本会话的 identity map 仍持有旧对象，
    # 必须 refresh 才能读到最新状态（否则返回的还是「已上传」）
    await db.refresh(doc)

    await record_audit(
        db,
        action="upload",
        user=current_user,
        target_type="document",
        target_id=doc.id,
        detail={
            "name": doc.name,
            "version": doc.version,
            "file_name": doc.file_name,
            "file_size": doc.file_size,
            "parse_ok": ok,
        },
        request=request,
    )
    return _doc_to_response(doc)


@router.get("", response_model=DocumentListResponse, summary="制度列表")
async def list_documents(
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    status: Optional[str] = None,
    keyword: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    query = select(Document).order_by(Document.created_at.desc())
    if status:
        query = query.where(Document.status == status)
    if keyword:
        query = query.where(Document.name.contains(keyword))

    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    items = result.scalars().all()

    return DocumentListResponse(items=[_doc_to_response(d) for d in items], total=total)


@router.get("/{doc_id}", response_model=DocumentResponse, summary="制度详情（含章节树）")
async def get_document(
    doc_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="制度不存在")
    return _doc_to_response(doc)


@router.get("/{doc_id}/questions", summary="该制度关联的题目列表")
async def list_document_questions(
    doc_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """spec 制度详情页要求展示「已从该制度生成/入库的题目」。"""
    query = (
        select(Question)
        .where(Question.document_id == doc_id)
        .order_by(Question.created_at.desc())
    )
    total = (await db.execute(select(func.count()).select_from(query.subquery()))).scalar() or 0
    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    items = result.scalars().all()

    return {
        "items": [
            {
                "id": q.id,
                "content": q.content,
                "question_type": q.question_type.value
                if hasattr(q.question_type, "value")
                else str(q.question_type),
                "status": q.status.value if hasattr(q.status, "value") else str(q.status),
                "chapter_no": q.chapter_no,
                "clause_no": q.clause_no,
                "options": json.loads(q.options) if q.options else [],
                "answer": json.loads(q.answer) if q.answer else [],
            }
            for q in items
        ],
        "total": total,
    }


@router.post(
    "/{doc_id}/parse", response_model=MessageResponse, summary="触发/重新解析"
)
async def parse_document(
    request: Request,
    doc_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("expert")),
):
    """重新解析制度文档并重建向量索引。"""
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="制度不存在")

    ok, message = await parse_and_index_document(doc_id)
    if not ok:
        raise HTTPException(status_code=400, detail=message)

    # 同上：解析在独立会话提交，需 refresh 本会话的缓存对象
    await db.refresh(doc)
    await record_audit(
        db,
        action="parse",
        user=_,
        target_type="document",
        target_id=doc_id,
        detail={"name": doc.name, "message": message},
        request=request,
    )
    return MessageResponse(message=message)


@router.delete("/{doc_id}", response_model=MessageResponse, summary="删除制度")
async def delete_document(
    request: Request,
    doc_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("expert")),
):
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="制度不存在")

    # 清理关联题目，避免出现孤儿数据
    question_result = await db.execute(select(Question).where(Question.document_id == doc_id))
    for question in question_result.scalars().all():
        await db.delete(question)

    doc_name = doc.name
    cascaded = len(question_result.scalars().all())
    file_path = doc.file_path
    await db.delete(doc)
    await db.commit()

    # 清理磁盘文件与向量索引
    try:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
    except OSError as exc:
        logger.warning("删除制度文件失败 path=%s：%s", file_path, exc)
    await remove_document_vectors(doc_id)

    await record_audit(
        db,
        action="config",
        user=_,
        target_type="document",
        target_id=doc_id,
        detail={"deleted_document": doc_name, "cascaded_questions": cascaded},
        request=request,
    )
    return MessageResponse(message="删除成功")


@router.get("/{doc_id}/parse-preview", summary="解析结果预览（不落库）")
async def preview_parse(
    doc_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("expert")),
):
    """对已上传文件做一次解析并返回结果，用于专家手动修正章节树前的对照。"""
    result = await db.execute(select(Document).where(Document.id == doc_id))
    doc = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="制度不存在")

    try:
        parsed = get_document_parser().parse(doc.file_path, doc.name)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=400, detail=f"解析失败：{type(exc).__name__}: {str(exc)[:200]}"
        ) from exc

    return parsed.to_structured_content()