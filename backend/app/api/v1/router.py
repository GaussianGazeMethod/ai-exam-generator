"""API v1 路由聚合。"""

from fastapi import APIRouter

from app.api.v1.auth import router as auth_router
from app.api.v1.documents import router as documents_router
from app.api.v1.exam_taking import router as exam_taking_router
from app.api.v1.exams import router as exams_router
from app.api.v1.generation import router as generation_router
from app.api.v1.notifications import router as notifications_router
from app.api.v1.questions import router as questions_router
from app.api.v1.statistics import router as statistics_router
from app.api.v1.system import router as system_router

api_router = APIRouter()

api_router.include_router(auth_router, prefix="/auth", tags=["S8-认证"])
api_router.include_router(documents_router, prefix="/documents", tags=["S1-制度文档"])
api_router.include_router(generation_router, prefix="/generate", tags=["S2-AI生成"])
api_router.include_router(questions_router, prefix="/questions", tags=["S3-题库"])
api_router.include_router(exams_router, prefix="/exams", tags=["S4-考试管理"])
api_router.include_router(exam_taking_router, prefix="/exam-taking", tags=["S5-在线答题"])
api_router.include_router(statistics_router, prefix="/statistics", tags=["S6-统计看板"])
api_router.include_router(notifications_router, prefix="/notifications", tags=["S7-通知"])
api_router.include_router(system_router, prefix="/system", tags=["S8-系统管理"])