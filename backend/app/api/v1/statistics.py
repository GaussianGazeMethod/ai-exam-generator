"""S6 统计看板服务路由 — 全景 / 章节 / 部门 / 个人 / 题目 / 合规 / 导出。

对应 spec/页面层级树与核心服务模块.md S6 接口：
    GET /api/v1/statistics/exams/{examId}/overview     考试全景统计
    GET /api/v1/statistics/exams/{examId}/by-chapter   按章节分析
    GET /api/v1/statistics/exams/{examId}/by-dept      按部门分析
    GET /api/v1/statistics/exams/{examId}/by-person    按个人分析
    GET /api/v1/statistics/exams/{examId}/by-question  按题目分析
    GET /api/v1/statistics/exams/{examId}/export/pdf   导出合规报告
    GET /api/v1/statistics/exams/{examId}/export/excel 导出统计明细

角色视图隔离（spec M6 设计要点：四种角色看到不同视图，而非同一张看板的不同筛选）：
- HR/管理员   → 全公司数据
- 部门管理者   → 仅本部门（服务层用 dept_scope 收口）
- 合规/高层   → 合规全景 + 导出
- 制度专家    → 题目质量分析

全部聚合逻辑在 app/services/statistics_service.py，
本文件只负责路由、角色鉴权与响应封装。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, require_role
from app.models.exam import Exam, ExamStatus
from app.models.user import Department, User, UserRole
from app.services import statistics_service as svc
from app.services.audit import record_audit
from app.services.report_export import build_compliance_pdf, build_statistics_excel

logger = logging.getLogger(__name__)

router = APIRouter()


# ──────────────────────────────────────────────────────────
# 内部工具
# ──────────────────────────────────────────────────────────


async def _load_ctx(db: AsyncSession, exam_id: str) -> svc.ExamStatsContext:
    try:
        return await svc.load_exam_context(db, exam_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _role_of(user: User) -> str:
    return user.role.value if hasattr(user.role, "value") else str(user.role)


@dataclass(frozen=True)
class ViewPolicy:
    """角色视图策略 —— 把「数据范围」与「可见明细粒度」一起收口。

    spec/页面层级树与核心服务模块.md 第四节「页面 × 角色访问矩阵」：
        | HR全景看板   | | ● | | | | |
        | 部门统计     | | | | ● | | |
        | 合规全景     | | | | | ● | |
        | 题目质量分析 | ● | | | | | |
    以及 S6 设计要点：「角色视图隔离 —— HR 看全公司、部门管理者看本部门、
    合规看全景、专家看题目质量」，与「四种角色看到**不同视图**，
    而非同一张看板的不同筛选」。

    因此合规角色的入口是「合规全景」（聚合口径），
    不应通过 HR 看板拿到逐人的成绩明细；
    但 spec 同时要求合规报告 PDF「含本次活动记录（谁/时间/得分/用时/IP）」，
    故个人明细只在**导出**这一条受控路径上提供。
    """

    dept_scope: list[str] | None
    """数据范围：None=全公司；列表=限定部门。"""

    include_person_detail: bool
    """是否返回逐人成绩明细（HR 为真；合规/专家为假）。"""

    @property
    def scope_label(self) -> str:
        return "department" if self.dept_scope else "company"


def _view_policy_for(user: User) -> ViewPolicy:
    """按角色返回视图策略。"""
    role = _role_of(user)

    if role == UserRole.DEPT_MANAGER.value:
        # 部门管理者：仅本部门，可见本部门成员明细
        return ViewPolicy(
            dept_scope=[user.department_id] if user.department_id else ["__none__"],
            include_person_detail=True,
        )

    if role == UserRole.COMPLIANCE.value:
        # 合规/高层：看全景（聚合），不返回逐人明细（明细走合规报告导出）
        return ViewPolicy(dept_scope=None, include_person_detail=False)

    if role == UserRole.EXPERT.value:
        # 专家：看题目质量，不看个人成绩
        return ViewPolicy(dept_scope=None, include_person_detail=False)

    # HR/管理员：全公司 + 逐人明细
    return ViewPolicy(dept_scope=None, include_person_detail=True)


def _dept_scope_for(user: User) -> list[str] | None:
    """数据范围（保留旧签名，供既有调用点使用）。"""
    return _view_policy_for(user).dept_scope



def _content_disposition(filename: str, *, ascii_name: str) -> str:
    """构造既兼容老客户端又支持中文名的 Content-Disposition。

    HTTP 头只能承载 latin-1，中文文件名直接放入会抛
    ``UnicodeEncodeError: 'latin-1' codec can't encode characters``。
    按 RFC 5987 提供两段：``filename`` 给英文回退名，
    ``filename*`` 给 UTF-8 百分号编码的真实名（现代浏览器优先采用后者）。
    """
    encoded = quote(filename, safe="")
    return f"attachment; filename=\"{ascii_name}\"; filename*=UTF-8''{encoded}"


# ──────────────────────────────────────────────────────────
# HR 全景看板（聚合接口，一次返回看板所需全部数据）
# ──────────────────────────────────────────────────────────


@router.get("/exams/{exam_id}/dashboard", summary="HR 全景看板（聚合）")
async def exam_dashboard(
    exam_id: str,
    error_threshold: float = Query(
        svc.DEFAULT_ERROR_THRESHOLD, ge=0, le=100, description="题目错误率预警阈值"
    ),
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("hr_admin", "dept_manager", "compliance")),
):
    """HR 全景看板：概览 + 章节排行 + 部门排行 + 三张明细表。

    对应 spec HR 全景看板页：概览卡片 / 图表区 / 明细表（按部门、按个人、按题目）。
    """
    ctx = await _load_ctx(db, exam_id)
    policy = _view_policy_for(current_user)
    scope = policy.dept_scope

    return {
        "overview": svc.build_overview(ctx, dept_scope=scope),
        "chapter_stats": svc.build_chapter_stats(ctx, dept_scope=scope),
        "dept_stats": svc.build_dept_stats(ctx, dept_scope=scope),
        # 合规/专家不返回逐人明细 —— spec「合规看全景」「专家看题目质量」，
        # 且明确要求四种角色看到不同视图而非同一看板的不同筛选。
        # 合规的个人留痕通过合规报告 PDF 导出提供。
        "person_stats": (
            svc.build_person_stats(ctx, dept_scope=scope)
            if policy.include_person_detail
            else []
        ),
        "question_stats": svc.build_question_stats(ctx, error_threshold=error_threshold),
        "scope": policy.scope_label,
        "person_detail_available": policy.include_person_detail,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


# ──────────────────────────────────────────────────────────
# 单维度接口（spec 列出的标准接口）
# ──────────────────────────────────────────────────────────


@router.get("/exams/{exam_id}/overview", summary="考试全景统计")
async def exam_overview(
    exam_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("hr_admin", "dept_manager", "compliance")),
):
    ctx = await _load_ctx(db, exam_id)
    return svc.build_overview(ctx, dept_scope=_dept_scope_for(current_user))


@router.get("/exams/{exam_id}/by-chapter", summary="按章节分析")
async def by_chapter(
    exam_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("hr_admin", "expert", "dept_manager")),
):
    ctx = await _load_ctx(db, exam_id)
    return {"chapters": svc.build_chapter_stats(ctx, dept_scope=_dept_scope_for(current_user))}


@router.get("/exams/{exam_id}/by-dept", summary="按部门分析")
async def by_dept(
    exam_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("hr_admin", "dept_manager")),
):
    ctx = await _load_ctx(db, exam_id)
    return {"departments": svc.build_dept_stats(ctx, dept_scope=_dept_scope_for(current_user))}


@router.get("/exams/{exam_id}/by-person", summary="按个人分析")
async def by_person(
    exam_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("hr_admin", "dept_manager")),
):
    ctx = await _load_ctx(db, exam_id)
    return {"persons": svc.build_person_stats(ctx, dept_scope=_dept_scope_for(current_user))}


@router.get("/exams/{exam_id}/by-question", summary="按题目分析")
async def by_question(
    exam_id: str,
    error_threshold: float = Query(svc.DEFAULT_ERROR_THRESHOLD, ge=0, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("expert", "hr_admin")),
):
    ctx = await _load_ctx(db, exam_id)
    questions = svc.build_question_stats(ctx, error_threshold=error_threshold)
    return {
        "questions": questions,
        "needs_review_count": sum(1 for q in questions if q["needs_review"]),
        "error_threshold": error_threshold,
    }


# ──────────────────────────────────────────────────────────
# 专家视角：题目质量分析 + 反馈闭环
# ──────────────────────────────────────────────────────────


@router.get("/question-quality", summary="题目质量分析（专家视角，可跨考试）")
async def question_quality(
    exam_id: Optional[str] = Query(None, description="为空则汇总所有已发布考试"),
    error_threshold: float = Query(svc.DEFAULT_ERROR_THRESHOLD, ge=0, le=100),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("expert", "hr_admin")),
):
    """spec 题目质量分析页：高错误率题目列表（错误率 > 阈值，可配置）。

    不指定 exam_id 时跨全部已发布考试汇总，同一题在多场考试中的作答数合并。
    """
    if exam_id:
        exam_ids = [exam_id]
    else:
        rows = (
            await db.execute(
                select(Exam.id).where(
                    Exam.status.in_(
                        [ExamStatus.IN_PROGRESS, ExamStatus.FINISHED, ExamStatus.TERMINATED]
                    )
                )
            )
        ).scalars().all()
        exam_ids = list(rows)

    aggregated: dict[str, dict] = {}
    for eid in exam_ids:
        try:
            ctx = await svc.load_exam_context(db, eid)
        except ValueError:
            continue
        for row in svc.build_question_stats(ctx, error_threshold=error_threshold):
            key = row["question_id"]
            if key not in aggregated:
                aggregated[key] = {**row, "exam_ids": [eid]}
            else:
                prev = aggregated[key]
                total = prev["total_answers"] + row["total_answers"]
                correct = prev["correct_answers"] + row["correct_answers"]
                prev["total_answers"] = total
                prev["correct_answers"] = correct
                prev["correct_rate"] = round(correct / total * 100, 1) if total else 0.0
                prev["error_rate"] = round(100 - prev["correct_rate"], 1) if total else 0.0
                prev["needs_review"] = bool(
                    total > 0 and prev["error_rate"] >= error_threshold
                )
                prev["exam_ids"].append(eid)

    items = sorted(aggregated.values(), key=lambda x: x["error_rate"], reverse=True)
    flagged = [q for q in items if q["needs_review"]]
    return {
        "questions": flagged[:limit],
        "total_scanned": len(items),
        "needs_review_count": len(flagged),
        "error_threshold": error_threshold,
        "exam_ids": exam_ids,
    }


@router.post("/exams/{exam_id}/push-quality-feedback", summary="推送题目质量反馈至题库")
async def push_quality_feedback(
    exam_id: str,
    error_threshold: float = Query(svc.DEFAULT_ERROR_THRESHOLD, ge=0, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("expert", "hr_admin")),
):
    """把高错误率题目回写题库并标记「待复查」（spec 闭环反馈 M6 → S3）。"""
    ctx = await _load_ctx(db, exam_id)
    stats = svc.build_question_stats(ctx, error_threshold=error_threshold)
    flagged = await svc.push_quality_feedback(db, stats, error_threshold=error_threshold)
    return {
        "message": f"已标记 {len(flagged)} 道题目为「待复查」",
        "flagged_question_ids": flagged,
        "error_threshold": error_threshold,
    }


# ──────────────────────────────────────────────────────────
# 合规全景
# ──────────────────────────────────────────────────────────


@router.get("/compliance", summary="合规全景（制度传达验证一览）")
async def compliance_overview(
    period: Optional[str] = Query(
        None, description="时间范围：2026Q3（季度）或 2026（年度）；留空为全部"
    ),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("compliance", "hr_admin")),
):
    """spec 合规全景页：时间范围筛选 + 制度传达验证一览表。"""
    items = await svc.build_compliance_overview(db, period=period)
    return {
        "items": items,
        "period": period,
        "total": len(items),
        "fully_covered": sum(1 for i in items if i["status"] == "complete"),
        "pending": sum(1 for i in items if i["status"] != "complete"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


# ──────────────────────────────────────────────────────────
# 下钻：单份答卷明细
# ──────────────────────────────────────────────────────────


@router.get("/answers/{answer_id}", summary="答卷逐题明细（下钻）")
async def answer_detail(
    answer_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(
        require_role("hr_admin", "dept_manager", "compliance", "expert", "employee")
    ),
):
    detail = await svc.get_answer_detail(db, answer_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="答卷不存在")

    role = _role_of(current_user)
    # 员工只能看自己的答卷
    if role == UserRole.EMPLOYEE.value and detail["user_id"] != current_user.id:
        raise HTTPException(status_code=403, detail="只能查看自己的答卷")

    # 部门管理者只能看本部门答卷
    scope = _dept_scope_for(current_user)
    if scope is not None:
        owner = (
            await db.execute(select(User).where(User.id == detail["user_id"]))
        ).scalar_one_or_none()
        if owner is None or owner.department_id not in scope:
            raise HTTPException(status_code=403, detail="只能查看本部门的答卷")

    return detail


# ──────────────────────────────────────────────────────────
# 导出
# ──────────────────────────────────────────────────────────


async def _collect_export_data(db: AsyncSession, exam_id: str) -> dict:
    ctx = await _load_ctx(db, exam_id)
    return {
        "exam_overview": svc.build_overview(ctx),
        "dept_stats": svc.build_dept_stats(ctx),
        "person_stats": svc.build_person_stats(ctx),
        "chapter_stats": svc.build_chapter_stats(ctx),
        "question_stats": svc.build_question_stats(ctx),
    }


@router.get("/exams/{exam_id}/export/pdf", summary="导出合规报告 PDF")
async def export_pdf(
    request: Request,
    exam_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("hr_admin", "compliance")),
):
    data = await _collect_export_data(db, exam_id)
    await record_audit(
        db, action="export", user=_, target_type="exam", target_id=exam_id,
        detail={"format": "pdf", "kind": "compliance_report"}, request=request,
    )
    pdf_bytes = build_compliance_pdf(**data)
    exam = (await db.execute(select(Exam).where(Exam.id == exam_id))).scalar_one_or_none()
    filename = f"合规报告_{exam.name if exam else exam_id}_{datetime.now():%Y%m%d}.pdf"
    ascii_name = f"compliance_{exam_id}_{datetime.now():%Y%m%d}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": _content_disposition(filename, ascii_name=ascii_name),
            "Content-Length": str(len(pdf_bytes)),
        },
    )


@router.get("/exams/{exam_id}/export/excel", summary="导出统计明细 Excel")
async def export_excel(
    request: Request,
    exam_id: str,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("hr_admin", "compliance")),
):
    data = await _collect_export_data(db, exam_id)
    await record_audit(
        db, action="export", user=_, target_type="exam", target_id=exam_id,
        detail={"format": "excel", "kind": "statistics_detail"}, request=request,
    )
    xlsx_bytes = build_statistics_excel(**data)
    exam = (await db.execute(select(Exam).where(Exam.id == exam_id))).scalar_one_or_none()
    filename = f"统计明细_{exam.name if exam else exam_id}_{datetime.now():%Y%m%d}.xlsx"
    ascii_name = f"statistics_{exam_id}_{datetime.now():%Y%m%d}.xlsx"
    return Response(
        content=xlsx_bytes,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": _content_disposition(filename, ascii_name=ascii_name),
            "Content-Length": str(len(xlsx_bytes)),
        },
    )


@router.get("/compliance/export/pdf", summary="导出合规全景报告 PDF")
async def export_compliance_pdf(
    period: Optional[str] = Query(None),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("compliance", "hr_admin")),
):
    """spec 合规全景页的「一键导出合规报告 PDF」。

    spec S6 明确要求合规报告「含本次活动记录（谁/时间/得分/用时/IP）」——
    这是合规留痕的核心用途（场景五：一份 PDF 即合规证据）。
    因此这里必须把期内的**逐人考试记录**一并导出，而不只是聚合数字。
    这也是合规角色在界面上看不到逐人明细、却仍能拿到留痕记录的受控路径。
    """
    items = await svc.build_compliance_overview(db, period=period)

    # 收集期内所有考试（兼容 build_compliance_overview 返回的 exams 字段）
    exam_ids: list[str] = []
    for item in items:
        for e in item.get("exams") or []:
            eid = e.get("exam_id") or e.get("id")
            if eid and eid not in exam_ids:
                exam_ids.append(eid)

    # 逐人合规留痕（谁/时间/得分/用时/IP）
    person_records: list[dict] = []
    dept_bucket: dict[str, list[float]] = {}
    for eid in exam_ids:
        try:
            ctx = await svc.load_exam_context(db, eid)
        except ValueError:
            continue
        for p in svc.build_person_stats(ctx):
            p = dict(p)
            p["exam_name"] = ctx.exam.name
            person_records.append(p)
            dept_bucket.setdefault(p.get("dept_name") or "未分配部门", []).append(p.get("score", 0))

    person_records.sort(key=lambda x: (x.get("dept_name") or "", -x.get("score", 0)))

    total_completed = sum(i.get("completed_count", 0) for i in items) or len(person_records)
    total_candidate = sum(i.get("candidate_count", 0) for i in items)
    scores = [p.get("score", 0) for p in person_records]

    overview = {
        "exam_name": f"制度传达验证汇总（{period or '全部时间'}）",
        "exam_status": "汇总",
        "candidate_count": total_candidate or len(person_records),
        "completed_count": total_completed,
        "not_started_count": max(0, (total_candidate or len(person_records)) - total_completed),
        "average_score": round(sum(scores) / len(scores), 1) if scores else 0,
        "pass_rate": round(
            sum(1 for p in person_records if p.get("is_passed")) / max(len(person_records), 1) * 100,
            1,
        ),
        "highest_score": max(scores) if scores else 0,
        "lowest_score": min(scores) if scores else 0,
    }

    summary_rows = [
        {
            "rank": idx + 1,
            "dept_name": i["document_name"],
            "candidate_count": i.get("exam_count", 0),
            "completed_count": i.get("completed_count", 0),
            "not_started_count": i.get("not_started_count", 0),
            "average_score": i.get("pass_rate", 0),
            "pass_rate": i.get("coverage_rate", 0),
        }
        for idx, i in enumerate(items)
    ]

    pdf_bytes = build_compliance_pdf(
        exam_overview=overview,
        dept_stats=summary_rows,
        person_stats=person_records,
        chapter_stats=None,
        question_stats=None,
    )
    filename = f"合规全景报告_{period or '全部'}_{datetime.now():%Y%m%d}.pdf"
    ascii_name = f"compliance_overview_{period or 'all'}_{datetime.now():%Y%m%d}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={
            "Content-Disposition": _content_disposition(filename, ascii_name=ascii_name),
            "Content-Length": str(len(pdf_bytes)),
        },
    )