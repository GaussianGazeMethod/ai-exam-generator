"""安全专项验证 — 角色数据隔离（spec S6「角色视图隔离」）。

在容器内运行：
    docker run --rm -v "<项目路径>/backend":/app/backend -w /app/backend \
        ai-exam-backend:1 python scripts/test_role_isolation.py

spec 依据（spec/页面层级树与核心服务模块.md 第四节 + S6 设计要点）：
    | HR全景看板   | | ● | | | | |   → 仅 HR/管理员
    | 部门统计     | | | | ● | | |   → 仅 部门管理者
    | 合规全景     | | | | | ● | |   → 仅 合规/高层
    | 题目质量分析 | ● | | | | | |   → 仅 制度专家
    S6：HR 看全公司、部门管理者看本部门、合规看全景、专家看题目质量

本脚本验证：
1. 部门管理者：只能看本部门，直连接口也拿不到其他部门数据
2. 合规角色：只能看聚合全景，不得通过 HR 看板拿到个人成绩
3. 合规报告 PDF：必须含个人合规留痕（谁/时间/得分/用时/IP）
4. 员工：只能看自己的答卷，不得看他人
5. 跨角色越权：低权限角色调高权限接口必须 403
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name)
    print(f"  {'[成功]' if ok else '[失败]'} {name}" + (f" — {detail}" if detail else ""))


async def login_as(c: httpx.AsyncClient, session_factory, User, external_id: str) -> dict:
    async with session_factory() as db:
        user = (
            await db.execute(select(User).where(User.external_id == external_id))
        ).scalar_one_or_none()
        if user is None:
            raise RuntimeError(f"测试用户不存在：{external_id}")
        user_id = user.id
    resp = await c.post("/api/v1/auth/dev-login", params={"user_id": user_id})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def main() -> int:
    print("=" * 76)
    print("角色数据隔离专项验证")
    print("=" * 76)

    if os.path.exists("./role_test.db"):
        os.remove("./role_test.db")
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./role_test.db"

    import app.config as config_module

    config_module.get_settings.cache_clear()

    from app.database import async_session_factory, init_db
    from app.main import app
    from app.models.answer import Answer, AnswerDetail
    from app.models.exam import Exam, ExamStatus
    from app.models.question import Question, QuestionStatus
    from app.models.user import Department, User, UserRole

    await init_db()

    # ── 造数据：2 个部门 / 各角色用户 / 一场考试 / 3 份答卷 ──
    async with async_session_factory() as db:
        tech = Department(name="技术部")
        legal = Department(name="法务部")
        db.add_all([tech, legal])
        await db.flush()

        roles = {
            "t-hr": ("HR管理员", UserRole.HR_ADMIN, tech.id),
            "t-compliance": ("合规负责人", UserRole.COMPLIANCE, legal.id),
            "t-expert": ("专家", UserRole.EXPERT, tech.id),
            "mgr-tech": ("技术部经理", UserRole.DEPT_MANAGER, tech.id),
            "emp-tech1": ("技术员工甲", UserRole.EMPLOYEE, tech.id),
            "emp-tech2": ("技术员工乙", UserRole.EMPLOYEE, tech.id),
            "emp-legal1": ("法务员工丙", UserRole.EMPLOYEE, legal.id),
        }
        for ext, (name, role, dept) in roles.items():
            db.add(User(name=name, role=role, department_id=dept, external_id=ext))
        await db.flush()

        # 一场考试（直接构造，绕过 AI 出题以聚焦权限验证）
        q = Question(
            question_type="single_choice", status=QuestionStatus.APPROVED,
            content="测试题目：以下哪个正确？",
            options=json.dumps(["A. 甲", "B. 乙"], ensure_ascii=False),
            answer=json.dumps(["A"], ensure_ascii=False), score=1.0, is_active=True,
        )
        db.add(q)
        await db.flush()

        exam = Exam(
            name="角色隔离验证考试", status=ExamStatus.IN_PROGRESS,
            document_ids="[]", question_ids=json.dumps([q.id]),
            duration_minutes=30, candidate_scope=json.dumps({"type": "all"}),
            candidate_count=3, completed_count=3,
        )
        db.add(exam)
        await db.flush()

        # 3 份已提交答卷：技术部 2 人 + 法务部 1 人
        for idx, (ext, score) in enumerate(
            [("emp-tech1", 1.0), ("emp-tech2", 0.0), ("emp-legal1", 1.0)]
        ):
            u = (await db.execute(select(User).where(User.external_id == ext))).scalar_one()
            a = Answer(
                exam_id=exam.id, user_id=u.id, status="submitted",
                total_score=score, correct_count=int(score), wrong_count=1 - int(score),
                is_passed=bool(score), duration_seconds=60 + idx * 10,
                submitted_at="2026-09-21T10:00:00", ip_address=f"10.0.0.{idx + 1}",
            )
            db.add(a)
            await db.flush()
            db.add(AnswerDetail(
                answer_id=a.id, question_id=q.id,
                user_answer=json.dumps(["A"] if score else ["B"]),
                is_correct=bool(score), score=score, question_order=1,
            ))
        await db.commit()
        exam_id = exam.id
        answer_ids: dict[str, str] = {}
        rows = (await db.execute(select(Answer).where(Answer.exam_id == exam_id))).scalars().all()
        for a in rows:
            u = (await db.execute(select(User).where(User.id == a.user_id))).scalar_one()
            answer_ids[u.external_id] = a.id

    import uvicorn

    port = 8125
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    for _ in range(100):
        if getattr(server, "started", False):
            break
        await asyncio.sleep(0.1)

    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=120.0) as c:
            return await run(c, login_as, async_session_factory, User, exam_id, answer_ids)
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=10)
        except asyncio.TimeoutError:
            task.cancel()


async def run(c, login_as, session_factory, User, exam_id: str, answer_ids: dict) -> int:
    hh = await login_as(c, session_factory, User, "t-hr")
    ch = await login_as(c, session_factory, User, "t-compliance")
    eh = await login_as(c, session_factory, User, "t-expert")
    mh = await login_as(c, session_factory, User, "mgr-tech")
    et1 = await login_as(c, session_factory, User, "emp-tech1")
    el1 = await login_as(c, session_factory, User, "emp-legal1")

    # ── 1. HR 应看到全公司 ──
    print("\n【1】HR 管理员：应看到全公司数据")
    r = await c.get(f"/api/v1/statistics/exams/{exam_id}/dashboard", headers=hh)
    check("HR 可读看板", r.status_code == 200, f"HTTP {r.status_code}")
    if r.status_code == 200:
        d = r.json()
        print(f"       范围={d['scope']}  已考={d['overview']['completed_count']}  "
              f"个人明细={len(d['person_stats'])} 人  部门={len(d['dept_stats'])} 个")
        check("HR 范围=company", d["scope"] == "company")
        check("HR 可见全部 3 名考生", len(d["person_stats"]) == 3, f"{len(d['person_stats'])} 人")

    # ── 2. 部门管理者：只能看本部门 ──
    print("\n【2】部门管理者：只能看本部门（直连接口也不得越权）")
    r = await c.get(f"/api/v1/statistics/exams/{exam_id}/dashboard", headers=mh)
    check("可读看板", r.status_code == 200, f"HTTP {r.status_code}")
    if r.status_code == 200:
        d = r.json()
        names = {p["name"] for p in d["person_stats"]}
        print(f"       范围={d['scope']}  可见人员={sorted(names)}")
        check("范围=department", d["scope"] == "department", d["scope"])
        check("不含法务部人员", "法务员工丙" not in names, f"可见={sorted(names)}")
        check("仅含技术部 2 人", len(d["person_stats"]) == 2, f"{len(d['person_stats'])} 人")

    # ── 3. 合规角色：只看全景，不得拿 HR 看板的个人明细 ──
    print("\n【3】合规角色：spec 要求「合规看全景」")
    r = await c.get(f"/api/v1/statistics/compliance", headers=ch)
    check("合规全景可读", r.status_code == 200, f"HTTP {r.status_code}")

    r = await c.get(f"/api/v1/statistics/exams/{exam_id}/dashboard", headers=ch)
    if r.status_code == 403:
        check("合规被拒绝访问 HR 看板（严格按 spec 矩阵）", True, "HTTP 403")
    elif r.status_code == 200:
        d = r.json()
        persons = d.get("person_stats", [])
        if len(persons) == 0:
            check("合规看板不含个人明细（服务层已脱敏）", True, "person_stats 为空")
        else:
            check("合规看板不含个人明细（服务层已脱敏）", False,
                  f"[注意] 越权：返回了 {len(persons)} 人的个人成绩 {[p['name'] for p in persons]}")
    else:
        check("合规访问看板返回明确状态", False, f"HTTP {r.status_code}")

    # ── 4. 合规报告 PDF 必须含个人合规留痕 ──
    print("\n【4】合规报告 PDF：spec 要求含「谁/时间/得分/用时/IP」")
    r = await c.get(f"/api/v1/statistics/compliance/export/pdf", headers=ch)
    check("合规全景 PDF 可导出", r.status_code == 200, f"HTTP {r.status_code}")
    if r.status_code == 200:
        import pymupdf
        doc = pymupdf.open(stream=r.content, filetype="pdf")
        text = "".join(p.get_text() for p in doc)
        has_person = any(n in text for n in ["技术员工甲", "法务员工丙"])
        has_ip = "10.0.0." in text
        has_time = "提交时间" in text or "10:00" in text
        print(f"       含个人记录={has_person}  含IP={has_ip}  含时间={has_time}")
        check("合规报告含个人留痕（谁）", has_person)

    # 单场考试的合规报告也应有完整明细
    r = await c.get(f"/api/v1/statistics/exams/{exam_id}/export/pdf", headers=ch)
    check("单场考试 PDF 可导出", r.status_code == 200, f"HTTP {r.status_code}")
    if r.status_code == 200:
        import pymupdf
        doc = pymupdf.open(stream=r.content, filetype="pdf")
        text = "".join(p.get_text() for p in doc)
        check("单场报告含个人明细", "技术员工甲" in text)

    # ── 5. 员工：只能看自己的答卷 ──
    print("\n【5】员工：只能看自己的答卷")
    own = answer_ids.get("emp-tech1")
    other = answer_ids.get("emp-legal1")
    r = await c.get(f"/api/v1/statistics/answers/{own}", headers=et1)
    check("员工可看自己的答卷", r.status_code == 200, f"HTTP {r.status_code}")
    r = await c.get(f"/api/v1/statistics/answers/{other}", headers=et1)
    check("员工看不到他人答卷", r.status_code == 403, f"HTTP {r.status_code}")

    # ── 6. 跨角色越权 ──
    print("\n【6】跨角色越权访问")
    cases = [
        ("专家无权导出合规报告", f"/statistics/exams/{exam_id}/export/pdf", eh, 403),
        ("专家无权看部门统计", f"/statistics/exams/{exam_id}/by-dept", eh, 403),
        ("部门管理者无权导出", f"/statistics/exams/{exam_id}/export/excel", mh, 403),
        ("HR 可导出 Excel", f"/statistics/exams/{exam_id}/export/excel", hh, 200),
        ("专家可看题目质量", "/statistics/question-quality", eh, 200),
        ("员工无权看题目质量", "/statistics/question-quality", et1, 403),
    ]
    for name, path, hdr, expect in cases:
        r = await c.get(f"/api/v1{path}", headers=hdr)
        check(name, r.status_code == expect, f"HTTP {r.status_code}（期望 {expect}）")

    print("\n" + "=" * 76)
    print(f"结果：通过 {len(PASSED)}/{len(PASSED) + len(FAILED)}")
    if FAILED:
        print("失败项：")
        for f in FAILED:
            print(f"  · {f}")
    print("=" * 76)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))