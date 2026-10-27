"""S8 系统管理路由 — 组织架构 / 权限 / 审计日志。"""

from __future__ import annotations
from datetime import datetime
from typing import Optional

from fastapi import HTTPException, APIRouter, Depends, Query
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_db, get_current_user, require_role
from app.models.audit_log import AuditLog
from app.models.system_config import OrgSyncRecord, SystemConfig
from app.models.user import Department, User, UserRole
from app.services import system_config as sysconf
from app.services.audit import record_audit
from app.schemas.user import (
    DepartmentCreate,
    DepartmentResponse,
    RoleConfig,
    UserCreate,
    UserListResponse,
    UserResponse,
    UserUpdate,
)
from app.schemas.common import DEFAULT_PAGE_SIZE, MAX_PAGE_SIZE

router = APIRouter()

# ── 组织架构 ──


@router.get("/departments", response_model=list[DepartmentResponse], summary="部门列表")
async def list_departments(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Department).order_by(Department.name))
    return result.scalars().all()


@router.post("/departments", response_model=DepartmentResponse, summary="创建部门")
async def create_department(
    body: DepartmentCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("it_admin")),
):
    dept = Department(**body.model_dump())
    db.add(dept)
    await db.flush()
    return dept


@router.get("/users", response_model=UserListResponse, summary="人员列表")
async def list_users(
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    role: Optional[str] = None,
    department_id: Optional[str] = None,
    keyword: Optional[str] = None,
    is_active: Optional[bool] = Query(
        None, description="按启用状态筛选；不传则不过滤"
    ),
    db: AsyncSession = Depends(get_db),
):
    """人员列表。

    注意：FastAPI 会**静默忽略**未声明的查询参数（返回 200 但不过滤），
    因此前端用到的筛选项必须在此显式声明，否则会出现「传了参数却没生效」
    且没有报错的排查陷阱（见 spec/开发自测清单.md 1.11）。
    """
    query = select(User)
    if role:
        query = query.where(User.role == role)
    if department_id:
        query = query.where(User.department_id == department_id)
    if keyword:
        query = query.where(User.name.contains(keyword))
    if is_active is not None:
        query = query.where(User.is_active == is_active)

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    result = await db.execute(
        query.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    )
    items = result.scalars().all()

    # 组织架构页要展示「所属部门」名称；一次性取部门映射，避免前端为每行再查一次
    dept_names = {d.id: d.name for d in (await db.execute(select(Department))).scalars().all()}

    return UserListResponse(
        items=[
            UserResponse(
                id=u.id,
                name=u.name,
                email=u.email,
                role=u.role.value,
                department_id=u.department_id,
                department_name=dept_names.get(u.department_id) if u.department_id else None,
                is_active=u.is_active,
                last_login_at=u.last_login_at,
                created_at=u.created_at,
            )
            for u in items
        ],
        total=total,
    )


@router.post("/users", response_model=UserResponse, summary="创建用户")
async def create_user(
    body: UserCreate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("it_admin")),
):
    user = User(**body.model_dump())
    db.add(user)
    await db.flush()
    return UserResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        role=user.role.value,
        department_id=user.department_id,
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


@router.put("/users/{user_id}", response_model=UserResponse, summary="更新用户")
async def update_user(
    user_id: str,
    body: UserUpdate,
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("it_admin")),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="用户不存在")
    # role 必须转成 UserRole 枚举再赋值：
    # UserUpdate.role 是 str，直接 setattr 会把裸字符串写进 ORM 的 Enum 列，
    # 随后 user.role.value 抛 AttributeError: 'str' object has no attribute 'value'（500）。
    for key, val in body.model_dump(exclude_none=True).items():
        if key == "role":
            user.role = UserRole(val) if isinstance(val, str) else val
        else:
            setattr(user, key, val)
    await db.flush()
    await db.refresh(user)
    return UserResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        role=user.role.value,
        department_id=user.department_id,
        is_active=user.is_active,
        last_login_at=user.last_login_at,
        created_at=user.created_at,
    )


# ── 组织架构同步 ──


