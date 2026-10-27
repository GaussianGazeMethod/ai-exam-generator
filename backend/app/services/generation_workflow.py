"""出题编排服务 — 串起 S1 解析、知识库、S2 出题、S3 入库。

完整链路（对应 spec 链路一「制度上传 → AI 出题 → 专家审核」）：
    1. 读取制度文档，必要时用 DocumentParser 解析出章节树
    2. 把条款写入向量库（供出题时语义检索相关条款）
    3. 调用 QuestionGenerator 做考点提取 → 题目生成 → 质量校验
    4. 把生成的题目以「待审核」状态落库，等待专家在审核工作台处理

设计说明：
- 该函数作为 FastAPI BackgroundTasks 的执行体，自行开启数据库会话
  （请求级会话在响应返回后即关闭，不能复用）
- 进度通过 GenerationTaskRegistry 暴露，供 GET /generate/tasks/{id} 轮询
- 使用 asyncio.to_thread 包裹同步的向量库调用，避免阻塞事件循环
"""

from __future__ import annotations

import asyncio
import json
import logging

from sqlalchemy import select

from app.ai.question_generator import (
    QUALITY_ITEM_LABELS,
    GeneratedQuestion,
    get_question_generator,
)
from app.ai.vector_store import get_vector_store
from app.database import async_session_factory
from app.models.document import DocStatus, Document
from app.models.question import Question, QuestionStatus
from app.services.document_parser import get_document_parser
from app.services.generation_tasks import get_task_registry

logger = logging.getLogger(__name__)


def _to_question_model(question: GeneratedQuestion, document_id: str) -> Question:
    """把生成的题目转成 ORM 记录。"""
    return Question(
        document_id=document_id,
        chapter_no=question.chapter_no or None,
        section_no=question.section_no or None,
        clause_no=question.clause_no or None,
        question_type=question.question_type,
        status=QuestionStatus.PENDING,
        content=question.content,
        options=json.dumps(question.options, ensure_ascii=False),
        answer=json.dumps(question.answer, ensure_ascii=False),
        score=1.0,
        # 保留 AI 原始版本，供题库「AI 原始 vs 专家修改后」对比（spec M3 要求）
        ai_raw_content=question.content,
        ai_raw_options=json.dumps(question.options, ensure_ascii=False),
        # M2-3 的质量自查结论必须落库，供审核端展示
        ai_quality_pass=question.quality_pass,
        ai_quality_note=_format_quality_note(question.quality_detail),
    )


def _format_quality_note(detail: dict | None) -> str | None:
    """把质量校验的结构化结论压成一句话，便于审核端直接展示。

    优先使用 `summarize_quality()` 生成的 `note`（它已经按「以分项为准、严格优先」
    的口径组织好文字，并会标注模型总评与分项自相矛盾的情况）；
    仅在 `note` 缺失时按传统方式兜底，避免历史数据无法展示。
    """
    if not detail:
        return None

    note = detail.get("note")
    if note:
        return str(note)[:900]

    parts: list[str] = []
    if detail.get("reason"):
        parts.append(str(detail["reason"]))
    issues = detail.get("issues") or []
    if issues:
        parts.append("问题：" + "；".join(str(i) for i in issues[:3]))
    scores = detail.get("scores") or {}
    # 严格口径：分项缺失同样视为未通过（不能默认放行）
    failed = [label for key, label in QUALITY_ITEM_LABELS.items() if scores.get(key) is not True]
    if failed:
        parts.append("未通过项：" + "、".join(failed))
    return " ".join(parts)[:900] if parts else None


