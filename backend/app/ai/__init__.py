"""AI 能力模块 — 基于 Agently 4 + DeepSeek + DashScope + Chroma。

组织方式遵循 Agently-Skills 的 owner 划分：

- `settings.yaml`    Provider 配置（model_pool / model_profiles，密钥用 ${ENV.xxx}）
- `agently_setup`    配置加载与生效校验（load_settings + host-owned override）
- `models`           结构化输出契约（Pydantic v2，供 .output(Model) 使用）
- `prompts`          Prompt 构建（input / info / instruct 三段式）
- `question_generator` M2 出题引擎（async-first、有界并发）
- `vector_store`     知识库（Agently 原生 ChromaCollection + 嵌入 Agent）

说明：Agently 4.1.3.8 不提供 `RecordStore`（4.1.4.x API），
知识库改用 Agently 官方集成 `agently.integrations.chromadb.ChromaCollection`
（capability-first 边界，非自造封装）。
"""

from app.ai.agently_setup import (  # noqa: F401
    MODEL_KEY_CHAT,
    MODEL_KEY_EMBED,
    effective_config,
    get_agent,
    get_embedding_agent,
    is_ai_available,
    is_embedding_available,
    setup_agently,
)
from app.ai.models import (  # noqa: F401
    GeneratedQuestionData,
    KeyPointExtraction,
    QuestionQualityResult,
)
from app.ai.question_generator import (  # noqa: F401
    GeneratedQuestion,
    GenerationResult,
    KeyPoint,
    QuestionGenerator,
    flatten_clauses,
    get_question_generator,
)
from app.ai.vector_store import VectorStore, get_vector_store  # noqa: F401