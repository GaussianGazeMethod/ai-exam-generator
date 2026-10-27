"""用户与部门模型 — M7 系统管理。"""

from __future__ import annotations
from typing import Optional

import enum

from sqlalchemy import Boolean, Enum, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDMixin


class UserRole(str, enum.Enum):
    EXPERT = "expert"  # 制度专家
    HR_ADMIN = "hr_admin"  # HR/管理员
    EMPLOYEE = "employee"  # 员工
    DEPT_MANAGER = "dept_manager"  # 部门管理者
    COMPLIANCE = "compliance"  # 合规/高层
    IT_ADMIN = "it_admin"  # IT 管理员


class Department(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "departments"

    name: Mapped[str] = mapped_column(String(100), unique=True, comment="部门名称")
    parent_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, comment="上级部门ID")
    external_id: Mapped[Optional[str]] = mapped_column(String(100), nullable=True, comment="HR 系统外部ID")


class User(Base, UUIDMixin, TimestampMixin):
    __tablename__ = "users"

    name: Mapped[str] = mapped_column(String(100), comment="姓名")
    email: Mapped[Optional[str]] = mapped_column(String(200), unique=True, nullable=True, comment="邮箱")
    external_id: Mapped[Optional[str]] = mapped_column(
        String(200), unique=True, nullable=True, comment="SSO/HR 系统唯一ID"
    )

    role: Mapped[UserRole] = mapped_column(Enum(UserRole), default=UserRole.EMPLOYEE, comment="角色")

    department_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True, comment="所属部门ID")

    is_active: Mapped[bool] = mapped_column(Boolean, default=True, comment="是否启用")
    last_login_at: Mapped[Optional[str]] = mapped_column(String(30), nullable=True, comment="最后登录时间")