"""Agently 4 接入配置 — 严格遵循 Agently-Skills 的 model-setup 指导。

Skills 要求（references/model-setup.md）：
- Provider 配置放到 settings 文件，用 `${ENV.xxx}` 占位符承载密钥
  「do not bake secrets or environment-specific endpoints into committed Python code
    when settings plus env placeholders fit」
- 用 `Agently.load_settings("yaml_file", path, auto_load_env=True)` 加载
- 用 `Agently.set_settings(...)` 做 host-owned override
- 加载后校验生效的 provider / base_url / model / auth 是否存在，而不是假定文件形状正确
- 多模型场景用 `model_pool` → `model_profiles` 别名体系，
  通过 `agent.activate_model("<别名>")` 或 `agent.create_request(model_key="<别名>")` 选择

Agently 4.1.3.8 版本约束（实测确认）：
- `${ENV.xxx}` 占位符仅对 `api_key` 生效，故 base_url 写在 YAML，
  端点若需按环境切换则由本模块以 host-owned override 覆盖
- `resolve_model_profile()` 与 `RecordStore` 是 4.1.4.x API，本版本不可用
- model_key 的解析是惰性的（发生在请求构建时，见 core/model/ModelRequest.py），
  因此 `activate_model()` 之后立即读 settings 仍是旧值，校验需在请求时或按 profile 读取
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path

from agently import Agently

from app.config import get_settings

logger = logging.getLogger(__name__)

_settings = get_settings()

# settings.yaml 与本模块同目录
SETTINGS_PATH = Path(__file__).resolve().parent / "settings.yaml"

# 模型别名（model_pool 的键）
MODEL_KEY_CHAT = "deepseek-v4"
MODEL_KEY_EMBED = "dashscope-embed"

_PROVIDER_NS = "plugins.ModelRequester.OpenAICompatible"

_lock = threading.Lock()
_configured = False
_effective: dict[str, str] = {}


def setup_agently() -> None:
    """加载 Agently 配置（幂等，进程内只执行一次）。"""
    global _configured
    if _configured:
        return

    with _lock:
        if _configured:
            return

        if not SETTINGS_PATH.exists():
            raise RuntimeError(f"Agently 配置文件不存在：{SETTINGS_PATH}")

        # 1) 从文件加载（auto_load_env=True 使 .env 参与 ${ENV.xxx} 解析）
        Agently.load_settings("yaml_file", str(SETTINGS_PATH), auto_load_env=True)

        # 2) host-owned override：按环境切换 provider 端点
        #    （密钥不出现在这里，仍由 settings.yaml 的 ${ENV.xxx} 承载）
        _apply_host_overrides()

        # 3) 校验生效配置
        _verify_effective_config()

        _configured = True


def _apply_host_overrides() -> None:
    """以 host-owned override 形式覆盖 provider 端点与请求参数。

    Skills：「use `Agently.set_settings(...)` ... for inline mappings or host-owned overrides」
    """
    overrides: dict[str, object] = {}

    deepseek_base = os.getenv("DEEPSEEK_BASE_URL", "").strip()
    if deepseek_base:
        url = deepseek_base.rstrip("/")
        if not url.endswith("/v1"):
            url = f"{url}/v1"
        Agently.set_settings(f"{_PROVIDER_NS}.base_url", url)
        overrides["deepseek_base_url"] = url

    dashscope_base = os.getenv("DASHSCOPE_OPENAI_BASE_URL", "").strip()
    if dashscope_base:
        # DashScope 专用 MaaS 端点由 model_profiles 承载；此处仅记录，
        # 实际覆盖通过 profile 级 settings 完成（见 get_embedding_agent）
        overrides["dashscope_base_url"] = dashscope_base.rstrip("/")

    if _settings.LLM_TIMEOUT_SECONDS:
        Agently.set_settings(
            f"{_PROVIDER_NS}.timeout",
            {
                "connect": 30.0,
                "read": float(_settings.LLM_TIMEOUT_SECONDS),
                "write": 30.0,
                "pool": 30.0,
            },
        )


def _verify_effective_config() -> None:
    """校验生效的 provider / 别名 / profile / auth，避免静默使用错误配置。

    注意：model_key 的解析是惰性的（发生在 ModelRequest 构建时，会把 profile
    字段写入 plugins.ModelRequester.<provider>.*）。因此校验读取
    model_pool / model_profiles 的权威定义，而不是插件命名空间的瞬时值。
    """
    global _effective

    provider = str(Agently.settings.get("plugins.ModelRequester.activate", ""))
    pool = Agently.settings.get("model_pool", {}) or {}
    profiles = Agently.settings.get("model_profiles", {}) or {}

    chat_alias = str(pool.get(MODEL_KEY_CHAT, "")) if isinstance(pool, dict) else ""
    embed_alias = str(pool.get(MODEL_KEY_EMBED, "")) if isinstance(pool, dict) else ""
    chat_profile = (profiles.get(chat_alias) or {}) if isinstance(profiles, dict) else {}
    embed_profile = (profiles.get(embed_alias) or {}) if isinstance(profiles, dict) else {}

    _effective = {
        "provider": provider,
        "chat_alias": chat_alias,
        "chat_model": str(chat_profile.get("model", "")),
        "chat_base_url": str(chat_profile.get("base_url", "")),
        "embed_alias": embed_alias,
        "embed_model": str(embed_profile.get("model", "")),
        "chat_auth_ready": "yes" if _settings.DEEPSEEK_API_KEY else "no",
        "embed_auth_ready": "yes" if _settings.DASHSCOPE_API_KEY else "no",
    }

    problems: list[str] = []
    if provider != "OpenAICompatible":
        problems.append(f"provider 未激活 OpenAICompatible（当前 {provider!r}）")
    if not chat_alias or not chat_profile:
        problems.append(f"model_pool/model_profiles 缺少对话模型别名 {MODEL_KEY_CHAT!r}")
    if not embed_alias or not embed_profile:
        problems.append(f"model_pool/model_profiles 缺少嵌入模型别名 {MODEL_KEY_EMBED!r}")
    if not _settings.DEEPSEEK_API_KEY:
        problems.append("DEEPSEEK_API_KEY 未配置（出题不可用）")
    if not _settings.DASHSCOPE_API_KEY:
        problems.append("DASHSCOPE_API_KEY 未配置（知识库不可用）")

    if problems:
        logger.warning("Agently 配置校验发现问题：%s", "；".join(problems))
    else:
        logger.info(
            "Agently 就绪：provider=%s chat=%s(%s) embed=%s(%s)",
            _effective["provider"],
            _effective["chat_alias"],
            _effective["chat_model"],
            _effective["embed_alias"],
            _effective["embed_model"],
        )


def effective_config() -> dict[str, str]:
    """返回生效配置的非敏感投影（供健康检查/系统管理页展示，不含密钥）。"""
    setup_agently()
    return dict(_effective)


def get_agent(
    *,
    role: str | None = None,
    temperature: float | None = None,
    model_key: str = MODEL_KEY_CHAT,
):
    """创建一个绑定指定模型别名的 Agently Agent。

    Args:
        role: Agent 级角色设定。**注意**：实测（4.1.3.8）`Agent.role(...)` 写入的是
            agent 级 prompt，而 `create_request()` 产出的请求在 `to_prompt_object()`
            时不继承 agent prompt，因此这个角色**不会**出现在发往模型的 messages 里。
            生成/校验请求必须另外调用 `request.system(role)`（见 question_generator._request）。
            保留此参数仅用于 Agently 内部同 agent 复用的场景，不应作为 Prompt 下发的唯一手段。
        temperature: 生成温度（出题 0.7，质量校验 0.0）
        model_key: model_pool 中的模型别名
    """
    setup_agently()
    agent = Agently.create_agent()
    agent.activate_model(model_key)

    if role:
        agent.role(role)
    if temperature is not None:
        agent.set_settings(
            f"{_PROVIDER_NS}.request_options",
            {
                "temperature": temperature,
                "max_tokens": _settings.LLM_MAX_TOKENS,
            },
        )
    return agent


def get_embedding_agent():
    """创建绑定嵌入模型别名的 Agent（DashScope text-embedding-v4）。

    spec 指定知识库模块的向量化由 Agently 承担。

    注：DashScope 专用 MaaS 端点由 host-owned override 注入到 profile 对应的
    provider 命名空间，与对话模型共用 OpenAICompatible 插件但端点不同；
    由于 model_key 解析发生在请求时并会重写该命名空间，
    此处对嵌入 Agent 采用「直接绑定请求」的方式（见 vector_store）。
    """
    setup_agently()
    agent = Agently.create_agent()
    agent.activate_model(MODEL_KEY_EMBED)
    return agent


def is_ai_available() -> bool:
    """对话模型（出题）是否可用。"""
    return bool(_settings.DEEPSEEK_API_KEY)


def is_embedding_available() -> bool:
    """嵌入模型（知识库）是否可用。"""
    return bool(_settings.DASHSCOPE_API_KEY)