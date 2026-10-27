"""知识库（生产模块 vector_store）验证脚本。

在容器内运行：
    docker run --rm -v "<项目路径>":/app -w /app/backend ai-exam-backend:1 \
        python scripts/test_vector_store.py

验证 app/ai/vector_store.py 的完整能力：
1. 向量库初始化与状态
2. 按制度索引入库（条款切分 + 元数据）
3. 按制度过滤的语义检索
4. 关联条款检索（排除条款自身）
5. 按制度删除向量
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.ai.vector_store import get_vector_store  # noqa: E402

DOC_ID = "doc-vs-test"

CHAPTERS = [
    {
        "chapter_no": "第三章",
        "sections": [{"section_no": "第一节", "title": "存储加密"}],
        "clauses": [
            {
                "clause_no": "第五条",
                "chapter_no": "第三章",
                "section_no": "第一节",
                "text": "敏感数据必须采用 AES-256 算法加密后存储。",
            },
            {
                "clause_no": "第六条",
                "chapter_no": "第三章",
                "section_no": "第一节",
                "text": "禁止将客户数据存储在个人设备上，包括个人电脑、手机、U盘等便携存储介质。",
            },
        ],
    },
    {
        "chapter_no": "第七章",
        "sections": [],
        "clauses": [
            {
                "clause_no": "第十二条",
                "chapter_no": "第七章",
                "section_no": "",
                "text": "员工差旅报销需在费用发生后 15 个工作日内提交审批。",
            },
        ],
    },
]

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    (PASSED if ok else FAILED).append(name)
    print(f"  {'[成功]' if ok else '[失败]'} {name}" + (f" — {detail}" if detail else ""))


def main() -> int:
    print("=" * 70)
    print("知识库（vector_store）验证")
    print("=" * 70)

    vs = get_vector_store()

    print("\n【1】初始化与状态")
    stats0 = vs.stats()
    print(f"     {stats0}")
    check("向量库可用", bool(stats0.get("available")), str(stats0.get("error") or ""))
    if not stats0.get("available"):
        return 1

    print("\n【2】按制度索引入库")
    vs.delete_document(DOC_ID)  # 清理历史
    n = vs.index_document(doc_id=DOC_ID, doc_name="数据安全管理规范", chapters=CHAPTERS)
    print(f"     写入条款数：{n}")
    check("条款成功入库", n == 3, f"期望 3 条，实际 {n}")

    print("\n【3】语义检索（按制度过滤）")
    hits = vs.search("能把客户资料放到自己的手机上吗", doc_id=DOC_ID, top_k=2)
    for h in hits:
        meta = h.get("metadata") or {}
        label = f"{meta.get('chapter_no', '')} {meta.get('clause_no', '')}".strip()
        dist = h.get("distance")
        dist_s = f"{dist:.4f}" if isinstance(dist, (int, float)) else str(dist)
        print(f"       · [{label}] {str(h.get('document', ''))[:40]}  (distance={dist_s})")
    check("检索有命中", len(hits) > 0, f"{len(hits)} 条")
    if hits:
        top = str(hits[0].get("document", ""))
        check("top1 语义相关", "个人设备" in top or "个人电脑" in top, top[:34])

    print("\n【4】关联条款检索（排除条款自身）")
    related = vs.search_related_clauses(
        doc_id=DOC_ID, query="敏感数据加密要求与密钥管理", exclude_clause_no="第五条", top_k=2
    )
    for r in related:
        print(f"       → {r[:44]}")
    check("返回关联条款", len(related) > 0, f"{len(related)} 条")
    check("未包含被排除条款", all("AES-256" not in r for r in related))

    print("\n【5】按制度删除")
    ok = vs.delete_document(DOC_ID)
    stats1 = vs.stats()
    print(f"     删除结果：{ok}，剩余向量数：{stats1.get('count')}")
    check("删除成功", ok)
    check("向量数已回落",
          int(stats1.get("count", 0)) <= int(stats0.get("count", 0)),
          f"{stats0.get('count')} → {stats1.get('count')}")

    total = len(PASSED) + len(FAILED)
    print("\n" + "=" * 70)
    print(f"结果：通过 {len(PASSED)}/{total}")
    if FAILED:
        print("失败项：" + "；".join(FAILED))
    print("=" * 70)
    return 0 if not FAILED else 1


if __name__ == "__main__":
    raise SystemExit(main())