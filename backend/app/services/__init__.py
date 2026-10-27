"""业务服务层。

- `document_parser`     M1 文档解析（python-docx / PyMuPDF + 编号规则引擎）
- `generation_tasks`    S2 出题异步任务注册表（进度追踪）
- `generation_workflow` 出题编排（解析 → 索引 → AI 出题 → 入库）
- `exam_composer`       M4 智能组卷（按题型配比随机抽取 + 单题替换）
- `statistics_service`  S6 多维统计聚合（角色视图隔离 + 质量反馈闭环）
- `report_export`       S6-3 报表导出（合规报告 PDF + 统计明细 Excel）
"""

from app.services.document_parser import (  # noqa: F401
    DocumentParser,
    ParseResult,
    get_document_parser,
)
from app.services.exam_composer import (  # noqa: F401
    DEFAULT_TYPE_RATIO,
    TOTAL_QUESTIONS,
    CompositionResult,
    RatioError,
    compose_exam,
    find_swap_candidate,
    normalize_ratio,
    parse_question_ids,
)
from app.services.generation_tasks import (  # noqa: F401
    GenerationTask,
    GenerationTaskRegistry,
    get_task_registry,
)
from app.services.generation_workflow import (  # noqa: F401
    parse_and_index_document,
    remove_document_vectors,
    run_generation_task,
)
from app.services.report_export import (  # noqa: F401
    build_compliance_pdf,
    build_statistics_excel,
)
from app.services.statistics_service import (  # noqa: F401
    DEFAULT_ERROR_THRESHOLD,
    ExamStatsContext,
    build_chapter_stats,
    build_compliance_overview,
    build_dept_stats,
    build_overview,
    build_person_stats,
    build_question_stats,
    get_answer_detail,
    load_exam_context,
    push_quality_feedback,
)