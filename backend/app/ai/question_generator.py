"""M2 AI 试题生成引擎 — 考点提取 → 题目生成 → 质量校验。

依据 spec/核心模块架构设计.md 模块二与 spec/页面层级树与核心服务模块.md S2。

实现严格遵循 Agently-Skills 指导：
- **async-first**：服务侧使用 `async_get_data_object()`，不用同步 `.start()`
  （Skills：「Treat sync APIs as wrappers for scripts, REPL use, or compatibility bridges」）
- **有界并发**：考点提取、逐题生成、质量校验三阶段各自并发执行
  （Skills：「Defaulting an unanalyzed complex workflow to all-serial execution is prohibited」）
- **结构化输出用 `.output(Model)`**：契约由 Pydantic 模型声明，
  不使用手写 JSON 解析器（Skills：「do not handwrite JSON post-processors when
  `.output(...)` already owns the contract」）
- **四段式边界**：input/info/instruct/output 分离（见 prompts.py 与 models.py）
- **规则前置**：质量校验的判定标准在首次请求前给出，不依赖「生成→硬拒绝→重试」猜测规则

LLM 编排由 Agently 承担，模型经 model_pool 别名选择（deepseek-v4 / dashscope-embed）。
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from app.ai.agently_setup import MODEL_KEY_CHAT, get_agent
from app.ai.models import (
    GeneratedQuestionData,
    KeyPointExtraction,
    QuestionQualityResult,
    make_acceptance_validator,
    to_agently_output_spec,
)
from app.ai.prompts import (
    EXAM_EXPERT_ROLE,
    QUALITY_AUDITOR_ROLE,
    PromptParts,
    build_extraction_prompt,
    build_generation_prompt,
    build_quality_check_prompt,
)

logger = logging.getLogger(__name__)

VALID_TYPES = ("single_choice", "multi_choice", "true_false", "fill_blank")

# 过短的条款多为标题或残句，不适合出题
MIN_CLAUSE_LENGTH = 12


def build_model_request(
    *,
    parts: PromptParts,
    output_model: type,
    role: str,
    temperature: float,
    model_key: str = MODEL_KEY_CHAT,
):
    """把四段式 Prompt + Pydantic 契约装配成一次 Agently 请求（未发送）。

    这是 M2 与模型之间**唯一**的请求装配入口，生产路径与测试共用它，
    以保证「真正发往模型的载荷」可被断言（不要在生产代码之外重新拼一遍请求，
    否则测试断言的是测试自己的拼装方式，而不是真实载荷）。

    已知约束（Agently 4.1.3.8 实测）：
    - 角色设定必须通过 `request.system(role)` 设在**请求**上。
      `Agent.role(...)` 写入 agent 级 prompt，而 `create_request()` 产出的请求在
      `to_prompt_object()` 时不继承 agent prompt，因此 agent 级角色不会进入 messages，
      属于静默失效（请求成功但模型看不到角色与规则）。
    - `.output(PydanticModel)` 在本版本会抛 TypeError，故用 `to_agently_output_spec()`。
    """
    agent = get_agent(role=role, temperature=temperature, model_key=model_key)
    request = agent.create_request()

    request.system(role)
    request.input(parts.input)
    if parts.info:
        request.info(parts.info)
    if parts.instruct:
        request.instruct(parts.instruct)

    # Agently 原生契约：由 Pydantic 模型推导，框架负责载体解析与字段约束
    request.output(to_agently_output_spec(output_model))
    # Pydantic 作为确定性接受权威（Skills 认可），契约不符时由 Agently 反馈重试
    request.validate(make_acceptance_validator(output_model))
    return request


# ── M2-3 质检结论口径 ──

# 五个校验项的中文名（审核端展示用）
QUALITY_ITEM_LABELS: dict[str, str] = {
    "consistency": "忠于原文",
    "unambiguous": "答案唯一",
    "no_duplicate": "选项不重复",
    "has_distractor": "干扰项有区分度",
    "self_contained": "题干自足",
}


def summarize_quality(raw: dict[str, Any] | None) -> dict[str, Any]:
    """把模型的质量校验输出收敛为一个**严格且确定**的结论。

    已确认口径（产品负责人）：**以五个分项为准，严格优先**。

    M2-3 这道质检存在的唯一目的是辅助 M3 人工审核 —— 它是「提醒器」，不是
    「放行开关」。模型在一次输出里同时给 5 个分项结论和 1 个总评时可能自相矛盾
    （分项指出「选项重复」，总评却写「通过」）。若采信总评，专家在审核工作台
    看不到任何提示，AI 已经指出的问题会被静默放行。

    代价不对称，故取严格口径：
    - 误报（合格题被判不合格）→ 专家多点一次「通过」，成本几秒
    - 漏报（不合格题被放行）→ 问题题进入正式考试，需重考，损害考核公信力

    规则（确定性，不受模型措辞影响）：
    - 校验未返回结构（模型失败） → `pass = None`（未知），**不是** False，
      避免把「没校验成功」误报成「题目不合格」
    - 五个分项全部为 True 且总评也通过 → `pass = True`
    - 任一项为 False 或缺失          → `pass = False`，且置 `contradiction = True`
      （缺失视为校验不完整，不放行）
    - 总评说「不通过」而分项全通过    → 同样 `pass = False` 并置 `contradiction = True`

    最后一档的理由：严格优先是**双向**的。模型明确说了不通过，就不该被分项规定
    覆盖成通过（那又是一次「静默放行」）。但这属于模型自相矛盾，所以要标出来，
    由专家定夺 —— 严格口径的代价只是多一次人工复核，而漏放问题题的代价要高得多。

    Returns:
        dict: 含 `pass` / `scores` / `items` / `failed` / `failed_labels` /
        `contradiction` / `issues` / `reason` / `note`
    """
    if raw is None:
        return {
            "pass": None,
            "scores": {},
            "items": [],
            "failed": [],
            "failed_labels": [],
            "contradiction": False,
            "issues": [],
            "reason": "质量校验请求未返回有效结构",
            "note": "质量校验未返回有效结构，请人工确认",
        }

    scores: dict[str, bool] = {}
    for key in QUALITY_ITEM_LABELS:
        value = (raw.get("scores") or {}).get(key)
        if isinstance(value, bool):
            scores[key] = value

    failed = [k for k in QUALITY_ITEM_LABELS if scores.get(k) is not True]
    failed_labels = [QUALITY_ITEM_LABELS[k] for k in failed]

    model_passed = raw.get("passed")
    items_all_pass = not failed
    contradiction = isinstance(model_passed, bool) and model_passed is not items_all_pass
    # 严格优先（双向）：分项不通过 → 不通过；模型明确说不通过 → 也判不通过。
    # 只有「分项全通过 且 总评也通过」才放行。
    strict_passed = items_all_pass and model_passed is not False

    issues = [str(i) for i in (raw.get("issues") or [])]
    reason = str(raw.get("reason") or "")

    note_parts: list[str] = []
    if contradiction:
        note_parts.append(
            "[注意] AI 分项结论与总评自相矛盾（已按严格口径判为不通过，请重点复核）"
        )
    if reason:
        note_parts.append(reason)
    if failed_labels:
        note_parts.append("未通过项：" + "、".join(failed_labels))
    if issues:
        note_parts.append("问题：" + "；".join(issues[:3]))

    return {
        "pass": strict_passed,
        "model_passed": model_passed if isinstance(model_passed, bool) else None,
        "scores": scores,
        "items": [
            {"key": k, "label": label, "passed": scores.get(k) is True}
            for k, label in QUALITY_ITEM_LABELS.items()
        ],
        "failed": failed,
        "failed_labels": failed_labels,
        "contradiction": contradiction,
        "issues": issues,
        "reason": reason,
        "note": " ".join(note_parts)[:900] or None,
    }


@dataclass
class KeyPoint:
    """考点（M2-1 产出）。"""

    chapter_no: str
    section_no: str
    clause_no: str
    clause_text: str
    question_type: str
    key_point: str = ""
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_no": self.chapter_no,
            "section_no": self.section_no,
            "clause_no": self.clause_no,
            "question_type": self.question_type,
            "key_point": self.key_point,
        }


@dataclass
class GeneratedQuestion:
    """生成的题目（M2-2 产出）。"""

    question_type: str
    content: str
    options: list[str]
    answer: list[str]
    chapter_no: str = ""
    section_no: str = ""
    clause_no: str = ""
    clause_text: str = ""
    explanation: str = ""
    quality_pass: bool | None = None
    quality_detail: dict[str, Any] | None = None

    @property
    def source_label(self) -> str:
        """来源标注：章/节/条款编号（spec 要求贯穿后续所有环节）。"""
        parts = [p for p in (self.chapter_no, self.section_no, self.clause_no) if p]
        return " ".join(parts)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question_type": self.question_type,
            "content": self.content,
            "options": self.options,
            "answer": self.answer,
            "explanation": self.explanation,
            "source": self.source_label,
            "chapter_no": self.chapter_no,
            "section_no": self.section_no,
            "clause_no": self.clause_no,
            "clause_text": self.clause_text,
            "quality_pass": self.quality_pass,
            "quality_detail": self.quality_detail,
        }


@dataclass
class GenerationResult:
    """一次生成的完整结果。"""

    document_name: str = ""
    questions: list[GeneratedQuestion] = field(default_factory=list)
    key_point_count: int = 0
    errors: list[str] = field(default_factory=list)
    duration_seconds: float = 0.0

    @property
    def type_stats(self) -> dict[str, int]:
        stats: dict[str, int] = {}
        for q in self.questions:
            stats[q.question_type] = stats.get(q.question_type, 0) + 1
        return stats

    def to_dict(self) -> dict[str, Any]:
        return {
            "document_name": self.document_name,
            "total": len(self.questions),
            "key_point_count": self.key_point_count,
            "type_stats": self.type_stats,
            "duration_seconds": round(self.duration_seconds, 1),
            "errors": self.errors,
            "questions": [q.to_dict() for q in self.questions],
        }


def flatten_clauses(chapters: list[dict[str, Any]]) -> list[dict[str, str]]:
    """把解析出的章节树拍平成条款列表（带章/节上下文）。"""
    flat: list[dict[str, str]] = []
    for ch in chapters or []:
        ch_no = ch.get("chapter_no", "") or ""
        for clause in ch.get("clauses", []) or []:
            text = (clause.get("text") or "").strip()
            if len(text) < MIN_CLAUSE_LENGTH:
                continue
            flat.append(
                {
                    "chapter_no": clause.get("chapter_no") or ch_no,
                    "section_no": clause.get("section_no") or "",
                    "clause_no": clause.get("clause_no") or "",
                    "text": text,
                }
            )
    return flat


class QuestionGenerator:
    """AI 出题引擎：考点提取 → 生成 → 质量校验（全异步、有界并发）。"""

    def __init__(
        self,
        *,
        generation_temperature: float = 0.7,
        audit_temperature: float = 0.0,
        max_concurrency: int = 4,
        max_attempts: int = 2,
    ) -> None:
        self.generation_temperature = generation_temperature
        self.audit_temperature = audit_temperature
        self.max_concurrency = max(1, max_concurrency)
        self.max_attempts = max(1, max_attempts)

    # ── 内部：受信号量约束的模型请求 ──

    async def _request(
        self,
        parts: PromptParts,
        output_model: type,
        *,
        role: str,
        temperature: float,
        semaphore: asyncio.Semaphore,
    ) -> Any | None:
        """发起一次结构化模型请求，返回校验后的 Pydantic 实例。

        Agently 负责 JSON 载体解析、Pydantic 校验与失败重试；
        本方法只处理「最终仍失败」的兜底，不手写解析逻辑。
        """

        async with semaphore:
            for attempt in range(1, self.max_attempts + 1):
                try:
                    request = build_model_request(
                        parts=parts,
                        output_model=output_model,
                        role=role,
                        temperature=temperature,
                        model_key=MODEL_KEY_CHAT,
                    )
                    data = await request.async_get_data()
                    # 强类型接受：不通则抛异常，进入下一次尝试
                    return output_model.model_validate(data)
                except Exception as exc:  # noqa: BLE001 - 模型/网络/校验异常统一兜底
                    logger.warning(
                        "模型请求失败（第 %s/%s 次，%s）：%s: %s",
                        attempt,
                        self.max_attempts,
                        output_model.__name__,
                        type(exc).__name__,
                        str(exc)[:200],
                    )
                    if attempt < self.max_attempts:
                        await asyncio.sleep(1.5 * attempt)
            return None

    # ── M2-1 考点提取 ──

    async def _extract_batch(
        self,
        document_name: str,
        batch: list[dict[str, str]],
        semaphore: asyncio.Semaphore,
    ) -> tuple[list[KeyPoint], str | None]:
        """提取一批条款的考点。"""
        batch_text = "\n".join(
            f"[{c['chapter_no']} {c['section_no']} {c['clause_no']}] {c['text']}" for c in batch
        )
        parts = build_extraction_prompt(document_name, batch_text)

        result = await self._request(
            parts,
            KeyPointExtraction,
            role=EXAM_EXPERT_ROLE,
            temperature=self.audit_temperature,
            semaphore=semaphore,
        )
        if result is None:
            return [], "考点提取失败（模型请求未返回有效结构）"

        # 用原始条款文本与编号作为权威来源，避免模型改写或截断条款
        index = {(c["chapter_no"], c["clause_no"]): c for c in batch if c["clause_no"]}
        points: list[KeyPoint] = []
        for item in result.points:
            if item.question_type not in VALID_TYPES:
                continue
            origin = index.get((item.chapter_no, item.clause_no))
            if origin is None:
                origin = next((c for c in batch if c["clause_no"] == item.clause_no), None)
            if origin is None:
                logger.debug("跳过无法匹配原文的考点：%s %s", item.chapter_no, item.clause_no)
                continue
            points.append(
                KeyPoint(
                    chapter_no=origin["chapter_no"],
                    section_no=origin["section_no"],
                    clause_no=origin["clause_no"],
                    clause_text=origin["text"],
                    question_type=item.question_type,
                    key_point=item.key_point,
                    reason=item.reason,
                )
            )
        return points, None

    async def extract_key_points(
        self,
        document_name: str,
        clauses: list[dict[str, str]],
        *,
        batch_size: int = 8,
        max_points: int = 40,
    ) -> tuple[list[KeyPoint], list[str]]:
        """并发提取考点（分批以控制单次 prompt 长度）。"""
        if not clauses:
            return [], []

        batches = [clauses[i : i + batch_size] for i in range(0, len(clauses), batch_size)]
        semaphore = asyncio.Semaphore(self.max_concurrency)

        results = await asyncio.gather(
            *(self._extract_batch(document_name, b, semaphore) for b in batches),
            return_exceptions=True,
        )

        points: list[KeyPoint] = []
        errors: list[str] = []
        seen: set[tuple[str, str]] = set()

        for idx, res in enumerate(results):
            if isinstance(res, BaseException):
                errors.append(f"考点提取批次 {idx + 1} 异常：{type(res).__name__}: {res}")
                continue
            batch_points, err = res
            if err:
                errors.append(f"考点提取批次 {idx + 1}：{err}")
            for point in batch_points:
                key = (point.chapter_no, point.clause_no)
                if key in seen:
                    continue
                seen.add(key)
                points.append(point)
                if len(points) >= max_points:
                    return points, errors

        return points, errors

    # ── M2-2 题目生成 ──

    async def generate_one(
        self,
        point: KeyPoint,
        document_name: str,
        *,
        related_clauses: list[str] | None = None,
        semaphore: asyncio.Semaphore | None = None,
    ) -> GeneratedQuestion | None:
        """为单个考点生成一道题。"""
        sem = semaphore or asyncio.Semaphore(self.max_concurrency)
        parts = build_generation_prompt(
            question_type=point.question_type,
            document_name=document_name,
            chapter_no=point.chapter_no,
            clause_no=point.clause_no,
            clause_text=point.clause_text,
            related_clauses=related_clauses,
        )

        data = await self._request(
            parts,
            GeneratedQuestionData,
            role=EXAM_EXPERT_ROLE,
            temperature=self.generation_temperature,
            semaphore=sem,
        )
        if data is None:
            return None

        content = data.content.strip()
        options = [o.strip() for o in data.options if o.strip()]
        answer = [a.strip() for a in data.answer if a.strip()]

        # 判断题选项固定，忽略模型输出差异
        if point.question_type == "true_false":
            options = ["正确", "错误"]

        return GeneratedQuestion(
            question_type=point.question_type,
            content=content,
            options=options,
            answer=answer,
            chapter_no=point.chapter_no,
            section_no=point.section_no,
            clause_no=point.clause_no,
            clause_text=point.clause_text,
            explanation=data.explanation.strip(),
        )

    # ── M2-3 质量校验 ──

    async def check_quality(
        self,
        question: GeneratedQuestion,
        *,
        semaphore: asyncio.Semaphore | None = None,
    ) -> dict[str, Any]:
        """对生成结果做质量校验（结构化模型裁判）。"""
        import json as _json

        sem = semaphore or asyncio.Semaphore(self.max_concurrency)
        payload = _json.dumps(
            {
                "question_type": question.question_type,
                "content": question.content,
                "options": question.options,
                "answer": question.answer,
            },
            ensure_ascii=False,
        )
        parts = build_quality_check_prompt(payload, question.clause_text)

        result: QuestionQualityResult | None = await self._request(
            parts,
            QuestionQualityResult,
            role=QUALITY_AUDITOR_ROLE,
            temperature=self.audit_temperature,
            semaphore=sem,
        )
        if result is None:
            return summarize_quality(None)

        # 结论口径统一由 summarize_quality 决定（以五个分项为准，严格优先），
        # 不直接采信模型自报的 passed —— 见函数 docstring 与
        # tests/test_spec_compliance.py::TestM2QualityVerdictPolicy
        return summarize_quality(
            {
                "passed": result.passed,
                "scores": {
                    "consistency": result.consistency,
                    "unambiguous": result.unambiguous,
                    "no_duplicate": result.no_duplicate,
                    "has_distractor": result.has_distractor,
                    "self_contained": result.self_contained,
                },
                "issues": result.issues,
                "reason": result.reason,
            }
        )

    # ── 全流程 ──

    async def generate_for_document(
        self,
        document_name: str,
        chapters: list[dict[str, Any]],
        *,
        max_points: int = 40,
        batch_size: int = 8,
        do_quality_check: bool = True,
        retrieve_related: Callable[[KeyPoint], list[str]] | None = None,
        progress_cb: Callable[[int, int, str], None] | None = None,
    ) -> GenerationResult:
        """对一份制度文档执行完整出题流程。

        Args:
            retrieve_related: 知识库检索回调，返回与考点语义相关的同制度条款
            progress_cb: 进度回调 (已完成数, 总数, 说明)
        """
        started = time.time()
        result = GenerationResult(document_name=document_name)

        clauses = flatten_clauses(chapters)
        if not clauses:
            result.errors.append("未从制度中解析出可用条款，无法出题")
            result.duration_seconds = time.time() - started
            return result

        # 1) 考点提取（内部已并发）
        points, errors = await self.extract_key_points(
            document_name, clauses, batch_size=batch_size, max_points=max_points
        )
        result.errors.extend(errors)
        result.key_point_count = len(points)
        if not points:
            result.errors.append("考点提取为空")
            result.duration_seconds = time.time() - started
            return result

        # 2) 逐题生成（有界并发）
        total = len(points)
        semaphore = asyncio.Semaphore(self.max_concurrency)
        done_counter = {"n": 0}

        async def _one(point: KeyPoint) -> GeneratedQuestion | None:
            related = retrieve_related(point) if retrieve_related else None
            question = await self.generate_one(
                point, document_name, related_clauses=related, semaphore=semaphore
            )
            done_counter["n"] += 1
            if progress_cb:
                progress_cb(done_counter["n"], total, f"已生成 {done_counter['n']}/{total} 题")
            return question

        generated = await asyncio.gather(
            *(_one(p) for p in points), return_exceptions=True
        )

        for idx, item in enumerate(generated):
            if isinstance(item, BaseException):
                p = points[idx]
                result.errors.append(
                    f"出题异常：{p.chapter_no} {p.clause_no} — {type(item).__name__}: {item}"
                )
            elif item is None:
                p = points[idx]
                result.errors.append(f"出题失败：{p.chapter_no} {p.clause_no}")
            else:
                result.questions.append(item)

        # 3) 质量校验（有界并发）
        if do_quality_check and result.questions:
            qa_semaphore = asyncio.Semaphore(self.max_concurrency)
            qualities = await asyncio.gather(
                *(self.check_quality(q, semaphore=qa_semaphore) for q in result.questions),
                return_exceptions=True,
            )
            for question, quality in zip(result.questions, qualities):
                if isinstance(quality, BaseException):
                    question.quality_pass = None
                    question.quality_detail = {
                        "pass": None,
                        "reason": f"校验异常：{type(quality).__name__}",
                    }
                else:
                    question.quality_pass = quality.get("pass")
                    question.quality_detail = quality

        result.duration_seconds = time.time() - started
        return result


_generator_singleton: QuestionGenerator | None = None


def get_question_generator() -> QuestionGenerator:
    global _generator_singleton
    if _generator_singleton is None:
        _generator_singleton = QuestionGenerator()
    return _generator_singleton