@router.post("/org/sync", summary="手动触发 HR 系统同步")
async def trigger_org_sync(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("it_admin")),
):
    """手动触发组织架构同步，并记录同步结果。

    spec M7-2：「对接 HR 系统 API，定时同步；部门树、人员列表」+
    「同步状态（上次同步时间、同步结果）」。

    原型阶段行为：
    - 若 HR 对接**未启用**（未配置或不完整）→ 记录一条 failed 记录并明确说明原因，
      同时在响应里标注这是原型阶段未对接，**不伪造「同步成功」**。
    - 若已启用 → 记录一条 success 记录，统计当前库内部门/人员规模
      （真实调用 HR API 待接入，此处不伪装成已对接）。
    """
    config = await sysconf.load_config(db, "hr")
    hr_ready = sysconf.is_configured(config)

    now_text = datetime.now().isoformat(timespec="seconds")

    if not hr_ready:
        record = OrgSyncRecord(
            trigger="manual",
            status="failed",
            message=(
                "HR 系统对接未启用或配置不完整：请在「组织架构」页填写 API 地址与密钥并启用。"
                "原型阶段未对接真实 HR 系统，本次未同步任何数据。"
            ),
            operator_id=current_user.id,
            operator_name=current_user.name,
            finished_at=now_text,
        )
        db.add(record)
        await db.flush()
        await record_audit(
            db, action="sync", user=current_user, target_type="system",
            detail={"trigger": "manual", "status": "failed", "reason": "hr_not_configured"},
        )
        return {
            "status": "failed",
            "record_id": record.id,
            "message": record.message,
            "prototype": True,
        }

    # 已配置：统计当前库内规模（真实 HR 拉取待接入）
    dept_count = (await db.execute(select(func.count()).select_from(Department))).scalar() or 0
    user_count = (await db.execute(select(func.count()).select_from(User))).scalar() or 0

    record = OrgSyncRecord(
        trigger="manual",
        status="success",
        department_count=dept_count,
        user_count=user_count,
        created_count=0,
        updated_count=0,
        deactivated_count=0,
        message=(
            f"已按配置执行同步流程：当前部门 {dept_count} 个、人员 {user_count} 名。"
            "注：原型阶段未发起真实 HR API 调用，计数为本地库现状。"
        ),
        operator_id=current_user.id,
        operator_name=current_user.name,
        finished_at=now_text,
    )
    db.add(record)
    await db.flush()
    await record_audit(
        db, action="sync", user=current_user, target_type="system",
        detail={
            "trigger": "manual",
            "status": "success",
            "department_count": dept_count,
            "user_count": user_count,
        },
    )
    return {
        "status": "success",
        "record_id": record.id,
        "message": record.message,
        "department_count": dept_count,
        "user_count": user_count,
        "prototype": True,
    }


# ── 权限管理 ──


@router.get("/roles", summary="角色与权限配置")
async def get_roles(_: User = Depends(require_role("it_admin"))):
    """返回预设角色及权限矩阵（与 spec 对齐）。"""
    roles: list[RoleConfig] = [
        RoleConfig(
            role="expert",
            name="制度专家",
            permissions=[
                "document:upload", "document:view", "document:delete",
                "question:review", "question:edit", "question:view",
                "statistics:question_quality",
            ],
            data_scope="all",
        ),
        RoleConfig(
            role="hr_admin",
            name="HR 管理员",
            permissions=[
                "exam:create", "exam:edit", "exam:publish", "exam:view",
                "exam:progress", "exam:remind", "exam:terminate",
                "statistics:overview", "statistics:export",
            ],
            data_scope="all",
        ),
        RoleConfig(
            role="employee",
            name="员工",
            permissions=["exam:take", "exam:view_result"],
            data_scope="self",
        ),
        RoleConfig(
            role="dept_manager",
            name="部门管理者",
            permissions=["statistics:dept_view"],
            data_scope="department",
        ),
        RoleConfig(
            role="compliance",
            name="合规 / 高层",
            permissions=["statistics:compliance", "statistics:export"],
            data_scope="all",
        ),
        RoleConfig(
            role="it_admin",
            name="IT 管理员",
            permissions=["system:*"],
            data_scope="all",
        ),
    ]
    return roles


# ── 审计日志 ──


