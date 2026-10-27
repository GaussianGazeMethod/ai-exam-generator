"""应用配置管理 — 基于 pydantic-settings，从 .env / 环境变量加载。"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── 应用 ──
    APP_NAME: str = "AI试题生成系统"
    DEBUG: bool = True
    CORS_ORIGINS: str = "http://localhost:5173,http://localhost:5174"

    # 对外访问基地址：用于生成考试二维码等需要绝对 URL 的场景。
    # 留空则按请求的 scheme+host 自动推断（开发环境够用）；
    # 生产环境应显式配置为对外域名（如 https://exam.example.com），
    # 因为发布接口在内网地址上被调用时，推断出的 host 考生无法访问。
    PUBLIC_BASE_URL: str = ""

    # ── 数据库 ──
    DATABASE_URL: str = "sqlite+aiosqlite:///./app.db"
    # SQL 语句回显独立开关：DEBUG=true 时默认关闭，避免日志淹没业务输出
    SQL_ECHO: bool = False

    # ── 安全 ──
    SECRET_KEY: str = "dev-secret-change-me"
    JWT_ALGORITHM: str = "HS256"
    JWT_EXPIRE_MINUTES: int = 480  # 8 小时

    # ── AI API：DeepSeek（出题主力模型）──
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com"
    DEEPSEEK_MODEL: str = "deepseek-chat"
    LLM_TEMPERATURE: float = 0.7
    LLM_MAX_TOKENS: int = 4096
    LLM_TIMEOUT_SECONDS: int = 180

    # ── AI API：阿里 DashScope MaaS（向量嵌入）──
    DASHSCOPE_API_KEY: str = ""
    DASHSCOPE_BASE_URL: str = "https://dashscope.aliyuncs.com/api/v1"
    DASHSCOPE_OPENAI_BASE_URL: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    EMBEDDING_MODEL: str = "text-embedding-v4"
    EMBEDDING_DIMENSION: int = 1024

    # ── 知识库（Chroma 向量库）──
    CHROMA_PERSIST_DIR: str = "./chroma_db"
    CHROMA_COLLECTION: str = "policy_clauses"
    RETRIEVAL_TOP_K: int = 5

    # ── 文件上传 ──
    UPLOAD_DIR: str = "./uploads"
    MAX_UPLOAD_SIZE_MB: int = 50
    ALLOWED_UPLOAD_EXTENSIONS: str = ".doc,.docx,.pdf"

    # ── 考试默认参数 ──
    DEFAULT_EXAM_DURATION_MINUTES: int = 30
    DEFAULT_EXAM_QUESTION_COUNT: int = 10
    DEFAULT_PASS_SCORE: int = 60

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()