async def run_generation_task(
    task_id: str,
    document_id: str,
    *,
    max_points: int = 40,
    do_quality_check: bool = True,
) -> None:
    """后台任务主体：解析 → 建索引 → AI 出题 → 入库。"""
    registry = get_task_registry()
    registry.update(
        task_id, status="running", progress=5, message="正在读取制度文档"
    )

    try:
        async with async_session_factory() as db:
            # ── 1. 读取制度 ──
            result = await db.execute(select(Document).where(Document.id == document_id))
            doc = result.scalar_one_or_none()
            if doc is None:
                registry.finish(task_id, status="failed", message="制度不存在", progress=100)
                return

            if doc.status not in (DocStatus.PARSED, DocStatus.READY):
                registry.update(task_id, progress=10, message="正在解析文档结构")
                parsed = await asyncio.to_thread(
                    get_document_parser().parse, doc.file_path, doc.name
                )
                doc.structured_content = json.dumps(
                    parsed.to_structured_content(), ensure_ascii=False
                )
                doc.page_count = parsed.page_count
                doc.status = DocStatus.PARSED
                await db.flush()

            structured = json.loads(doc.structured_content or "{}")
            chapters = structured.get("chapters", [])
            if not chapters:
                registry.finish(
                    task_id, status="failed", message="文档未解析出章节结构，无法出题", progress=100
                )
                doc.status = DocStatus.PARSED
                await db.commit()
                return

            doc.status = DocStatus.GENERATING
            await db.flush()
            await db.commit()

            # ── 2. 建立向量索引（供出题时检索关联条款）──
            registry.update(task_id, progress=15, message="正在建立条款向量索引")
            indexed = await asyncio.to_thread(
                get_vector_store().index_document,
                doc_id=doc.id,
                doc_name=doc.name,
                chapters=chapters,
            )
            logger.info("制度 %s 已索引 %s 条条款", doc.name, indexed)

            # ── 3. AI 出题 ──
            registry.update(task_id, progress=25, message="正在提取考点并生成题目")

            def _retrieve(point) -> list[str]:
                """出题时检索同制度关联条款，保证覆盖全面（spec 知识库用途）。"""
                try:
                    return get_vector_store().search_related_clauses(
                        doc_id=doc.id,
                        query=f"{point.key_point}\n{point.clause_text}",
                        exclude_clause_no=point.clause_no,
                        top_k=3,
                    )
                except Exception as exc:  # noqa: BLE001 - 检索失败不应阻断出题
                    logger.warning("关联条款检索失败：%s", exc)
                    return []

            def _progress(done: int, total: int, message: str) -> None:
                # 出题阶段占进度 25% → 90%
                pct = 25 + int((done / max(total, 1)) * 65)
                registry.update(
                    task_id, progress=min(pct, 90), message=message, key_point_count=total
                )

            generator = get_question_generator()
            generation = await generator.generate_for_document(
                doc.name,
                chapters,
                max_points=max_points,
                do_quality_check=do_quality_check,
                retrieve_related=_retrieve,
                progress_cb=_progress,
            )

            # ── 4. 落库（待审核）──
            registry.update(task_id, progress=92, message="正在写入题库")
            for question in generation.questions:
                db.add(_to_question_model(question, doc.id))

            doc.status = DocStatus.READY
            doc.question_count = (doc.question_count or 0) + len(generation.questions)
            await db.commit()

            # ── 5. 完成 ──
            summary = (
                f"生成 {len(generation.questions)} 道待审核题目"
                + (f"，{len(generation.errors)} 项告警" if generation.errors else "")
            )
            registry.update(
                task_id,
                generated_count=len(generation.questions),
                key_point_count=generation.key_point_count,
                type_stats=generation.type_stats,
                errors=generation.errors[:20],
            )
            registry.finish(task_id, status="completed", message=summary, progress=100)
            logger.info("出题任务完成 task=%s doc=%s %s", task_id, doc.name, summary)

    except Exception as exc:  # noqa: BLE001 - 后台任务必须兜住所有异常
        logger.exception("出题任务失败 task=%s", task_id)
        registry.finish(
            task_id,
            status="failed",
            message=f"任务失败：{type(exc).__name__}: {str(exc)[:200]}",
            progress=100,
        )


async def parse_and_index_document(document_id: str) -> tuple[bool, str]:
    """解析制度文档并建立向量索引（供上传/重新解析接口复用）。

    Returns:
        (是否成功, 说明信息)
    """
    async with async_session_factory() as db:
        result = await db.execute(select(Document).where(Document.id == document_id))
        doc = result.scalar_one_or_none()
        if doc is None:
            return False, "制度不存在"

        try:
            doc.status = DocStatus.PARSING
            await db.flush()
            await db.commit()

            parsed = await asyncio.to_thread(
                get_document_parser().parse, doc.file_path, doc.name
            )
            structured = parsed.to_structured_content()
            doc.structured_content = json.dumps(structured, ensure_ascii=False)
            doc.page_count = parsed.page_count
            doc.status = DocStatus.PARSED
            await db.commit()

            # 建索引失败不应让整个解析失败（出题时检索是增强能力，非必需）
            indexed = await asyncio.to_thread(
                get_vector_store().index_document,
                doc_id=doc.id,
                doc_name=doc.name,
                chapters=structured["chapters"],
            )

            message = (
                f"解析完成：{parsed.chapter_count} 章 / {parsed.section_count} 节 / "
                f"{parsed.clause_count} 条，已索引 {indexed} 条条款"
            )
            if parsed.warnings:
                message += f"；{'；'.join(parsed.warnings[:2])}"
            return True, message

        except Exception as exc:  # noqa: BLE001
            logger.exception("解析制度失败 document_id=%s", document_id)
            doc.status = DocStatus.UPLOADED
            await db.commit()
            return False, f"解析失败：{type(exc).__name__}: {str(exc)[:200]}"


async def remove_document_vectors(document_id: str) -> None:
    """删除制度时同步清理向量索引。"""
    try:
        await asyncio.to_thread(get_vector_store().delete_document, document_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("清理向量索引失败 document_id=%s：%s", document_id, exc)