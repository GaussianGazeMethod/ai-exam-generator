"""S8 认证路由 — 登录 / Token / 当前用户。"""

from __future__ import annotations
from typing import Optional

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, status
from jose import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_db
from app.config import get_settings
from app.models.user import User, UserRole
from app.schemas.user import LoginResponse, UserResponse
from app.services.audit import record_audit

router = APIRouter()
settings = get_settings()


def _role_str(role) -> str:
    """兼容 Enum 和 str 类型。"""
    return role.value if hasattr(role, "value") else str(role)


def _create_token(user: User) -> str:
    expire = datetime.now() + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    payload = {"sub": user.id, "role": _role_str(user.role), "exp": expire}
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def _user_to_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        role=_role_str(user.role),
        department_id=user.department_id,
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


@router.get("/login", summary="SSO 登录跳转（原型阶段：开发登录）")
async def sso_login_redirect():
    """生产环境：重定向到公司 SSO 页面。开发环境：返回提示。"""
    return {
        "message": "请通过 /api/v1/auth/dev-login 开发登录，或配置 SSO 后使用 /api/v1/auth/sso/callback",
    }


@router.post("/dev-login", response_model=LoginResponse, summary="开发登录（原型阶段）")
async def dev_login(
    request: Request,
    user_id: Optional[str] = None,
    role: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """开发阶段绕过 SSO 的简易登录。

    原型阶段需要按角色验证不同页面的权限，因此支持指定角色：
    - 传入 `role`（如 expert / hr_admin / employee）→ 复用或创建该角色的开发用户
    - 传入 `user_id` → 直接使用指定用户
    - 都不传 → 使用第一个用户，无用户时创建 expert（便于走通上传/出题/审核全链路）

    生产环境改用 `/auth/sso/callback`，本接口不再暴露。
    """
    user: User | None = None

    if user_id:
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()
    elif role:
        try:
            want_role = UserRole(role)
        except ValueError:
            raise HTTPException(
                status_code=400,
                detail=f"未知角色：{role}，可选：{[r.value for r in UserRole]}",
            )
        # 每个角色复用一个固定的开发用户，便于反复切换角色测试
        result = await db.execute(
            select(User).where(User.external_id == f"dev-{want_role.value}").limit(1)
        )
        user = result.scalar_one_or_none()
        if user is None:
            user = User(
                name=f"开发用户-{want_role.value}",
                role=want_role,
                external_id=f"dev-{want_role.value}",
            )
            db.add(user)
            await db.flush()
    else:
        result = await db.execute(select(User).limit(1))
        user = result.scalar_one_or_none()

    if user is None:
        # 默认创建 expert：只有该角色能走通「上传制度 → 出题 → 审核」完整链路
        user = User(
            name="开发用户-expert",
            role=UserRole.EXPERT,
            external_id="dev-expert",
        )
        db.add(user)
        await db.flush()

    user.last_login_at = datetime.now().isoformat()
    token = _create_token(user)

    # spec M7-4：登录属于必须留痕的操作
    await record_audit(
        db,
        action="login",
        user=user,
        target_type="user",
        target_id=user.id,
        detail={
            "role": user.role.value if hasattr(user.role, "value") else str(user.role),
            "mode": "dev",
        },
        request=request,
    )

    return LoginResponse(access_token=token, user=_user_to_response(user))


@router.get("/dev-roles", summary="开发登录可选角色")
async def list_dev_roles():
    """返回开发登录可选的 6 类角色，供登录页选择器使用。"""
    labels = {
        "expert": "制度专家（上传制度 / 审核题目 / 题库）",
        "hr_admin": "HR 管理员（组卷 / 发布考试 / 统计）",
        "employee": "员工（答题）",
        "dept_manager": "部门管理者（本部门统计）",
        "compliance": "合规 / 高层（合规全景）",
        "it_admin": "IT 管理员（系统管理）",
    }
    return {"roles": [{"value": r.value, "label": labels[r.value]} for r in UserRole]}


@router.get("/me", response_model=UserResponse, summary="当前用户信息")
async def get_me(user: User = Depends(get_current_user)):
    return _user_to_response(user)


@router.get("/sso/callback", summary="SSO 回调（生产环境）")
async def sso_callback(code: str, state: str = ""):
    """OIDC / SAML 回调，原型阶段占位。"""
    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="SSO 待配置")