FROM python:3.10-slim

# Install system dependencies for Tika and file handling
RUN apt-get update && apt-get install -y \
    openjdk-17-jre-headless \
    libmagic1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy only requirements first for better caching
COPY src/requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY src/ ./

ENV PYTHONPATH=/app

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8000/ || exit 1

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
