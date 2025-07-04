from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

class Doc(BaseModel):
    id: str
    title: str
    content: str
    tags: Optional[List[str]] = Field(default_factory=list)
    file_type: Optional[str] = None
    original_filename: Optional[str] = None
    author: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.now)
    source_system: Optional[str] = "ingestion-api"
    file_size_bytes: Optional[int] = None
    chunk_index: Optional[int] = None
    parent_document_id: Optional[str] = None
