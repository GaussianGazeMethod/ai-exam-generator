"""AI 结构化输出契约（Pydantic v2）+ Agently `.output()` 契约推导。

依据 Agently-Skills 的 output-control 指导：
- 机器可读结果用 `.output(...)` 声明契约，而不是在 Prompt 文本里写 JSON 格式、
  再用手写解析器兜底（那是被明确禁止的反模式）
- 字段顺序按依赖关系排列：先给依据/校验项，最后给结论字段
  （「order dependent fields before the final decision or user-facing answer field」）
- 「do not rebuild a stable shared schema in Python if prompt config can own it once」
  —— 因此本模块是 schema 的唯一真源，Agently 契约由它自动推导，避免两处定义漂移

Agently 4.1.3.8 版本约束（实测确认）：
- `.output(PydanticModel)` 直接投影需 4.1.4.3+，本版本会抛
  `TypeError: Unable to generator output model because the output is not a structure data`
- 本版本原生形态为：`{"字段": (类型, 说明, ensure)}`，列表字段为
  `{"字段": [(元素类型, 说明, ensure)]}`（见 Skills 的 output-control-minimal 示例）
- 因此：**Agently 契约负责载体解析与字段约束，Pydantic 模型负责强类型接受**
  —— 后者是 Skills 明确认可的「deterministic acceptance authority」
"""

from __future__ import annotations

import types
from typing import Any, Literal, Union, get_args, get_origin

from pydantic import BaseModel, Field

# 四种客观题题型（spec：单选/多选/判断/填空）
QuestionTypeLiteral = Literal["single_choice", "multi_choice", "true_false", "fill_blank"]


class KeyPointItem(BaseModel):
    """单个考点（M2-1 产出）。"""

    clause_no: str = Field(description="考点所在条款编号，如「第六条」；须与给定条款完全一致")
    chapter_no: str = Field(description="考点所在章编号，如「第三章」")
    clause_text: str = Field(description="该条款的完整原文，须逐字照抄给定内容，不得改写或截断")
    question_type: QuestionTypeLiteral = Field(
        description="推荐题型：列举型用 multi_choice；定义型用 fill_blank；禁止/必须型用 true_false；其余用 single_choice"
    )
    key_point: str = Field(description="该条款的核心考点，一句话概括")
    reason: str = Field(description="选择该题型的理由，一句话")


class KeyPointExtraction(BaseModel):
    """考点提取结果（M2-1）。"""

    points: list[KeyPointItem] = Field(description="适合出题的考点列表；不适合出题的条款不要出现在此列表中")


class GeneratedQuestionData(BaseModel):
    """单道题目的生成契约（M2-2）。

    字段顺序：先内容与选项，再答案，最后解析 —— 先产出结构化事实再给结论。
    """

    content: str = Field(
        description="题干本身，不含选项文本。填空题用 4 个下划线 ____ 表示唯一空缺；单选题与多选题的题面不得泄露答案"
    )
    options: list[str] = Field(
        description="选项列表。单选题 4 项、多选题 4-5 项，每项形如「A. 选项内容」；判断题固定为「正确」「错误」；填空题给空列表"
    )
    answer: list[str] = Field(
        description="正确答案。单选填 1 个选项字母；多选填 2-3 个字母；判断题填「正确」或「错误」；填空题填空缺处的原文术语"
    )
    explanation: str = Field(description="依据条款原文说明答案正确的理由，一句话")


class QuestionQualityResult(BaseModel):
    """质量校验结果（M2-3）—— 扁平结构，校验项先于最终结论。

    Skills：「Flatten redundant hierarchy into a compact decision projection where useful」
    """

    consistency: bool = Field(description="题目与答案严格忠于条款原文，无制度外知识、无曲解")
    unambiguous: bool = Field(description="答案唯一确定，不存在多种合理解读或边界争议")
    no_duplicate: bool = Field(description="选项之间无重复、无语义等价")
    has_distractor: bool = Field(
        description="干扰项有区分度：既不明显错误也不牵强误导；多选题未出现全选或全不选"
    )
    self_contained: bool = Field(description="题干自足，不依赖「本制度」「如上所述」等上下文指代")
    issues: list[str] = Field(description="发现的问题清单；未发现问题时给出空列表")
    reason: str = Field(description="总体结论的理由，一句话")
    passed: bool = Field(description="综合结论：五个校验项全为 true 时为 true，任一项不通过则为 false")


# ──────────────────────────────────────────────────────────
# Agently .output() 契约推导
# ──────────────────────────────────────────────────────────

_ENSURE_REQUIRED = "not_null"


def _scalar_spec(annotation: Any, description: str) -> tuple[Any, str, Any]:
    """标量字段 → (类型, 说明, ensure)。

    Literal 枚举把可选值写进说明、类型退化为 str：既让枚举约束对模型可见，
    又避免 4.1.3.8 对复杂类型投影的不确定性。
    """
    origin = get_origin(annotation)

    if origin is Literal:
        allowed = " / ".join(str(a) for a in get_args(annotation))
        return str, f"{description}（取值必须是：{allowed}）", _ENSURE_REQUIRED

    if origin in (Union, types.UnionType):
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return _scalar_spec(args[0], description)
        return str, description, _ENSURE_REQUIRED

    if annotation in (int, float, bool):
        return annotation, description, _ENSURE_REQUIRED

    return str, description, _ENSURE_REQUIRED


def to_agently_output_spec(model_cls: type[BaseModel]) -> dict[str, Any]:
    """由 Pydantic 模型推导 Agently `.output(...)` 的字典契约。

    支持三种字段形态（均已在 4.1.3.8 实测验证）：
    - 标量：   "字段": (类型, 说明, ensure)
    - 标量列表："字段": [(元素类型, 说明, ensure)]
    - 对象列表："字段": [{子字段契约}]        ← 嵌套模型列表，用于考点提取
    以及嵌套对象："字段": {子字段契约}
    """
    spec: dict[str, Any] = {}

    for name, field in model_cls.model_fields.items():
        annotation = field.annotation
        description = field.description or name
        origin = get_origin(annotation)

        if origin is list:
            args = get_args(annotation)
            item_type = args[0] if args else str

            if isinstance(item_type, type) and issubclass(item_type, BaseModel):
                # 对象列表：Agently 接受 [{子字段契约}]
                spec[name] = [to_agently_output_spec(item_type)]
                continue

            if get_origin(item_type) is not None:
                raise ValueError(
                    f"字段 {model_cls.__name__}.{name} 的列表元素类型不受支持：{item_type!r}"
                )

            scalar_type, scalar_desc, _ = _scalar_spec(item_type, f"{description} 的单个元素")
            spec[name] = [(scalar_type, scalar_desc, True)]
            continue

        if isinstance(annotation, type) and issubclass(annotation, BaseModel):
            # 嵌套对象
            spec[name] = to_agently_output_spec(annotation)
            continue

        spec[name] = _scalar_spec(annotation, description)

    return spec


def make_acceptance_validator(model_cls: type[BaseModel]):
    """把 Pydantic 校验包装成 Agently `.validate(...)` 处理器。

    Skills：Pydantic 与 `.validate(...)` 是确定性接受权威；
    validator 的 reason 只用于修正已声明的契约，不作为发现规则的手段。
    """

    def _validator(data: Any, _context: Any = None) -> bool:
        if isinstance(data, model_cls):
            return True
        try:
            model_cls.model_validate(data)
            return True
        except Exception:  # noqa: BLE001 - 校验失败即返回 False，由 Agently 处理重试
            return False

    return _validator