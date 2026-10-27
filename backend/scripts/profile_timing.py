"""耗时剖析脚本 — 定位向量库/AI 初始化各阶段的实际开销。

在容器内运行：
    docker run --rm -v "<项目路径>/backend":/app/backend -w /app/backend \
        ai-exam-backend:1 python scripts/profile_timing.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

_START = time.time()
_LAST = _START


def lap(label: str) -> float:
    """记录一个阶段的耗时（输入为上一阶段的起点）。"""
    global _LAST
    now = time.time()
    print(f"  {label:<40s} {now - _LAST:7.2f}s")
    _LAST = now
    return now


def main() -> int:
    print("阶段计时（容器内）")
    print("─" * 52)
    base = time.time()

    import chromadb
    from chromadb.config import Settings

    lap("import chromadb")

    from app.ai.agently_setup import get_embedding_agent

    lap("import app.ai + 配置加载")

    conn = chromadb.PersistentClient(
        path="/tmp/timing_chroma", settings=Settings(anonymized_telemetry=False)
    )
    lap("chromadb PersistentClient 初始化")

    from agently.integrations.chromadb import ChromaCollection

    col = ChromaCollection(
        collection_name="timing_probe",
        conn=conn,
        embedding_agent=get_embedding_agent(),
        get_or_create=True,
    )
    lap("ChromaCollection 创建（含嵌入 Agent）")

    col.add(
        [
            {"document": f"测试条款内容第{i}条", "metadata": {"i": i}, "id": f"t{i}"}
            for i in range(3)
        ]
    )
    lap("写入 3 条（1 次嵌入 API 调用）")

    col.query("测试条款", top_n=2)
    lap("检索（1 次嵌入 API 调用）")

    from app.ai.vector_store import get_vector_store

    get_vector_store()
    lap("vector_store 单例初始化")

    print("─" * 52)
    print(f"  {'合计':<40s} {time.time() - base:7.2f}s")
    print(f"  {'（含 Python 解释器与依赖导入）':<38s}")
    print("─" * 52)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())