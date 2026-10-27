"""S2 AI 生成服务路由 — 触发出题（异步）/ 任务进度 / 题型分配策略。

对应 spec/页面层级树与核心服务模块.md S2 接口：
    POST /api/v1/generate/questions          对指定制度触发题目生成（异步任务）
    GET  /api/v1/generate/tasks/{taskId}     查询生成任务进度
    GET  /api/v1/generate/strategy           查询题型分配策略

真实能力（不再有假数据）：
- 触发后立即返回 task_id，出题在 BackgroundTasks 中执行，不阻塞前端
- 出题链路：文档解析 → 向量索引 → 考点提取 → 题目生成 → 质量校验 → 待审核入库
- 进度由 GenerationTaskRegistry 追踪，前端轮询 /tasks/{task_id}
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, require_role
from app.ai.agently_setup import effective_config, is_ai_available
from app.models.document import DocStatus, Document
from app.models.user import User
from app.services.generation_tasks import get_task_registry
from app.services.generation_workflow import run_generation_task

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/questions", summary="对指定制度触发题目生成（异步任务）")
async def trigger_generation(
    background_tasks: BackgroundTasks,
    document_id: str = Query(..., description="制度ID"),
    max_points: int = Query(40, ge=1, le=200, description="本次最多生成多少个考点"),
    quality_check: bool = Query(True, description="是否执行 AI 质量校验"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("expert")),
):
    """触发 AI 出题。

    立即返回 task_id，前端轮询 `/generate/tasks/{task_id}` 获取进度。
    spec 关键设计决策：大模型调用可能分钟级，必须异步以免阻塞前端。
    """
    if not is_ai_available():
        raise HTTPException(
            status_code=503, detail="AI 出题不可用：DEEPSEEK_API_KEY 未配置"
        )

    result = await db.execute(select(Document).where(Document.id == document_id))
    doc = result.scalar_one_or_none()
    if doc is None:
        raise HTTPException(status_code=404, detail="制度不存在")

    if doc.status == DocStatus.PARSING:
        raise HTTPException(status_code=409, detail="文档正在解析中，请稍后再触发出题")

    # 登记任务并立即返回
    task = get_task_registry().create(document_id=doc.id, document_name=doc.name)
    background_tasks.add_task(
        run_generation_task,
        task.task_id,
        doc.id,
        max_points=max_points,
        do_quality_check=quality_check,
    )

    logger.info("已触发出题任务 task=%s doc=%s(%s)", task.task_id, doc.name, doc.id)
    return {
        "task_id": task.task_id,
        "document_id": doc.id,
        "document_name": doc.name,
        "status": task.status,
        "message": "出题任务已创建，请通过 /generate/tasks/{task_id} 查询进度",
    }


@router.get("/tasks", summary="最近的出题任务列表")
async def list_generation_tasks(
    limit: int = Query(20, ge=1, le=100),
    _: User = Depends(get_current_user),
):
    tasks = get_task_registry().list_recent(limit=limit)
    return {"items": [t.to_dict() for t in tasks], "total": len(tasks)}


@router.get("/tasks/{task_id}", summary="查询生成任务进度")
async def get_generation_task(
    task_id: str,
    _: User = Depends(get_current_user),
):
    task = get_task_registry().get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="任务不存在或已过期")
    return task.to_dict()


@router.get("/strategy", summary="查询题型分配策略")
async def get_strategy(_: User = Depends(get_current_user)):
    """返回当前生效的题型分配策略（与 app/ai/prompts.py 中的规则保持一致）。"""
    return {
        "rules": [
            {
                "clause_pattern": "列举型条款",
                "keywords": ["包括", "如下", "以下", "之一", "等情形"],
                "recommended_type": "multi_choice",
                "reason": "条款列举多个要素，适合考察是否掌握完整范围",
            },
            {
                "clause_pattern": "定义型条款",
                "keywords": ["是指", "定义为", "含义为", "称为"],
                "recommended_type": "fill_blank",
                "reason": "考察关键术语的记忆，填空可精准定位薄弱点",
            },
            {
                "clause_pattern": "禁止/必须型条款",
                "keywords": ["不得", "严禁", "禁止", "必须", "应当"],
                "recommended_type": "true_false",
                "reason": "规则判定类条款，判断正误即可区分是否理解约束",
            },
            {
                "clause_pattern": "一般描述型条款",
                "keywords": [],
                "recommended_type": "single_choice",
                "reason": "考察对条款的理解程度",
            },
        ]
    }


@router.get("/status", summary="AI 能力状态")
async def get_ai_status(_: User = Depends(get_current_user)):
    """返回 AI 与知识库的可用状态（供系统管理页/健康检查使用，不含密钥）。"""
    from app.ai.agently_setup import is_embedding_available
    from app.ai.vector_store import get_vector_store

    try:
        vector_stats = get_vector_store().stats()
    except Exception as exc:  # noqa: BLE001
        vector_stats = {"available": False, "error": str(exc), "count": 0}

    return {
        "llm_available": is_ai_available(),
        "embedding_available": is_embedding_available(),
        "config": effective_config(),
        "vector_store": vector_stats,
    }
