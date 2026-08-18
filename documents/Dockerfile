FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr tesseract-ocr-eng tesseract-ocr-spa \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY documents/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY database/documents.sql /app/documents.sql
COPY documents/document_service ./document_service
RUN useradd --system --uid 10001 --home-dir /nonexistent document-rag

USER document-rag

CMD ["uvicorn", "document_service.api:app", "--host", "0.0.0.0", "--port", "8789"]
