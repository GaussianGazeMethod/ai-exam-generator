"""出题 Prompt 构建 — 按 Agently-Skills 的 ModelRequest 四段式边界组织。

Skills 要求（references/output-control.md、SKILL.md「ModelRequest Boundary」）：
    runtime facts                → .input(...)
    authoritative schemas/facts  → .info(...)
    transformation/call rules    → .instruct(...)
    exact consumed shape         → .output(Model)   ← 见 app/ai/models.py

另遵循：
- 「Keep every prompt-slot item request-local」——每个片段都必须改变本次请求的
  任务、契约、证据、限制或结果，不写与本次请求无关的项目叙述
- 「Never promote literals or behavior from a single observed instance into
  normative prompt instruction」——不把个别样例写进规范性指令
- 干扰项与题型分配策略属于「当前权威业务规则」，是必需的请求上下文，
  不算特例

覆盖 spec 的 M2 能力：
- M2-1 考点提取（含题型分配策略）
- M2-2 题目生成（四题型，含干扰项质量要求）
- M2-3 质量校验（选项不重复 / 答案唯一 / 语义一致）
"""

from __future__ import annotations

from dataclasses import dataclass

# ──────────────────────────────────────────────────────────
# 角色设定（Agent role）
# ──────────────────────────────────────────────────────────

EXAM_EXPERT_ROLE = (
    "你是企业制度考试命题专家，精通根据公司内部规章制度编写客观题。"
    "你的命题严格忠于制度原文，不引入制度之外的知识、法律条文或业务常识推断。"
    "你清楚考试的目的是验证员工是否真正理解并记住制度的关键约束。"
)

QUALITY_AUDITOR_ROLE = (
    "你是试题质量审核专家，只做客观校验，不修改题目。"
    "你依据制度原文判断题目是否忠实、答案是否唯一确定、选项是否有区分度。"
)


@dataclass
class PromptParts:
    """四段式 Prompt 的三段（output 契约由 Pydantic 模型承载）。"""

    input: str = ""
    info: str = ""
    instruct: str = ""


# ──────────────────────────────────────────────────────────
# 共享的业务规则（属于请求级权威上下文，非特例）
# ──────────────────────────────────────────────────────────

_TYPE_STRATEGY = """题型分配策略（依据条款的语言特征判断，不得随机分配）：
- 列举型条款（出现「包括」「如下」「以下」「之一」「等情形」）→ multi_choice 多选题
- 定义型条款（出现「是指」「定义为」「含义为」「称为」）→ fill_blank 填空题
- 禁止/必须型条款（出现「不得」「严禁」「禁止」「必须」「应当」）→ true_false 判断题
- 一般描述型条款（以上都不符合）→ single_choice 单选题"""

_SELECTION_CRITERIA = """考点筛选标准：
- 只选有明确、可考核约束的条款
- 纯流程性、纯背景说明、纯目的性表述的条款不适合出题，不要纳入结果
- 每条条款最多产出 1 个考点
- 优先选择员工日常工作中容易违反或产生误解的条款"""


# ──────────────────────────────────────────────────────────
# 一、考点提取（M2-1）
# ──────────────────────────────────────────────────────────


def build_extraction_prompt(document_name: str, clauses_text: str) -> PromptParts:
    """构建考点提取请求的三段内容。

    Args:
        document_name: 制度名称
        clauses_text: 待分析的条款文本（每行形如 `[第X章 第Y节 第Z条] 原文`）
    """
    return PromptParts(
        input=(
            f"待分析的制度条款如下，每行格式为「[章编号 节编号 条款编号] 条款原文」：\n\n"
            f"{clauses_text}"
        ),
        info=(
            f"制度名称：{document_name}\n\n"
            f"{_TYPE_STRATEGY}\n\n"
            f"{_SELECTION_CRITERIA}\n\n"
            "注意：clause_text 字段必须逐字照抄给定条款原文，不得改写、概括或截断；"
            "clause_no 与 chapter_no 必须与给定内容完全一致。"
        ),
        instruct=(
            "逐条分析给定条款，从中提取适合出题的考点。"
            "纯背景或流程性条款直接跳过，不要凑数。"
            "如果某条款没有可考核的明确约束，就不要为它生成考点。"
        ),
    )


# ──────────────────────────────────────────────────────────
# 二、题目生成（M2-2）— 四题型分别的 instruct
# ──────────────────────────────────────────────────────────

_COMMON_RULES = """命题铁律（违反任何一条即视为不合格）：
1. 题目与答案严格来自给定条款原文，不引入条款之外的制度、法律或业务常识
2. 不得出现「根据本制度」「如上所述」这类依赖上下文才能理解的表述，题目必须自足
3. 题面使用简体中文，表述简洁明确
4. 答案必须唯一确定，不存在歧义"""