@router.get("/audit-logs", summary="审计日志查询")
async def get_audit_logs(
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
    action: Optional[str] = Query(None, description="按操作类型精确筛选"),
    user_id: Optional[str] = None,
    start_time: Optional[str] = Query(
        None, description="起始时间（含），ISO 格式如 2026-09-01 或 2026-09-01T00:00:00"
    ),
    end_time: Optional[str] = Query(
        None, description="结束时间（含），ISO 格式如 2026-09-30 或 2026-09-30T23:59:59"
    ),
    keyword: Optional[str] = Query(
        None, description="关键字：匹配操作者姓名或操作详情"
    ),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("it_admin", "compliance")),
):
    """审计日志查询。

    spec M7-4「审计日志页」要求支持时间范围筛选 + 操作类型筛选。
    参数未声明时 FastAPI 会静默忽略（返回 200 但不过滤），
    故这里把所有前端用到的筛选项显式声明。
    """
    query = select(AuditLog).order_by(AuditLog.created_at.desc())
    if action:
        query = query.where(AuditLog.action == action)
    if user_id:
        query = query.where(AuditLog.user_id == user_id)

    # 时间范围：允许只传日期（自动补全为当日 00:00:00 / 23:59:59）
    start_dt = _parse_datetime(start_time, end_of_day=False)
    end_dt = _parse_datetime(end_time, end_of_day=True)
    if start_dt is not None:
        query = query.where(AuditLog.created_at >= start_dt)
    if end_dt is not None:
        query = query.where(AuditLog.created_at <= end_dt)

    if keyword:
        query = query.where(
            or_(AuditLog.user_name.contains(keyword), AuditLog.detail.contains(keyword))
        )

    count_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(count_query)).scalar() or 0

    result = await db.execute(query.offset((page - 1) * page_size).limit(page_size))
    items = result.scalars().all()
    return {
        "items": [
            {
                "id": log.id,
                "created_at": log.created_at,
                "user_id": log.user_id,
                "user_name": log.user_name,
                "action": log.action,
                "target_type": log.target_type,
                "target_id": log.target_id,
                "detail": log.detail,
                "ip_address": log.ip_address,
            }
            for log in items
        ],
        "total": total,
    }


def _parse_datetime(value: Optional[str], *, end_of_day: bool) -> Optional[datetime]:
    """宽松解析日期/日期时间字符串。

    支持 `2026-09-21`、`2026-09-21 10:30:00`、`2026-09-21T10:30:00`；
    只给日期时，起始补 00:00:00、结束补 23:59:59，符合用户直觉。
    """
    if not value:
        return None
    text = value.strip().replace("T", " ")
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text, fmt)
        except ValueError:
            continue
        if fmt == "%Y-%m-%d" and end_of_day:
            dt = dt.replace(hour=23, minute=59, second=59)
        return dt
    # 交给 fromisoformat 兜底（支持带时区等写法）
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


# ──────────────────────────────────────────────────────────
# M7-1 SSO 配置（持久化）
# ──────────────────────────────────────────────────────────


@router.get("/sso", summary="读取 SSO 配置")
async def get_sso_config(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("it_admin")),
):
    """spec M7-1：SSO 配置页读取。

    敏感字段（client_secret）返回掩码，前端据此显示「已配置」状态，
    不会把真实密钥下发到浏览器。
    """
    config = await sysconf.load_config(db, "sso")
    return {"config": config, "ready": sysconf.is_configured(config)}


@router.put("/sso", summary="保存 SSO 配置")
async def save_sso_config(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("it_admin")),
):
    """保存 SSO 配置。

    client_secret 传掩码 ``******`` 或留空表示「不修改」。
    """
    try:
        config = await sysconf.save_config(db, "sso", payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await record_audit(
        db,
        action="config",
        user=current_user,
        target_type="system",
        detail={"category": "sso", "protocol": config.get("protocol"), "enabled": config.get("enabled")},
    )
    return {"config": config, "ready": sysconf.is_configured(config), "message": "SSO 配置已保存"}


@router.post("/sso/test", summary="SSO 连接测试")
async def test_sso_connection(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("it_admin")),
):
    """连接测试。

    原型阶段做**配置完整性校验**（不实际发起外网握手），
    并在响应中明确标注这是配置校验而非真实连通性验证，
    避免把「校验通过」误当成「已接通 IdP」。
    """
    config = await sysconf.load_config(db, "sso")
    problems: list[str] = []
    protocol = str(config.get("protocol") or "").lower()

    if not config.get("enabled"):
        problems.append("SSO 尚未启用")
    if not config.get("endpoint"):
        problems.append("未填写 Endpoint")
    if protocol == "ldap":
        if not config.get("base_dn"):
            problems.append("LDAP 协议需要填写 Base DN")
    else:
        if not config.get("client_id"):
            problems.append("未填写 Client ID")
        if not config.get("_secret_configured", {}).get("client_secret"):
            problems.append("未填写 Client Secret")

    return {
        "ok": not problems,
        "mode": "config_validation",
        "note": "原型阶段仅做配置完整性校验，未发起真实 IdP 握手；生产需实测连通性",
        "protocol": protocol,
        "problems": problems,
    }


