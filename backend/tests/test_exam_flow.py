"""考试全链路真实业务测试（TDD）——发布试题 → 员工答题 → 角色看板呈现。

编写纪律：
- 断言来自 spec 与**用户可见的业务流程**，不来自现有实现
- 必须走真实接口，不用 ORM 直接造「进行中考试」绕过发布流程
  （历史教训：既有测试用夹具把考试直接建成 IN_PROGRESS，
   恰好绕过了「创建 → 发布」这段坏掉的链路，所以 bug 一直没被测出来）
- 失败必须是**因为业务不符**，而不是因为测试写错

覆盖的业务流程（用户实际会走的每一步）：
    ① HR 管理员配置并发布考试
    ② 员工在「我的考试」看到待考，扫码/链接进入答题
    ③ 员工逐题作答并提交，即时判卷
    ④ HR / 部门管理者 / 合规 三类角色看板各自呈现结果
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 测试库路径与 client/tokens 夹具见 tests/conftest.py


# ══════════════════════════════════════════════════════════
# 夹具
# ══════════════════════════════════════════════════════════

# spec 默认配比：单选4 + 多选3 + 判断2 + 填空1 = 10 题
_SPEC_QUESTIONS: list[tuple[str, list[str], list[str]]] = (
    [("single_choice", ["A. 甲", "B. 乙", "C. 丙", "D. 丁"], ["A"])] * 4
    + [("multi_choice", ["A. 甲", "B. 乙", "C. 丙", "D. 丁"], ["A", "B"])] * 3
    + [("true_false", ["正确", "错误"], ["正确"])] * 2
    + [("fill_blank", [], ["答案"])] * 1
)


@pytest.fixture(scope="session")
async def seeded_bank(client, tokens):
    """播种足量**已通过**题目（组卷只从已通过题目里抽）。"""
    from app.database import async_session_factory
    from app.models.question import Question, QuestionStatus

    async with async_session_factory() as db:
        ids = []
        for idx, (qt, opts, ans) in enumerate(_SPEC_QUESTIONS):
            q = Question(
                question_type=qt,
                status=QuestionStatus.APPROVED,
                content=(f"填空：请填写____。" if qt == "fill_blank" else f"考试用题目第{idx + 1}题？"),
                options=json.dumps(opts, ensure_ascii=False),
                answer=json.dumps(ans, ensure_ascii=False),
                score=1.0,
                is_active=True,
                chapter_no=f"第{idx % 3 + 1}章",
                clause_no=f"第{idx + 1}条",
                ai_quality_pass=True,
                ai_quality_note="（测试数据）AI 自查通过",
            )
            db.add(q)
            await db.flush()
            ids.append(q.id)
        await db.commit()
    return {"ids": ids, "answers": {q[0]: q[2] for q in _SPEC_QUESTIONS}}


# ══════════════════════════════════════════════════════════
# ① 发布链路：创建 → 配置 → 发布
# ══════════════════════════════════════════════════════════


class TestFlow1Publish:
    """用户流程①：HR 管理员配置考试并发布。

    spec 链路二：「发布考试 → S4 调用 S7 发送通知 → 返回发布成功 + 二维码/链接」
    spec M4-2：「发布考试：生成考试链接与二维码」
    """

    async def test_创建考试后无需人工干预即可发布(self, client, tokens, seeded_bank):
        """真实业务流程：新建考试 → 配置 → 发布，全程只用接口，不改数据库。

        立论：用户从「新建考试」向导一路点下来，最后一步必须能发布成功。
        若创建出的状态与发布接口的准入条件不一致，用户就会卡在
        「发布失败：Request failed with status code 400」，且无从得知原因。
        """
        # 创建（对应向导「配置参数」页提交）
        r = await client.post(
            "/api/v1/exams",
            headers=tokens["t-hr"],
            json={
                "name": "全链路-发布测试",
                "document_ids": [],
                "duration_minutes": 30,
                "deadline": "2026-12-31 23:59",
                "candidate_scope": {"type": "all"},
                "allow_retake": False,
                "max_retake_count": 0,
            },
        )
        assert r.status_code == 200, f"创建考试失败：{r.status_code} {r.text[:200]}"
        exam = r.json()
        exam_id = exam["id"]
        assert len(json.loads(exam["question_ids"])) == 10, "组卷应为 spec 默认 10 题"

        # 配置（对应向导「预览与发布」页确认配置）
        r = await client.post(
            f"/api/v1/exams/{exam_id}/configure",
            headers=tokens["t-hr"],
            json={
                "name": "全链路-发布测试",
                "duration_minutes": 30,
                "deadline": "2026-12-31 23:59",
                "candidate_scope": {"type": "all"},
                "allow_retake": False,
                "max_retake_count": 0,
            },
        )
        assert r.status_code == 200, f"配置考试失败：{r.status_code} {r.text[:200]}"

        # 发布
        r = await client.post(
            f"/api/v1/exams/{exam_id}/publish",
            headers=tokens["t-hr"],
            params={"notify": False},
        )
        assert r.status_code == 200, (
            f"发布失败：{r.status_code} {r.text[:300]}\n"
            "用户可见影响：向导走到最后一步点发布报 400，考试无法上线"
        )
        published = r.json()
        assert published["status"] == "in_progress", "发布后状态应为「进行中」"
        assert published["published_at"], "发布后应有发布时间"
        assert published["exam_link"], "spec M4-2 要求返回考试链接"
        assert published["qr_code_url"], "spec M4-2 要求返回二维码"

        # 二维码可访问且指向移动端
        r = await client.get(f"/api/v1/exams/{exam_id}/qr", headers=tokens["t-hr"])
        assert r.status_code == 200
        assert r.headers.get("content-type", "").startswith("image/png")
        # 指向移动答题端，员工扫码即可进入
        assert "/m/exam/" in (r.headers.get("x-qr-target") or published["exam_link"])

    async def test_草稿考试可编辑与删除(self, client, tokens, seeded_bank):
        """用户流程①的补充：草稿状态可继续编辑或删除。

        立论（来自真实用户反馈）：「草稿状态的考试任务无法再编辑或者删除」。
        实际后果：用户点错一步就产生一个永远删不掉的草稿，列表里越堆越多，
        且没有任何办法修正配置——只能重新建，制造垃圾数据。

        spec 页面层级树 M4：「考试列表（Draft/进行中/已完成）」，草稿是流程中的
        正常中间态，必须可控。
        """
        r = await client.post(
            "/api/v1/exams",
            headers=tokens["t-hr"],
            json={
                "name": "全链路-草稿可编辑",
                "document_ids": [],
                "duration_minutes": 30,
                "deadline": "2026-12-31 23:59",
                "candidate_scope": {"type": "all"},
            },
        )
        assert r.status_code == 200
        exam_id = r.json()["id"]

        # 编辑：改名称与时长
        r = await client.put(
            f"/api/v1/exams/{exam_id}",
            headers=tokens["t-hr"],
            json={"name": "全链路-草稿已改名", "duration_minutes": 45, "deadline": "2026-11-30 18:00"},
        )
        assert r.status_code == 200, f"草稿编辑失败：{r.status_code} {r.text[:200]}"
        edited = r.json()
        assert edited["name"] == "全链路-草稿已改名"
        assert edited["duration_minutes"] == 45
        assert edited["deadline"] == "2026-11-30 18:00"

        # 删除：草稿可删
        r = await client.delete(f"/api/v1/exams/{exam_id}", headers=tokens["t-hr"])
        assert r.status_code in (200, 204), (
            f"草稿删除失败：{r.status_code} {r.text[:200]}\n"
            "用户可见影响：草稿越堆越多且无法清理"
        )
        r = await client.get(f"/api/v1/exams/{exam_id}", headers=tokens["t-hr"])
        assert r.status_code == 404, "删除后应查不到"

    async def test_进行中的考试不得被删除(self, client, tokens, seeded_bank):
        """已发布的考试承载考生答卷与合规留痕，不能一删了之。"""
        r = await client.post(
            "/api/v1/exams",
            headers=tokens["t-hr"],
            json={"name": "全链路-已发布不可删", "document_ids": [], "duration_minutes": 30},
        )
        exam_id = r.json()["id"]
        await client.post(
            f"/api/v1/exams/{exam_id}/configure",
            headers=tokens["t-hr"],
            json={"name": "全链路-已发布不可删", "duration_minutes": 30},
        )
        await client.post(
            f"/api/v1/exams/{exam_id}/publish",
            headers=tokens["t-hr"],
            params={"notify": False},
        )
        r = await client.delete(f"/api/v1/exams/{exam_id}", headers=tokens["t-hr"])
        assert r.status_code == 400, (
            f"已发布考试不应允许删除（合规留痕要求），实际 {r.status_code}"
        )


# ══════════════════════════════════════════════════════════
# ②③ 员工答题链路
# ══════════════════════════════════════════════════════════


class TestFlow2EmployeeAnswering:
    """用户流程②③：员工看到待考 → 进入答题 → 提交判卷。"""

    @pytest.fixture(scope="class")
    async def live_exam(self, client, tokens, seeded_bank):
        """走真实「创建 → 配置 → 发布」，得到一场员工可答的考试。"""
        r = await client.post(
            "/api/v1/exams",
            headers=tokens["t-hr"],
            json={
                "name": "全链路-员工答题",
                "document_ids": [],
                "duration_minutes": 30,
                "deadline": "2026-12-31 23:59",
                "candidate_scope": {"type": "all"},
            },
        )
        assert r.status_code == 200, r.text[:200]
        exam_id = r.json()["id"]
        r = await client.post(
            f"/api/v1/exams/{exam_id}/configure",
            headers=tokens["t-hr"],
            json={
                "name": "全链路-员工答题",
                "duration_minutes": 30,
                "deadline": "2026-12-31 23:59",
                "candidate_scope": {"type": "all"},
            },
        )
        assert r.status_code == 200, r.text[:200]
        r = await client.post(
            f"/api/v1/exams/{exam_id}/publish",
            headers=tokens["t-hr"],
            params={"notify": False},
        )
        assert r.status_code == 200, (
            f"员工答题测试的前置条件「发布」失败：{r.status_code} {r.text[:200]}"
        )
        return r.json()

    async def test_员工在我的考试看到待考(self, client, tokens, live_exam):
        """spec S5「我的考试」：员工必须能看到已发布的待考考试。"""
        r = await client.get("/api/v1/exam-taking/my-exams", headers=tokens["t-employee"])
        assert r.status_code == 200
        items = r.json()["items"]
        target = next((i for i in items if i["exam_id"] == live_exam["id"]), None)
        assert target is not None, (
            "员工在「我的考试」里看不到刚发布的考试 —— 无法进入答题流程"
        )
        assert target["status"] == "in_progress"
        assert target["my_status"] == "not_started", "尚未作答应为「未作答」"

    async def test_员工开始答题_拿到10题且不含答案(self, client, tokens, live_exam):
        """spec M5「题目不含正确答案」（防作弊）。"""
        r = await client.post(
            f"/api/v1/exam-taking/{live_exam['id']}/start", headers=tokens["t-employee"]
        )
        assert r.status_code == 200, f"开始答题失败：{r.status_code} {r.text[:200]}"
        body = r.json()
        assert body["question_count"] == 10, "spec：每场考试 10 题"
        assert len(body["questions"]) == 10
        assert body["answer_id"], "应返回答卷 id（移动端路由需要）"
        for q in body["questions"]:
            assert "answer" not in q or not q.get("answer"), (
                f"issuing 给考生的题目泄露了正确答案：{q.get('content')}"
            )

    async def test_员工逐题作答并提交_得到分数(self, client, tokens, live_exam, seeded_bank):
        """spec 场景说明文档：提交后即时判卷给分（≥60 分通过）。"""
        r = await client.post(
            f"/api/v1/exam-taking/{live_exam['id']}/start", headers=tokens["t-employee"]
        )
        assert r.status_code == 200
        start = r.json()
        answer_id = start["answer_id"]

        # 逐题作答：全部按题库里的正确答案作答 → 应得满分
        for q in start["questions"]:
            correct = seeded_bank["answers"].get(q["question_type"])
            # 选项顺序会被打乱，需要按内容找到「正确答案」对应的字母
            payload_answer = _pick_answer(q, seeded_bank)
            r = await client.post(
                f"/api/v1/exam-taking/{live_exam['id']}/answer",
                headers=tokens["t-employee"],
                json={"answer_id": answer_id, "question_id": q["id"], "answer": payload_answer},
            )
            assert r.status_code == 200, (
                f"保存作答失败：{r.status_code} {r.text[:200]}（题 {q['question_type']}）"
            )
            assert correct is not None  # 保留引用，防止 lint 误删

        r = await client.post(
            f"/api/v1/exam-taking/{live_exam['id']}/submit",
            headers=tokens["t-employee"],
            json={"answer_id": answer_id},
        )
        assert r.status_code == 200, f"提交答卷失败：{r.status_code} {r.text[:200]}"
        result = r.json()
        assert result["max_score"] > 0, "应返回满分，供前端展示「X 分 / 满分」"
        assert result["total_score"] == pytest.approx(result["max_score"]), (
            f"全部按正确答案作答却未得满分：{result['total_score']} / {result['max_score']}"
        )
        assert result["is_passed"] is True, "满分应判定为通过"
        assert result["correct_count"] == 10
        assert result["details"], "应返回逐题结果（含条款说明）"

    async def test_提交后我的考试显示已提交与分数(self, client, tokens, live_exam):
        """员工回到「我的考试」应看到已提交状态与分数。"""
        r = await client.get("/api/v1/exam-taking/my-exams", headers=tokens["t-employee"])
        assert r.status_code == 200
        target = next(
            (i for i in r.json()["items"] if i["exam_id"] == live_exam["id"]), None
        )
        assert target is not None
        assert target["my_status"] == "submitted", f"应显示已提交，实际 {target['my_status']}"
        assert target["my_is_passed"] is True
        assert target["my_score"] is not None


def _pick_answer(question: dict, seeded_bank: dict) -> list[str]:
    """根据题目内容/选项，挑出正确答案。

    组卷会给每场考试随机打乱选项顺序，因此不能硬编码字母，必须按选项文本反查。
    """
    qtype = question["question_type"]
    options = question.get("options") or []
    if qtype == "fill_blank":
        return ["答案"]
    if qtype == "true_false":
        return ["正确"]
    if qtype == "multi_choice":
        # 选项文本形如 "A. 甲"，正确项为「甲」「乙」
        letters = [o.split(".")[0].strip() for o in options if o.split(".")[-1].strip() in ("甲", "乙")]
        return letters
    letters = [o.split(".")[0].strip() for o in options if o.split(".")[-1].strip() == "甲"]
    return letters


# ══════════════════════════════════════════════════════════
# ④ 三类角色看板呈现
# ══════════════════════════════════════════════════════════


class TestFlow3RoleDashboards:
    """用户流程④：答卷提交后，三类角色看板各自呈现结果。

    依赖前面的类已把考试发布并让员工提交（pytest 按文件中类定义顺序执行）。
    """

    @pytest.fixture(scope="class")
    async def submitted_exam_id(self, client, tokens, seeded_bank):
        """独立造一场「已发布 + 员工已提交」的考试，避免依赖其它类的执行顺序。"""
        r = await client.post(
            "/api/v1/exams",
            headers=tokens["t-hr"],
            json={
                "name": "全链路-看板呈现",
                "document_ids": [],
                "duration_minutes": 30,
                "deadline": "2026-12-31 23:59",
                "candidate_scope": {"type": "all"},
            },
        )
        assert r.status_code == 200, r.text[:200]
        exam_id = r.json()["id"]
        await client.post(
            f"/api/v1/exams/{exam_id}/configure",
            headers=tokens["t-hr"],
            json={
                "name": "全链路-看板呈现",
                "duration_minutes": 30,
                "deadline": "2026-12-31 23:59",
                "candidate_scope": {"type": "all"},
            },
        )
        r = await client.post(
            f"/api/v1/exams/{exam_id}/publish",
            headers=tokens["t-hr"],
            params={"notify": False},
        )
        assert r.status_code == 200, f"发布失败：{r.status_code} {r.text[:200]}"

        # 员工答题并提交
        r = await client.post(
            f"/api/v1/exam-taking/{exam_id}/start", headers=tokens["t-employee"]
        )
        assert r.status_code == 200, r.text[:200]
        start = r.json()
        for q in start["questions"]:
            await client.post(
                f"/api/v1/exam-taking/{exam_id}/answer",
                headers=tokens["t-employee"],
                json={
                    "answer_id": start["answer_id"],
                    "question_id": q["id"],
                    "answer": _pick_answer(q, seeded_bank),
                },
            )
        r = await client.post(
            f"/api/v1/exam-taking/{exam_id}/submit",
            headers=tokens["t-employee"],
            json={"answer_id": start["answer_id"]},
        )
        assert r.status_code == 200, f"提交失败：{r.status_code} {r.text[:200]}"
        return exam_id

    async def test_HR看板呈现真实参与数据(self, client, tokens, submitted_exam_id):
        """spec M6：HR 视角看全公司数据（含个人明细）。"""
        r = await client.get(
            f"/api/v1/statistics/exams/{submitted_exam_id}/overview", headers=tokens["t-hr"]
        )
        assert r.status_code == 200, f"HR 概览失败：{r.status_code} {r.text[:200]}"
        body = r.json()
        assert body.get("completed_count", 0) >= 1, (
            "员工已提交答卷，HR 概览的已考人数仍为 0 —— 看板与实际答题脱节"
        )

        r = await client.get(
            f"/api/v1/statistics/exams/{submitted_exam_id}/by-person", headers=tokens["t-hr"]
        )
        assert r.status_code == 200
        rows = r.json()["persons"]
        assert rows, "HR 应能看到个人成绩明细"
        names = [row["name"] for row in rows]
        assert "员工甲" in names, f"个人明细里找不到提交答卷的员工：{names}"
        assert all(row["duration_seconds"] >= 0 for row in rows), (
            "spec 合规留痕七要素包含「用时」，个人明细必须带 duration_seconds"
        )

    async def test_部门管理者只见本部门(self, client, tokens, submitted_exam_id):
        """spec §4 权限矩阵：部门管理者仅本部门数据。"""
        r = await client.get(
            f"/api/v1/statistics/exams/{submitted_exam_id}/by-dept", headers=tokens["t-mgr"]
        )
        assert r.status_code == 200, f"部门统计失败：{r.status_code} {r.text[:200]}"
        rows = r.json()["departments"]
        dept_names = {row["dept_name"] for row in rows}
        assert dept_names, "部门管理者应能看到本部门数据"
        assert "法务部" not in dept_names, (
            f"部门管理者看到了其他部门数据：{dept_names}（越权）"
        )

    async def test_合规看板只呈现汇总不放个人明细(self, client, tokens, submitted_exam_id):
        """spec §4 权限矩阵：合规角色可见汇总，但取不到个人成绩明细。

        「取不到」的正确实现是**直接拒绝**（403），而不是返回空列表 ——
        返回空列表会让前端误以为「本场考试无人参加」，属于误导性响应。
        """
        r = await client.get(
            f"/api/v1/statistics/exams/{submitted_exam_id}/by-person",
            headers=tokens["t-compliance"],
        )
        assert r.status_code == 403, (
            f"合规角色取个人成绩明细应被拒绝（spec 权限矩阵），实际 {r.status_code}"
            f"：{r.text[:200]}"
        )

        # 汇总必须能看到，且确实包含这场考试的参与数据
        r = await client.get("/api/v1/statistics/compliance", headers=tokens["t-compliance"])
        assert r.status_code == 200, f"合规全景失败：{r.status_code} {r.text[:200]}"
        body = r.json()
        assert body.get("items"), "合规全景应返回按制度分组的汇总"
        # 考试挂在制度分组下（items[].exams[]），带 exam_id 供前端下钻
        grouped = [
            e
            for group in body["items"]
            for e in (group.get("exams") or [])
        ]
        target = next((e for e in grouped if e["exam_id"] == submitted_exam_id), None)
        assert target is not None, (
            f"合规全景里找不到刚发布并有人答题的考试；已有：{[e['name'] for e in grouped]}"
        )
        # 该制度分组的参与人数应体现已提交的答卷
        group = next(g for g in body["items"] if any(e["exam_id"] == submitted_exam_id for e in g["exams"]))
        assert group.get("completed_count", 0) >= 1, (
            f"员工已提交答卷，合规全景该制度分组的已考人数仍为 {group.get('completed_count')}"
            " —— 看板与实际答题脱节"
        )

    async def test_专家看板只呈现题目质量(self, client, tokens, submitted_exam_id):
        """spec §4：制度专家视角只看题目质量，不看个人成绩。"""
        r = await client.get(
            f"/api/v1/statistics/exams/{submitted_exam_id}/by-person",
            headers=tokens["t-expert"],
        )
        assert r.status_code == 403, "专家不应能取个人成绩明细"

        r = await client.get(
            "/api/v1/statistics/question-quality", headers=tokens["t-expert"]
        )
        assert r.status_code == 200, f"题目质量分析失败：{r.status_code} {r.text[:200]}"
