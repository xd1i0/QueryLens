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
    #author: Optional[str] = None
    #timestamp: Optional[datetime] = None
    #data_source_url: Optional[str] = None

