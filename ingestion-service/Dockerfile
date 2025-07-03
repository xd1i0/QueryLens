FROM python:3.10-slim

# Install Java (required for Tika)
RUN apt-get update && apt-get install -y \
    libmagic1 \
    libmagic-dev \
    antiword \
    unrtf \
    poppler-utils \
    tesseract-ocr \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY src/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY src/ ./

ENV PYTHONPATH=/app

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]