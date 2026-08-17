FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY mcp/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY mcp/memory_mcp ./memory_mcp
COPY mcp/start.sh .
RUN chmod +x start.sh

CMD ["./start.sh"]
