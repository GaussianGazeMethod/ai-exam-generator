"""审计日志记录 — M7-4「操作全程记录」。

spec/核心模块架构设计.md 模块七 M7-4：
    「审计日志 | 操作全程记录 | 登录、上传、审核、组卷、答题、导出等全操作日志；IP 记录」

spec/页面层级树与核心服务模块.md S8：
    「审计日志 | 关键操作全量记录：登录/上传/审核/组卷/答题/查看/导出/配置变更」

设计要点：
- **不侵入业务逻辑**：以一行 `await audit(...)` 调用，失败不影响主流程
  （审计写入失败不应让用户的正常业务操作失败，但会记 warning 便于排查）
- **统一的 action 词表**：与前端审计页的筛选项一致，避免出现
  「日志里有记录但按类型筛不到」的问题
- **IP 记录**：从请求头/客户端信息提取，兼容反向代理的 X-Forwarded-For
"""

from __future__ import annotations

import json
import logging
from typing import Any, Literal, Optional

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.audit_log import AuditLog
from app.models.user import User

logger = logging.getLogger(__name__)

# 操作类型词表（与前端审计页筛选项、spec S8 的描述保持一致）
AuditAction = Literal[
    "login",          # 登录
    "logout",         # 登出
    "upload",         # 上传制度
    "parse",          # 解析/重新解析制度
    "generate",       # 触发 AI 出题
    "review",         # 审核题目（通过/弃用）
    "question_edit",  # 编辑题目
    "compose",        # 组卷/创建考试
    "publish",        # 发布考试
    "remind",         # 催促未考人员
    "terminate",      # 终止考试
    "exam_take",      # 答题（开始/提交）
    "export",         # 导出报表
    "view",           # 敏感数据查看
    "config",         # 配置变更
    "sync",           # 组织架构同步
]

ACTION_LABELS: dict[str, str] = {
    "login": "登录",
    "logout": "登出",
    "upload": "上传制度",
    "parse": "解析制度",
    "generate": "AI 出题",
    "review": "审核题目",
    "question_edit": "编辑题目",
    "compose": "组卷",
    "publish": "发布考试",
    "remind": "催促未考",
    "terminate": "终止考试",
    "exam_take": "答题",
    "export": "导出报表",
    "view": "查看数据",
    "config": "配置变更",
    "sync": "组织同步",
}

TargetType = Literal["document", "question", "exam", "answer", "user", "system"]


def client_ip(request: Optional[Request]) -> Optional[str]:
    """提取客户端 IP，兼容反向代理。"""
    if request is None:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        # 取第一段为原始客户端
        return forwarded.split(",")[0].strip()[:50]
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip.strip()[:50]
    if request.client:
        return (request.client.host or "")[:50] or None
    return None


async def record_audit(
    db: AsyncSession,
    *,
    action: AuditAction,
    user: Optional[User] = None,
    target_type: Optional[TargetType] = None,
    target_id: Optional[str] = None,
    detail: Optional[dict[str, Any] | str] = None,
    request: Optional[Request] = None,
    ip_address: Optional[str] = None,
) -> Optional[AuditLog]:
    """写入一条审计日志。

    本函数**不抛异常**：审计失败不应阻断用户的正常操作。
    失败时记 warning 并返回 None。
    """
    try:
        if isinstance(detail, dict):
            detail_text = json.dumps(detail, ensure_ascii=False)[:2000]
        else:
            detail_text = (detail or None) and str(detail)[:2000]

        log = AuditLog(
            user_id=user.id if user else None,
            user_name=user.name if user else None,
            action=action,
            target_type=target_type,
            target_id=target_id,
            detail=detail_text,
            ip_address=ip_address or client_ip(request),
        )
        db.add(log)
        # 用 flush 而非 commit：与业务操作同事务，
        # 业务回滚时日志一并回滚（避免记录未真正发生的操作）
        await db.flush()
        return log
    except Exception as exc:  # noqa: BLE001 - 审计失败不得影响主流程
        logger.warning("审计日志写入失败 action=%s: %s", action, exc)
        return None


def describe(action: str) -> str:
    """操作类型的中文名（供前端展示，也便于日志排查）。"""
    return ACTION_LABELS.get(action, action)
