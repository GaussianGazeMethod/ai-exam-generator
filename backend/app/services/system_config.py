"""系统配置持久化服务 — M7-1 SSO 配置 / M7-2 HR 对接配置。

spec M7-1：「SSO 配置页：协议选择（OIDC/LDAP/SAML）+ 参数配置（Client ID、Secret、
Endpoint 等）+ 连接测试按钮 + 状态反馈」——配置必须落库，否则刷新即丢。

spec M7-2：「组织架构页：HR 系统对接配置（API 地址、密钥）+ 同步状态
（上次同步时间、同步结果）+ 手动触发同步 + 同步频率设置」——同样需要持久化。

设计要点：
- 分类 + 键值存储（SystemConfig），容纳不同对接方的差异化字段
- **敏感字段掩码**：client_secret / api_key 读取时返回 ``******``，
  写入时收到掩码值则视为「不修改」，避免前端回显导致密钥被清空或泄露
- 提供默认值，未配置时也能返回结构完整的表单
"""

from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.system_config import SystemConfig

logger = logging.getLogger(__name__)

MASK = "******"

# 各分类的字段定义：key -> (默认值, 是否敏感)
SSO_FIELDS: dict[str, tuple[Any, bool]] = {
    "enabled": (False, False),
    "protocol": ("oidc", False),          # oidc / ldap / saml
    "client_id": ("", False),
    "client_secret": ("", True),          # 敏感
    "endpoint": ("", False),              # 授权端点 / LDAP URL
    "issuer": ("", False),                # OIDC issuer
    "base_dn": ("", False),               # LDAP Base DN
    "redirect_uri": ("", False),
    "auto_create_user": (True, False),    # 首次登录是否自动建用户
    "default_role": ("employee", False),  # 自动建用户的默认角色
}

HR_FIELDS: dict[str, tuple[Any, bool]] = {
    "enabled": (False, False),
    "api_url": ("", False),
    "api_key": ("", True),                # 敏感
    "sync_frequency": ("daily_2am", False),  # daily_2am / hourly / manual
    "sync_departments": (True, False),
    "sync_users": (True, False),
    "deactivate_missing": (False, False),  # 源系统不存在的人员是否停用
}

# 角色权限矩阵：保存的是一个 list 结构，用单键承载
ROLES_FIELDS: dict[str, tuple[Any, bool]] = {
    "matrix": ([], False),
}

FIELDS_BY_CATEGORY: dict[str, dict[str, tuple[Any, bool]]] = {
    "sso": SSO_FIELDS,
    "hr": HR_FIELDS,
    "roles": ROLES_FIELDS,
}


async def load_config(
    db: AsyncSession, category: str, *, reveal_secrets: bool = False
) -> dict[str, Any]:
    """读取某分类配置，缺失字段用默认值补齐。

    Args:
        reveal_secrets: 仅内部服务对接使用；API 一律 False（返回掩码）
    """
    fields = FIELDS_BY_CATEGORY.get(category, {})
    result: dict[str, Any] = {k: v[0] for k, v in fields.items()}

    rows = list(
        (
            await db.execute(select(SystemConfig).where(SystemConfig.category == category))
        )
        .scalars()
        .all()
    )
    # 先记录「库中是否存在非空值」，再做掩码 ——
    # 否则掩码后的 ****** 会让「已配置」判定永远为 False
    secret_configured: dict[str, bool] = {k: False for k, v in fields.items() if v[1]}

    for row in rows:
        try:
            value = json.loads(row.value)
        except (json.JSONDecodeError, TypeError):
            value = row.value

        if row.key in secret_configured and row.is_secret:
            secret_configured[row.key] = value not in (None, "", MASK)

        if row.is_secret and not reveal_secrets and value:
            # 已配置的敏感字段返回掩码，前端据此显示「已配置」而非明文
            value = MASK
        result[row.key] = value

    # 显式标记「敏感字段是否已配置」，避免前端靠字符串比较判断
    result["_secret_configured"] = secret_configured
    return result


async def save_config(
    db: AsyncSession, category: str, values: dict[str, Any], *, actor: Any = None
) -> dict[str, Any]:
    """保存某分类配置。

    规则：
    - 敏感字段收到掩码 ``******`` 或未提供 → 保留原值（不覆盖）
    - 未知键被忽略（防止前端误传脏数据入库）
    """
    fields = FIELDS_BY_CATEGORY.get(category, {})
    if not fields:
        raise ValueError(f"未知配置分类：{category}")

    existing = {
        row.key: row
        for row in (
            await db.execute(select(SystemConfig).where(SystemConfig.category == category))
        )
        .scalars()
        .all()
    }

    for key, value in values.items():
        if key not in fields:
            continue
        default, is_secret = fields[key]

        if is_secret and value in (MASK, "", None):
            # 掩码 / 空值 → 保留原值（前端未修改该字段）
            continue

        row = existing.get(key)
        if row is None:
            row = SystemConfig(category=category, key=key, is_secret=is_secret)
            db.add(row)
        row.value = json.dumps(value, ensure_ascii=False)

    await db.flush()
    logger.info("配置已保存 category=%s keys=%s", category, list(values.keys()))
    return await load_config(db, category)


def is_configured(config: dict[str, Any]) -> bool:
    """判断配置是否具备启用条件（连接测试/同步的前置校验）。

    必须**按分类**判断：SSO 用 endpoint + client_id/secret，
    HR 用 api_url + api_key。曾因只按 SSO 字段判断，
    导致 HR 配置填好了仍被判定为「未配置」。
    """
    if not config.get("enabled"):
        return False

    secrets_ok = config.get("_secret_configured", {}) or {}
    # 依据配置里出现的关键字段推断分类（调用方也常直接传 load_config 的结果）
    if "protocol" in config:
        protocol = str(config.get("protocol") or "").lower()
        if not config.get("endpoint"):
            return False
        if protocol == "ldap":
            return bool(config.get("base_dn"))
        return bool(config.get("client_id")) and bool(secrets_ok.get("client_secret"))

    if "api_url" in config:
        return bool(config.get("api_url")) and bool(secrets_ok.get("api_key"))

    # 未知分类：只要求启用
    return True
