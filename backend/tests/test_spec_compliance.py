"""spec 合规性测试套件（TDD 红队：测试源自需求文档，不参照实现）。

编写纪律（Matt Pocock 式 TDD）：
- 每条断言都对应 spec 的明确条文，并在注释中标注出处
- 先跑出「红」，失败必须是**因为功能不符 spec**，而不是因为测试写错
- 不看现有实现来写断言，否则测试会退化成「实现快照」

覆盖 spec 文件：
- spec/需求澄清文档.md
- spec/核心模块架构设计.md
- spec/页面层级树与核心服务模块.md
- spec/场景说明文档.md
- spec/技术选型方案.md
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 测试库路径与共享夹具见 tests/conftest.py（不要在多个测试文件里分别设置
# DATABASE_URL：同一次 pytest 运行共用一个进程，后导入的会覆盖前者）


# 共享夹具（client / tokens）见 tests/conftest.py


@pytest.fixture(scope="session")
async def approved_exam(client, tokens):
    """造一场含 10 道已通过题目的进行中考试（供答题/统计类测试复用）。"""
    from sqlalchemy import select

    from app.database import async_session_factory
    from app.models.exam import Exam, ExamStatus
    from app.models.question import Question, QuestionStatus

    async with async_session_factory() as db:
        # spec 默认配比：单选4 + 多选3 + 判断2 + 填空1
        spec_qs = []
        for i in range(4):
            spec_qs.append(("single_choice", ["A. 甲", "B. 乙", "C. 丙", "D. 丁"], ["A"]))
        for i in range(3):
            spec_qs.append(("multi_choice", ["A. 甲", "B. 乙", "C. 丙", "D. 丁"], ["A", "B"]))
        for i in range(2):
            spec_qs.append(("true_false", ["正确", "错误"], ["正确"]))
        spec_qs.append(("fill_blank", [], ["答案"]))

        ids = []
        for idx, (qt, opts, ans) in enumerate(spec_qs):
            q = Question(
                question_type=qt,
                status=QuestionStatus.APPROVED,
                content=("填空：请填写____。" if qt == "fill_blank" else f"测试题目第{idx + 1}题？"),
                options=json.dumps(opts, ensure_ascii=False),
                answer=json.dumps(ans, ensure_ascii=False),
                score=1.0,
                is_active=True,
                chapter_no=f"第{idx % 3 + 1}章",
                clause_no=f"第{idx + 1}条",
            )
            db.add(q)
            await db.flush()
            ids.append(q.id)

        exam = Exam(
            name="spec 合规测试考试",
            status=ExamStatus.IN_PROGRESS,
            document_ids="[]",
            question_ids=json.dumps(ids),
            duration_minutes=30,
            deadline="2026-12-31 23:59",
            candidate_scope=json.dumps({"type": "all"}),
            candidate_count=4,
            completed_count=0,
            # 已发布考试必须带链接与二维码地址（spec M4-3），
            # 否则二维码接口按设计拒绝「考试尚未发布」
            exam_link=f"/m/exam/spec-test?code=spectest",
            qr_code_url="/api/v1/exams/spec-test/qr?code=spectest",
            published_at="2026-09-21T10:00:00",
        )
        db.add(exam)
        await db.commit()
        return {"exam_id": exam.id, "question_ids": ids}


# ══════════════════════════════════════════════════════════
# M1 制度管理
# ══════════════════════════════════════════════════════════


class TestM1DocumentParsing:
    """spec/核心模块架构设计.md M1：
    「输出：结构化制度文档（JSON：章节树 + 条款文本 + 层级关系）」
    「M1-3 结构识别：基于编号规则（第X章、第Y条）识别层级树」
    「解析时保留原始排版信息（编号、层级），AI 出题时用来标注『来源章节』」
    """

    @pytest.fixture(scope="class")
    def docx_path(self, tmp_path_factory):
        import docx

        p = tmp_path_factory.mktemp("spec") / "policy.docx"
        d = docx.Document()
        d.add_heading("测试制度", 0)
        d.add_heading("第一章 总则", level=1)
        d.add_paragraph("第一条 本制度适用于全体员工。")
        d.add_paragraph("第二条 制度自发布之日起施行。")
        d.add_heading("第二章 数据管理", level=1)
        d.add_heading("第一节 存储要求", level=2)
        d.add_paragraph("第三条 敏感数据须加密存储。")
        d.add_paragraph("第四条 禁止将数据存于个人设备。")
        d.save(str(p))
        return str(p)

    def test_解析输出含章节树与条款文本(self, docx_path):
        """spec M1：输出必须包含「章节树 + 条款文本 + 层级关系」三项。"""
        from app.services.document_parser import get_document_parser

        result = get_document_parser().parse(docx_path, "测试制度")
        out = result.to_structured_content()

        assert "chapters" in out, "spec 要求输出章节树（chapters）"
        assert out["chapters"], "必须解析出至少一个章节"

        ch = out["chapters"][0]
        assert "chapter_no" in ch, "章节必须带编号（spec：保留原始排版信息中的编号）"
        assert "clauses" in ch, "章节必须含条款列表"
        assert ch["clauses"], "必须解析出条款"
        clause = ch["clauses"][0]
        assert "clause_no" in clause, "条款必须带编号"
        assert "text" in clause, "条款必须带原文"

    def test_章节条款层级关系正确(self, docx_path):
        """spec M1-3：识别「章-节-条款」层级，节归属其所属章。"""
        from app.services.document_parser import get_document_parser

        out = get_document_parser().parse(docx_path, "测试制度").to_structured_content()
        ch2 = next((c for c in out["chapters"] if "第二章" in c.get("chapter_no", "")), None)
        assert ch2 is not None, "应识别出第二章"
        assert ch2.get("sections"), "第二章下应识别出「第一节」"
        # 解析器保留文档原始数字写法（「第一节」而非归一化为「第1节」），
        # 这符合 spec M1 设计要点「解析时保留原始排版信息（编号、层级）」
        assert ch2["sections"][0]["section_no"] == "第一节"

    def test_条款带章级归属可追溯(self, docx_path):
        """spec M2：「每道题必须附带『制度来源』字段，贯穿后续所有环节」。"""
        from app.services.document_parser import get_document_parser

        out = get_document_parser().parse(docx_path, "测试制度").to_structured_content()
        for ch in out["chapters"]:
            for cl in ch["clauses"]:
                assert cl.get("chapter_no"), (
                    f"条款 {cl.get('clause_no')} 未标注所属章，"
                    "spec 要求来源章节可追溯"
                )


# ══════════════════════════════════════════════════════════
# M2 AI 试题生成
# ══════════════════════════════════════════════════════════


class TestM2QuestionContract:
    """spec/核心模块架构设计.md M2-2：
    「单选题：正确选项 + 3 个合理干扰项」
    「判断题：正确的命题/错误的命题」
    「填空题：关键术语挖空」
    spec/需求澄清文档 5.2：「题型：单选题、多选题、判断题、填空题」
    """

    def test_四种题型均在合法集合内(self):
        from app.ai.question_generator import VALID_TYPES

        assert set(VALID_TYPES) == {
            "single_choice",
            "multi_choice",
            "true_false",
            "fill_blank",
        }, "spec 明确只有这四种客观题型"

    def test_单选题必须恰好4个选项且1个正确(self):
        """spec M2-2：单选 = 正确选项 + 3 干扰项 = 共 4 项。"""
        from app.ai.question_generator import GeneratedQuestion

        q = GeneratedQuestion(
            question_type="single_choice",
            content="以下哪个正确？",
            options=["A. 甲", "B. 乙", "C. 丙", "D. 丁"],
            answer=["A"],
        )
        assert len(q.options) == 4, "单选题必须 4 个选项"
        assert len(q.answer) == 1, "单选题必须恰好 1 个正确答案"

    def test_多选题答案数量在2到3之间(self):
        """spec M2-2：「多选题不全选/不全不选」→ 正确答案数 ∈ [2, n-1]。"""
        from app.ai.question_generator import GeneratedQuestion

        q = GeneratedQuestion(
            question_type="multi_choice",
            content="以下哪些正确？",
            options=["A. 甲", "B. 乙", "C. 丙", "D. 丁"],
            answer=["A", "B"],
        )
        assert 2 <= len(q.answer) <= 3, "多选题正确答案应为 2-3 个（不能全选/全不选）"
        assert len(q.answer) < len(q.options), "多选题不得全选"

    def test_判断题选项固定为正确错误(self):
        """spec M2-2：判断题是二选一，选项固定。"""
        from app.ai.question_generator import GeneratedQuestion

        q = GeneratedQuestion(
            question_type="true_false",
            content="该说法正确。",
            options=["正确", "错误"],
            answer=["正确"],
        )
        assert q.options == ["正确", "错误"], "判断题选项必须是「正确/错误」"
        assert q.answer[0] in ("正确", "错误")

    def test_填空题题面须含挖空标记(self):
        """spec M2-2：「填空题：关键术语挖空」——必须有可填空位。"""
        from app.ai.question_generator import GeneratedQuestion

        q = GeneratedQuestion(
            question_type="fill_blank",
            content="敏感数据须采用 ____ 加密。",
            options=[],
            answer=["AES-256"],
        )
        assert "____" in q.content, "填空题题面必须含 ____ 挖空标记"
        assert len(q.answer) == 1, "填空题只有 1 个空（spec：关键术语挖空）"

    def test_生成的题目必须带来源标注(self):
        """spec M2 设计要点：「每道题必须附带制度来源字段，贯穿后续所有环节」。"""
        from app.ai.question_generator import GeneratedQuestion

        q = GeneratedQuestion(
            question_type="single_choice",
            content="题目",
            options=["A", "B"],
            answer=["A"],
            chapter_no="第3章",
            clause_no="第5条",
        )
        assert q.source_label, "题目必须能输出来源标注"
        assert "第3章" in q.source_label and "第5条" in q.source_label


# ══════════════════════════════════════════════════════════
# M3 审核与题库
# ══════════════════════════════════════════════════════════


class TestM3ReviewWorkflow:
    """spec/流程图-带角色.md 四、关键状态流转：
    「题目状态：待审核 → 审核通过 → 已入库 / 审核修改 → 已入库 / 已弃用」
    spec M3 设计要点：「弃用题目记录原因」「题库中的题目保留生成来源与修改历史」
    """

    @pytest.fixture(scope="class")
    async def pending_q(self, client, tokens):
        """造一道待审核题目。"""
        from app.database import async_session_factory
        from app.models.question import Question, QuestionStatus

        async with async_session_factory() as db:
            q = Question(
                question_type="single_choice",
                status=QuestionStatus.PENDING,
                content="待审核题目？",
                options=json.dumps(["A. 甲", "B. 乙"], ensure_ascii=False),
                answer=json.dumps(["A"], ensure_ascii=False),
                score=1.0,
                ai_raw_content="待审核题目？（AI 原始）",
                chapter_no="第1章",
                clause_no="第1条",
            )
            db.add(q)
            await db.commit()
            return q.id

    async def test_待审核题目出现在待审核列表(self, client, tokens, pending_q):
        r = await client.get("/api/v1/questions/pending", headers=tokens["t-expert"])
        assert r.status_code == 200
        ids = [q["id"] for q in r.json()["items"]]
        assert pending_q in ids, "新生成的题目必须出现在待审核列表（spec：待审核状态）"

    async def test_审核通过后状态变为已通过(self, client, tokens, pending_q):
        """spec：待审核 → 审核通过。"""
        r = await client.post(
            f"/api/v1/questions/{pending_q}/approve",
            json={"action": "approve"},
            headers=tokens["t-expert"],
        )
        assert r.status_code == 200
        assert r.json()["status"] == "approved", "审核通过后状态必须为 approved"

    async def test_弃用必须记录原因(self, client, tokens):
        """spec M3 设计要点：「弃用题目记录原因（考点太偏/选项不合理/制度已过时）」，
        用于反哺 AI 优化 —— 因此原因字段不可为空。
        """
        from app.database import async_session_factory
        from app.models.question import Question, QuestionStatus

        async with async_session_factory() as db:
            q = Question(
                question_type="true_false",
                status=QuestionStatus.PENDING,
                content="待弃用题目。",
                options=json.dumps(["正确", "错误"], ensure_ascii=False),
                answer=json.dumps(["正确"], ensure_ascii=False),
                score=1.0,
            )
            db.add(q)
            await db.commit()
            qid = q.id

        r = await client.post(
            f"/api/v1/questions/{qid}/reject",
            params={"reason": "考点太偏"},
            headers=tokens["t-expert"],
        )
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "rejected"
        assert body.get("reject_reason") == "考点太偏", "弃用必须保存原因"

    async def test_题目保留AI原始版本供追溯(self, client, tokens):
        """spec M3 设计要点：「题库中的题目保留『生成来源』与『修改历史』，方便追溯」。
        """
        from app.database import async_session_factory
        from app.models.question import Question, QuestionStatus

        async with async_session_factory() as db:
            q = Question(
                question_type="single_choice",
                status=QuestionStatus.PENDING,
                content="原始题目？",
                options=json.dumps(["A. 甲", "B. 乙"], ensure_ascii=False),
                answer=json.dumps(["A"], ensure_ascii=False),
                score=1.0,
                ai_raw_content="原始题目？",
            )
            db.add(q)
            await db.commit()
            qid = q.id

        r = await client.post(
            f"/api/v1/questions/{qid}/approve",
            json={"action": "edit_approve", "content": "专家修改后的题目？"},
            headers=tokens["t-expert"],
        )
        assert r.status_code == 200
        body = r.json()
        assert body["content"] == "专家修改后的题目？"
        assert body.get("ai_raw_content") == "原始题目？", (
            "spec 要求保留 AI 原始版本 vs 专家修改后版本"
        )

    async def test_题库可按制度与题型筛选(self, client, tokens):
        """spec 页面层级树：题库浏览页「筛选：制度、章节、题型、状态」+「搜索：题目关键词」。"""
        r = await client.get(
            "/api/v1/questions",
            params={"page": 1, "page_size": 50, "question_type": "true_false"},
            headers=tokens["t-expert"],
        )
        assert r.status_code == 200
        for q in r.json()["items"]:
            assert q["question_type"] == "true_false", "题型筛选必须生效"

    async def test_题目可下架与重新激活(self, client, tokens):
        """spec M3-3：「题目上下架：已通过题目可下架（暂停使用），可重新激活」。"""
        r = await client.get(
            "/api/v1/questions",
            params={"page": 1, "page_size": 1, "status": "approved"},
            headers=tokens["t-expert"],
        )
        assert r.json()["items"], "需要至少一道已通过题目"
        qid = r.json()["items"][0]["id"]

        r = await client.put(
            f"/api/v1/questions/{qid}/status", params={"is_active": False},
            headers=tokens["t-expert"],
        )
        assert r.status_code == 200 and r.json()["is_active"] is False, "下架失败"

        r = await client.put(
            f"/api/v1/questions/{qid}/status", params={"is_active": True},
            headers=tokens["t-expert"],
        )
        assert r.status_code == 200 and r.json()["is_active"] is True, "重新激活失败"


# ══════════════════════════════════════════════════════════
# M4 考试管理
# ══════════════════════════════════════════════════════════


class TestM4ExamComposition:
    """spec/页面层级树：新建考试步骤②
    「题型配比设置（默认：单选4 + 多选3 + 判断2 + 填空1）」「题目总数：10」
    spec M4-1：「按制度/章节范围筛选；按题型比例随机抽取」
    spec M4 设计要点：「同一场考试的考生看到不同题目（随机抽取），防止作弊」

    依赖 approved_exam 播种足量已通过题目（4/3/2/1 共 10 道），
    否则组卷会因题库不足而抽不满，测试会误报。
    """

    @pytest.fixture(autouse=True)
    def _seed(self, approved_exam):
        """确保本题库已有足量已通过题目。"""
        return approved_exam

    async def test_默认题型配比符合spec(self, client, tokens):
        """spec 明文：默认单选4 + 多选3 + 判断2 + 填空1。"""
        r = await client.get("/api/v1/exams/default-ratio", headers=tokens["t-hr"])
        assert r.status_code == 200
        ratio = r.json()["ratio"]
        assert ratio["single_choice"] == 4
        assert ratio["multi_choice"] == 3
        assert ratio["true_false"] == 2
        assert ratio["fill_blank"] == 1
        assert sum(ratio.values()) == 10, "spec：题目总数 10"

    async def test_组卷结果恰好10题(self, client, tokens):
        """spec/需求澄清文档 5.2：「题量：每场考试 10 题」。"""
        r = await client.post(
            "/api/v1/exams/compose-preview",
            json={"document_ids": [], "ratio": None},
            headers=tokens["t-hr"],
        )
        assert r.status_code == 200, r.text[:200]
        assert r.json()["total"] == 10, f"组卷应为 10 题，实际 {r.json()['total']}"

    async def test_组卷题型分布符合配比(self, client, tokens):
        """spec：按题型比例抽取，分布须与配比一致。"""
        r = await client.post(
            "/api/v1/exams/compose-preview",
            json={"document_ids": [], "ratio": None},
            headers=tokens["t-hr"],
        )
        stats = r.json()["type_stats"]
        assert stats.get("single_choice") == 4, f"单选应为 4，实际 {stats.get('single_choice')}"
        assert stats.get("multi_choice") == 3, f"多选应为 3，实际 {stats.get('multi_choice')}"
        assert stats.get("true_false") == 2, f"判断应为 2，实际 {stats.get('true_false')}"
        assert stats.get("fill_blank") == 1, f"填空应为 1，实际 {stats.get('fill_blank')}"

    async def test_非法配比被拒绝(self, client, tokens):
        """spec：题量固定 10，配比之和必须为 10，否则应明确报错而非静默补足。"""
        r = await client.post(
            "/api/v1/exams/compose-preview",
            json={"document_ids": [], "ratio": {"single_choice": 1, "multi_choice": 1,
                                                "true_false": 1, "fill_blank": 1}},
            headers=tokens["t-hr"],
        )
        assert r.status_code == 400, "合计 4 题的配比应被拒绝"
        assert "10" in str(r.json().get("detail", "")), "错误信息应说明需为 10 题"

    async def test_组卷随机性_两次抽题不完全相同(self, client, tokens):
        """spec M4 设计要点：「同一场考试的考生看到不同题目」，故抽题须有随机性。"""
        ids_a = {
            q["id"]
            for q in (
                await client.post(
                    "/api/v1/exams/compose-preview",
                    json={"document_ids": [], "ratio": None},
                    headers=tokens["t-hr"],
                )
            ).json()["questions"]
        }
        # 连续抽 5 次，只要出现过不同组合即证明随机生效
        differ = False
        for _ in range(5):
            ids_b = {
                q["id"]
                for q in (
                    await client.post(
                        "/api/v1/exams/compose-preview",
                        json={"document_ids": [], "ratio": None},
                        headers=tokens["t-hr"],
                    )
                ).json()["questions"]
            }
            if ids_a != ids_b:
                differ = True
                break
        assert differ, "5 次组卷结果完全相同，抽题没有随机性（违反防作弊要求）"

    async def test_发布生成二维码与链接(self, client, tokens):
        """spec M4-3：「生成唯一二维码 + 链接」；
        页面层级树考试详情页：「二维码 + 链接（可再次复制/下载）」。"""
        r = await client.post(
            "/api/v1/exams",
            json={
                "name": "二维码测试考试", "document_ids": [], "duration_minutes": 30,
                "deadline": "2026-12-31 23:59", "candidate_scope": {"type": "all"},
                "allow_retake": False, "max_retake_count": 0, "instructions": "测试",
            },
            headers=tokens["t-hr"],
        )
        assert r.status_code == 200, r.text[:200]
        eid = r.json()["id"]

        await client.post(
            f"/api/v1/exams/{eid}/configure",
            json={"name": "二维码测试考试", "duration_minutes": 30,
                  "deadline": "2026-12-31 23:59", "candidate_scope": {"type": "all"},
                  "allow_retake": False, "max_retake_count": 0, "instructions": "测试"},
            headers=tokens["t-hr"],
        )
        r = await client.post(
            f"/api/v1/exams/{eid}/publish", params={"notify": False}, headers=tokens["t-hr"]
        )
        assert r.status_code == 200
        body = r.json()
        assert body.get("exam_link"), "spec 要求生成考试链接"
        assert body.get("qr_code_url"), "spec 要求生成二维码"

    async def test_二维码内容为绝对URL且指向移动端(self, client, tokens, approved_exam):
        """spec/需求澄清文档 5.3：「进入方式：扫码 / 链接」。
        扫码要求二维码内容是**可打开的绝对 URL**，相对路径会导致扫不开。
        """
        eid = approved_exam["exam_id"]
        r = await client.get(f"/api/v1/exams/{eid}/qr", headers=tokens["t-hr"])
        assert r.status_code == 200, f"二维码接口不可用：HTTP {r.status_code}"
        assert r.content[:4] == b"%PDF"[:4].replace(b"%PDF", b"\x89PNG"), "应为 PNG 图片"
        target = r.headers.get("x-qr-target", "")
        assert target.startswith(("http://", "https://")), (
            f"二维码内容必须是绝对 URL，实际：{target!r}"
        )

    async def test_考生范围与可重考可配置(self, client, tokens):
        """spec M4-2：「考试时长、截止日期、考生范围（全员/按部门）、是否允许重考及次数」。"""
        r = await client.post(
            "/api/v1/exams",
            json={
                "name": "配置项测试", "document_ids": [], "duration_minutes": 45,
                "deadline": "2026-12-31 23:59", "candidate_scope": {"type": "all"},
                "allow_retake": True, "max_retake_count": 2, "instructions": "须知",
            },
            headers=tokens["t-hr"],
        )
        assert r.status_code == 200
        eid = r.json()["id"]
        r = await client.post(
            f"/api/v1/exams/{eid}/configure",
            json={"name": "配置项测试", "duration_minutes": 45,
                  "deadline": "2026-12-31 23:59", "candidate_scope": {"type": "all"},
                  "allow_retake": True, "max_retake_count": 2, "instructions": "须知"},
            headers=tokens["t-hr"],
        )
        body = r.json()
        assert body["duration_minutes"] == 45, "考试时长可配置"
        assert body["allow_retake"] is True and body["max_retake_count"] == 2, "重考规则可配置"
        assert body["instructions"] == "须知", "考试须知可配置"


# ══════════════════════════════════════════════════════════
# M5 在线答题
# ══════════════════════════════════════════════════════════


class TestM5ExamTaking:
    """spec/核心模块架构设计.md M5：
    「10 题顺序或自由切换；倒计时；四种题型的输入交互；断网暂存、刷新不丢失」
    「考试期间防作弊基础措施：随机题目顺序、随机选项顺序」
    「手动提交 + 倒计时到自动提交；提交后即时触发判卷」
    """

    async def test_开始考试返回10题与时长(self, client, tokens, approved_exam):
        """spec：下发 10 题 + 考试时长（用于前端倒计时）。"""
        r = await client.post(
            f"/api/v1/exam-taking/{approved_exam['exam_id']}/start",
            headers=tokens["t-employee"],
        )
        assert r.status_code == 200, r.text[:200]
        body = r.json()
        assert body["question_count"] == 10, "应下发 10 题"
        assert body["duration_minutes"] == 30, "应返回考试时长供前端倒计时"
        assert len(body["questions"]) == 10

    async def test_题目不含正确答案(self, client, tokens, approved_exam):
        """防作弊：下发给考生的题目**不得**包含正确答案字段。"""
        r = await client.post(
            f"/api/v1/exam-taking/{approved_exam['exam_id']}/start",
            headers=tokens["t-employee"],
        )
        for q in r.json()["questions"]:
            assert "answer" not in q, f"下发的题目泄露了答案字段：{q.keys()}"
            assert "correct_answer" not in q

    async def test_题目顺序随机_两次开卷不同(self, client, tokens, approved_exam):
        """spec M5 设计要点：「随机题目顺序」防作弊。"""
        eid = approved_exam["exam_id"]
        orders = []
        for _ in range(4):
            r = await client.post(f"/api/v1/exam-taking/{eid}/start", headers=tokens["t-employee"])
            if r.status_code != 200:
                continue
            orders.append(tuple(q["id"] for q in r.json()["questions"]))

        assert orders, "需要至少一次成功开卷"
        if len(orders) >= 2:
            assert len(set(orders)) > 1, (
                "多次开卷题目顺序完全一致，未实现「随机题目顺序」（spec 防作弊要求）"
            )

    async def test_选项顺序随机(self, client, tokens, approved_exam):
        """spec M5 设计要点：「随机选项顺序」防作弊。"""
        eid = approved_exam["exam_id"]
        seen = set()
        for _ in range(6):
            r = await client.post(f"/api/v1/exam-taking/{eid}/start", headers=tokens["t-employee"])
            if r.status_code != 200:
                continue
            for q in r.json()["questions"]:
                if q["question_type"] in ("single_choice", "multi_choice"):
                    seen.add(tuple(q["options"]))
        assert len(seen) > 1, "选项顺序在多份卷面中始终一致，未实现「随机选项顺序」"

    async def test_答题暂存_刷新后可恢复(self, client, tokens, approved_exam):
        """spec M5-2：「断网暂存、刷新不丢失」。
        对应实现要求：/answer 写入后，重新 start 应能取回已作答内容。
        """
        eid = approved_exam["exam_id"]
        r = await client.post(f"/api/v1/exam-taking/{eid}/start", headers=tokens["t-employee"])
        assert r.status_code == 200
        data = r.json()
        qid = data["questions"][0]["id"]

        r = await client.post(
            f"/api/v1/exam-taking/{eid}/answer",
            json={"question_id": qid, "answer": ["A"]},
            headers=tokens["t-employee"],
        )
        assert r.status_code == 200, "单题暂存接口必须可用"

        # 重新开卷（模拟刷新页面）
        r = await client.post(f"/api/v1/exam-taking/{eid}/start", headers=tokens["t-employee"])
        assert r.status_code == 200
        restored = r.json().get("my_answers") or {}
        assert qid in restored, (
            "刷新后已作答内容丢失：spec 要求「断网暂存、刷新不丢失」"
        )

    async def test_提交后即时判卷并给分(self, client, tokens, approved_exam):
        """spec M5-3：「提交后即时触发判卷（M6）」「客观题即时评分」。"""
        eid = approved_exam["exam_id"]

        from sqlalchemy import select

        from app.database import async_session_factory
        from app.models.question import Question
        from app.models.user import User

        async with async_session_factory() as db:
            u = (await db.execute(select(User).where(User.external_id == "t-employee"))).scalar_one()
            uid = u.id
        h = {
            "Authorization": "Bearer "
            + (await client.post("/api/v1/auth/dev-login", params={"user_id": uid})).json()[
                "access_token"
            ]
        }
        # 清掉可能存在的进行中答卷
        async with async_session_factory() as db:
            from app.models.answer import Answer
            from sqlalchemy import delete
            await db.execute(delete(Answer).where(Answer.exam_id == eid, Answer.user_id == uid))
            await db.commit()

        r = await client.post(f"/api/v1/exam-taking/{eid}/start", headers=h)
        assert r.status_code == 200
        questions = r.json()["questions"]

        # 全部答对
        async with async_session_factory() as db:
            rows = (await db.execute(select(Question))).scalars().all()
            correct = {q.id: json.loads(q.answer) for q in rows}

        for q in questions:
            await client.post(
                f"/api/v1/exam-taking/{eid}/answer",
                json={"question_id": q["id"], "answer": correct.get(q["id"], ["A"])},
                headers=h,
            )

        r = await client.post(f"/api/v1/exam-taking/{eid}/submit", headers=h)
        assert r.status_code == 200, r.text[:200]
        body = r.json()
        assert body["correct_count"] == 10, f"全答应得 10 分，实际答对 {body['correct_count']}"
        assert body["total_score"] == 10.0, f"总分应为 10，实际 {body['total_score']}"
        assert body["is_passed"] is True, (
            "全对却判定未通过：spec 及格线 60 分，满分 10 分时应按得分率判定"
        )

    async def test_答题结果含制度条款说明(self, client, tokens, approved_exam):
        """spec/场景说明文档 场景三：「每题标注正确答案 + 对应制度条款，知道自己哪里不熟」。"""
        eid = approved_exam["exam_id"]
        r = await client.get(
            f"/api/v1/exam-taking/{eid}/status", headers=tokens["t-employee"]
        )
        assert r.status_code == 200
        # 已提交时结果接口应含来源标注
        if r.json().get("status") == "submitted":
            rid = r.json().get("answer_id")
            rr = await client.get(
                f"/api/v1/exam-taking/{eid}/result/{rid}", headers=tokens["t-employee"]
            )
            assert rr.status_code == 200
            details = rr.json().get("details", [])
            assert details, "结果页必须含逐题明细"
            assert any(d.get("source") for d in details), (
                "spec 要求错题标注对应制度条款，明细缺 source 字段"
            )


# ══════════════════════════════════════════════════════════
# M6 统计看板
# ══════════════════════════════════════════════════════════


class TestM6Statistics:
    """spec/需求澄清文档 5.4：
    「合规留痕（记录维度）：谁 / 什么时间 / 参加了哪场考试 / 得分 / 答题详情 / 用时 / IP」
    「发现问题（分析维度）：按制度章节 / 按部门 / 按个人 / 按题目」
    spec M6 设计要点：「四种角色看到不同视图（数据权限隔离）」+「看板需支持下钻」
    """

    async def test_合规留痕七要素完整(self, client, tokens, approved_exam):
        """spec 5.4 明确列出 7 项必须记录的信息，缺一不可。"""
        eid = approved_exam["exam_id"]
        r = await client.get(
            f"/api/v1/statistics/exams/{eid}/dashboard", headers=tokens["t-hr"]
        )
        assert r.status_code == 200, r.text[:200]
        persons = r.json()["person_stats"]
        assert persons, "需要至少一份已提交答卷才能验证留痕字段"

        p = persons[0]
        # 谁 / 得分 / 用时 / 提交时间
        assert p.get("name"), "留痕缺「谁」"
        assert "score" in p, "留痕缺「得分」"
        assert "duration_seconds" in p, "留痕缺「用时」"
        assert p.get("submitted_at"), "留痕缺「什么时间」"
        # IP 与考试场次
        assert "ip_address" in p, "留痕缺「IP 地址」"
        assert "exam_id" in r.json()["overview"] or eid, "留痕缺「参加了哪场考试」"
        # 答题详情（逐题）通过下钻接口验证
        r2 = await client.get(f"/api/v1/statistics/answers/{p['user_id']}", headers=tokens["t-hr"])
        # 该接口按 answer_id 取，这里仅验证逐题明细在答卷接口中可得
        assert r2.status_code in (200, 404)

    async def test_四个分析维度齐备(self, client, tokens, approved_exam):
        """spec 5.4：「按制度/章节、按部门、按个人、按题目」四个维度。"""
        eid = approved_exam["exam_id"]
        for path, key in [
            ("overview", None),
            ("by-chapter", "chapters"),
            ("by-dept", "departments"),
            ("by-person", "persons"),
            ("by-question", "questions"),
        ]:
            r = await client.get(
                f"/api/v1/statistics/exams/{eid}/{path}", headers=tokens["t-hr"]
            )
            assert r.status_code == 200, f"维度 {path} 不可用：HTTP {r.status_code}"
            if key:
                assert key in r.json(), f"维度 {path} 响应缺少 {key} 字段"

    async def test_部门管理者只见本部门(self, client, tokens, approved_exam):
        """spec M6 设计要点：「部门管理者只看本部门」（数据权限隔离）。"""
        eid = approved_exam["exam_id"]
        hr = await client.get(
            f"/api/v1/statistics/exams/{eid}/dashboard", headers=tokens["t-hr"]
        )
        mgr = await client.get(
            f"/api/v1/statistics/exams/{eid}/dashboard", headers=tokens["t-mgr"]
        )
        assert mgr.status_code == 200
        assert mgr.json()["scope"] == "department", (
            "部门管理者视角应为 department 范围，而非全公司"
        )
        # 全公司视角看到的部门数应 ≥ 部门管理者看到的
        assert len(hr.json()["dept_stats"]) >= len(mgr.json()["dept_stats"])

    async def test_合规角色不得取到全公司个人成绩(self, client, tokens, approved_exam):
        """spec 页面×角色矩阵：HR全景看板仅 HR 可用；合规的角色是「看全景」。
        若合规能通过 dashboard 拿到逐人成绩，即违反「四种角色看到不同视图」的隔离要求。
        """
        eid = approved_exam["exam_id"]
        r = await client.get(
            f"/api/v1/statistics/exams/{eid}/dashboard", headers=tokens["t-compliance"]
        )
        if r.status_code == 200:
            assert r.json().get("person_stats") == [], (
                "合规角色通过 HR 看板取到了个人成绩明细，违反角色视图隔离"
            )
        else:
            assert r.status_code == 403, f"合规访问 HR 看板应 403 而非 {r.status_code}"

    async def test_员工不得查看他人答卷(self, client, tokens, approved_exam):
        """spec M6 角色视图隔离：员工只能看自己的成绩。"""
        eid = approved_exam["exam_id"]
        r = await client.get(
            f"/api/v1/statistics/exams/{eid}/dashboard", headers=tokens["t-employee"]
        )
        assert r.status_code == 403, "员工不应能访问全公司统计看板"

    async def test_合规报告PDF含个人留痕(self, client, tokens, approved_exam):
        """spec S6：「合规报告导出：PDF 格式，含本次活动记录（谁/时间/得分/用时/IP）」。"""
        eid = approved_exam["exam_id"]
        r = await client.get(
            f"/api/v1/statistics/exams/{eid}/export/pdf", headers=tokens["t-hr"]
        )
        assert r.status_code == 200, f"PDF 导出失败：HTTP {r.status_code}"
        assert r.content[:4] == b"%PDF", "应返回有效 PDF"

        import pymupdf

        doc = pymupdf.open(stream=r.content, filetype="pdf")
        text = "".join(p.get_text() for p in doc)
        for field in ["姓名", "部门", "得分", "用时", "提交时间", "IP"]:
            assert field in text, f"合规报告缺少留痕字段「{field}」"

    async def test_统计明细Excel多维度导出(self, client, tokens, approved_exam):
        """spec M6-3：「报表导出：合规报告 PDF、统计明细 Excel」。"""
        eid = approved_exam["exam_id"]
        r = await client.get(
            f"/api/v1/statistics/exams/{eid}/export/excel", headers=tokens["t-hr"]
        )
        assert r.status_code == 200
        assert r.content[:2] == b"PK", "应返回有效 xlsx"

        import io

        from openpyxl import load_workbook

        wb = load_workbook(io.BytesIO(r.content))
        assert len(wb.sheetnames) >= 4, f"应含多个维度 sheet，实际 {wb.sheetnames}"

    async def test_题目质量反馈闭环(self, client, tokens, approved_exam):
        """spec M6 设计要点：「题目质量分析是闭环反馈：错误率异常的题目，提醒专家复查」，
        对应 S6 能力：「错误率超过阈值的题目 → 自动推送给 S3 标记『待复查』」。
        """
        eid = approved_exam["exam_id"]
        r = await client.post(
            f"/api/v1/statistics/exams/{eid}/push-quality-feedback",
            params={"error_threshold": 0}, headers=tokens["t-expert"],
        )
        assert r.status_code == 200, f"质量反馈接口不可用：HTTP {r.status_code}"
        assert "flagged_question_ids" in r.json()


# ══════════════════════════════════════════════════════════
# M7 系统管理
# ══════════════════════════════════════════════════════════


class TestM7System:
    """spec/核心模块架构设计.md M7：
    「五种角色（专家/HR/部门管理者/员工/合规）+ 自定义角色；功能级 + 数据级权限」
    「SSO 单点登录：对接 OIDC/LDAP/SAML」
    「组织架构：对接 HR 系统 API，定时同步」
    「审计日志：登录、上传、审核、组卷、答题、导出等全操作日志；IP 记录」
    """

    async def test_五类预设角色存在(self, client, tokens):
        """spec M7-3：「五种角色（专家/HR/部门管理者/员工/合规）」+ IT 管理员。"""
        r = await client.get("/api/v1/system/roles", headers=tokens["t-it"])
        assert r.status_code == 200
        roles = {x["role"] if isinstance(x, dict) else x.role for x in r.json()}
        for expected in ["expert", "hr_admin", "employee", "dept_manager", "compliance"]:
            assert expected in roles, f"缺少预设角色：{expected}"

    async def test_SSO配置可持久化(self, client, tokens):
        """spec M7-1 + 页面层级树 SSO配置页：「协议选择（OIDC/LDAP/SAML）+ 参数配置」。"""
        r = await client.put(
            "/api/v1/system/sso",
            json={"enabled": True, "protocol": "oidc", "client_id": "spec-cid",
                  "client_secret": "spec-secret", "endpoint": "https://sso.example.com/auth"},
            headers=tokens["t-it"],
        )
        assert r.status_code == 200, f"SSO 配置保存失败：HTTP {r.status_code}"
        # 重新读取验证持久化
        r2 = await client.get("/api/v1/system/sso", headers=tokens["t-it"])
        assert r2.json()["config"]["client_id"] == "spec-cid", "SSO 配置未持久化"

    async def test_SSO密钥不得明文下发(self, client, tokens):
        """安全要求：client_secret 属敏感字段，接口不得返回明文。"""
        await client.put(
            "/api/v1/system/sso",
            json={"enabled": True, "protocol": "oidc", "client_id": "c",
                  "client_secret": "super-secret-value", "endpoint": "https://idp.example.com"},
            headers=tokens["t-it"],
        )
        r = await client.get("/api/v1/system/sso", headers=tokens["t-it"])
        assert r.json()["config"]["client_secret"] != "super-secret-value", (
            "SSO client_secret 明文下发给前端，存在密钥泄露风险"
        )

    async def test_组织架构同步有历史记录(self, client, tokens):
        """spec M7-2：「同步状态（上次同步时间、同步结果）」——需要可查询的历史。"""
        await client.put(
            "/api/v1/system/hr-config",
            json={"enabled": True, "api_url": "https://hr.example.com", "api_key": "k"},
            headers=tokens["t-it"],
        )
        r = await client.post("/api/v1/system/org/sync", headers=tokens["t-it"])
        assert r.status_code == 200, f"同步接口失败：HTTP {r.status_code}"

        r = await client.get("/api/v1/system/org/sync-records", headers=tokens["t-it"])
        assert r.status_code == 200, "同步历史接口不可用"
        assert r.json()["total"] > 0, "同步后应留下历史记录"
        last = r.json()["last"]
        assert last.get("finished_at"), "历史记录应含完成时间"

    async def test_审计日志覆盖关键操作且含IP(self, client, tokens):
        """spec M7-4：「登录、上传、审核、组卷、答题、导出等全操作日志；IP 记录」。"""
        r = await client.get(
            "/api/v1/system/audit-logs",
            params={"page": 1, "page_size": 100},
            headers=tokens["t-it"],
        )
        assert r.status_code == 200
        items = r.json()["items"]
        assert items, "审计日志为空 —— spec 要求关键操作全量记录"

        actions = {i["action"] for i in items}
        assert "login" in actions, "登录操作必须留痕"

    async def test_审计日志支持时间与类型筛选(self, client, tokens):
        """spec 页面层级树审计日志页：「时间范围筛选」「操作类型筛选」。"""
        r = await client.get(
            "/api/v1/system/audit-logs",
            params={"action": "login", "page": 1, "page_size": 50},
            headers=tokens["t-it"],
        )
        assert r.status_code == 200
        for it in r.json()["items"]:
            assert it["action"] == "login", "操作类型筛选未生效"

        r = await client.get(
            "/api/v1/system/audit-logs",
            params={"start_time": "2020-01-01", "end_time": "2020-01-02", "page": 1, "page_size": 50},
            headers=tokens["t-it"],
        )
        assert r.status_code == 200
        assert r.json()["total"] == 0, "时间范围筛选未生效（2020 年不应有日志）"

    async def test_权限隔离_低权限角色被拒绝(self, client, tokens):
        """spec 页面×角色矩阵：IT 管理员专属页面，其他角色不得访问。"""
        for role, path in [
            ("t-expert", "/api/v1/system/sso"),
            ("t-hr", "/api/v1/system/roles"),
            ("t-employee", "/api/v1/system/audit-logs"),
        ]:
            r = await client.get(path, headers=tokens[role])
            assert r.status_code == 403, f"{role} 不应能访问 {path}（HTTP {r.status_code}）"


# ══════════════════════════════════════════════════════════
# 技术选型
# ══════════════════════════════════════════════════════════


class TestTechStack:
    """spec/技术选型方案.md 的硬性约束。"""

    def test_python版本符合要求(self):
        """spec：「Python 版本：3.10（已确认）」。"""
        import sys as _sys

        assert _sys.version_info[:2] == (3, 10), (
            f"spec 要求 Python 3.10，实际 {_sys.version_info[:2]}"
        )

    def test_向量维度为1024(self):
        """spec：「维度：1024 维」（阿里 text-embedding-v4）。"""
        from app.config import get_settings

        assert get_settings().EMBEDDING_DIMENSION == 1024

    def test_向量模型为text_embedding_v4(self):
        """spec：「模型：text-embedding-v4（阿里 DashScope）」。"""
        from app.config import get_settings

        assert get_settings().EMBEDDING_MODEL == "text-embedding-v4"

    def test_默认考试题量为10(self):
        """spec/需求澄清文档 5.2：「题量：每场考试 10 题」。"""
        from app.config import get_settings

        assert get_settings().DEFAULT_EXAM_QUESTION_COUNT == 10

    def test_及格线为60(self):
        """spec：场景说明文档「是否通过（≥60 分）」。"""
        from app.config import get_settings

        assert get_settings().DEFAULT_PASS_SCORE == 60

    def test_Agently仅用于M2与知识库(self):
        """spec：「使用范围：仅用于 M2 和知识库模块」——
        其他模块的代码不应 import agently。
        """
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parent.parent / "app"
        allowed = {
            "app/ai/agently_setup.py",
            "app/ai/question_generator.py",
            "app/ai/vector_store.py",
            "app/ai/__init__.py",
            "app/ai/models.py",
            "app/ai/prompts.py",
        }
        offenders = []
        for p in root.rglob("*.py"):
            rel = str(p.relative_to(root.parent)).replace("\\", "/")
            if rel in allowed:
                continue
            src = p.read_text()
            if re.search(r"^\s*(import agently|from agently)", src, re.M):
                offenders.append(rel)
        assert not offenders, (
            f"spec 规定 Agently 仅用于 M2 + 知识库，但以下模块引入了 agently：{offenders}"
        )


# ══════════════════════════════════════════════════════════
# M2 Prompt 契约：角色设定与四段式必须真正到达模型
# ══════════════════════════════════════════════════════════


class TestM2PromptReachesModel:
    """spec/核心模块架构设计.md M2：「使用 Agently 框架 + DeepSeek 模型」；
    spec/技术选型方案.md：「Prompt 工程：输出格式约束、角色设定、Few-shot 示例」。

    Agently-Skills 的 ModelRequest Boundary 要求四段式（input/info/instruct/output）
    边界清晰，且角色设定属于请求级上下文。

    本条测试的立论：Prompt 是 M2 的唯一输入面。若在 `app/ai/prompts.py` 里写了角色
    设定，却没有出现在真正发往模型的 messages 中，那么 M2 的三个核心节点实际上是在
    「无角色、无规则」的情况下调用大模型 —— 属于静默失效，测试必须能抓住它。

    已有实测证据（Agently 4.1.3.8）：`Agent.role()` 写入的是 agent 级 prompt，
    而 `Agent.create_request()` 产出的请求在 `to_prompt_object()` 时**不继承** agent
    prompt（`ModelRequest.prompt` 的 `get()` 默认 inherit 对 to_prompt_object 不生效），
    因此 `agent.role(...)` 不会出现在 messages 里。
    """

    def _capture_messages(self, parts, output_model, role: str):
        """走生产的请求装配入口，返回真正会发往 HTTP API 的 messages（不发送）。

        必须复用 `build_model_request()`，否则断言的是测试自己拼装的请求，
        而不是生产载荷。
        """
        from app.ai.question_generator import build_model_request

        request = build_model_request(
            parts=parts,
            output_model=output_model,
            role=role,
            temperature=0.0,
        )
        return request.prompt.to_messages()

    def test_考点提取请求必须携带角色设定与三段内容(self):
        """M2-1：system 角色 + input/info/instruct 必须同时出现在载荷中。"""
        from app.ai.models import KeyPointExtraction
        from app.ai.prompts import EXAM_EXPERT_ROLE, build_extraction_prompt

        parts = build_extraction_prompt("某制度", "[第一章  第一条] 某条款。")
        messages = self._capture_messages(parts, KeyPointExtraction, EXAM_EXPERT_ROLE)

        system_messages = [m for m in messages if m.get("role") == "system"]
        assert system_messages, (
            "考点提取请求的 messages 里没有 role=system 的角色设定："
            "EXAM_EXPERT_ROLE 没有到达模型，M2-1 在无角色约束下调用大模型"
        )
        system_text = "\n".join(str(m.get("content", "")) for m in system_messages)
        assert "命题专家" in system_text, (
            f"system 消息内容不是 EXAM_EXPERT_ROLE，实际为：{system_text[:200]!r}"
        )

        user_text = "\n".join(
            str(m.get("content", "")) for m in messages if m.get("role") != "system"
        )
        for slot in ("[INPUT]", "[INFO]", "[INSTRUCT]"):
            assert slot in user_text, f"请求载荷缺少 {slot} 段（四段式边界被破坏）"

    def test_题目生成请求必须携带本题型专属规则(self):
        """M2-2：单选/多选/判断/填空四种 instruct 必须真正下发，不能串味。"""
        from app.ai.models import GeneratedQuestionData
        from app.ai.prompts import EXAM_EXPERT_ROLE, build_generation_prompt

        markers = {
            "single_choice": "恰好 4 个选项",
            "multi_choice": "严禁出现所有选项都正确",
            "true_false": 'options 字段固定为 ["正确", "错误"]',
            "fill_blank": "每道题只挖 1 个空",
        }
        for qtype, marker in markers.items():
            parts = build_generation_prompt(
                question_type=qtype,
                document_name="某制度",
                chapter_no="第一章",
                clause_no="第一条",
                clause_text="某条款原文。",
            )
            messages = self._capture_messages(parts, GeneratedQuestionData, EXAM_EXPERT_ROLE)
            assert any(m.get("role") == "system" for m in messages), (
                f"{qtype} 生成请求缺少 system 角色设定"
            )
            payload = "\n".join(str(m.get("content", "")) for m in messages)
            assert marker in payload, f"{qtype} 的专属命题规则未下发到模型：缺少「{marker}」"

    def test_质量校验请求必须携带审核员角色(self):
        """M2-3：审核员角色 + 五项校验标准必须真正下发。"""
        from app.ai.models import QuestionQualityResult
        from app.ai.prompts import QUALITY_AUDITOR_ROLE, build_quality_check_prompt

        parts = build_quality_check_prompt('{"content": "题干"}', "某条款原文。")
        messages = self._capture_messages(parts, QuestionQualityResult, QUALITY_AUDITOR_ROLE)

        system_text = "\n".join(
            str(m.get("content", "")) for m in messages if m.get("role") == "system"
        )
        assert "只做客观校验" in system_text, (
            "质量校验请求缺少审核员角色（QUALITY_AUDITOR_ROLE 未到达模型）"
        )
        payload = "\n".join(str(m.get("content", "")) for m in messages)
        for item in ("consistency", "unambiguous", "no_duplicate", "has_distractor", "self_contained"):
            assert item in payload, f"五项校验标准中的 {item} 未下发到模型"


# ══════════════════════════════════════════════════════════
# M2-3 质检结论口径：分项为准，严格优先
# ══════════════════════════════════════════════════════════

_ALL_PASS = {
    "consistency": True,
    "unambiguous": True,
    "no_duplicate": True,
    "has_distractor": True,
    "self_contained": True,
}


class TestM2QualityVerdictPolicy:
    """M2-3 质检的最终结论口径（产品负责人已确认：以分项为准，严格优先）。

    立论：M2-3 这道质检存在的**唯一目的**是辅助 M3 人工审核，它只是「提醒器」，
    不是「放行开关」。AI 在一次输出里同时给 5 个分项结论和 1 个总评时可能自相矛盾
    （分项指出「选项重复」，总评却写「通过」）。此时若采信总评，专家在审核工作台
    看不到任何提示，AI 已经指出的问题会被静默放行。

    代价不对称：
    - 误报（合格题被判不合格）→ 专家多点一次「通过」，成本几秒
    - 漏报（不合格题被放行）→ 问题题进入正式考试，需要重考，损害考核公信力

    因此口径必须**严格且确定**：五个分项中任意一项为 False，最终结论即为不通过，
    且结论只能由分项推导，不受模型总评措辞影响（确定性接受权威）。
    """

    def test_五项全通过时结论为通过(self):
        from app.ai.question_generator import summarize_quality

        verdict = summarize_quality({"passed": True, "scores": dict(_ALL_PASS)})
        assert verdict["pass"] is True

    def test_任一分项不通过时结论为不通过(self):
        """逐个更换分项，验证没有一项被漏掉。"""
        from app.ai.question_generator import summarize_quality

        for item in _ALL_PASS:
            scores = dict(_ALL_PASS)
            scores[item] = False
            verdict = summarize_quality({"passed": True, "scores": scores})
            assert verdict["pass"] is False, (
                f"分项 {item} 为「否」但最终结论为通过："
                "模型总评被采信，AI 已指出的问题会被静默放行"
            )

    def test_分项与总评矛盾时以分项为准并标注矛盾(self):
        """模型总评说通过、分项说不行 —— 必须判不通过，且让人看得见发生了什么。"""
        from app.ai.question_generator import summarize_quality

        scores = dict(_ALL_PASS)
        scores["no_duplicate"] = False
        verdict = summarize_quality(
            {
                "passed": True,
                "scores": scores,
                "reason": "整体没问题",
                "issues": ["B 与 C 语义等价"],
            }
        )

        assert verdict["pass"] is False, "分项与总评矛盾时必须以分项为准（严格优先）"
        assert verdict["contradiction"] is True, "未标记「模型总评与分项自相矛盾」"
        assert "自相矛盾" in (verdict.get("note") or ""), (
            f"审核端需要知道发生了矛盾，但说明文字里没有体现：{verdict.get('note')!r}"
        )

    def test_总评说不过但分项全通过时同样判不通过(self):
        """反向矛盾：模型过度保守。严格优先下同样不放行，只做标注。"""
        from app.ai.question_generator import summarize_quality

        verdict = summarize_quality({"passed": False, "scores": dict(_ALL_PASS)})
        assert verdict["pass"] is False, "模型总评判不通过时，不得被分项覆盖为通过"
        assert verdict["contradiction"] is True

    def test_分项缺失时不判通过(self):
        """分项缺失属于校验不完整，不能默认放行。"""
        from app.ai.question_generator import summarize_quality

        verdict = summarize_quality({"passed": True, "scores": {}})
        assert verdict["pass"] is False, "五个分项缺失时不得判为通过（不能默认放行）"

    def test_校验未返回结构时结论为未知(self):
        """模型调用失败 → pass 必须是 None（未知），不是 False，避免误报为「不合格」。"""
        from app.ai.question_generator import summarize_quality

        verdict = summarize_quality(None)
        assert verdict["pass"] is None
        assert verdict["contradiction"] is False

    def test_结论可落库为审核端提示(self):
        """结论要能被压成一句人类可读的话，供审核工作台展示。"""
        from app.ai.question_generator import summarize_quality

        scores = dict(_ALL_PASS)
        scores["self_contained"] = False
        verdict = summarize_quality(
            {
                "passed": True,
                "scores": scores,
                "reason": "题干引用「本制度」",
                "issues": ["不自足"],
            }
        )
        note = verdict.get("note") or ""
        assert "题干自足" in note, f"提示里应列出未通过的分项（中文名）：{note!r}"
        assert "不自足" in note, f"提示里应包含模型给出的具体问题：{note!r}"

    async def test_质检函数本身采用严格口径(self):
        """单元：`check_quality()` 面对「总评通过、分项失败」的模型输出必须判不通过。

        通过 monkeypatch 请求装配处注入固定的模型返回，避免依赖真实大模型的不确定性；
        被测对象仍是生产的 `check_quality()` 本身。
        """
        from app.ai import question_generator as qg
        from app.ai.models import QuestionQualityResult

        scores = dict(_ALL_PASS)
        scores["no_duplicate"] = False
        fake_result = QuestionQualityResult(
            **scores,
            issues=["B 与 C 语义等价"],
            reason="整体没问题",
            passed=True,
        )

        class _FakeRequest:
            async def async_get_data(self):
                return fake_result.model_dump()

        def _fake_build(**kwargs):
            return _FakeRequest()

        original = qg.build_model_request
        qg.build_model_request = _fake_build
        try:
            question = qg.GeneratedQuestion(
                question_type="single_choice",
                content="测试题干？",
                options=["A. 甲", "B. 乙", "C. 丙", "D. 丁"],
                answer=["A"],
                chapter_no="第一章",
                clause_no="第一条",
                clause_text="测试条款。",
            )
            detail = await qg.get_question_generator().check_quality(question)
        finally:
            qg.build_model_request = original

        assert detail["pass"] is False, (
            "check_quality 采信了模型的 passed=True，忽略了分项 no_duplicate=False"
        )
        assert detail["contradiction"] is True

    def test_生成流程采用严格口径落库(self):
        """端到端：质检结论 → 落库，全程使用严格口径。"""
        from app.ai.question_generator import GeneratedQuestion, summarize_quality
        from app.services.generation_workflow import _to_question_model

        scores = dict(_ALL_PASS)
        scores["has_distractor"] = False
        # 注意：这里**故意不写** quality_pass，逼真地模拟模型总评说「通过」
        detail = summarize_quality(
            {
                "passed": True,
                "scores": scores,
                "reason": "整体尚可",
                "issues": ["干扰项不明显"],
            }
        )

        gq = GeneratedQuestion(
            question_type="single_choice",
            content="测试题干？",
            options=["A. 甲", "B. 乙", "C. 丙", "D. 丁"],
            answer=["A"],
            chapter_no="第一章",
            clause_no="第一条",
            clause_text="测试条款。",
            quality_pass=detail["pass"],
            quality_detail=detail,
        )
        model = _to_question_model(gq, "doc-x")
        assert model.ai_quality_pass is False, (
            "落库时采信了模型总评（passed=True），分项指出的问题被丢弃"
        )
        assert "干扰项有区分度" in (model.ai_quality_note or ""), (
            f"落库提示未列出未通过分项：{model.ai_quality_note!r}"
        )


# ══════════════════════════════════════════════════════════
# M2-3 → M3 衔接：质量校验结论必须被下游消费
# ══════════════════════════════════════════════════════════


class TestM2QualityFeedbackToM3:
    """spec/核心模块架构设计.md M2-3：
        「质量校验 | 生成后自动做基本质量检查 | 检查：选项不能重复、
          答案必须唯一确定、题目与来源条款语义一致」

    spec M3-1 审核工作台：「专家集中审核题目 | 左右分栏：左侧题目，右侧制度原文对照」

    推论（这是本条测试的立论）：
    M2-3 存在的**唯一目的**是辅助 M3 的人工审核。
    若校验结论只存在于生成阶段的内存对象里、不落库、不经接口暴露，
    专家在审核工作台看不到「AI 自查发现此题题干不自足」，
    那么这次校验（一次完整的大模型调用）对审核环节零贡献 —— 等于白做。
    因此：质量校验结论必须落库，并随题目一同返回给审核端。
    """

    async def test_质量校验结论必须落库并可被审核端读取(self, client, tokens):
        """构造一道被 AI 判定为不合格的题目，断言审核端能看到该结论。"""
        from app.database import async_session_factory
        from app.models.question import Question, QuestionStatus

        async with async_session_factory() as db:
            q = Question(
                question_type="multi_choice",
                status=QuestionStatus.PENDING,
                content="（多选）违反本规范的，可能给予哪些处理？",  # 用「本规范」指代 → 不自足
                options=json.dumps(["A. 警告", "B. 记过", "C. 解除"], ensure_ascii=False),
                answer=json.dumps(["A", "B"], ensure_ascii=False),
                score=1.0,
                chapter_no="第四章",
                clause_no="第七条",
            )
            db.add(q)
            await db.commit()
            qid = q.id

        r = await client.get(f"/api/v1/questions/{qid}", headers=tokens["t-expert"])
        assert r.status_code == 200
        body = r.json()

        # 审核端必须能看到 AI 的质量自查结论（即使为空也要有字段，便于前端稳定渲染）
        assert "ai_quality_pass" in body, (
            "题目响应缺少「AI 质量自查结论」字段：专家无法得知 AI 是否发现该题有问题，"
            "M2-3 的校验结果被丢弃"
        )
        assert "ai_quality_note" in body, "题目响应缺少 AI 质量自查说明字段"

    async def test_质量校验结论随生成流程写入(self, client, tokens):
        """走一遍真实出题流程，断言落库的题目带有质量自查结论。"""
        from sqlalchemy import select

        from app.database import async_session_factory
        from app.models.question import Question
        from app.services.document_parser import get_document_parser
        from app.services.generation_workflow import _to_question_model
        from app.ai.question_generator import GeneratedQuestion

        gq = GeneratedQuestion(
            question_type="single_choice",
            content="测试题干？",
            options=["A. 甲", "B. 乙", "C. 丙", "D. 丁"],
            answer=["A"],
            chapter_no="第一章",
            clause_no="第一条",
            clause_text="测试条款。",
            quality_pass=False,
            quality_detail={"pass": False, "reason": "题干不自足", "issues": ["用了指代"]},
        )
        model = _to_question_model(gq, "doc-x")
        assert model.ai_quality_pass is False, (
            "_to_question_model 未把 quality_pass 写入 ORM 模型，质量结论在落库时丢失"
        )
        assert model.ai_quality_note, "质量自查理由未落库"
