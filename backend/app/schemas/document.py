"""制度文档 Schema。"""

from __future__ import annotations
from typing import Optional

from datetime import datetime

from pydantic import BaseModel


class DocumentBase(BaseModel):
    name: str
    version: str = "1.0"
    remark: Optional[str] = None


class DocumentCreate(DocumentBase):
    pass


class DocumentUpdate(BaseModel):
    name: Optional[str] = None
    remark: Optional[str] = None


class ClauseItem(BaseModel):
    clause_no: str
    text: str


class SectionItem(BaseModel):
    chapter_no: str
    title: str
    clauses: list[ClauseItem] = []


class DocumentResponse(BaseModel):
    id: str
    name: str
    version: str
    file_name: str
    file_type: str
    file_size: int
    page_count: int
    status: str
    structured_content: Optional[str] = None
    remark: Optional[str] = None
    question_count: int = 0
    uploader_id: Optional[str] = None
    created_at: datetime | str
    updated_at: datetime | str

    model_config = {"from_attributes": True}


class DocumentListResponse(BaseModel):
    items: list[DocumentResponse]
    total: int