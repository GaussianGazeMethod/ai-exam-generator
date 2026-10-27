"""AI 管线端到端测试（async）— 验证按 Agently-Skills 重构后的实现。

在容器内运行：
    docker run --rm -v "<项目路径>":/app -w /app/backend ai-exam-backend:1 \
        python scripts/test_ai_pipeline.py

覆盖：
1. Agently 配置加载与生效校验（model_pool / model_profiles）
2. 文档解析（python-docx + 编号规则引擎）
3. M2-1 考点提取（异步、并发分批）
4. M2-2 题目生成（异步、有界并发、Pydantic 结构化输出）
5. M2-3 质量校验（结构化模型裁判）
6. 知识库（Agently ChromaCollection + DashScope 嵌入）
7. 全流程 generate_for_document + 并发加速比

说明（遵循 Skills 的验收分类）：
- 结构类断言（字段存在、题型合法、填空含 ____ 等）属确定性硬门禁，用 assert
- 语义质量（是否忠于条款、答案是否唯一）由 M2-3 的模型裁判判定，
  本脚本只汇总裁判结论，不用关键词匹配冒充语义判定
"""

from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        PASSED.append(name)
        print(f"  [成功] {name}" + (f" — {detail}" if detail else ""))
    else:
        FAILED.append(name)
        print(f"  [失败] {name}" + (f" — {detail}" if detail else ""))


def make_test_docx(path: str) -> None:
    """生成一份贴近真实制度格式的测试文档。"""
    import docx

    d = docx.Document()
    d.add_heading("数据安全管理规范", 0)
    d.add_paragraph("为规范公司数据安全管理，保护客户信息安全，特制定本规范。")

    d.add_heading("第一章 总则", level=1)
    d.add_paragraph("第一条 本规范适用于公司全体员工及外包人员。")
    d.add_paragraph("第二条 数据安全管理工作实行统一管理、分级负责的原则。")

    d.add_heading("第二章 数据分类标准", level=1)
    d.add_paragraph("第三条 公司数据分为公开数据、内部数据、敏感数据三级。")
    d.add_paragraph(
        "第四条 客户身份证号、银行账号、联系方式属于敏感数据，须加密存储。"
    )

    d.add_heading("第三章 数据加密要求", level=1)
    d.add_heading("第一节 存储加密", level=2)
    d.add_paragraph("第五条 敏感数据必须采用 AES-256 算法加密后存储。")
    d.add_paragraph(
        "第六条 禁止将客户数据存储在个人设备上，包括个人电脑、手机、U盘等便携存储介质。"
    )
    d.add_paragraph("第七条 加密密钥须每 90 天轮换一次。")

    d.add_heading("第四章 违规处理", level=1)
    d.add_paragraph(
        "第八条 违反本规范的，视情节轻重给予警告、记过直至解除劳动合同。"
    )

    d.save(path)


