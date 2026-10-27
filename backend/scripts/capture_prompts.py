"""核型处理节点 Prompt 原文捕获 — 打印真正发往 DeepSeek 的 HTTP 载荷。

与 scripts/trace_generation.py 的区别：
- trace_generation.py 打印的是 `app/ai/prompts.py` 里的三段字符串（工程侧视图）
- 本脚本拦截 Agently 的 PromptGenerator / ResponseAdapter，打印**框架真正拼装出来的
  完整请求体**（system + 四段 slot + OUTPUT REQUIREMENT 契约段），以及模型原始流式返回

拦截点（Agently 4.1.3.8 实测）：
- `prompt.to_messages()`  ← builtins/plugins/PromptGenerator/AgentlyPromptGenerator.py:799
  其返回值就是 request_builder 写入 `request_data["data"]["messages"]` 的内容
- `broadcast_response()`  ← builtins/plugins/ModelRequester/OpenAICompatible/modules/response_adapter.py:31
  可拿到 `original_delta`（SSE 原始分片）与 `delta`（累积正文）

运行：
    python3 scripts/services.py start          # 或直接在容器内跑
    docker run --rm -v "<项目>/backend":/app/backend -w /app/backend \
        --env-file backend/.env ai-exam-backend:1 python scripts/capture_prompts.py

    # 只渲染 Prompt 模板，不调用模型（零成本，用于逐字审阅每一题型）
    ... python scripts/capture_prompts.py --dry

    # 指定输出文件
    ... python scripts/capture_prompts.py --out docs/prompt原文.md
"""

from __future__ import annotations

import argparse
import asyncio
import io
import json
import sys
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

W = 96
OUT = io.StringIO()


def emit(text: str = "") -> None:
    print(text)
    OUT.write(text + "\n")


def hr(title: str = "", char: str = "═") -> None:
    if title:
        emit(f"\n{char * 3} {title} ".ljust(W, char))
    else:
        emit(char * W)


def sub(title: str) -> None:
    emit(f"\n{'─' * 3} {title} ".ljust(W, "─"))


def block(text: str, indent: int = 0) -> None:
    pad = " " * indent
    for line in str(text).splitlines() or [""]:
        emit(f"{pad}{line}")


# ══════════════════════════════════════════════════════════
# 拦截器：抓取真正发往模型的 messages + 模型原始返回
# ══════════════════════════════════════════════════════════


