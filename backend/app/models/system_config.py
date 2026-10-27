"""系统配置与同步记录模型 — M7 系统管理的持久化载体。

背景（spec M7-1 / M7-2）：
- 「SSO 配置页：协议选择（OIDC / LDAP / SAML）+ 参数配置（Client ID、Secret、Endpoint）
   + 连接测试按钮 + 状态反馈」——配置必须落库，否则刷新即丢
- 「组织架构页：HR 系统对接配置（API 地址、密钥）+ 同步状态（上次同步时间、同步结果）
   + 同步频率设置 + 部门树/人员列表预览」——同样需要持久化与历史记录

设计说明：
- `SystemConfig` 采用 **分类 + 键值** 结构而非为每类配置建表：
  SSO 与 HR 对接的字段会随对接方不同而变化（OIDC 要 issuer，LDAP 要 base_dn…），
  键值结构能容纳差异而不必频繁改表。
- 敏感字段（client_secret / api_key）存储时**不加密**仅作原型，
  但读取接口一律做掩码返回，避免密钥通过接口泄露给前端。
"""

from __future__ import annotations

from sqlalchemy import Boolean, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class SystemConfig(Base, UUIDMixin, TimestampMixin):
    """系统配置项（分类 + 键值）。"""

    __tablename__ = "system_configs"

    category: Mapped[str] = mapped_column(
        String(50), index=True, comment="配置分类：sso / hr / notify / general"
    )
    key: Mapped[str] = mapped_column(String(100), comment="配置键")
    # 值统一存 JSON 字符串，兼容布尔/数字/嵌套结构
    value: Mapped[str] = mapped_column(Text, default="null", comment="配置值（JSON）")
    # 标记敏感字段：读取时掩码、写入时空值表示「不修改」
    is_secret: Mapped[bool] = mapped_column(
        Boolean, default=False, comment="是否敏感字段"
    )


class OrgSyncRecord(Base, UUIDMixin, TimestampMixin):
    """组织架构同步记录（spec M7-2「同步状态：上次同步时间、同步结果」）。"""

    __tablename__ = "org_sync_records"

    trigger: Mapped[str] = mapped_column(
        String(20), default="manual", comment="触发方式：manual / scheduled"
    )
    status: Mapped[str] = mapped_column(
        String(20), default="success", comment="结果：success / failed / partial"
    )
    department_count: Mapped[int] = mapped_column(Integer, default=0, comment="同步部门数")
    user_count: Mapped[int] = mapped_column(Integer, default=0, comment="同步人员数")
    created_count: Mapped[int] = mapped_column(Integer, default=0, comment="新增人数")
    updated_count: Mapped[int] = mapped_column(Integer, default=0, comment="更新人数")
    deactivated_count: Mapped[int] = mapped_column(Integer, default=0, comment="停用人数")
    message: Mapped[str | None] = mapped_column(Text, nullable=True, comment="同步说明/错误")
    operator_id: Mapped[str | None] = mapped_column(String(36), nullable=True, comment="操作者ID")
    operator_name: Mapped[str | None] = mapped_column(String(100), nullable=True, comment="操作者姓名")
    finished_at: Mapped[str | None] = mapped_column(String(30), nullable=True, comment="完成时间")