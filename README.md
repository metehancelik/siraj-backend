# Siraj RAG Backend

A **source-grounded (RAG)** backend for mobile chat, built on data collected from trusted religious
sources (Diyanet meal/tefsir/hadis/fetva, TDV İslâm Ansiklopedisi, Risale-i Nur Külliyatı).
It connects to your own `gemma-4-12b` model (OpenAI-compatible endpoint), bases its answers
only on the retrieved texts, and attaches a clickable list of sources to every answer.

```
Mobile app ──POST /v1/chat (SSE)──▶ Backend ──┬─▶ TEI (bge-m3)  → embed the question
                                               ├─▶ Postgres/pgvector → hybrid search
                                               └─▶ gemma-4-12b  → streamed answer
```

The LLM key lives **only on the backend**; the mobile app never talks to the model directly.

---

## ⚠️ CPU-only server: a small model is required instead of 12B

The current `gemma-4-12b` was measured and is unusable for mobile chat on CPU:

| | Generation | Prompt processing | ~120-word answer |
|---|---|---|---|
| gemma-4-12b (CPU) | **~1.9 tokens/s** | ~20 tokens/s | **1.5–2.5 minutes** |

In RAG the retrieved context changes with every question, so prompt processing is not cached;
no setting makes 12B acceptable on CPU.

**Solution: use a smaller chat model.** RAG makes this safe: the model's job is no longer to
*remember* facts, but to summarize the Diyanet text placed in front of it in Turkish, cite the
source, and refuse if the text doesn't cover it. A 3-4B model can do that; the hard knowledge
sits in the retrieved context.

Recommendations (on CPU with llama.cpp, from fastest to highest quality):
1. **Gemma 3 4B Instruct (Q4_K_M)** - familiar family, good Turkish, strong instruction following. Try this first.
2. **Qwen2.5-7B-Instruct (Q4_K_M)** - if 4B quality feels thin; ~1.5× slower but more accurate.

Repeat the same `timings` measurement on the new model and decide:
```bash
curl -N -s <endpoint>/v1/chat/completions -H "Authorization: Bearer <key>" \
  -H "Content-Type: application/json" \
  -d '{"model":"<yeni-model>","stream":true,"max_tokens":200,
       "messages":[{"role":"user","content":"'"$(head -c 1500 /dev/urandom | base64)"'"}]}' \
  | grep -o '"timings":{[^}]*}' | tail -1
```

### llama.cpp settings on CPU (important)
- `--threads N` : N = number of **physical** cores (not hyperthreads).
- `--ctx-size 4096` : since the context is kept small, 4096 is enough; a smaller ctx means less memory and more speed.
- `-b 512 -ub 512` : improves prompt processing throughput.
- The build should use AVX2 (AVX-512 if available); a binary compiled with `-march=native` makes a big difference.
- Keep the system prompt fixed and at the very start (the backend already does this) → llama.cpp caches that prefix.

### Realistic expectations
With the reduced context (`TOP_K=3`, `MAX_CHUNK_CHARS=550`, `LLM_MAX_TOKENS=320`) and a 4B model,
expect roughly **~15–45 seconds** per question on CPU (depending on core count and memory bandwidth).
It won't be instant chat; it's more like "a friend who thinks it over and answers with sources". The
mobile side smooths this out: sources are shown immediately before generation, the answer streams
token by token, and a "preparing answer" indicator carries the wait. If speed is critical, a small
rented GPU (L4/A10) brings the same work down to 2–4 seconds - but the backend is the same either way,
only `LLM_BASE_URL` changes.

---

## Docker + Coolify deployment (recommended)

A single image runs both the API and the ingest. The image is **code only** (small); the JSONL data
is not baked into the image but mounted as a directory during ingest. This lets the backend live in
its own GitHub repo (data/crawler stay separate). TEI (bge-m3) and pgvector Postgres are wired up
alongside it via `docker-compose.yml`. The schema is applied automatically on the backend's first start.

> **Note (architecture):** TEI's CPU image is **amd64** only; that's fine on a typical x86_64 server
> (Coolify included), but it won't run locally on an Apple Silicon Mac. pgvector and the
> backend are multi-arch.
>
> **Note (Postgres extensions):** The schema requires the `vector` and `unaccent` extensions
> (`unaccent` is needed so Turkish search matches "zekat" ↔ "Zekât"). The official/pgvector
> Postgres images include both. If you use a managed Postgres, make sure these
> extensions are enabled.

### 1) Build both images and push them to Docker Hub

From this directory, on a machine with good internet (the server is amd64):