# ──────────────────────────────────────────────────────────
# M7-2 HR 系统对接（持久化 + 同步历史）
# ──────────────────────────────────────────────────────────


@router.get("/hr-config", summary="读取 HR 系统对接配置")
async def get_hr_config(
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("it_admin")),
):
    config = await sysconf.load_config(db, "hr")
    return {"config": config, "ready": sysconf.is_configured(config)}


@router.put("/hr-config", summary="保存 HR 系统对接配置")
async def save_hr_config(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("it_admin")),
):
    try:
        config = await sysconf.save_config(db, "hr", payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    await record_audit(
        db,
        action="config",
        user=current_user,
        target_type="system",
        detail={
            "category": "hr",
            "api_url": config.get("api_url"),
            "sync_frequency": config.get("sync_frequency"),
        },
    )
    return {"config": config, "ready": sysconf.is_configured(config), "message": "HR 对接配置已保存"}


@router.get("/org/sync-records", summary="组织架构同步历史")
async def list_sync_records(
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    _: User = Depends(require_role("it_admin")),
):
    """spec M7-2：「同步状态（上次同步时间、同步结果）」。"""
    rows = list(
        (
            await db.execute(
                select(OrgSyncRecord).order_by(OrgSyncRecord.created_at.desc()).limit(limit)
            )
        )
        .scalars()
        .all()
    )
    items = [
        {
            "id": r.id,
            "trigger": r.trigger,
            "status": r.status,
            "department_count": r.department_count,
            "user_count": r.user_count,
            "created_count": r.created_count,
            "updated_count": r.updated_count,
            "deactivated_count": r.deactivated_count,
            "message": r.message,
            "operator_name": r.operator_name,
            "finished_at": r.finished_at,
            "created_at": r.created_at,
        }
        for r in rows
    ]
    return {"items": items, "total": len(items), "last": items[0] if items else None}


@router.post("/roles/reset", summary="角色权限恢复默认")
async def reset_roles(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("it_admin")),
):
    """把角色权限矩阵恢复为系统预设（清除自定义覆盖）。"""
    rows = list(
        (
            await db.execute(select(SystemConfig).where(SystemConfig.category == "roles"))
        )
        .scalars()
        .all()
    )
    for row in rows:
        await db.delete(row)
    await db.flush()
    await record_audit(
        db, action="config", user=current_user, target_type="system",
        detail={"category": "roles", "action": "reset_to_default"},
    )
    return {"message": "已恢复预设权限矩阵", "cleared": len(rows)}


@router.put("/roles", summary="保存角色权限矩阵")
async def save_roles(
    payload: dict,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_role("it_admin")),
):
    """保存角色权限矩阵（功能权限 + 数据范围）。

    spec M7-3：「每角色 → 功能权限矩阵（可配置）」「数据权限范围设置」。

    注意：这里保存的是**配置意图**；实际鉴权仍由各接口的 ``require_role``
    与 ``_view_policy_for`` 决定。因此本接口会同时返回
    ``effective_hint``，说明哪些改动需要改代码才真正生效，
    避免出现「界面改了权限但实际没生效」的错觉。
    """
    roles = payload.get("roles")
    if not isinstance(roles, list) or not roles:
        raise HTTPException(status_code=400, detail="请提供 roles 数组")

    valid_roles = {r.value for r in UserRole}
    cleaned: list[dict] = []
    for item in roles:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        if role not in valid_roles:
            continue
        cleaned.append(
            {
                "role": role,
                "name": str(item.get("name") or role),
                "permissions": [str(p) for p in (item.get("permissions") or [])],
                "data_scope": str(item.get("data_scope") or "all"),
            }
        )

    if not cleaned:
        raise HTTPException(status_code=400, detail="没有可保存的有效角色配置")

    await sysconf.save_config(db, "roles", {"matrix": cleaned})
    await record_audit(
        db,
        action="config",
        user=current_user,
        target_type="system",
        detail={"category": "roles", "role_count": len(cleaned)},
    )

    return {
        "message": f"已保存 {len(cleaned)} 个角色的权限配置",
        "roles": cleaned,
        "effective_hint": (
            "权限矩阵已持久化；接口级鉴权仍由后端 require_role 控制，"
            "如需让矩阵直接决定访问控制，需接入动态权限中间件。"
        ),
    }
