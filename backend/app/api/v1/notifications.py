"""S7 通知服务路由 — 推送 / 模板 / 通道。"""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import false, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db, require_role
from app.models.answer import Answer
from app.models.exam import Exam
from app.models.notification import Notification, NotificationChannel
from app.models.user import User
from app.schemas.common import MessageResponse

router = APIRouter()


@router.post("/send", response_model=MessageResponse, summary="发送通知（内部服务调用）")
async def send_notification(
    exam_id: str = Query(...),
    template_type: str = Query(..., description="exam_publish / exam_remind / report_ready"),
    channel: str = Query("dingtalk"),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(get_current_user),
):
    """发送通知并记录推送。

    接收人按考试配置的**考生范围**真实计算：

    - exam_publish → 全部考生
    - exam_remind  → 尚未提交答卷的考生
    - report_ready → 已完成答卷的考生

    原型阶段不外发到 IM，但接收人、内容与推送状态都落库，
    便于后续统计送达率（spec S7「推送记录：记录推送状态、推送时间」）。
    """
    exam = (await db.execute(select(Exam).where(Exam.id == exam_id))).scalar_one_or_none()
    if exam is None:
        raise HTTPException(status_code=404, detail="考试不存在")

    # 考生范围 → 候选人
    try:
        scope = json.loads(exam.candidate_scope or '{"type":"all"}')
    except json.JSONDecodeError:
        scope = {"type": "all"}

    query = select(User).where(User.is_active == True)  # noqa: E712
    if scope.get("type") == "departments":
        dept_ids = scope.get("dept_ids") or []
        query = query.where(User.department_id.in_(dept_ids)) if dept_ids else query.where(false())
    candidates = list((await db.execute(query)).scalars().all())

    submitted_ids = set(
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

    if template_type == "exam_remind":
        recipients = [u for u in candidates if u.id not in submitted_ids]
    elif template_type == "report_ready":
        recipients = [u for u in candidates if u.id in submitted_ids]
    else:
        recipients = candidates

    question_count = len(json.loads(exam.question_ids or "[]"))
    context = {
        "exam_name": exam.name,
        "deadline": exam.deadline or "截止日期前",
        "count": str(question_count),
        "duration": str(exam.duration_minutes),
    }

    templates = {
        "exam_publish": {
            "title": "考试通知",
            "content": "《{exam_name}》已发布，请于 {deadline} 前完成，共 {count} 题，时长 {duration} 分钟。",
        },
        "exam_remind": {
            "title": "考试提醒",
            "content": "《{exam_name}》即将截止，您尚未完成答题，请尽快完成。",
        },
        "report_ready": {
            "title": "统计报告已生成",
            "content": "《{exam_name}》统计报告已生成，可查看章节正确率与部门排名。",
        },
    }
    template = templates.get(template_type, templates["exam_publish"])

    try:
        channel_enum = NotificationChannel(channel)
    except ValueError:
        channel_enum = NotificationChannel.DINGTALK

    notification = Notification(
        exam_id=exam_id,
        template_type=template_type,
        channel=channel_enum,
        recipients=json.dumps([u.id for u in recipients], ensure_ascii=False),
        title=template["title"],
        content=template["content"].format(**context),
        is_sent=True,
        sent_at=datetime.now().isoformat(timespec="seconds"),
    )
    db.add(notification)
    await db.flush()

    return MessageResponse(
        message=f"通知已记录（原型阶段未外发到 IM），接收人 {len(recipients)} 名",
        detail={
            "notification_id": notification.id,
            "channel": channel_enum.value,
            "recipient_count": len(recipients),
            "template_type": template_type,
        },
    )


@router.get("/channels", summary="查询推送通道配置")
async def get_channels(_: User = Depends(get_current_user)):
    return {
        "channels": [
            {"id": "wecom", "name": "企业微信", "enabled": False},
            {"id": "dingtalk", "name": "钉钉", "enabled": True},
            {"id": "feishu", "name": "飞书", "enabled": False},
            {"id": "sms", "name": "短信", "enabled": False},
        ]
    }


@router.get("/templates", summary="查询消息模板")
async def get_templates(_: User = Depends(get_current_user)):
    return {
        "templates": [
            {"type": "exam_publish", "title": "考试通知", "content": "{{exam_name}} 已发布，请于 {{deadline}} 前完成，共 {{count}} 题。"},
            {"type": "exam_remind", "title": "考试提醒", "content": "{{exam_name}} 即将截止，您尚未完成，请尽快答题！"},
            {"type": "report_ready", "title": "统计报告已生成", "content": "{{exam_name}} 统计报告已生成，点击查看。"},
        ]
    }


@router.get("/inbox", summary="工作台消息中心（按角色聚合待办与提醒）")
async def get_inbox(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """把「属于当前角色的待处理事项 + 已推送消息」聚合成一个消息中心。

    评审稿工作台右上角有通知入口；这里不新增消息实体，而是把各模块
    已有的待办（待审核题目 / 过期考试 / 未考人数）与通知记录聚合，
    避免为原型再造一套消息表。
    """
    from sqlalchemy import func as _func

    from app.models.document import Document
    from app.models.exam import Exam, ExamStatus
    from app.models.question import Question, QuestionStatus

    role = current_user.role.value
    items: list[dict] = []

    if role == "expert":
        pending = (
            await db.execute(
                select(_func.count())
                .select_from(Question)
                .where(Question.status == QuestionStatus.PENDING)
            )
        ).scalar() or 0
        if pending:
            items.append(
                {
                    "type": "review",
                    "level": "warn",
                    "title": f"{pending} 道题目待审核",
                    "detail": "AI 已生成题目，等待专家逐题核对制度原文",
                    "link": "/review",
                }
            )
        docs = (
            await db.execute(
                select(Document).where(Document.question_count == 0).order_by(Document.created_at.desc()).limit(3)
            )
        ).scalars().all()
        for d in docs:
            items.append(
                {
                    "type": "document",
                    "level": "info",
                    "title": f"《{d.name}》尚未出题",
                    "detail": "解析完成，可触发 AI 出题",
                    "link": f"/documents/{d.id}",
                }
            )

    if role in ("hr_admin", "compliance"):
        exams = (
            await db.execute(
                select(Exam).where(
                    Exam.status.in_([ExamStatus.IN_PROGRESS, ExamStatus.PENDING])
                )
            )
        ).scalars().all()
        for e in exams:
            remaining = max((e.candidate_count or 0) - (e.completed_count or 0), 0)
            items.append(
                {
                    "type": "exam",
                    "level": "warn" if remaining else "info",
                    "title": f"{e.name} · 未考 {remaining} 人",
                    "detail": f"应考 {e.candidate_count} 人 / 已完成 {e.completed_count} 人",
                    "link": f"/exams/{e.id}",
                }
            )

        finished = (
            await db.execute(
                select(Exam)
                .where(Exam.status == ExamStatus.FINISHED)
                .order_by(Exam.updated_at.desc())
                .limit(3)
            )
        ).scalars().all()
        for e in finished:
            items.append(
                {
                    "type": "report",
                    "level": "success",
                    "title": f"{e.name} 统计报告可查看",
                    "detail": "考试已截止，可查看章节正确率与部门排名",
                    "link": f"/exams/{e.id}",
                }
            )

    if role == "dept_manager":
        items.append(
            {
                "type": "statistics",
                "level": "info",
                "title": "部门统计看板已就绪",
                "detail": "查看本部门完成率、平均分与薄弱章节",
                "link": "/statistics/dept",
            }
        )

    notifications = (
        await db.execute(select(Notification).order_by(Notification.created_at.desc()).limit(10))
    ).scalars().all()
    for n in notifications:
        items.append(
            {
                "type": "notification",
                "level": "info" if n.is_sent else "warn",
                "title": n.title,
                "detail": n.content,
                "link": f"/exams/{n.exam_id}" if n.exam_id else None,
                "created_at": n.created_at,
            }
        )

    return {
        "items": items,
        "total": len(items),
        "unread": sum(1 for i in items if i.get("level") in ("warn", "danger")),
    }