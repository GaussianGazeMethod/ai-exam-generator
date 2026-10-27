"""AI 出题异步任务注册表 — 支撑 spec S2 的「异步任务 + 进度查询」。

spec/页面层级树与核心服务模块.md S2 关键设计决策：
    「AI 出题为异步任务 | S2 生成题目是异步的，前端轮询/推送进度 |
      大模型调用耗时可能较长（分钟级），避免前端阻塞」

spec 接口约定：
    POST /api/generate/questions        → 触发异步任务，立即返回 task_id
    GET  /api/generate/tasks/{taskId}   → 查询生成任务进度

实现说明（遵循 spec/技术选型方案.md 2.8 异步任务）：
- 开发/原型：FastAPI BackgroundTasks + 进程内任务注册表（本模块）
- 生产：Celery + Redis，任务状态迁移到 Redis，本模块接口保持不变

Skills 提示：进程内状态不是耐久记录；生产切 Celery 时应把注册表实现换成
Redis 后端，调用方接口不变。
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

TaskStatus = Literal["pending", "running", "completed", "failed"]


@dataclass
class GenerationTask:
    """一次出题任务的进度快照。"""

    task_id: str
    document_id: str
    document_name: str
    status: TaskStatus = "pending"
    progress: int = 0
    message: str = "任务已创建，等待执行"
    generated_count: int = 0
    key_point_count: int = 0
    type_stats: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    created_at: str = ""
    finished_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "document_id": self.document_id,
            "document_name": self.document_name,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "generated_count": self.generated_count,
            "key_point_count": self.key_point_count,
            "type_stats": self.type_stats,
            "errors": self.errors,
            "created_at": self.created_at,
            "finished_at": self.finished_at,
        }


class GenerationTaskRegistry:
    """进程内任务注册表（线程安全）。

    生产环境替换为 Redis 后端时，保持 create/get/update/list_recent 四个方法签名。
    """

    def __init__(self, max_history: int = 200) -> None:
        self._tasks: dict[str, GenerationTask] = {}
        self._order: list[str] = []
        self._lock = threading.RLock()
        self._max_history = max_history

    def create(self, *, document_id: str, document_name: str) -> GenerationTask:
        task = GenerationTask(
            task_id=uuid.uuid4().hex,
            document_id=document_id,
            document_name=document_name,
            created_at=datetime.now().isoformat(timespec="seconds"),
        )
        with self._lock:
            self._tasks[task.task_id] = task
            self._order.append(task.task_id)
            # 控制内存占用：超出上限时淘汰最旧的任务
            while len(self._order) > self._max_history:
                old = self._order.pop(0)
                self._tasks.pop(old, None)
        return task

    def get(self, task_id: str) -> GenerationTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def update(self, task_id: str, **changes: Any) -> GenerationTask | None:
        with self._lock:
            task = self._tasks.get(task_id)
            if task is None:
                return None
            for key, value in changes.items():
                if hasattr(task, key):
                    setattr(task, key, value)
            return task

    def finish(
        self,
        task_id: str,
        *,
        status: TaskStatus,
        message: str,
        progress: int = 100,
    ) -> GenerationTask | None:
        return self.update(
            task_id,
            status=status,
            message=message,
            progress=progress,
            finished_at=datetime.now().isoformat(timespec="seconds"),
        )

    def list_recent(self, limit: int = 20) -> list[GenerationTask]:
        with self._lock:
            ids = list(reversed(self._order[-limit:]))
            return [self._tasks[i] for i in ids if i in self._tasks]


_registry_singleton: GenerationTaskRegistry | None = None


def get_task_registry() -> GenerationTaskRegistry:
    global _registry_singleton
    if _registry_singleton is None:
        _registry_singleton = GenerationTaskRegistry()
    return _registry_singleton