"""FastAPI 应用入口。"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.v1.router import api_router
from app.config import get_settings
from app.database import init_db
from app.services.scheduler import start_scheduler, stop_scheduler

logger = logging.getLogger(__name__)

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):  # type: ignore[arg-type]
    """应用生命周期：建表 + 启动到期扫描调度器。

    spec M4 明确要求「自动截止：定时任务，到达截止时间后自动将考试置为已截止」。
    开发/原型用进程内 asyncio 周期任务；生产换 Celery Beat，
    判定逻辑在 app/services/scheduler.py 中可直接复用。
    """
    await init_db()
    start_scheduler()
    logger.info("%s 已启动", settings.APP_NAME)
    try:
        yield
    finally:
        await stop_scheduler()


app = FastAPI(
    title=settings.APP_NAME,
    version="0.1.0",
    docs_url="/docs",
    redoc_url="/redoc",
    lifespan=lifespan,
)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册业务路由
app.include_router(api_router, prefix="/api/v1")


@app.get("/health", tags=["系统"])
async def health_check() -> dict[str, str]:
    return {"status": "ok", "app": settings.APP_NAME}