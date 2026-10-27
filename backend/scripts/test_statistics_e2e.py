"""S6 统计看板验证 — 端到端跑通「出题 → 组卷 → 发布 → 答题 → 统计 → 导出」。

在容器内运行：
    docker run --rm -v "<项目路径>/backend":/app/backend -w /app/backend \
        ai-exam-backend:1 python scripts/test_statistics_e2e.py

覆盖 spec S6 的全部能力：
- 考试全景 / 按章节 / 按部门 / 按个人 / 按题目
- 角色视图隔离（HR 全公司 vs 部门管理者本部门）
- 题目质量分析 + 反馈闭环（高错误率题目标记待复查）
- 合规全景（制度传达验证一览）
- 数据下钻（答卷逐题明细）
- 导出合规报告 PDF + 统计明细 Excel
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from sqlalchemy import select  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name)
    print(f"  {'[成功]' if ok else '[失败]'} {name}" + (f" — {detail}" if detail else ""))


def make_docx(path: str) -> None:
    import docx

    d = docx.Document()
    d.add_heading("统计验证制度", 0)
    d.add_heading("第一章 总则", level=1)
    d.add_paragraph("第一条 本制度适用于公司全体员工。")
    d.add_heading("第二章 账号管理", level=1)
    d.add_paragraph("第二条 员工账号密码长度不得少于 12 位，且须包含大小写字母与数字。")
    d.add_paragraph("第三条 禁止将个人账号借予他人使用，包括同事与上级。")
    d.add_heading("第三章 数据保护", level=1)
    d.add_paragraph("第四条 敏感数据必须采用 AES-256 算法加密后存储。")
    d.add_paragraph("第五条 禁止将客户数据存储在个人设备上，包括个人电脑、手机、U盘。")
    d.add_heading("第四章 违规处理", level=1)
    d.add_paragraph("第六条 违反本制度的，视情节给予警告直至解除劳动合同。")
    d.save(path)


async def login_as(c: httpx.AsyncClient, session_factory, User, external_id: str) -> dict:
    """按 external_id 找到用户并用其 user_id 登录，返回鉴权头。"""
    async with session_factory() as db:
        user = (
            await db.execute(select(User).where(User.external_id == external_id))
        ).scalar_one_or_none()
        if user is None:
            raise RuntimeError(f"测试用户不存在：{external_id}")
        user_id = user.id
    resp = await c.post("/api/v1/auth/dev-login", params={"user_id": user_id})
    if resp.status_code != 200:
        raise RuntimeError(f"登录失败 {external_id}: HTTP {resp.status_code} {resp.text[:120]}")
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def main() -> int:
    print("=" * 76)
    print("S6 统计看板端到端验证")
    print("=" * 76)

    for stale in ("./stats_test.db",):
        if os.path.exists(stale):
            os.remove(stale)
    os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///./stats_test.db"

    import app.config as config_module

    config_module.get_settings.cache_clear()

    from app.database import async_session_factory, init_db
    from app.main import app
    from app.models.user import Department, User, UserRole

    await init_db()

    # 预创建全部角色用户与部门。
    # 不用「先 dev-login 建用户、再改角色、再重登」的写法——那依赖两次请求间的
    # 会话可见性，脆弱且难排查。这里一次性建好，后续按 user_id 登录即可。
    async with async_session_factory() as db:
        if not (
            await db.execute(select(Department).where(Department.name == "技术部"))
        ).scalar_one_or_none():
            tech = Department(name="技术部")
            legal = Department(name="法务部")
            db.add_all([tech, legal])
            await db.flush()

            db.add_all(
                [
                    User(name="专家", role=UserRole.EXPERT,
                         external_id="t-expert", department_id=tech.id),
                    User(name="HR管理员", role=UserRole.HR_ADMIN,
                         external_id="t-hr", department_id=tech.id),
                    User(name="合规负责人", role=UserRole.COMPLIANCE,
                         external_id="t-compliance", department_id=legal.id),
                    User(name="员工甲", role=UserRole.EMPLOYEE,
                         external_id="emp-1", department_id=tech.id),
                    User(name="员工乙", role=UserRole.EMPLOYEE,
                         external_id="emp-2", department_id=tech.id),
                    User(name="员工丙", role=UserRole.EMPLOYEE,
                         external_id="emp-3", department_id=legal.id),
                ]
            )
            await db.commit()

    import uvicorn

    port = 8124
    server = uvicorn.Server(
        uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    )
    task = asyncio.create_task(server.serve())
    for _ in range(100):
        if getattr(server, "started", False):
            break
        await asyncio.sleep(0.1)

    try:
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}", timeout=300.0) as c:
            return await run(c, make_docx, async_session_factory, User, UserRole)
    finally:
        server.should_exit = True
        try:
            await asyncio.wait_for(task, timeout=10)
        except asyncio.TimeoutError:
            task.cancel()


async def run(c: httpx.AsyncClient, make_docx, session_factory, User, UserRole) -> int:
    # ── 准备：专家上传制度 + 出题 + 审核通过 ──
    print("\n【准备】专家：上传制度 → AI 出题 → 审核通过")
    eh = await login_as(c, session_factory, User, "t-expert")

    docx_path = "/tmp/stats_policy.docx"
    make_docx(docx_path)
    with open(docx_path, "rb") as fh:
        content = fh.read()
    r = await c.post(
        "/api/v1/documents",
        params={"name": f"统计验证制度-{int(asyncio.get_event_loop().time())}"},
        files={"file": ("p.docx", content, "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        headers=eh,
    )
    doc = r.json()
    print(f"       制度已上传：{doc['status']}")
    r = await c.post("/api/v1/generate/questions", params={"document_id": doc["id"], "max_points": 8}, headers=eh)
    tid = r.json()["task_id"]
    for _ in range(600):
        t = (await c.get(f"/api/v1/generate/tasks/{tid}", headers=eh)).json()
        if t["status"] in ("completed", "failed"):
            break
        await asyncio.sleep(0.6)
    print(f"       AI 出题：{t['status']}，{t['generated_count']} 题")

    pending = (await c.get("/api/v1/questions/pending", params={"page": 1, "page_size": 100}, headers=eh)).json()
    approved = 0
    for q in pending["items"]:
        rr = await c.post(f"/api/v1/questions/{q['id']}/approve", json={"action": "approve"}, headers=eh)
        if rr.status_code == 200:
            approved += 1
    print(f"       审核通过：{approved} 题")
    check("准备阶段完成", approved > 0, f"{approved} 题入库")

    # ── 准备：HR 组卷 + 发布 ──
    print("\n【准备】HR：组卷 → 发布")
    hh = await login_as(c, session_factory, User, "t-hr")

    r = await c.post("/api/v1/exams", json={
        "name": "统计验证考试", "document_ids": [doc["id"]], "duration_minutes": 30,
        "deadline": "2026-12-31 23:59", "candidate_scope": {"type": "all"},
        "allow_retake": False, "max_retake_count": 0, "instructions": "测试",
    }, headers=hh)
    exam = r.json()
    exam_id = exam["id"]
    r = await c.post(f"/api/v1/exams/{exam_id}/configure", json={
        "name": "统计验证考试", "duration_minutes": 30, "deadline": "2026-12-31 23:59",
        "candidate_scope": {"type": "all"}, "allow_retake": False, "max_retake_count": 0,
        "instructions": "测试",
    }, headers=hh)
    r = await c.post(f"/api/v1/exams/{exam_id}/publish", params={"notify": False}, headers=hh)
    check("考试已发布", r.status_code == 200 and r.json()["status"] == "in_progress")

    # ── 准备：3 名员工答题（其中 1 人故意全错，制造高错误率题目）──
    print("\n【准备】员工：答题提交（1 人全对 / 1 人全错 / 1 人部分对）")
    questions = (await c.get(f"/api/v1/exams/{exam_id}/questions", headers=hh)).json()["items"]
    submit_count = 0
    for idx, emp_ext in enumerate(["emp-1", "emp-2", "emp-3"]):
        ch = await login_as(c, session_factory, User, emp_ext)
        start = await c.post(f"/api/v1/exam-taking/{exam_id}/start", headers=ch)
        if start.status_code != 200:
            continue
        answer_id = start.json()["answer_id"]
        for q in questions:
            correct = q["answer"]
            if idx == 0:
                ans = correct                      # 全对
            elif idx == 1:
                ans = ["__wrong__"]                # 全错
            else:
                ans = correct if hash(q["id"]) % 2 == 0 else ["__wrong__"]  # 部分对
            await c.post(
                f"/api/v1/exam-taking/{exam_id}/answer",
                json={"question_id": q["id"], "answer": ans},
                headers=ch,
            )
        sub = await c.post(f"/api/v1/exam-taking/{exam_id}/submit", headers=ch)
        if sub.status_code == 200:
            submit_count += 1
            print(f"       员工{idx + 1} 提交：{sub.json()['total_score']} 分，"
                  f"对 {sub.json()['correct_count']} / 错 {sub.json()['wrong_count']}")
    check("有员工提交答卷", submit_count > 0, f"{submit_count} 份")

    # ── 1. HR 全景看板 ──
    print("\n【1】HR 全景看板（聚合接口）")
    r = await c.get(f"/api/v1/statistics/exams/{exam_id}/dashboard", headers=hh)
    check("看板可读", r.status_code == 200, f"HTTP {r.status_code}")
    if r.status_code != 200:
        print(f"       {r.text[:200]}")
        return 1
    d = r.json()
    ov = d["overview"]
    print(f"       概览：应考{ov['candidate_count']} 已考{ov['completed_count']} "
          f"平均分{ov['average_score']} 通过率{ov['pass_rate']}% "
          f"最高{ov['highest_score']} 最低{ov['lowest_score']}")
    check("概览字段完整",
          all(k in ov for k in ["candidate_count","completed_count","not_started_count",
                                "average_score","pass_rate","highest_score","lowest_score"]))
    check("已考人数与提交数一致", ov["completed_count"] == submit_count, f"{ov['completed_count']} vs {submit_count}")
    check("最高分 ≥ 最低分", ov["highest_score"] >= ov["lowest_score"])
    check("章节统计非空", len(d["chapter_stats"]) > 0, f"{len(d['chapter_stats'])} 个章节")
    check("部门统计非空", len(d["dept_stats"]) > 0, f"{len(d['dept_stats'])} 个部门")
    check("个人统计非空", len(d["person_stats"]) > 0, f"{len(d['person_stats'])} 人")
    check("题目统计非空", len(d["question_stats"]) > 0, f"{len(d['question_stats'])} 题")
    check("数据范围为全公司", d["scope"] == "company", d["scope"])

    # 部门维度校验：应考人数不应等于已考人数（旧实现用已考数代填的 bug）
    dept = d["dept_stats"][0]
    print(f"       部门示例：{dept['dept_name']} 应考{dept['candidate_count']} "
          f"已考{dept['completed_count']} 平均{dept['average_score']} 排名{dept['rank']}")
    check("部门含应考人数字段", "candidate_count" in dept)
    check("部门有排名", "rank" in dept)

    # 章节按正确率升序（最薄弱在前）
    if len(d["chapter_stats"]) > 1:
        rates = [x["correct_rate"] for x in d["chapter_stats"]]
        check("章节按正确率升序（薄弱在前）", rates == sorted(rates), str(rates))

    # 题目按错误率降序
    if len(d["question_stats"]) > 1:
        errs = [x["error_rate"] for x in d["question_stats"]]
        check("题目按错误率降序", errs == sorted(errs, reverse=True), str(errs[:4]))

    # ── 2. 单维度接口 ──
    print("\n【2】单维度接口")
    for path, key in [("overview", None), ("by-chapter", "chapters"),
                      ("by-dept", "departments"), ("by-person", "persons"),
                      ("by-question", "questions")]:
        rr = await c.get(f"/api/v1/statistics/exams/{exam_id}/{path}", headers=hh)
        ok = rr.status_code == 200
        size = len(rr.json().get(key, [])) if ok and key else "-"
        check(f"{path}", ok, f"HTTP {rr.status_code} 条目={size}")

    # ── 3. 数据下钻 ──
    print("\n【3】数据下钻：答卷逐题明细")
    persons = (await c.get(f"/api/v1/statistics/exams/{exam_id}/by-person", headers=hh)).json()["persons"]
    if persons:
        # 通过答卷接口找到 answer_id
        async with session_factory() as db:
            from app.models.answer import Answer
            ans = (await db.execute(select(Answer).where(Answer.exam_id == exam_id).limit(1))).scalar_one_or_none()
            aid = ans.id if ans else None
        if aid:
            rr = await c.get(f"/api/v1/statistics/answers/{aid}", headers=hh)
            check("答卷明细可读", rr.status_code == 200, f"HTTP {rr.status_code}")
            if rr.status_code == 200:
                detail = rr.json()
                print(f"       {detail['user_name']} / {detail['dept_name']} "
                      f"得分{detail['total_score']} 逐题明细{len(detail['details'])} 条")
                check("明细含逐题作答", len(detail["details"]) > 0)
                check("明细含正确答案对照",
                      all("correct_answer" in x and "user_answer" in x for x in detail["details"]))
                check("明细含合规字段（IP/用时）",
                      "ip_address" in detail and "duration_seconds" in detail)

    # ── 4. 角色视图隔离 ──
    print("\n【4】角色视图隔离（部门管理者只能看本部门）")
    async with session_factory() as db:
        mgr_user = (await db.execute(select(User).where(User.external_id == "emp-1"))).scalar_one()
        mgr_user.role = UserRole.DEPT_MANAGER
        mgr_dept = mgr_user.department_id
        await db.commit()
    mh = await login_as(c, session_factory, User, "emp-1")
    rr = await c.get(f"/api/v1/statistics/exams/{exam_id}/dashboard", headers=mh)
    check("部门管理者可读看板", rr.status_code == 200, f"HTTP {rr.status_code}")
    if rr.status_code == 200:
        md = rr.json()
        check("范围为 department", md["scope"] == "department", md["scope"])
        dept_ids = {x["dept_id"] for x in md["dept_stats"] if x["dept_id"]}
        check("只返回本部门数据", dept_ids <= {mgr_dept}, f"返回部门数={len(dept_ids)}")
        print(f"       部门管理者看到：应考{md['overview']['candidate_count']} "
              f"已考{md['overview']['completed_count']} 部门数{len(md['dept_stats'])}")

    # 部门管理者无权导出
    rr = await c.get(f"/api/v1/statistics/exams/{exam_id}/export/pdf", headers=mh)
    check("部门管理者无导出权限", rr.status_code == 403, f"HTTP {rr.status_code}")

    # ── 5. 题目质量分析 + 反馈闭环 ──
    print("\n【5】题目质量分析（专家视角）")
    rr = await c.get("/api/v1/statistics/question-quality", params={"error_threshold": 50}, headers=eh)
    check("质量分析可读", rr.status_code == 200, f"HTTP {rr.status_code}")
    if rr.status_code == 200:
        qq = rr.json()
        print(f"       扫描 {qq['total_scanned']} 题，其中 {qq['needs_review_count']} 题错误率 ≥50%")
        check("返回高错误率题目结构", isinstance(qq["questions"], list))

    rr = await c.post(
        f"/api/v1/statistics/exams/{exam_id}/push-quality-feedback",
        params={"error_threshold": 50}, headers=eh,
    )
    check("质量反馈推送成功", rr.status_code == 200, rr.json().get("message", "")[:60])

    # ── 6. 合规全景 ──
    print("\n【6】合规全景（制度传达验证）")
    ch2 = await login_as(c, session_factory, User, "t-compliance")
    rr = await c.get("/api/v1/statistics/compliance", headers=ch2)
    check("合规全景可读", rr.status_code == 200, f"HTTP {rr.status_code}")
    if rr.status_code == 200:
        cd = rr.json()
        print(f"       {cd['total']} 项制度，{cd['fully_covered']} 项全覆盖，{cd['pending']} 项待跟进")
        if cd["items"]:
            it = cd["items"][0]
            print(f"       示例：{it['document_name']} 覆盖率{it['coverage_rate']}% "
                  f"通过率{it['pass_rate']}% 状态{it['status_label']}")
            check("含覆盖率/通过率/状态", all(k in it for k in ["coverage_rate","pass_rate","status_label"]))

    # 季度筛选
    rr = await c.get("/api/v1/statistics/compliance", params={"period": "2026Q3"}, headers=ch2)
    check("季度筛选可用", rr.status_code == 200, f"HTTP {rr.status_code}")

    # ── 7. 导出 ──
    print("\n【7】导出合规报告 PDF + 统计明细 Excel")
    rr = await c.get(f"/api/v1/statistics/exams/{exam_id}/export/pdf", headers=hh)
    check("PDF 导出成功", rr.status_code == 200, f"HTTP {rr.status_code}")
    if rr.status_code == 200:
        pdf = rr.content
        check("PDF 文件有效", pdf[:4] == b"%PDF", f"{len(pdf)} 字节, 头={pdf[:4]!r}")
        check("Content-Type 正确", "application/pdf" in rr.headers.get("content-type", ""))
        check("含下载文件名", "attachment" in rr.headers.get("content-disposition", ""))
        open("/tmp/stats_report.pdf", "wb").write(pdf)
        # 校验中文未乱码
        import pymupdf
        doc_pdf = pymupdf.open(stream=pdf, filetype="pdf")
        # 合规留痕表在后续页，必须遍历全部页面而非只看第一页
        text = "".join(page.get_text() for page in doc_pdf)
        check("PDF 中文渲染正确", "合规报告" in text, f"页数={doc_pdf.page_count}")
        check("PDF 含合规留痕字段（谁/时间/得分/用时/IP）",
              all(f in text for f in ["姓名", "部门", "得分", "用时", "提交时间", "IP"]),
              f"页数={doc_pdf.page_count}")

    rr = await c.get(f"/api/v1/statistics/exams/{exam_id}/export/excel", headers=hh)
    check("Excel 导出成功", rr.status_code == 200, f"HTTP {rr.status_code}")
    if rr.status_code == 200:
        xlsx = rr.content
        check("Excel 文件有效", xlsx[:2] == b"PK", f"{len(xlsx)} 字节")
        open("/tmp/stats_report.xlsx", "wb").write(xlsx)
        from openpyxl import load_workbook
        wb = load_workbook("/tmp/stats_report.xlsx")
        print(f"       Sheet：{wb.sheetnames}")
        check("多 sheet 导出", len(wb.sheetnames) >= 4, str(wb.sheetnames))

    rr = await c.get("/api/v1/statistics/compliance/export/pdf", headers=ch2)
    check("合规全景 PDF 导出", rr.status_code == 200 and rr.content[:4] == b"%PDF",
          f"HTTP {rr.status_code}")

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