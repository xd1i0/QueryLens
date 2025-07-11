from pydantic import BaseModel, Field
from typing import List, Optional
from datetime import datetime

class Doc(BaseModel):
    id: str
    title: str
    content: str
    tags: Optional[List[str]] = Field(default_factory=list)
    author: Optional[str] = None
    source_system: Optional[str] = "ingestion-api"
    timestamp: datetime = Field(default_factory=datetime.now)