```bash
cd siraj-backend
docker login

# a) Backend (code only, small):
docker buildx build --platform linux/amd64 \
  -t metehancelik/siraj-backend:latest --push .

# b) Embeddings (TEI image with the bge-m3 model BAKED IN, ~3.85GB):
docker buildx build --platform linux/amd64 -f Dockerfile.embeddings \
  -t metehancelik/siraj-embeddings:latest --push .
```

> Why a baked-in embeddings image? A broken proxy env on the server prevented standard TEI from
> downloading the model from HuggingFace at runtime ("relative URL without a base").
> Since the model is baked into the image, TEI never downloads anything → the problem goes away. Also,
> TEI's CPU image uses ONNX (not safetensors), so bge-m3's onnx weights are the ones baked in.

### 2) Run it with compose on Coolify

Give `backend/docker-compose.yml` to Coolify as a "Docker Compose" resource and enter the
environment variables:

| Variable | Example |
|---|---|
| `SIRAJ_IMAGE` | `metehancelik/siraj-backend:latest` |
| `POSTGRES_PASSWORD` | a strong password |
| `LLM_BASE_URL` | `http://<llama-cpp-host>:8080/v1` (your own model) |
| `LLM_API_KEY` | your key (empty if none) |
| `LLM_MODEL` | `gemma-3-4b-it` (CPU recommendation) |
| `API_TOKEN` | Bearer token the mobile app sends (empty = authentication disabled) |

On deploy, `backend` (:8000), `embeddings` (TEI - model baked in, no download, "Ready" right away)
and `db` (pgvector) come up. The backend applies the schema automatically. In Coolify, bind 8000 to a
domain (the reverse proxy must disable buffering for SSE - see the nginx note below).

