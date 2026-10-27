"""知识库 — 制度条款语义检索（Agently + Chroma + DashScope text-embedding-v4）。

依据 spec/技术选型方案.md「知识库体系」：
- 向量数据库：Chroma（嵌入式，与 FastAPI 同一进程）
- 向量模型：阿里 DashScope text-embedding-v4（1024 维）
- 存储内容：制度文档按条款分段向量化，附带元数据（制度名称、章、节、条款编号）
- 用途：语义检索 —— 出题时检索与考点语义相关的全文条款，
        确保题目覆盖全面、不遗漏关联条款

依据 spec 的模块归属表，知识库模块的向量化同样由 Agently 承担
（`agently.integrations.chromadb.ChromaCollection` + 嵌入 Agent）。
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from app.ai.agently_setup import get_embedding_agent, is_embedding_available
from app.config import get_settings

logger = logging.getLogger(__name__)

_settings = get_settings()


class VectorStore:
    """制度条款向量库（Chroma 嵌入式，进程内单例）。"""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._conn: Any = None
        self._collection: Any = None
        self._init_error: str | None = None

    # ── 初始化 ──

    def _ensure_collection(self) -> Any | None:
        """惰性初始化 Chroma 集合。初始化失败返回 None（不阻断主流程）。"""
        if self._collection is not None:
            return self._collection
        if self._init_error is not None:
            return None

        with self._lock:
            if self._collection is not None:
                return self._collection

            if not is_embedding_available():
                self._init_error = "DASHSCOPE_API_KEY 未配置，向量库不可用"
                logger.warning(self._init_error)
                return None

            try:
                import chromadb
                from chromadb.config import Settings

                from agently.integrations.chromadb import ChromaCollection

                self._conn = chromadb.PersistentClient(
                    path=_settings.CHROMA_PERSIST_DIR,
                    settings=Settings(anonymized_telemetry=False),
                )
                self._collection = ChromaCollection(
                    collection_name=_settings.CHROMA_COLLECTION,
                    conn=self._conn,
                    embedding_agent=get_embedding_agent(),
                    get_or_create=True,
                    hnsw_space="cosine",
                )
                logger.info(
                    "向量库就绪：dir=%s collection=%s model=%s",
                    _settings.CHROMA_PERSIST_DIR,
                    _settings.CHROMA_COLLECTION,
                    _settings.EMBEDDING_MODEL,
                )
                return self._collection
            except Exception as exc:  # noqa: BLE001 - 向量库异常不应阻断出题主流程
                self._init_error = f"{type(exc).__name__}: {exc}"
                logger.error("向量库初始化失败：%s", self._init_error)
                return None

    # ── 写入 ──

    def index_document(
        self,
        *,
        doc_id: str,
        doc_name: str,
        chapters: list[dict[str, Any]],
    ) -> int:
        """把一份制度的所有条款向量化并写入向量库。

        Args:
            doc_id: 制度 ID（用于后续按制度过滤检索）
            doc_name: 制度名称
            chapters: 解析出的章节树

        Returns:
            实际写入的条款数量（向量库不可用时返回 0）
        """
        collection = self._ensure_collection()
        if collection is None:
            return 0

        items: list[dict[str, Any]] = []
        for ch in chapters or []:
            ch_no = ch.get("chapter_no", "") or ""
            for clause in ch.get("clauses", []) or []:
                text = (clause.get("text") or "").strip()
                if not text:
                    continue
                clause_no = clause.get("clause_no", "") or ""
                section_no = clause.get("section_no", "") or ""
                # 稳定且唯一的 id：制度 + 章 + 节 + 条款
                point_id = f"{doc_id}|{ch_no}|{section_no}|{clause_no}"
                items.append(
                    {
                        "document": text,
                        "metadata": {
                            "doc_id": doc_id,
                            "doc_name": doc_name,
                            "chapter_no": ch_no,
                            "section_no": section_no,
                            "clause_no": clause_no,
                        },
                        "id": point_id,
                    }
                )

        if not items:
            return 0

        try:
            with self._lock:
                # 同一制度重复索引时先清理旧向量，避免残留
                self._delete_by_doc_id(doc_id)
                collection.add(items)
            logger.info("向量库已写入 %s 条条款（制度：%s）", len(items), doc_name)
            return len(items)
        except Exception as exc:  # noqa: BLE001
            logger.error("向量库写入失败：%s: %s", type(exc).__name__, exc)
            return 0

    # ── 检索 ──

    def search(
        self,
        query: str,
        *,
        doc_id: str | None = None,
        top_k: int | None = None,
        max_distance: float | None = None,
    ) -> list[dict[str, Any]]:
        """语义检索相关条款。

        Args:
            query: 查询文本（如考点描述）
            doc_id: 限定在某份制度内检索
            top_k: 返回条数
            max_distance: 距离阈值（越小越相关），超过则丢弃

        Returns:
            [{document, metadata, distance, id}, ...]；不可用时返回空列表
        """
        collection = self._ensure_collection()
        if collection is None or not query.strip():
            return []

        k = top_k or _settings.RETRIEVAL_TOP_K
        where = {"doc_id": doc_id} if doc_id else None

        try:
            with self._lock:
                hits = collection.query(
                    query.strip(),
                    top_n=k,
                    where=where,
                    distance=max_distance,
                )
            return list(hits or [])
        except Exception as exc:  # noqa: BLE001
            logger.error("向量库检索失败：%s: %s", type(exc).__name__, exc)
            return []

    def search_related_clauses(
        self,
        *,
        doc_id: str,
        query: str,
        exclude_clause_no: str = "",
        top_k: int | None = None,
        max_distance: float = 0.6,
    ) -> list[str]:
        """检索与考点相关的其他条款文本（用于补充出题上下文）。

        排除条款自身，避免把考点条款重复塞进 prompt。
        """
        hits = self.search(query, doc_id=doc_id, top_k=(top_k or _settings.RETRIEVAL_TOP_K) + 2,
                           max_distance=max_distance)
        results: list[str] = []
        for hit in hits:
            meta = hit.get("metadata") or {}
            if exclude_clause_no and meta.get("clause_no") == exclude_clause_no:
                continue
            doc = hit.get("document")
            if doc:
                results.append(str(doc))
            if len(results) >= (top_k or _settings.RETRIEVAL_TOP_K):
                break
        return results

    # ── 删除 ──

    def _delete_by_doc_id(self, doc_id: str) -> None:
        """删除某制度的全部向量（内部调用，需已持有锁）。"""
        if self._collection is None:
            return
        try:
            self._collection._collection.delete(where={"doc_id": doc_id})  # noqa: SLF001
        except Exception as exc:  # noqa: BLE001 - 首次索引时集合为空属正常
            logger.debug("清理旧向量时未命中（可忽略）：%s", exc)

    def delete_document(self, doc_id: str) -> bool:
        """删除某制度的全部向量。"""
        collection = self._ensure_collection()
        if collection is None:
            return False
        try:
            with self._lock:
                self._delete_by_doc_id(doc_id)
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("向量库删除失败：%s: %s", type(exc).__name__, exc)
            return False

    # ── 状态 ──

    def stats(self) -> dict[str, Any]:
        """向量库状态（用于健康检查 / 系统管理页展示）。"""
        collection = self._ensure_collection()
        if collection is None:
            return {"available": False, "error": self._init_error, "count": 0}
        try:
            count = collection._collection.count()  # noqa: SLF001
            return {
                "available": True,
                "error": None,
                "count": count,
                "collection": _settings.CHROMA_COLLECTION,
                "model": _settings.EMBEDDING_MODEL,
                "persist_dir": _settings.CHROMA_PERSIST_DIR,
            }
        except Exception as exc:  # noqa: BLE001
            return {"available": False, "error": str(exc), "count": 0}


_store_singleton: VectorStore | None = None


def get_vector_store() -> VectorStore:
    global _store_singleton
    if _store_singleton is None:
        _store_singleton = VectorStore()
    return _store_singleton