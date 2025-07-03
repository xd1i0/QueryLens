import os
import time
from elasticsearch import Elasticsearch
from fastapi import FastAPI, HTTPException, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from models import Doc
import tempfile
from typing import Optional
import magic
import textract


ES_HOST = os.getenv("ELASTICSEARCH_HOST", "http://localhost:9200")
es = Elasticsearch(hosts=[ES_HOST], verify_certs=False)

app = FastAPI(title="QueryLens Ingestion & Search")

# Allow all origins for local testing
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

INDEX = "docs"

def wait_for_es(max_retries=30, delay=2):
    """Wait for Elasticsearch to be ready"""
    for i in range(max_retries):
        try:
            if es.ping():
                print(f"Connected to Elasticsearch at {ES_HOST}")
                return True
        except Exception as e:
            print(f"Attempt {i+1}: Waiting for Elasticsearch... ({e})")
            time.sleep(delay)
    return False

def extract_text_from_file(file_content: bytes, filename: str) -> tuple[str, str]:
    """Extract text from a file using Apache Tika"""
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=f"_{filename}") as temp_file:
            temp_file.write(file_content)
            temp_file.flush()

            file_type = magic.from_file(temp_file.name, mime=True)

            content = textract.process(temp_file.name).decode("utf-8")
            content = content.strip()

            os.unlink(temp_file.name)

            return content, file_type

    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to extract text from file: {str(e)}")

@app.on_event("startup")
async def startup():
    print("Starting up application...")

    if not wait_for_es():
        print(f"Warning: Could not connect to Elasticsearch at {ES_HOST}")
    print("Application startup complete")

@app.get("/")
def root():
    return {"status": "ok", "elasticsearch": ES_HOST}

@app.post("/docs/", status_code=201)
async def index_doc(doc: Doc):
    """Index a document with structured data"""
    try:
        resp = es.index(index=INDEX, id=doc.id, document=doc.dict())
        return {"result": resp["result"], "id": resp["_id"]}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to index document: {str(e)}")

@app.post("/docs/upload/")
async def upload_doc(
        file: UploadFile = File(...),
        doc_id: str = Form(...),
        title: Optional[str] = Form(None),
        tags: Optional[str] = Form(None)
):
    """Upload and index a document from a file ( PDF, Word, Markdown, etc.)"""
    try:
        #Read file
        file_content = await file.read()

        # Extract text using tika
        extracted_content, file_type = extract_text_from_file(file_content, file.filename)

        if not extracted_content:
            raise HTTPException(status_code=400, detail="No text extracted from file")

        # Parse tags if provided
        tags_list = [tag.strip() for tag in tags.split(",")] if tags else []

        # Create document object
        doc = Doc(
            id=doc_id,
            title=title or file.filename or "Untitled",
            content=extracted_content,
            tags=tags_list,
            file_type=file_type,
            original_filename=file.filename
        )

        response = es.index(index=INDEX, id=doc.id, document=doc.dict())

        return {
            "result": response["result"],
            "id": response["_id"],
            "extracted_chars": len(extracted_content),
            "file_type": file_type,
            "original_filename": file.filename,
            "message": "Document indexed successfully"
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to upload document: {str(e)}")

@app.get("/docs/{doc_id}")
async def get_doc(doc_id: str):
    try:
        resp = es.get(index=INDEX, id=doc_id)
        return resp["_source"]
    except Exception:
        raise HTTPException(status_code=404, detail="Document not found")

@app.get("/search/")
async def search(q: str, size: int = 10):
    try:
        body = {
            "query": {
                "multi_match": {
                    "query": q,
                    "fields": ["title^2", "content"]
                }
            },
            "size": size
        }
        resp = es.search(index=INDEX, body=body)
        hits = [
            {"id": hit["_id"], **hit["_source"], "score": hit["_score"]}
            for hit in resp["hits"]["hits"]
        ]
        return {"total": resp["hits"]["total"]["value"], "results": hits}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

# For local testing (not used in Docker)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