class ModelCallTap:
    """记录每一次模型调用：请求 messages + 流式返回原文。

    实现要点（实测）：Agently 在一次请求中会读取 prompt **两次**
    （构建请求体一次、内部快照一次），两次内容相同但是不同的对象，
    而流式返回只会写到最后一次快照上。因此这里按「载荷内容」归并：
    内容相同的重复读取合并为同一条记录，流式返回才不会被丢掉。
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self._pending: dict | None = None
        # 累计统计（take() 会清空 calls，故单独计数）
        self.total_reads = 0
        self.total_unique = 0
        self.summary: list[dict] = []

    @staticmethod
    def _payload_key(messages: list[dict]) -> tuple:
        return tuple((m.get("role"), str(m.get("content"))) for m in messages)

    def record_messages(self, messages: list[dict]) -> None:
        self.total_reads += 1
        key = self._payload_key(messages)
        for existing in self.calls:
            if existing["key"] == key:
                # 同一请求的重复读取：复用已有记录，让流式返回落到同一条上
                self._pending = existing
                return
        self._pending = {"key": key, "messages": messages, "raw_chunks": [], "content": ""}
        self.calls.append(self._pending)

    def record_delta(self, content: str) -> None:
        if self._pending is not None:
            self._pending["content"] += content

    def record_original(self, chunk: str) -> None:
        if self._pending is not None:
            self._pending["raw_chunks"].append(chunk)

    def take(self) -> dict | None:
        """返回本次请求的载荷记录（含累积的模型返回正文）。

        若本次请求触发了 Agently 的契约重试，`self.calls` 中会有多份**不同**载荷；
        此处返回第一份并把额外次数记在 `extra_attempts`，由调用方提示，
        避免静默吞掉真实的多轮请求。
        """
        unique = self.calls
        call = unique[0] if unique else None
        if call is not None:
            call["extra_attempts"] = len(unique) - 1
            self.total_unique += len(unique)
            self.summary.append(call)
        self.calls = []
        self._pending = None
        return call

    def unique_calls(self) -> list[dict]:
        """已归并的调用记录（同一请求的重复 prompt 读取已合并）。"""
        return list(self.calls)


@contextmanager
def tap_model_traffic(tap: ModelCallTap):
    """在 with 块内，所有 Agently 模型调用的 messages 与流式返回都会被记录。"""
    from agently.builtins.plugins.PromptGenerator.AgentlyPromptGenerator import (
        AgentlyPromptGenerator,
    )
    from agently.builtins.plugins.ModelRequester.OpenAICompatible.modules.response_adapter import (
        OpenAICompatibleResponseAdapterMixin,
    )

    original_to_messages = AgentlyPromptGenerator.to_messages
    original_broadcast = OpenAICompatibleResponseAdapterMixin.broadcast_response

    def patched_to_messages(self, **kwargs):  # noqa: ANN001
        messages = original_to_messages(self, **kwargs)
        tap.record_messages(messages)
        return messages

    async def patched_broadcast(self, response_generator):  # noqa: ANN001
        async for event, payload in original_broadcast(self, response_generator):
            if event == "delta":
                tap.record_delta(str(payload))
            elif event == "original_delta":
                tap.record_original(str(payload))
            yield event, payload

    AgentlyPromptGenerator.to_messages = patched_to_messages
    OpenAICompatibleResponseAdapterMixin.broadcast_response = patched_broadcast
    try:
        yield tap
    finally:
        AgentlyPromptGenerator.to_messages = original_to_messages
        OpenAICompatibleResponseAdapterMixin.broadcast_response = original_broadcast


# ══════════════════════════════════════════════════════════
# 展示
# ══════════════════════════════════════════════════════════


def dump_request(call: dict, *, show_raw_sse: bool = False) -> None:
    """按「真正发往 HTTP API 的载荷」逐字打印。"""
    messages = call["messages"]
    emit(f"  请求共 {len(messages)} 条 message：")
    if call.get("extra_attempts"):
        emit(f"  [注意] 本次请求触发了 Agently 契约重试 {call['extra_attempts']} 次（另有不同载荷，未展开）")
    for i, msg in enumerate(messages, 1):
        role = msg.get("role", "?")
        content = msg.get("content", "")
        emit("")
        emit(f"  ┌── messages[{i}] · role = {role} " + "─" * max(0, W - 30 - len(role)))
        if isinstance(content, list):
            for part in content:
                block(json.dumps(part, ensure_ascii=False, indent=2), 4)
        else:
            block(content, 4)
        emit("  └" + "─" * (W - 4))


def dump_response(call: dict, *, show_raw_sse: bool = False) -> None:
    content = (call.get("content") or "").strip()
    emit("")
    emit("  【模型返回 · 正文（累积后）】")
    if content:
        block(content, 4)
    else:
        block("（空）", 4)
    if show_raw_sse:
        emit("")
        emit(f"  【模型返回 · SSE 原始分片，共 {len(call['raw_chunks'])} 片】")
        for chunk in call["raw_chunks"][:6]:
            block(chunk.strip(), 4)
        if len(call["raw_chunks"]) > 6:
            block(f"…（其余 {len(call['raw_chunks']) - 6} 片省略）", 4)


# ══════════════════════════════════════════════════════════
# 样本制度（覆盖 4 种题型的语言特征）
# ══════════════════════════════════════════════════════════

SAMPLE_DOC_NAME = "数据安全管理规范（Prompt 审阅样例）"

SAMPLE_CLAUSES: list[dict[str, str]] = [
    {
        "chapter_no": "第一章",
        "section_no": "",
        "clause_no": "第二条",
        "text": "数据安全管理工作实行统一管理、分级负责的原则。",
    },
    {
        "chapter_no": "第二章",
        "section_no": "",
        "clause_no": "第三条",
        "text": "公司数据分为公开数据、内部数据、敏感数据三级。",
    },
    {
        "chapter_no": "第二章",
        "section_no": "",
        "clause_no": "第四条",
        "text": "客户身份证号、银行账号、联系方式属于敏感数据，须加密存储。",
    },
    {
        "chapter_no": "第三章",
        "section_no": "第一节",
        "clause_no": "第六条",
        "text": "禁止将客户数据存储在个人设备上，包括个人电脑、手机、U盘等便携存储介质。",
    },
]


def clauses_text(batch: list[dict[str, str]]) -> str:
    return "\n".join(
        f"[{c['chapter_no']} {c['section_no']} {c['clause_no']}] {c['text']}" for c in batch
    )


# ══════════════════════════════════════════════════════════
# 主流程
# ══════════════════════════════════════════════════════════


async def run_live(show_raw_sse: bool, model_override: str | None) -> int:
    """真实调用 DeepSeek，捕获每个核心节点的完整载荷。"""
    from app.ai.agently_setup import effective_config
    from app.ai.models import (
        GeneratedQuestionData,
        KeyPointExtraction,
        QuestionQualityResult,
        to_agently_output_spec,
    )
    from app.ai.prompts import (
        EXAM_EXPERT_ROLE,
        QUALITY_AUDITOR_ROLE,
        build_extraction_prompt,
        build_generation_prompt,
        build_quality_check_prompt,
    )

    cfg = effective_config()
    hr("核心处理节点 Prompt 原文捕获（真实调用）")
    emit(f"  provider      : {cfg['provider']}")
    emit(f"  chat 别名     : {cfg['chat_alias']} → {cfg['chat_model']}")
    emit(f"  chat base_url : {cfg['chat_base_url']}")
    emit(f"  embed 别名    : {cfg['embed_alias']} → {cfg['embed_model']}")
    emit(f"  制度          : {SAMPLE_DOC_NAME}")
    emit(f"  样本条款      : {len(SAMPLE_CLAUSES)} 条")
    emit("")
    emit("  拦截点：PromptGenerator.to_messages() → 即 request_data['data']['messages']")
    emit("  说明：以下每条 message 内容均为逐字原文，未经改写或省略")

    tap = ModelCallTap()

    # ── 节点 1：M2-1 考点提取 ──
    hr("节点 1／4　M2-1 考点提取（KeyPointExtraction）", "─")
    emit("  温度：0.0（audit_temperature）　契约：KeyPointExtraction")
    parts = build_extraction_prompt(SAMPLE_DOC_NAME, clauses_text(SAMPLE_CLAUSES))

    from app.ai.question_generator import QuestionGenerator

    gen = QuestionGenerator()
    with tap_model_traffic(tap):
        points, errors = await gen.extract_key_points(
            SAMPLE_DOC_NAME, SAMPLE_CLAUSES, batch_size=8, max_points=8
        )
    call = tap.take()
    if call:
        dump_request(call)
    dump_response(call or {}, show_raw_sse=show_raw_sse)
    emit("")
    emit(f"  【解析后考点】{len(points)} 个" + (f"　告警：{errors}" if errors else ""))
    for i, p in enumerate(points, 1):
        tl = {
            "single_choice": "单选题",
            "multi_choice": "多选题",
            "true_false": "判断题",
            "fill_blank": "填空题",
        }.get(p.question_type, p.question_type)
        emit(f"    {i}. {p.chapter_no} {p.clause_no} → {tl}｜{p.key_point}")

    if not points:
        emit("  [失败] 未提取到考点，终止后续节点")
        return 1

    # 保证 4 种题型都覆盖：缺哪种就补一个样本考点
    by_type = {p.question_type: p for p in points}
    from app.ai.question_generator import KeyPoint

    fallback = {
        "single_choice": ("第一章", "", "第二条", SAMPLE_CLAUSES[0]["text"]),
        "multi_choice": ("第二章", "", "第三条", SAMPLE_CLAUSES[1]["text"]),
        "fill_blank": ("第二章", "", "第四条", SAMPLE_CLAUSES[2]["text"]),
        "true_false": ("第三章", "第一节", "第六条", SAMPLE_CLAUSES[3]["text"]),
    }
    for qtype, (ch, sec, cl, text) in fallback.items():
        if qtype not in by_type:
            kp = KeyPoint(
                chapter_no=ch,
                section_no=sec,
                clause_no=cl,
                clause_text=text,
                question_type=qtype,
                key_point="（模板补位，用于覆盖该题型的 instruct 原文）",
                reason="确保四题型 Prompt 均可审阅",
            )
            points.append(kp)
            by_type[qtype] = kp

    # ── 节点 2：M2-2 题目生成（四题型） ──
    type_label = {
        "single_choice": "单选题",
        "multi_choice": "多选题",
        "true_false": "判断题",
        "fill_blank": "填空题",
    }
    order = ["single_choice", "multi_choice", "true_false", "fill_blank"]

    for idx, qtype in enumerate(order, 1):
        point = by_type.get(qtype)
        if point is None:
            continue
        hr(f"节点 2.{idx}／4　M2-2 题目生成 · {type_label[qtype]}（GeneratedQuestionData）", "─")
        emit("  温度：0.7（generation_temperature）　契约：GeneratedQuestionData")
        with tap_model_traffic(tap):
            result = await gen.generate_one(point, SAMPLE_DOC_NAME)
        call = tap.take()
        if call:
            dump_request(call)
        dump_response(call or {}, show_raw_sse=show_raw_sse)
        emit("")
        if result is None:
            emit("  [失败] 生成失败（模型未返回合规结构）")
            continue
        emit("  【Pydantic 验收后的结构化结果】")
        emit(f"     题型：{type_label[qtype]}")
        emit(f"     题干：{result.content}")
        for opt in result.options:
            emit(f"       {opt}")
        emit(f"     答案：{result.answer}")
        emit(f"     解析：{result.explanation}")
        emit(f"     来源：{result.source_label}")

        if idx != 1:
            continue
        # ── 节点 3：M2-3 质量校验（对第 1 题） ──
        hr("节点 3／4　M2-3 质量校验（QuestionQualityResult）", "─")
        emit("  温度：0.0　契约：QuestionQualityResult")
        import json as _json

        qparts = build_quality_check_prompt(
            _json.dumps(
                {
                    "question_type": result.question_type,
                    "content": result.content,
                    "options": result.options,
                    "answer": result.answer,
                },
                ensure_ascii=False,
            ),
            result.clause_text,
        )
        with tap_model_traffic(tap):
            quality = await gen.check_quality(result)
        call = tap.take()
        if call:
            dump_request(call)
        dump_response(call or {}, show_raw_sse=show_raw_sse)
        emit("")
        emit("  【Pydantic 验收后的裁判结论】")
        scores = quality.get("scores") or {}
        labels = {
            "consistency": "忠于原文",
            "unambiguous": "答案唯一",
            "no_duplicate": "选项不重复",
            "has_distractor": "干扰项有区分度",
            "self_contained": "题干自足",
        }
        for k, label in labels.items():
            emit(f"     {'[成功]' if scores.get(k) else '[失败]'} {label}")
        emit(f"     结论：pass = {quality.get('pass')}")
        emit(f"     理由：{quality.get('reason')}")
        emit(f"     问题：{quality.get('issues')}")

    hr("捕获汇总")
    emit(f"  真实模型请求次数（按载荷内容去重后）：{tap.total_unique}")
    emit(f"  framework 内部读取 prompt 的次数（含重复读取）：{tap.total_reads}")
    for i, c in enumerate(tap.summary, 1):
        roles = [m.get("role") for m in c["messages"]]
        chars = sum(len(str(m.get("content", ""))) for m in c["messages"])
        emit(f"    {i}. roles={roles}　prompt 字符数={chars}")
    hr()
    emit("以上均为真实 DeepSeek 调用，message 内容为逐字原文。")
    hr()
    return 0


def run_dry() -> int:
    """不调用模型，仅逐字渲染每个核心节点的 Prompt 模板。"""
    from app.ai.models import (
        GeneratedQuestionData,
        KeyPointExtraction,
        QuestionQualityResult,
        to_agently_output_spec,
    )
    from app.ai.prompts import (
        EXAM_EXPERT_ROLE,
        QUALITY_AUDITOR_ROLE,
        build_extraction_prompt,
        build_generation_prompt,
        build_quality_check_prompt,
    )

    hr("核型处理节点 Prompt 模板（--dry，未调用模型）")
    emit(f"  制度：{SAMPLE_DOC_NAME}")

    sections = [
        (
            "节点 1／4　M2-1 考点提取",
            EXAM_EXPERT_ROLE,
            build_extraction_prompt(SAMPLE_DOC_NAME, clauses_text(SAMPLE_CLAUSES)),
            KeyPointExtraction,
            "temperature = 0.0",
        ),
    ]
    type_map = {
        "single_choice": ("单选题", SAMPLE_CLAUSES[0]),
        "multi_choice": ("多选题", SAMPLE_CLAUSES[1]),
        "true_false": ("判断题", SAMPLE_CLAUSES[3]),
        "fill_blank": ("填空题", SAMPLE_CLAUSES[2]),
    }
    for qtype, (label, clause) in type_map.items():
        sections.append(
            (
                f"节点 2　M2-2 题目生成 · {label}",
                EXAM_EXPERT_ROLE,
                build_generation_prompt(
                    question_type=qtype,
                    document_name=SAMPLE_DOC_NAME,
                    chapter_no=clause["chapter_no"],
                    clause_no=clause["clause_no"],
                    clause_text=clause["text"],
                    related_clauses=["（检索到的同制度关联条款，仅作背景）"],
                ),
                GeneratedQuestionData,
                "temperature = 0.7",
            )
        )
    sections.append(
        (
            "节点 3／4　M2-3 质量校验",
            QUALITY_AUDITOR_ROLE,
            build_quality_check_prompt(
                json.dumps(
                    {
                        "question_type": "single_choice",
                        "content": "（待校验题干）",
                        "options": ["A. （选项）", "B. （选项）", "C. （选项）", "D. （选项）"],
                        "answer": ["A"],
                    },
                    ensure_ascii=False,
                ),
                SAMPLE_CLAUSES[0]["text"],
            ),
            QuestionQualityResult,
            "temperature = 0.0",
        )
    )

    for title, role, parts, model, note in sections:
        hr(title, "─")
        emit(f"  采样参数：{note}")
        emit("")
        emit("  ┌── message[1] · role = system（Agent role） " + "─" * 20)
        block(role, 4)
        emit("  ├── message[1] 续 · 四段 slot 由 framework 追加")
        for slot_name, slot_text in (("input", parts.input), ("info", parts.info), ("instruct", parts.instruct)):
            if not slot_text:
                continue
            emit(f"  │   [{slot_name.upper()}]")
            block(slot_text, 6)
        emit("  ├── OUTPUT REQUIREMENT（由 Pydantic 契约投影）")
        block(
            json.dumps(to_agently_output_spec(model), ensure_ascii=False, default=str, indent=2),
            6,
        )
        emit("  └" + "─" * (W - 4))

    hr()
    emit("注：system 与四段 slot 的实际拼接由 Agently 完成；")
    emit("    去掉 --dry 运行本脚本可看到 framework 拼装后的完整 message 原文。")
    hr()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="捕获核心处理节点发往模型的 Prompt 原文")
    parser.add_argument("--dry", action="store_true", help="不调用模型，仅渲染模板")
    parser.add_argument("--out", default="docs/prompt原文.md", help="输出文件（相对项目根）")
    parser.add_argument("--raw-sse", action="store_true", help="同时打印 SSE 原始分片")
    parser.add_argument("--model-key", default=None, help="覆盖 model_pool 别名")
    args = parser.parse_args()

    code = run_dry() if args.dry else asyncio.run(run_live(args.raw_sse, args.model_key))

    project_root = Path(__file__).resolve().parent.parent.parent
    out_path = (project_root / args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# 核心处理节点 Prompt 原文\n\n"
        "> 本文件由 `backend/scripts/capture_prompts.py` 生成，内容为逐字原文（未改写、未省略）。\n"
        f"> 生成方式：{'--dry（模板渲染，未调用模型）' if args.dry else '真实调用 DeepSeek，拦截 Agently 拼装后的完整请求体'}\n\n"
        "```text\n"
    )
    out_path.write_text(header + OUT.getvalue() + "\n```\n", encoding="utf-8")
    print(f"\n（已将完整原文写入 {out_path}）")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