_SINGLE_CHOICE_RULES = """本题型为单选题，额外要求：
1. 题干考察该条款的核心约束，是能判断对错的实务场景或明确表述
2. 恰好 4 个选项，形如「A. 选项内容」，其中恰好 1 个正确
3. 3 个干扰项必须与正确答案同类但错误：
   - 若考的是禁止行为，干扰项应是看似合理但实际被允许的行为
   - 若考的是具体要求，干扰项应是数值、对象或条件被改动的错误表述
4. 干扰项不能一眼就错（例如「完全不管」「随意处理」这类明显荒谬的表述）
5. 打乱正确选项位置，不要总是 A"""

_MULTI_CHOICE_RULES = """本题型为多选题，额外要求：
1. 题干考察该条款列举的多个要素（适用范围、禁止情形、必备条件等），题干中要含「多选」提示
2. 提供 4-5 个选项，其中 2-3 个正确
3. 严禁出现所有选项都正确（全选）或没有任何正确选项（全不选）的情况
4. 至少 1 个干扰项是条款未提及、但业务上看似合理的内容"""

_TRUE_FALSE_RULES = """本题型为判断题，额外要求：
1. 题干是一个具体陈述句，考察该条款的禁止性或强制性规则
2. options 字段固定为 ["正确", "错误"]，不要输出其他选项
3. 通过改动关键词（如「不得」改「可以」、「必须」改「无需」、「所有」改「部分」）
   构造错误命题；不要总是生成正确命题
4. 不得使用「以上说法是否正确」这类空洞表述，命题必须具体可判断"""

_FILL_BLANK_RULES = """本题型为填空题，额外要求：
1. 挖空条款中的关键术语：定义性名词、数值、时限、比例或责任主体
2. 题面用 4 个下划线 ____ 表示空缺，每道题只挖 1 个空
3. 不得挖无关虚词（如「的」「和」「以及」）
4. answer 必须是与条款原文用词完全一致的术语，不得改写或同义替换
5. options 字段输出空列表 []"""

_RULES_BY_TYPE = {
    "single_choice": _SINGLE_CHOICE_RULES,
    "multi_choice": _MULTI_CHOICE_RULES,
    "true_false": _TRUE_FALSE_RULES,
    "fill_blank": _FILL_BLANK_RULES,
}


def build_generation_prompt(
    *,
    question_type: str,
    document_name: str,
    chapter_no: str,
    clause_no: str,
    clause_text: str,
    related_clauses: list[str] | None = None,
) -> PromptParts:
    """构建单题生成请求的三段内容。

    Args:
        related_clauses: 知识库语义检索到的同制度关联条款，用于理解上下文，
            明确禁止作为考点来源（避免题目脱离本条约束）
    """
    info_parts = [
        f"制度名称：{document_name}",
        f"考点来源：{chapter_no or '（未标注）'} {clause_no or '（未标注）'}",
    ]
    if related_clauses:
        related = "\n".join(f"- {t}" for t in related_clauses if t)
        if related:
            info_parts.append(
                "同制度关联条款（仅用于理解本条的制度背景，"
                "不得作为本题考点，也不得据此改变答案）：\n" + related
            )

    return PromptParts(
        input=(
            f"本考点的条款原文如下：\n\n{clause_text}"
        ),
        info="\n\n".join(info_parts),
        instruct=(
            f"{_RULES_BY_TYPE.get(question_type, _SINGLE_CHOICE_RULES)}\n\n{_COMMON_RULES}"
        ),
    )


# ──────────────────────────────────────────────────────────
# 三、质量校验（M2-3）
# ──────────────────────────────────────────────────────────


def build_quality_check_prompt(question_json: str, clause_text: str) -> PromptParts:
    """构建质量校验请求的三段内容。

    依据 Skills：校验规则必须在首次尝试前给出（rule-first business validation），
    不得依赖「生成 → 硬拒绝 → 重试」来让模型猜规则。
    """
    return PromptParts(
        input=f"待校验的题目：\n{question_json}",
        info=f"该题所依据的制度条款原文：\n{clause_text}",
        instruct=(
            "逐项校验上面这道题，对五个校验项分别给出结论与依据：\n"
            "1. consistency —— 题目与答案是否严格忠于条款原文，有无制度外知识或曲解\n"
            "2. unambiguous —— 答案是否唯一确定，有无多种合理解读或边界争议\n"
            "3. no_duplicate —— 选项之间有无重复或语义等价\n"
            "4. has_distractor —— 干扰项是否有区分度；多选题是否出现全选或全不选\n"
            "5. self_contained —— 题干是否自足，有无依赖上下文指代\n\n"
            "注意：如果题目确实不合格，如实指出问题，不要为了通过而放宽标准；"
            "如果题目合格，issues 给出空列表。"
        ),
    )


# ──────────────────────────────────────────────────────────
# 四、知识库检索查询构造
# ──────────────────────────────────────────────────────────


def build_retrieval_query(
    *,
    document_name: str,
    chapter_no: str = "",
    clause_no: str = "",
    clause_text: str,
    key_point: str = "",
) -> str:
    """构造语义检索查询文本。"""
    parts = [document_name]
    if chapter_no or clause_no:
        parts.append(f"{chapter_no} {clause_no}".strip())
    if key_point:
        parts.append(key_point)
    parts.append(clause_text)
    return "\n".join(p for p in parts if p)