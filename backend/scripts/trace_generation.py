"""AI 出题全流程追踪 — 逐步展示每一步的真实输入 / Prompt / 模型原始返回。

用途：人工审阅 AI 出题的完整过程，而不是只看最终结果。

运行：
    docker run --rm -v "<项目路径>/backend":/app/backend -w /app/backend \
        ai-exam-backend:1 python scripts/trace_generation.py [文档路径]

默认使用内置示例制度（不依赖已有数据）。
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

W = 78


def hr(title: str = "", char: str = "═") -> None:
    if title:
        print(f"\n{char * 3} {title} ".ljust(W, char))
    else:
        print(char * W)


def sub(title: str) -> None:
    print(f"\n{'─' * 3} {title} ".ljust(W, "─"))


def block(text: str, indent: int = 2) -> None:
    pad = " " * indent
    for line in str(text).splitlines() or [""]:
        print(f"{pad}{line}")


def make_sample_docx(path: str) -> None:
    """造一份贴近真实制度的样例文档。"""
    import docx

    d = docx.Document()
    d.add_heading("数据安全管理规范", 0)
    d.add_paragraph("为规范公司数据安全管理，保护客户信息安全，特制定本规范。")

    d.add_heading("第一章 总则", level=1)
    d.add_paragraph("第一条 本规范适用于公司全体员工及外包人员。")
    d.add_paragraph("第二条 数据安全管理工作实行统一管理、分级负责的原则。")

    d.add_heading("第二章 数据分类标准", level=1)
    d.add_paragraph("第三条 公司数据分为公开数据、内部数据、敏感数据三级。")
    d.add_paragraph("第四条 客户身份证号、银行账号、联系方式属于敏感数据，须加密存储。")

    d.add_heading("第三章 数据加密要求", level=1)
    d.add_heading("第一节 存储加密", level=2)
    d.add_paragraph("第五条 敏感数据必须采用 AES-256 算法加密后存储。")
    d.add_paragraph("第六条 禁止将客户数据存储在个人设备上，包括个人电脑、手机、U盘等便携存储介质。")

    d.add_heading("第四章 违规处理", level=1)
    d.add_paragraph("第七条 违反本规范的，视情节轻重给予警告、记过直至解除劳动合同。")
    d.save(path)


async def main() -> int:
    docx_path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/trace_policy.docx"
    if not Path(docx_path).exists():
        make_sample_docx(docx_path)
        print(f"（已生成样例制度文档：{docx_path}）")

    doc_name = Path(docx_path).stem

    # ══════════════════════════════════════════════════════
    hr("AI 出题全流程追踪")
    print(f"  制度文档：{docx_path}")
    print(f"  制度名称：{doc_name}")
    print(f"  管线：解析 → 向量索引 → 考点提取 → 题目生成 → 质量校验 → 落库")

    # ══════════════════════════════════════════════════════
    # 阶段 1：文档解析
    # ══════════════════════════════════════════════════════
    hr("阶段 1/6　文档解析（M1-2 格式解析 + M1-3 结构识别）", "─")
    print("  引擎：python-docx + 自研编号规则引擎（正则识别 第X章/第Y节/第Z条）")
    print("  spec 依据：M1「输出：结构化制度文档（JSON：章节树 + 条款文本 + 层级关系）」")

    from app.services.document_parser import get_document_parser

    parsed = get_document_parser().parse(docx_path, doc_name)
    structured = parsed.to_structured_content()

    print(f"\n  解析方式：{structured['parse_method']}")
    print(f"  解析统计：{structured['stats']}")
    if structured["warnings"]:
        print(f"  告警：{structured['warnings']}")

    sub("输出的章节树（这就是后续所有环节的数据基础）")
    for ch in structured["chapters"]:
        print(f"  【{ch['chapter_no']}】{ch['title']}")
        for sec in ch["sections"]:
            print(f"      └ {sec['section_no']} {sec['title']}")
        for cl in ch["clauses"]:
            sec = f"[{cl['section_no']}]" if cl["section_no"] else ""
            print(f"      {cl['clause_no']}{sec} {cl['text'][:52]}")
    print(f"\n  → 共 {structured['stats']['clause_count']} 条可用条款")

    from app.ai.question_generator import flatten_clauses

    clauses = flatten_clauses(structured["chapters"])

    # ══════════════════════════════════════════════════════
    # 阶段 2：向量索引
    # ══════════════════════════════════════════════════════
    hr("阶段 2/6　建立向量索引（知识库）", "─")
    print("  引擎：Agently 官方 ChromaCollection + 阿里 DashScope text-embedding-v4")
    print("  spec 依据：技术选型「制度文档按条款分段向量化，附带元数据（制度名称/章/节/条款编号）」")

    from app.ai.agently_setup import get_embedding_agent
    from app.ai.vector_store import get_vector_store

    vs = get_vector_store()
    doc_id = "trace-doc"
    vs.delete_document(doc_id)
    n_indexed = await asyncio.to_thread(
        vs.index_document, doc_id=doc_id, doc_name=doc_name, chapters=structured["chapters"]
    )
    print(f"\n  索引条款数：{n_indexed}")
    stats = vs.stats()
    print(f"  向量库状态：{stats}")

    sub("语义检索演示（出题时会用同制度关联条款补充上下文）")
    demo_query = "客户数据能不能存在自己手机上"
    print(f'  查询："{demo_query}"')
    hits = vs.search(demo_query, doc_id=doc_id, top_k=3)
    for h in hits:
        meta = h.get("metadata") or {}
        dist = h.get("distance")
        print(f"    · [距离 {dist:.4f}] {meta.get('chapter_no','')} {meta.get('clause_no','')}"
              f"  {str(h.get('document',''))[:44]}")

    # ══════════════════════════════════════════════════════
    # 阶段 3：考点提取
    # ══════════════════════════════════════════════════════
    hr("阶段 3/6　考点提取（M2-1）", "─")
    print("  引擎：Agently 4 + DeepSeek（结构化输出由 Pydantic 契约约束）")
    print("  spec 依据：M2-1「从条款中识别适合出题的知识点，判断适合什么题型」")

    from app.ai.prompts import EXAM_EXPERT_ROLE, build_extraction_prompt

    from app.ai.question_generator import get_question_generator

    gen = get_question_generator()
    batch = clauses[:6]
    parts = build_extraction_prompt(
        doc_name,
        "\n".join(f"[{c['chapter_no']} {c['section_no']} {c['clause_no']}] {c['text']}" for c in batch),
    )

    sub("发给模型的完整 Prompt")
    print("  ┌─ system（请求级角色设定，对应 messages[1]，role=system）")
    block(EXAM_EXPERT_ROLE, 4)
    print("  ├─ input（运行时事实）")
    block(parts.input, 4)
    print("  ├─ info（权威规则与领域约束）")
    block(parts.info, 4)
    print("  ├─ instruct（转换规则）")
    block(parts.instruct, 4)
    print("  └─ output（Pydantic 契约，由 framework 投影为字段要求）")
    from app.ai.models import KeyPointExtraction, to_agently_output_spec

    block(json.dumps(to_agently_output_spec(KeyPointExtraction), ensure_ascii=False, default=str)[:400], 4)

    print("\n  ⏳ 调用模型…")
    points, errors = await gen.extract_key_points(doc_name, batch, batch_size=6, max_points=6)

    sub(f"模型返回并解析后的考点（{len(points)} 个）")
    if errors:
        print(f"  告警：{errors}")
    for i, p in enumerate(points, 1):
        tl = {"single_choice": "单选题", "multi_choice": "多选题",
              "true_false": "判断题", "fill_blank": "填空题"}.get(p.question_type, p.question_type)
        print(f"  {i}. 来源：{p.chapter_no} {p.clause_no}")
        print(f"     条款原文：{p.clause_text[:60]}")
        print(f"     → 分配题型：{tl}")
        print(f"     考点：{p.key_point}")
        print(f"     题型理由：{p.reason}")
        print()

    if not points:
        print("  [失败] 未提取到考点，后续阶段跳过")
        return 1

    # ══════════════════════════════════════════════════════
    # 阶段 4：题目生成（逐题型展示）
    # ══════════════════════════════════════════════════════
    hr("阶段 4/6　题目生成（M2-2）", "─")
    print("  spec 依据：M2-2「单选：1 正确 + 3 干扰项；多选：N 正确 + 合理干扰项；")
    print("            判断：正确的命题/错误的命题；填空：关键术语挖空」")

    from app.ai.prompts import build_generation_prompt

    shown: set[str] = set()
    generated = []
    for p in points:
        if p.question_type in shown:
            continue
        shown.add(p.question_type)

        r = await gen.generate_one(p, doc_name)
        if r is None:
            continue
        generated.append(r)

        gp = build_generation_prompt(
            question_type=p.question_type,
            document_name=doc_name,
            chapter_no=p.chapter_no,
            clause_no=p.clause_no,
            clause_text=p.clause_text,
        )
        tl = {"single_choice": "单选题", "multi_choice": "多选题",
              "true_false": "判断题", "fill_blank": "填空题"}[p.question_type]

        hr(f"　题型：{tl}　（{p.chapter_no} {p.clause_no}）", "·")
        print("  【Prompt · input】")
        block(gp.input, 4)
        print("  【Prompt · info】")
        block(gp.info, 4)
        print("  【Prompt · instruct】（命题铁律，四题型各有专属规则）")
        block(gp.instruct, 4)

        print("  【模型返回 → 结构化结果】")
        print(f"     题干：{r.content}")
        for opt in r.options:
            print(f"       {opt}")
        print(f"     答案：{r.answer}")
        print(f"     解析：{r.explanation}")
        print(f"     来源标注：{r.source_label}")

    # ══════════════════════════════════════════════════════
    # 阶段 5：质量校验
    # ══════════════════════════════════════════════════════
    hr("阶段 5/6　质量校验（M2-3，模型裁判）", "─")
    print("  spec 依据：M2-3「检查选项不重复、答案唯一确定、题目与来源条款语义一致」")
    print("  实现：独立一次 Agently 调用，用 Pydantic 契约约束 5 项布尔判定 + 结论")

    from app.ai.prompts import QUALITY_AUDITOR_ROLE, build_quality_check_prompt

    target = generated[0]
    qparts = build_quality_check_prompt(
        json.dumps(
            {"question_type": target.question_type, "content": target.content,
             "options": target.options, "answer": target.answer},
            ensure_ascii=False,
        ),
        target.clause_text,
    )
    print("\n  【Prompt · system】")
    block(QUALITY_AUDITOR_ROLE, 4)
    print("  【Prompt · input（待校验题目）】")
    block(qparts.input, 4)
    print("  【Prompt · info（条款原文）】")
    block(qparts.info, 4)
    print("  【Prompt · instruct（五项校验标准 + 规则前置）】")
    block(qparts.instruct, 4)

    print("\n  ⏳ 调用模型…")
    quality = await gen.check_quality(target)
    sub("模型裁判结论")
    scores = quality.get("scores") or {}
    labels = {
        "consistency": "忠于原文",
        "unambiguous": "答案唯一",
        "no_duplicate": "选项不重复",
        "has_distractor": "干扰项有区分度",
        "self_contained": "题干自足",
    }
    for k, label in labels.items():
        mark = "[成功]" if scores.get(k) else "[失败]"
        print(f"    {mark} {label}")
    print(f"    结论：pass = {quality.get('pass')}")
    print(f"    理由：{quality.get('reason')}")
    if quality.get("issues"):
        print(f"    问题：{quality['issues']}")

    # ══════════════════════════════════════════════════════
    # 阶段 6：落库
    # ══════════════════════════════════════════════════════
    hr("阶段 6/6　落库为待审核题目（S3）", "─")
    print("  spec 依据：M2→M3「审核关卡：M2 的产出必须经过 M3 人工审核才能进入 M4」")
    print("  状态机：待审核 → 已通过 / 已弃用")

    result = await gen.generate_for_document(
        doc_name, structured["chapters"], max_points=6, do_quality_check=True
    )
    print(f"\n  全流程产出：{len(result.questions)} 题，耗时 {result.duration_seconds:.1f}s")
    print(f"  题型分布：{result.type_stats}")
    print(f"  考点数：{result.key_point_count}")
    if result.errors:
        print(f"  告警：{result.errors[:3]}")

    sub("最终题目（将以「待审核」状态入库，等待专家审核）")
    for i, q in enumerate(result.questions, 1):
        tl = {"single_choice": "单选题", "multi_choice": "多选题",
              "true_false": "判断题", "fill_blank": "填空题"}.get(q.question_type, q.question_type)
        qm = "[成功]" if q.quality_pass else ("[失败]" if q.quality_pass is False else "—")
        print(f"  {i}. [{tl}] {qm}")
        print(f"     {q.content[:70]}")
        for opt in q.options:
            print(f"       {opt}")
        print(f"     答案：{q.answer}")
        print(f"     来源：{q.source_label}")
        print()

    vs.delete_document(doc_id)
    hr()
    print("追踪结束。以上每一步均为真实调用（DeepSeek 出题 + DashScope 向量化）")
    hr()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
