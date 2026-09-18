# Siraj RAG backend - standalone image (code only; data is NOT baked in).
# The build context is THIS directory (backend/):
#   cd backend && docker build -t <user>/siraj-backend:latest .
#
# The same image runs both the API (default CMD) and ingest. Ingest reads the JSONL data
# from /app/data, mounted at runtime (see docker-compose.yml).

FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# asyncpg/httpx/fastapi ship manylinux wheels; no compiler needed.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# App code and schema (no data → small image, code iterations never touch data)
COPY app /app/app
COPY ingest /app/ingest
COPY schema.sql /app/schema.sql

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