async def main() -> int:
    print("=" * 74)
    print("AI 管线端到端测试（Agently 4 + DeepSeek + DashScope + Chroma）")
    print("=" * 74)

    # ── 1. Agently 配置 ──
    print("\n【1】Agently 配置加载与生效校验")
    from app.ai.agently_setup import effective_config, setup_agently

    try:
        setup_agently()
        cfg = effective_config()
        print(f"     生效配置：{cfg}")
        check("配置加载成功", bool(cfg))
        check("provider 为 OpenAICompatible", cfg.get("provider") == "OpenAICompatible")
        check("对话模型别名已注册", cfg.get("chat_alias") == "deepseek_profile",
              f"chat={cfg.get('chat_alias')}/{cfg.get('chat_model')}")
        check("嵌入模型别名已注册", cfg.get("embed_alias") == "dashscope_embed_profile",
              f"embed={cfg.get('embed_alias')}/{cfg.get('embed_model')}")
        check("双模型密钥就绪",
              cfg.get("chat_auth_ready") == "yes" and cfg.get("embed_auth_ready") == "yes")
    except Exception as exc:  # noqa: BLE001
        check("配置加载成功", False, f"{type(exc).__name__}: {exc}")
        return 1

    # ── 2. 文档解析 ──
    print("\n【2】文档解析（python-docx + 编号规则引擎）")
    from app.services.document_parser import get_document_parser

    docx_path = "/tmp/ai_pipeline_policy.docx"
    make_test_docx(docx_path)
    parsed = get_document_parser().parse(docx_path, "数据安全管理规范")
    structured = parsed.to_structured_content()
    stats = structured["stats"]
    print(f"     统计：{stats}")
    check("识别到章节结构", stats["chapter_count"] >= 4, f"章数={stats['chapter_count']}")
    check("识别到条款", stats["clause_count"] >= 8, f"条数={stats['clause_count']}")

    # ── 3. 考点提取（异步并发） ──
    print("\n【3】M2-1 考点提取（异步、并发分批）")
    from app.ai.question_generator import get_question_generator, flatten_clauses

    gen = get_question_generator()
    clauses = flatten_clauses(structured["chapters"])
    print(f"     可用条款：{len(clauses)} 条")

    t0 = time.time()
    points, errors = await gen.extract_key_points("数据安全管理规范", clauses)
    extract_secs = time.time() - t0
    print(f"     提取考点：{len(points)} 个（耗时 {extract_secs:.1f}s）")
    if errors:
        print(f"     告警：{errors[:2]}")
    check("提取到考点", len(points) > 0, f"{len(points)} 个")
    if points:
        types = {p.question_type for p in points}
        check("题型在合法集合内",
              types <= {"single_choice", "multi_choice", "true_false", "fill_blank"},
              f"出现题型：{sorted(types)}")
        check("考点带来源条款", all(p.clause_no for p in points))

    # ── 4. 题目生成（异步并发 + 结构化输出） ──
    print("\n【4】M2-2 题目生成（异步、有界并发、Pydantic 契约）")
    if points:
        t0 = time.time()
        semaphore = asyncio.Semaphore(gen.max_concurrency)
        questions = await asyncio.gather(
            *(gen.generate_one(p, "数据安全管理规范", semaphore=semaphore) for p in points[:4])
        )
        gen_secs = time.time() - t0
        ok_questions = [q for q in questions if q is not None]
        print(f"     生成 {len(ok_questions)}/{len(points[:4])} 题（耗时 {gen_secs:.1f}s，"
              f"并发上限 {gen.max_concurrency}）")
        for q in ok_questions:
            print(f"       · [{q.question_type}] {q.content[:44]}")
        check("题目生成成功", len(ok_questions) > 0)

        # 确定性结构门禁
        struct_ok = True
        for q in ok_questions:
            if not q.content or not q.answer:
                struct_ok = False
            if q.question_type == "fill_blank" and "____" not in q.content:
                struct_ok = False
                print(f"       [注意] 填空题缺少空格标记：{q.content[:36]}")
            if q.question_type == "true_false" and q.options != ["正确", "错误"]:
                struct_ok = False
            if q.question_type == "single_choice" and len(q.options) != 4:
                struct_ok = False
            if q.question_type == "multi_choice" and not (2 <= len(q.answer) <= 3):
                struct_ok = False
        check("题目结构符合题型契约", struct_ok)
    else:
        ok_questions = []
        check("题目生成成功", False, "无可用考点")

    # ── 5. 质量校验（结构化模型裁判） ──
    print("\n【5】M2-3 质量校验（结构化模型裁判）")
    if ok_questions:
        semaphore = asyncio.Semaphore(gen.max_concurrency)
        qualities = await asyncio.gather(
            *(gen.check_quality(q, semaphore=semaphore) for q in ok_questions)
        )
        judged = [q for q in qualities if q.get("pass") is not None]
        passed_n = sum(1 for q in judged if q.get("pass"))
        print(f"     校验结论：{passed_n}/{len(judged)} 通过")
        for q, res in zip(ok_questions, qualities):
            scores = res.get("scores") or {}
            flags = "".join("✓" if v else "✗" for v in scores.values())
            print(f"       · [{q.question_type}] pass={res.get('pass')} 分项={flags}")
        check("质量校验返回有效结论", len(judged) > 0, f"{len(judged)} 条有效")
        check("至少一题通过质量校验", passed_n > 0, f"{passed_n} 题通过")
    else:
        check("质量校验返回有效结论", False, "无题目可校验")

    # ── 6. 知识库 ──
    print("\n【6】知识库（Agently ChromaCollection + DashScope 嵌入）")
    try:
        import shutil
        import tempfile

        import chromadb
        from chromadb.config import Settings

        from agently.integrations.chromadb import ChromaCollection

        from app.ai.agently_setup import get_embedding_agent

        persist_dir = tempfile.mkdtemp(prefix="chroma_pipeline_")
        conn = chromadb.PersistentClient(
            path=persist_dir, settings=Settings(anonymized_telemetry=False)
        )
        collection = ChromaCollection(
            collection_name="pipeline_clauses",
            conn=conn,
            embedding_agent=get_embedding_agent(),
            get_or_create=True,
        )

        docs = [
            (c["text"], {"doc_id": "d1", "chapter_no": c["chapter_no"], "clause_no": c["clause_no"]})
            for c in clauses
        ]
        collection.add(
            [{"document": t, "metadata": m, "id": f"k{i}"} for i, (t, m) in enumerate(docs)]
        )
        print(f"     已写入 {len(docs)} 条条款")

        hits = collection.query("客户数据能不能存在自己手机上", top_n=2)
        for h in hits:
            meta = h.get("metadata") or {}
            print(f"       · [{meta.get('clause_no','')}] {str(h.get('document',''))[:38]}"
                  f" (distance={h.get('distance'):.4f})")
        check("知识库写入成功", len(docs) > 0)
        check("语义检索有命中", len(hits) > 0)
        if hits:
            top = str(hits[0].get("document", ""))
            check("检索结果语义相关", "个人设备" in top or "个人电脑" in top,
                  f"top1={top[:30]}")

        shutil.rmtree(persist_dir, ignore_errors=True)
    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc()
        check("知识库可用", False, f"{type(exc).__name__}: {exc}")

    # ── 7. 全流程 + 并发加速比 ──
    print("\n【7】全流程 generate_for_document")
    t0 = time.time()
    result = await gen.generate_for_document(
        "数据安全管理规范", structured["chapters"], max_points=8, do_quality_check=True
    )
    full_secs = time.time() - t0
    print(f"     耗时 {full_secs:.1f}s，生成 {len(result.questions)} 题，"
          f"考点 {result.key_point_count} 个")
    print(f"     题型分布：{result.type_stats}")
    if result.errors:
        print(f"     错误：{result.errors[:3]}")
    check("全流程产出题目", len(result.questions) > 0, f"{len(result.questions)} 题")
    check("题目带来源标注", all(q.source_label for q in result.questions))

    # ── 汇总 ──
    total = len(PASSED) + len(FAILED)
    print("\n" + "=" * 74)
    print(f"结果：通过 {len(PASSED)}/{total}")
    if FAILED:
        print("失败项：")
        for f in FAILED:
            print(f"  · {f}")
    print("=" * 74)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))