`POSTGRES_PASSWORD` **cannot be empty** (if it is, pgvector won't start and the backend gets "connection refused").

### 3) Starting the ingest afterwards

Since the data isn't in the image, first copy the JSONL to the server (once):

```bash
# From your own machine (crawler output is in ~/Desktop/siraj-crawler/data):
scp data/*.jsonl kullanici@sunucu:/opt/siraj/data/
```

Then, where the compose file lives, point `DATA_DIR` at that directory and run the ingest as a
one-off job (compose mounts this directory as `/app/data`):

```bash
DATA_DIR=/opt/siraj/data docker compose --profile ingest run --rm ingest
# If it will take long, run it detached:
DATA_DIR=/opt/siraj/data docker compose --profile ingest up -d ingest
docker compose logs -f ingest
```

It embeds ~235,000 chunks with bge-m3 and loads them into pgvector (an overnight job on CPU with TEI;
it can be re-run, ON CONFLICT updates existing rows). When it finishes, check retrieval:

```bash
docker compose exec backend python -m ingest.check_retrieval
```

If the top passages for the sample questions are on topic, chat is ready.

---

## Manual setup (without Docker, on a VPS)

For ingest, Postgres + the embedding service + the JSONL data must be in the same place. The simplest
topology: put everything on the VPS and copy the `data/*.jsonl` files there.

### 1) Embedding model: **BAAI/bge-m3** (setup and access)

`bge-m3` was chosen because it is one of the strongest multilingual embeddings for mixed Turkish +
Arabic religious text (1024 dimensions, 8192 context, no prefix required).

**The concept first:** LiteLLM doesn't *run* models, it only *routes* to them. Your gemma also actually
runs on a server behind LiteLLM. bge-m3 needs a runner too - the best fit is
**HF Text Embeddings Inference (TEI)**. Once TEI is running, the backend can reach it in two ways.

**Run TEI** (bge-m3, ~2.3GB RAM; downloads the model on first start):
```bash
# CPU (since your server is CPU; the image is amd64):
docker run -d --name tei -p 8080:80 -v $PWD/tei-data:/data \
  ghcr.io/huggingface/text-embeddings-inference:cpu-1.5 --model-id BAAI/bge-m3
# With a GPU: --gpus all + image ':1.5'
```
> If you use `docker-compose.yml`, the `embeddings` service already does this; don't run it separately.

**Access - two options:**

| | How | When |
|---|---|---|
| **A. Direct TEI** | `.env`: `EMBEDDING_MODE=tei`, `EMBEDDING_URL=http://embeddings:80` | Simplest and fastest. The backend reaches TEI privately over the compose network; no need to expose it. **Ideal for ingest** (no LiteLLM hop). This is the compose default. |
| **B. Via LiteLLM** | Register TEI in LiteLLM, `.env`: `EMBEDDING_MODE=openai`, `EMBEDDING_BASE_URL=<litellm>/v1`, `EMBEDDING_MODEL=bge-m3`, `EMBEDDING_API_KEY=<anahtar>` | If you want to reach bge-m3 through the **same gateway and key** as gemma, with logging/limits in one place. |

**For B, add to LiteLLM's `config.yaml`** (TEI serves an OpenAI-compatible `/v1/embeddings`):
```yaml
model_list:
  - model_name: bge-m3
    litellm_params:
      model: openai/bge-m3
      api_base: http://TEI_HOST:8080/v1   # address where TEI runs
      api_key: "none"
```
Save and restart LiteLLM; `POST <litellm>/v1/embeddings` (model: `bge-m3`,
your existing Bearer key) now works.

> Recommendation: for the single embedding at query time, B (unified gateway) is nice; but **ingest makes
> 235,000 calls** - there A (direct TEI) is faster and doesn't bloat the LiteLLM logs. You can
> mix them: run the ingest with `EMBEDDING_MODE=tei` and the backend with `openai`.

> Alternative model: `intfloat/multilingual-e5-large` (1024 dimensions). If you use it, set
> `EMBEDDING_USE_E5_PREFIX=true` in `.env` - e5 requires `query:`/`passage:` prefixes, bge-m3 does not.

### 2) Postgres + pgvector

You can use your existing Postgres.

```bash
# pgvector extension (Debian/Ubuntu, PG 16 example):
sudo apt install postgresql-16-pgvector
# Schema:
psql "$DATABASE_URL" -f schema.sql
```

### 3) Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # fill in DATABASE_URL, EMBEDDING_URL, LLM_* values
```

### 4) Load the data (ingest)

```bash
# First validate the pipeline with a small trial run:
python -m ingest.ingest --data-dir ../data --source fetva --limit 50

# Everything (~235,000 chunks; depending on embedding speed ~30-60 min on GPU, hours on CPU):
python -m ingest.ingest --data-dir ../data
```

Chunking depends on the source: fetva stays whole (question/answer), meal is split verse by verse,
tefsir/hadis/dia/risale are split into ~300-token windows (dia's boilerplate header/footnote and tabs
are stripped; each risale window is prefixed with the section's path within the Külliyat).

### 5) Check retrieval quality (before moving on to the LLM)

```bash
python -m ingest.check_retrieval
```

If the top passages for the sample questions are on topic, continue. "Bugünün duası nedir?"
("What is today's prayer?") is an app-state question; it's normal for it to come back irrelevant or
empty - chat politely declines it.

### 6) Start the server

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
# Make it persistent with systemd/pm2; put it behind nginx for TLS (buffering off for SSE).
```

Health check: `curl localhost:8000/health` → `{"status":"ok","chunks": N}`

---

## API

`POST /v1/chat` - `Authorization: Bearer <API_TOKEN>` (if set)

```json
{ "messages": [ {"role":"user","content":"Namaz nasıl kılınır?"} ] }
```

The response is `text/event-stream`, each line `data: {json}`:

| type | fields | meaning |
|---|---|---|
| `sources` | `sources:[{n,source,label,title,url}]` | retrieved sources (once, before generation) |

The request body is `{"messages":[...], "lang":"tr"|"en"}`. The corpus is entirely Turkish; when
`lang=en` arrives, the question is first translated into Turkish and searched that way, and the model
reads the sources in Turkish and writes the answer in English (source labels are translated into
English too, titles stay Turkish).

Why translation is needed: searching the English query directly was measured and didn't work - the
relevance threshold (`retrieval_max_distance`) doesn't discriminate at cross-lingual distances ("What
is the capital of France?" scored 0.31, closer than real questions), and since the `tsv` column is
`to_tsvector('turkish')`, full-text search never kicks in. Once translated into Turkish, both work as
measured and tuned. The cost is ~3-3.5 s per question (repeated questions come from the cache).
| `delta` | `text` | a chunk of generated text (many times) |
| `done` | - | finished |
| `error` | `message` | error |

The mobile side reads this protocol by streaming it over XHR in `siraj/src/services/chatService.ts`.

## Settings (`.env`)

To lower latency on slow hardware: `TOP_K` (4→3), `MAX_CHUNK_CHARS` (900→500),
`LLM_MAX_TOKENS` (512→256). All are documented in `.env.example`.

## Nginx note (SSE)

```nginx
location /v1/chat {
    proxy_pass http://127.0.0.1:8000;
    proxy_buffering off;          # required so the stream passes through immediately
    proxy_read_timeout 600s;
}
```
