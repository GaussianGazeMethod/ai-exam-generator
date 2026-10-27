"""制度文档解析引擎 — M1-2 格式解析 + M1-3 结构识别。

依据 spec/核心模块架构设计.md 模块一：
- M1-2 格式解析：Word 解析（含表格）、PDF 解析（含扫描件 OCR）
- M1-3 结构识别：基于编号规则（第X章、第Y条）或标题样式自动识别层级树

依据 spec/技术选型方案.md 文档解析选型：
- Word (.docx) → python-docx（提取文本 + 样式/标题层级）
- Word (.doc)  → LibreOffice 转 docx 后解析
- PDF（文本型）→ PyMuPDF（fitz）
- PDF（扫描件）→ Unstructured + Tesseract OCR（备用，当前以文本型为主）
- 结构识别    → 自研规则引擎（编号正则）

输出统一 JSON 结构：章节树 + 条款原文 + 层级关系
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────
# 编号规则正则（中文数字 + 阿拉伯数字 + 全角数字）
# ──────────────────────────────────────────────────────────

_CN_NUM = r"[一二三四五六七八九十百零〇\d]+"

# 章：第X章 / 第X章 标题
RE_CHAPTER = re.compile(rf"^\s*第\s*({_CN_NUM})\s*章\s*(.*)$")
# 节：第X节 / 第一节
RE_SECTION = re.compile(rf"^\s*第\s*({_CN_NUM})\s*节\s*(.*)$")
# 条：第X条 / 第X.Y条 / 第X条之N
RE_CLAUSE = re.compile(rf"^\s*第\s*({_CN_NUM}(?:[\.\-]\d+)?)\s*条(?:之({_CN_NUM}))?\s*(.*)$")
# 款/项：（一）/（1）/ 1. / 一、
RE_ITEM = re.compile(rf"^\s*[（(]\s*({_CN_NUM})\s*[)）]\s*(.*)$")
RE_ITEM2 = re.compile(rf"^\s*({_CN_NUM})\s*[、\.]\s*(.+)$")

# 标题样式关键字（python-docx style.name）—— 标题层级辅助判断
_HEADING_STYLE_HINTS = ("heading", "标题", "title")


@dataclass
class ParsedClause:
    """单条条款。"""

    clause_no: str
    text: str
    chapter_no: str = ""
    section_no: str = ""
    paragraph_index: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "clause_no": self.clause_no,
            "chapter_no": self.chapter_no,
            "section_no": self.section_no,
            "text": self.text,
            "paragraph_index": self.paragraph_index,
        }


@dataclass
class ParsedSection:
    section_no: str
    title: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"section_no": self.section_no, "title": self.title}


@dataclass
class ParsedChapter:
    chapter_no: str
    title: str = ""
    sections: list[ParsedSection] = field(default_factory=list)
    clauses: list[ParsedClause] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chapter_no": self.chapter_no,
            "title": self.title,
            "sections": [s.to_dict() for s in self.sections],
            "clauses": [c.to_dict() for c in self.clauses],
        }


@dataclass
class ParseResult:
    """解析结果统一结构（供 S1 存储 / S2 消费）。"""

    document_name: str = ""
    file_type: str = ""
    page_count: int = 0
    parse_method: str = ""
    chapters: list[ParsedChapter] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def clause_count(self) -> int:
        return sum(len(c.clauses) for c in self.chapters)

    @property
    def chapter_count(self) -> int:
        return len(self.chapters)

    @property
    def section_count(self) -> int:
        return sum(len(c.sections) for c in self.chapters)

    def to_structured_content(self) -> dict[str, Any]:
        """转成写入 Document.structured_content 的 JSON 结构。"""
        return {
            "document_name": self.document_name,
            "file_type": self.file_type,
            "page_count": self.page_count,
            "parse_method": self.parse_method,
            "stats": {
                "chapter_count": self.chapter_count,
                "section_count": self.section_count,
                "clause_count": self.clause_count,
            },
            "chapters": [c.to_dict() for c in self.chapters],
            "warnings": self.warnings,
        }


# ──────────────────────────────────────────────────────────
# 解析引擎
# ──────────────────────────────────────────────────────────


class DocumentParser:
    """制度文档解析器：Word / PDF → 结构化章节树。"""

    # ── 对外入口 ──

    def parse(self, file_path: str, document_name: str = "") -> ParseResult:
        """解析制度文档。

        Args:
            file_path: 文档绝对/相对路径
            document_name: 制度名称（用于结果标注）

        Raises:
            FileNotFoundError: 文件不存在
            ValueError: 不支持的文件格式
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"文件不存在: {file_path}")

        ext = os.path.splitext(file_path)[1].lower()
        result = ParseResult(document_name=document_name, file_type=ext.lstrip("."))

        if ext == ".docx":
            blocks, page_count = self._extract_docx(file_path)
            result.parse_method = "python-docx + 编号规则引擎"
        elif ext == ".pdf":
            blocks, page_count = self._extract_pdf(file_path)
            result.parse_method = "PyMuPDF + 编号规则引擎"
        elif ext == ".doc":
            # spec：.doc 用 LibreOffice 转 docx 后解析
            converted = self._convert_doc_to_docx(file_path)
            blocks, page_count = self._extract_docx(converted)
            result.parse_method = "LibreOffice 转换 + python-docx + 编号规则引擎"
            try:
                os.unlink(converted)
            except OSError:
                pass
        else:
            raise ValueError(f"不支持的文件格式: {ext}（支持 .doc/.docx/.pdf）")

        result.page_count = page_count
        result.chapters = self._build_tree(blocks, result.warnings)

        if not result.chapters:
            result.warnings.append("未识别到任何章节或条款，建议人工修正章节树")

        return result

    # ── 文本块提取 ──

    def _extract_docx(self, file_path: str) -> tuple[list[dict[str, Any]], int]:
        """用 python-docx 提取段落（含表格文本）与标题样式。"""
        try:
            import docx  # python-docx
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("缺少 python-docx，请先安装依赖") from exc

        document = docx.Document(file_path)
        blocks: list[dict[str, Any]] = []
        idx = 0

        for para in document.paragraphs:
            text = (para.text or "").strip()
            if not text:
                continue

            style_name = ""
            try:
                style_name = (para.style.name or "") if para.style else ""
            except Exception:  # noqa: BLE001 - 样式读取失败不影响正文解析
                style_name = ""

            is_heading = any(h in style_name.lower() for h in _HEADING_STYLE_HINTS)
            blocks.append(
                {
                    "text": text,
                    "style": style_name,
                    "is_heading": is_heading,
                    "paragraph_index": idx,
                }
            )
            idx += 1

        # 表格内容作为独立块（制度中常以表格承载要求，spec 要求"Word 解析（含表格）"）
        for t_i, table in enumerate(document.tables):
            for r_i, row in enumerate(table.rows):
                cells = [(c.text or "").strip() for c in row.cells]
                # 去掉合并单元格造成的重复
                deduped: list[str] = []
                for c in cells:
                    if not deduped or deduped[-1] != c:
                        deduped.append(c)
                line = " | ".join([c for c in deduped if c])
                if line:
                    blocks.append(
                        {
                            "text": f"[表格{t_i + 1} 第{r_i + 1}行] {line}",
                            "style": "",
                            "is_heading": False,
                            "paragraph_index": idx,
                        }
                    )
                    idx += 1

        # python-docx 无法直接得到渲染页数；用"每页约 40 段"做保守估算，仅作展示用
        page_count = max(1, round(len(blocks) / 40)) if blocks else 0

        return blocks, page_count

    def _extract_pdf(self, file_path: str) -> tuple[list[dict[str, Any]], int]:
        """用 PyMuPDF 提取 PDF 文本块，并按行拆分。"""
        try:
            import fitz  # PyMuPDF
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("缺少 PyMuPDF，请先安装依赖") from exc

        blocks: list[dict[str, Any]] = []
        idx = 0
        corrupted = 0

        with fitz.open(file_path) as pdf:
            page_count = pdf.page_count
            for page in pdf:
                try:
                    text = page.get_text("text") or ""
                except Exception as exc:  # noqa: BLE001 - 损坏页跳过
                    corrupted += 1
                    logger.warning("PDF 第 %s 页文本提取失败: %s", page.number + 1, exc)
                    continue

                for raw_line in text.splitlines():
                    line = raw_line.strip()
                    if not line:
                        continue
                    blocks.append(
                        {
                            "text": line,
                            "style": "",
                            "is_heading": False,
                            "paragraph_index": idx,
                            "page": page.number + 1,
                        }
                    )
                    idx += 1

        if corrupted:
            logger.warning("共 %s 页提取失败，可能为扫描件（需 OCR）", corrupted)

        return blocks, page_count

    def _convert_doc_to_docx(self, file_path: str) -> str:
        """用 LibreOffice 将 .doc 转为 .docx（spec 指定的兼容方案）。"""
        out_dir = tempfile.mkdtemp(prefix="doc_convert_")
        try:
            proc = subprocess.run(
                ["soffice", "--headless", "--convert-to", "docx", "--outdir", out_dir, file_path],
                capture_output=True,
                timeout=120,
                check=False,
            )
        except FileNotFoundError as exc:
            raise RuntimeError(
                "本机未安装 LibreOffice，无法解析 .doc 格式。"
                "请将文档另存为 .docx，或在镜像内安装 libreoffice。"
            ) from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("LibreOffice 转换 .doc 超时") from exc

        if proc.returncode != 0:
            raise RuntimeError(f"LibreOffice 转换失败: {proc.stderr.decode(errors='ignore')[:300]}")

        target = os.path.join(
            out_dir, os.path.splitext(os.path.basename(file_path))[0] + ".docx"
        )
        if not os.path.exists(target):
            raise RuntimeError("LibreOffice 转换后未找到输出文件")
        return target

    # ── 结构识别 ──

    def _build_tree(
        self, blocks: list[dict[str, Any]], warnings: list[str]
    ) -> list[ParsedChapter]:
        """按编号规则构建 章-节-条款 层级树。"""
        # ── 第一遍：扫描出扁平的条款列表 + 章节顺序 ──
        flat_clauses: list[ParsedClause] = []
        chapter_order: list[tuple[str, str]] = []          # [(章编号, 章标题)]
        section_map: dict[str, dict[str, str]] = {}        # 章编号 -> {节编号: 节标题}
        preamble_lines: list[str] = []                     # 任何编号之前的正文

        cur_ch: tuple[str, str] = ("", "")
        cur_sec: tuple[str, str] = ("", "")
        cur_clause: ParsedClause | None = None

        def _flush_clause() -> None:
            nonlocal cur_clause
            if cur_clause is not None and cur_clause.text.strip():
                flat_clauses.append(cur_clause)
            cur_clause = None

        def _register_chapter(pair: tuple[str, str]) -> None:
            if pair[0] not in [c[0] for c in chapter_order]:
                chapter_order.append(pair)
                section_map.setdefault(pair[0], {})

        for block in blocks:
            text: str = block["text"]

            # 1) 章
            m_ch = RE_CHAPTER.match(text)
            if m_ch:
                _flush_clause()
                cur_ch = (f"第{m_ch.group(1)}章", m_ch.group(2).strip())
                cur_sec = ("", "")
                _register_chapter(cur_ch)
                continue

            # 2) 节
            m_sec = RE_SECTION.match(text)
            if m_sec:
                _flush_clause()
                cur_sec = (f"第{m_sec.group(1)}节", m_sec.group(2).strip())
                _register_chapter(cur_ch)
                section_map[cur_ch[0]][cur_sec[0]] = cur_sec[1]
                continue

            # 3) 条
            m_cl = RE_CLAUSE.match(text)
            if m_cl:
                _flush_clause()
                label = f"第{m_cl.group(1)}条"
                if m_cl.group(2):
                    label += f"之{m_cl.group(2)}"
                cur_clause = ParsedClause(
                    clause_no=label,
                    text=m_cl.group(3).strip(),
                    chapter_no=cur_ch[0],
                    section_no=cur_sec[0],
                    paragraph_index=block.get("paragraph_index", 0),
                )
                continue

            # 4) 普通正文
            if cur_clause is not None:
                # 续接到当前条款（条款常有多个自然段）
                cur_clause.text = f"{cur_clause.text}\n{text}".strip()
            else:
                # 尚未出现任何条款 → 归入前言
                preamble_lines.append(text)

        _flush_clause()

        # ── 第二遍：按章分组，构建层级树 ──
        chapters: list[ParsedChapter] = []
        by_chapter: dict[str, ParsedChapter] = {}

        for no, title in chapter_order:
            ch = ParsedChapter(chapter_no=no, title=title)
            ch.sections = [
                ParsedSection(section_no=s_no, title=s_title)
                for s_no, s_title in section_map.get(no, {}).items()
            ]
            by_chapter[no] = ch
            chapters.append(ch)

        orphan_clauses: list[ParsedClause] = []
        for clause in flat_clauses:
            target = by_chapter.get(clause.chapter_no)
            if target is not None:
                target.clauses.append(clause)
            else:
                orphan_clauses.append(clause)

        # 未挂到任何章的条款（含"有节无章"或全篇无编号）→ 独立前言章
        if orphan_clauses:
            preamble = ParsedChapter(chapter_no="", title="（前言 / 未编号内容）")
            preamble.clauses = orphan_clauses
            chapters.insert(0, preamble)

        if not chapter_order and orphan_clauses:
            warnings.append("未识别到章/节编号结构，已按段落归为单章，建议人工修正章节树")
        if preamble_lines:
            warnings.append(f"文档开头有 {len(preamble_lines)} 段未编号正文，未纳入条款树")

        return chapters


_parser_singleton: DocumentParser | None = None


def get_document_parser() -> DocumentParser:
    global _parser_singleton
    if _parser_singleton is None:
        _parser_singleton = DocumentParser()
    return _parser_singleton
