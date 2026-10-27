"""数据库引擎与会话管理 — SQLAlchemy 2.0 异步风格。"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.config import get_settings

# ── 将 Base 统一放在 models/base.py，这里只引用 ──
from app.models.base import Base  # noqa: F401  # re-export

settings = get_settings()

engine = create_async_engine(
    settings.DATABASE_URL,
    # SQL 回显由独立开关控制（DEBUG 下混杂 SQL 语句会淹没业务输出）
    echo=settings.SQL_ECHO,
    # SQLite 需要 check_same_thread=False 以支持异步
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {},
)

async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


async def get_db() -> AsyncSession:  # type: ignore[misc]
    """FastAPI 依赖：每请求一个数据库会话。"""
    async with async_session_factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


async def init_db() -> None:
    """创建所有表 + 补齐缺失列（原型阶段使用，生产用 Alembic 迁移）。

    `create_all` 只建**新表**，对已存在的表**不会补列**。
    开发阶段频繁加字段，若不补列，老库会在查询时报
    `no such column`。这里做一次轻量列对齐：只做「加列」，不删不改，
    避免误伤数据；生产环境仍应由 Alembic 管理迁移。
    """
    # 确保所有模型已注册到 Base.metadata
    from app.models import (  # noqa: F401
        answer,
        audit_log,
        document,
        exam,
        notification,
        question,
        system_config,
        user,
    )

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_add_missing_columns)


def _add_missing_columns(conn) -> None:  # type: ignore[no-untyped-def]
    """对已有表补齐模型中新增的列（仅加列，不改类型、不删列）。"""
    from sqlalchemy import inspect, text

    inspector = inspect(conn)
    existing_tables = set(inspector.get_table_names())

    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        have = {c["name"] for c in inspector.get_columns(table.name)}
        for column in table.columns:
            if column.name in have:
                continue
            # 用 SQLite/PG 通用的 ADD COLUMN 语法；类型由 SQLAlchemy 编译
            ddl = f'ALTER TABLE "{table.name}" ADD COLUMN {column.name} {column.type.compile(conn.dialect)}'
            default = column.default
            if default is not None and getattr(default, "is_scalar", False):
                ddl += f" DEFAULT {default.arg!r}"
            try:
                conn.execute(text(ddl))
            except Exception:  # noqa: BLE001 - 加列失败不应阻断启动（如非空无默认）
                pass