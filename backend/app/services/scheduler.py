"""后台定时任务 — M4 自动截止 / M5 答卷超时判定。

spec/核心模块架构设计.md 模块四（M4）：
    「自动截止 | 定时任务：到达截止时间后自动将考试置为『已截止』」

spec 模块五（M5）：
    「倒计时管理 | 服务端控制倒计时，到期自动提交」

实现方式（依据 spec/技术选型方案.md 2.8 异步任务）：
- 开发/原型：进程内 asyncio 周期任务（本模块），随 FastAPI lifespan 启停
- 生产：换成 Celery Beat 周期任务，判定逻辑（本模块函数）可直接复用

关键设计：
- 判定逻辑与调度机制**分离**：`sweep_expired_exams()` / `is_answer_expired()`
  是纯业务函数，既能被 asyncio 调度器调用，也能被 Celery 任务或手动接口调用。
- 服务端是超时的**唯一权威**：前端倒计时只是展示，
  提交时服务端必须重新判定，防止改本地时间绕过（spec「服务端控制倒计时」）。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models.exam import Exam, ExamStatus

logger = logging.getLogger(__name__)

# 调度间隔（秒）。考试截止以分钟为单位，30s 精度足够且开销极低。
SWEEP_INTERVAL_SECONDS = 30


# ──────────────────────────────────────────────────────────
# 纯业务判定（可被任意调度器复用）
# ──────────────────────────────────────────────────────────


def parse_deadline(raw: str | None) -> datetime | None:
    """宽松解析截止时间字符串。"""
    if not raw:
        return None
    text = str(raw).strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt == "%Y-%m-%d":
            dt = dt.replace(hour=23, minute=59, second=59)
        return dt
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def is_exam_expired(exam: Exam, now: datetime | None = None) -> bool:
    """考试是否已过截止时间。"""
    deadline = parse_deadline(exam.deadline)
    if deadline is None:
        return False  # 未设置截止时间 → 不自动截止
    return (now or datetime.now()) >= deadline


def answer_deadline(exam: Exam, started_at: str | None) -> datetime | None:
    """按「开始时间 + 考试时长」计算该份答卷的截止时刻。"""
    if not started_at:
        return None
    try:
        started = datetime.fromisoformat(str(started_at).replace("T", " "))
    except ValueError:
        return None
    return started + timedelta(minutes=max(1, exam.duration_minutes or 30))


def is_answer_expired(
    exam: Exam, started_at: str | None, *, now: datetime | None = None, grace_seconds: int = 0
) -> tuple[bool, int]:
    """答卷是否已超时。

    Returns:
        (是否超时, 剩余秒数)。剩余秒数为负表示已超时。

    grace_seconds 用于容忍网络延迟与前端时钟误差，避免「刚好卡点提交」被判超时。
    """
    now = now or datetime.now()
    deadline = answer_deadline(exam, started_at)
    if deadline is None:
        return False, 0
    remaining = int((deadline - now).total_seconds())
    return remaining < -grace_seconds, remaining


# ──────────────────────────────────────────────────────────
# 自动截止
# ──────────────────────────────────────────────────────────


async def sweep_expired_exams(db: AsyncSession | None = None) -> dict[str, Any]:
    """把所有已到期且仍「进行中」的考试置为「已截止」。

    同时也处理开始时间已过但迟迟没有提交的答卷（标记超时）。

    Returns:
        统计信息 {"closed_exams": [...], "closed_answers": n}
    """
    own_session = db is None
    session = db or async_session_factory()
    closed_exams: list[str] = []
    closed_answers = 0

    try:
        now = datetime.now()
        exams = list(
            (
                await session.execute(
                    select(Exam).where(Exam.status == ExamStatus.IN_PROGRESS)
                )
            )
            .scalars()
            .all()
        )

        for exam in exams:
            if not is_exam_expired(exam, now):
                continue
            exam.status = ExamStatus.FINISHED
            closed_exams.append(exam.name)

            # 该场考试中仍未提交、且已超时的答卷 → 标记为超时未交
            from app.models.answer import Answer

            stale = list(
                (
                    await session.execute(
                        select(Answer).where(
                            Answer.exam_id == exam.id, Answer.status == "in_progress"
                        )
                    )
                )
                .scalars()
                .all()
            )
            for ans in stale:
                expired, _remaining = is_answer_expired(exam, ans.started_at, now=now)
                if expired:
                    ans.status = "expired"
                    closed_answers += 1

        if closed_exams or closed_answers:
            await session.commit()
            logger.info(
                "自动截止：%s 场考试已关闭，%s 份答卷标记超时", len(closed_exams), closed_answers
            )
    finally:
        if own_session:
            await session.close()

    return {"closed_exams": closed_exams, "closed_answers": closed_answers}


# ──────────────────────────────────────────────────────────
# 进程内调度器
# ──────────────────────────────────────────────────────────

_scheduler_task: asyncio.Task[None] | None = None


async def _scheduler_loop() -> None:
    """周期执行到期扫描。异常不终止循环，只记日志。"""
    logger.info("到期扫描调度器已启动（间隔 %ss）", SWEEP_INTERVAL_SECONDS)
    while True:
        try:
            await asyncio.sleep(SWEEP_INTERVAL_SECONDS)
            result = await sweep_expired_exams()
            if result["closed_exams"]:
                logger.info("本轮自动截止：%s", result["closed_exams"])
        except asyncio.CancelledError:
            logger.info("到期扫描调度器已停止")
            raise
        except Exception as exc:  # noqa: BLE001 - 调度器必须容错，不能因单次失败退出
            logger.warning("到期扫描失败（将在下轮重试）：%s: %s", type(exc).__name__, exc)


def start_scheduler() -> None:
    """启动进程内调度器（幂等）。"""
    global _scheduler_task
    if _scheduler_task is not None and not _scheduler_task.done():
        return
    _scheduler_task = asyncio.create_task(_scheduler_loop())


async def stop_scheduler() -> None:
    """停止调度器（应用关闭时调用）。"""
    global _scheduler_task
    if _scheduler_task is None:
        return
    _scheduler_task.cancel()
    try:
        await _scheduler_task
    except asyncio.CancelledError:
        pass
    _scheduler_task = None