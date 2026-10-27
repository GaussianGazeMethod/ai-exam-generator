"""用户 Schema。"""

from __future__ import annotations
from typing import Optional

from datetime import datetime

from pydantic import BaseModel


class UserBase(BaseModel):
    name: str
    email: Optional[str] = None
    role: str = "employee"
    department_id: Optional[str] = None


class UserCreate(UserBase):
    external_id: Optional[str] = None


class UserUpdate(BaseModel):
    name: Optional[str] = None
    role: Optional[str] = None
    department_id: Optional[str] = None
    is_active: Optional[bool] = None


class DepartmentCreate(BaseModel):
    name: str
    parent_id: Optional[str] = None


class DepartmentResponse(BaseModel):
    id: str
    name: str
    parent_id: Optional[str] = None
    external_id: Optional[str] = None

    model_config = {"from_attributes": True}


class UserResponse(BaseModel):
    id: str
    name: str
    email: Optional[str] = None
    role: str
    department_id: Optional[str] = None
    department_name: Optional[str] = None
    is_active: bool = True
    last_login_at: Optional[str] = None
    created_at: datetime | str

    model_config = {"from_attributes": True}


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class UserListResponse(BaseModel):
    items: list[UserResponse]
    total: int


class RoleConfig(BaseModel):
    role: str
    name: str
    permissions: list[str]  # 功能权限列表
    data_scope: str = "all"  # 数据范围: all / department