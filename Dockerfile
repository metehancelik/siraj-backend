# Siraj RAG backend — bağımsız imaj (yalnızca kod; veri gömülü DEĞİL).
# Derleme bağlamı BU dizin (backend/):
#   cd backend && docker build -t <kullanici>/siraj-backend:latest .
#
# Aynı imaj hem API'yi (varsayılan CMD) hem ingest'i çalıştırır. Ingest, JSONL verisini
# çalışma anında mount edilen /app/data dizininden okur (docker-compose.yml'e bakın).

FROM python:3.12-slim

WORKDIR /app
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

# asyncpg/httpx/fastapi manylinux tekerlekleriyle gelir; derleyici gerekmez.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Uygulama kodu ve şema (veri yok → imaj küçük, kod iterasyonu veriye dokunmaz)
COPY app /app/app
COPY ingest /app/ingest
COPY schema.sql /app/schema.sql

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
