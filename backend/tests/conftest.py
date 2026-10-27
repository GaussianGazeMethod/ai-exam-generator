"""共享测试夹具。

为什么必须有 conftest.py（这是一次真实翻车的产物）：
两个测试文件各自在模块顶层写 `os.environ["DATABASE_URL"] = ".../xxx_test.db"`，
但 pytest 把同一次运行的所有测试文件导入**同一个进程** —— 后导入的会覆盖前者的
环境变量，于是两个文件实际上共用同一个库文件，又各自在 fixture 里
`os.remove(...)`，互相把对方的表和数据删掉，表现为
`UNIQUE constraint failed: departments.name` 这类莫名其妙的全量 ERROR。

现在把数据库路径与 client/tokens 夹具统一放在这里，只定义一次：
- 库名固定为 spec_test.db（单进程单库），避免两个文件互相覆盖环境变量
- client 夹具只在会话开始时删一次库
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 统一测试库（必须在 import app 之前设置）
TEST_DB = "./spec_test.db"
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{TEST_DB}"


@pytest.fixture(scope="session")
async def client():
    """ASGI 直连应用（含 lifespan，故调度器会正常启停）。

    不用真实 uvicorn 端口：本套件不测「后台任务与响应并发」的场景，
    ASGITransport 更快更稳，也避免端口占用与事件循环冲突。
    """
    if os.path.exists(TEST_DB):
        os.remove(TEST_DB)

    import app.config as config_module

    config_module.get_settings.cache_clear()

    import httpx

    from app.database import init_db
    from app.main import app

    # 进入一次 lifespan，等价于真实启动（建表 + 启动到期扫描调度器）
    async with app.router.lifespan_context(app):
        await init_db()
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(
            transport=transport, base_url="http://spec-test", timeout=300.0
        ) as c:
            yield c


# 六个角色（spec 场景说明文档 §角色清单）
_ROLES: list[tuple[str, str, str, str]] = [
    ("t-expert", "专家", "expert", "tech"),
    ("t-hr", "HR管理员", "hr_admin", "tech"),
    ("t-employee", "员工甲", "employee", "tech"),
    ("t-mgr", "技术部经理", "dept_manager", "tech"),
    ("t-compliance", "合规负责人", "compliance", "legal"),
    ("t-it", "IT管理员", "it_admin", "tech"),
]


@pytest.fixture(scope="session")
async def tokens(client):
    """预置 6 类角色用户并登录。"""
    from sqlalchemy import select

    from app.database import async_session_factory
    from app.models.user import Department, User, UserRole

    async with async_session_factory() as db:
        tech = Department(name="技术部")
        legal = Department(name="法务部")
        db.add_all([tech, legal])
        await db.flush()
        id_map = {"tech": tech.id, "legal": legal.id}
        for ext, name, role, dept_key in _ROLES:
            db.add(
                User(
                    name=name,
                    role=UserRole(role),
                    department_id=id_map[dept_key],
                    external_id=ext,
                )
            )
        await db.commit()

    out: dict[str, dict] = {}
    async with async_session_factory() as db:
        for ext, *_ in _ROLES:
            u = (await db.execute(select(User).where(User.external_id == ext))).scalar_one()
            r = await client.post("/api/v1/auth/dev-login", params={"user_id": u.id})
            out[ext] = {"Authorization": f"Bearer {r.json()['access_token']}"}
    return out
