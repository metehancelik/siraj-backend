# Siraj RAG Backend

Diyanet kaynaklarından (meal, tefsir, hadis, fetva, TDV İslâm Ansiklopedisi) toplanan
veriyle çalışan, mobil sohbet için **kaynağa dayalı (RAG)** backend. Kendi `gemma-4-12b`
modelinize (OpenAI-uyumlu endpoint) bağlanır, cevapları yalnızca getirilen Diyanet
metinlerine dayandırır ve her cevaba tıklanabilir kaynak listesi ekler.

```
Mobil uygulama ──POST /v1/chat (SSE)──▶ Backend ──┬─▶ TEI (bge-m3)  → soruyu embed'le
                                                   ├─▶ Postgres/pgvector → hibrit arama
                                                   └─▶ gemma-4-12b  → akışlı cevap
```

LLM anahtarı **sadece backend'de** durur; mobil uygulama modele doğrudan erişmez.

---

## ⚠️ CPU-only sunucu: 12B yerine küçük model şart

Mevcut `gemma-4-12b` ölçüldü — CPU'da mobil sohbet için kullanılamaz:

| | Üretim | Prompt işleme | ~120 kelimelik cevap |
|---|---|---|---|
| gemma-4-12b (CPU) | **~1.9 token/sn** | ~20 token/sn | **1.5–2.5 dakika** |

RAG'de getirilen bağlam her soruda değiştiği için prompt işleme cache'lenmez; hiçbir ayar
12B'yi CPU'da kabul edilebilir yapmaz.

**Çözüm: sohbet modelini küçültün.** RAG bunu güvenli kılar — modelin işi artık bilgiyi
*hatırlamak* değil, önüne konan Diyanet metnini Türkçe özetleyip kaynak göstermek ve metinde
yoksa reddetmek. Bu görevi 3–4B'lik bir model de yapar; zor bilgi getirilen bağlamda durur.

Öneri (CPU'da llama.cpp ile, hızlıdan kaliteliye):
1. **Gemma 3 4B Instruct (Q4_K_M)** — tanıdık aile, iyi Türkçe, güçlü talimat takibi. İlk bunu deneyin.
2. **Qwen2.5-7B-Instruct (Q4_K_M)** — 4B kalitesi ince gelirse; ~1.5× daha yavaş ama daha isabetli.

Aynı `timings` ölçümünü yeni modelde tekrarlayıp karar verin:
```bash
curl -N -s <endpoint>/v1/chat/completions -H "Authorization: Bearer <key>" \
  -H "Content-Type: application/json" \
  -d '{"model":"<yeni-model>","stream":true,"max_tokens":200,
       "messages":[{"role":"user","content":"'"$(head -c 1500 /dev/urandom | base64)"'"}]}' \
  | grep -o '"timings":{[^}]*}' | tail -1
```

### CPU'da llama.cpp ayarları (önemli)
- `--threads N` : N = **fiziksel** çekirdek sayısı (hyperthread değil).
- `--ctx-size 4096` : bağlam küçük tutulduğu için 4096 yeter; küçük ctx daha az bellek/daha hızlı.
- `-b 512 -ub 512` : prompt işleme verimini artırır.
- Derleme AVX2 (varsa AVX-512) kullanmalı; `-march=native` ile derlenmiş binary büyük fark yaratır.
- Sistem promptu sabit ve en başta (backend zaten böyle yapıyor) → llama.cpp bu öneki cache'ler.

### Gerçekçi beklenti
Küçültülmüş bağlam (`TOP_K=3`, `MAX_CHUNK_CHARS=550`, `LLM_MAX_TOKENS=320`) + 4B model ile
CPU'da soru başına kabaca **~15–45 saniye** (çekirdek sayısı ve bellek bant genişliğine bağlı).
Anlık sohbet olmaz; "düşünüp kaynaklı cevap veren arkadaş" deneyimidir. Mobil taraf bunu
yumuşatır: kaynaklar üretimden önce anında gösterilir, cevap token token akar, "cevap
hazırlanıyor" göstergesi bekleyişi taşır. Hız kritikse, kiralık küçük bir GPU (L4/A10) aynı
işi 2–4 saniyeye indirir — ama backend her iki durumda da aynı, sadece `LLM_BASE_URL` değişir.

---

## Docker + Coolify dağıtımı (önerilen)

Tek imaj hem API'yi hem ingest'i çalıştırır. İmaj **yalnızca koddur** (küçük); JSONL verisi
imaja gömülü değildir, ingest sırasında bir dizin olarak mount edilir. Bu sayede backend
kendi başına bir GitHub reposu olabilir (veri/crawler ayrı durur). Yanına TEI (bge-m3) ve
pgvector Postgres `docker-compose.yml` ile bağlanır. Şema, backend ilk açılışta otomatik uygulanır.

> **Not (mimari):** TEI'nin CPU imajı yalnızca **amd64**'tür; tipik x86_64 sunucuda
> (Coolify dahil) sorun yok, ancak Apple Silicon Mac'te yerelde çalışmaz. pgvector ve
> backend çok-mimarilidir.
>
> **Not (Postgres eklentileri):** Şema `vector` ve `unaccent` eklentilerini ister
> (Türkçe aramada "zekat" ↔ "Zekât" eşleşmesi için `unaccent` şart). Resmi/pgvector
> Postgres imajları ikisini de içerir. Yönetilen bir Postgres kullanıyorsanız bu
> eklentilerin açık olduğundan emin olun.

### 1) İmajı derleyip Docker Hub'a gönderin

Derleme bağlamı bu dizindir (`backend/`); veri gerekmez:

```bash
cd backend
docker login
# Sunucu amd64 ise:
docker buildx build --platform linux/amd64 \
  -t <kullanici>/siraj-backend:latest --push .
```

### 2) Coolify'da compose ile çalıştırın

`backend/docker-compose.yml`'i Coolify'a "Docker Compose" kaynağı olarak verin ve ortam
değişkenlerini girin:

| Değişken | Örnek |
|---|---|
| `SIRAJ_IMAGE` | `<kullanici>/siraj-backend:latest` |
| `POSTGRES_PASSWORD` | güçlü bir parola |
| `LLM_BASE_URL` | `http://<llama-cpp-host>:8080/v1` (kendi modeliniz) |
| `LLM_API_KEY` | anahtarınız (yoksa boş) |
| `LLM_MODEL` | `gemma-3-4b-it` (CPU önerisi) |
| `API_TOKEN` | mobil uygulamanın göndereceği Bearer (boş = kimlik doğrulama kapalı) |

Deploy edince: `backend` (:8000), `embeddings` (TEI, ilk açılışta ~2GB model indirir) ve
`db` (pgvector) ayağa kalkar. Backend şemayı otomatik uygular. Coolify'da 8000'i bir
domain'e bağlayın (reverse proxy SSE için buffering'i kapatmalı — aşağıdaki nginx notu).

### 3) Ingest'i sonra başlatma

Veri imajda olmadığı için önce JSONL'i sunucuya kopyalayın (bir kez):

```bash
# Kendi makinenizden (crawler çıktısı ~/Desktop/siraj-crawler/data içinde):
scp data/*.jsonl kullanici@sunucu:/opt/siraj/data/
```

Sonra compose'un olduğu yerde `DATA_DIR`'i o dizine ayarlayıp ingest'i tek seferlik iş
olarak çalıştırın (compose bu dizini `/app/data` olarak bağlar):

```bash
DATA_DIR=/opt/siraj/data docker compose --profile ingest run --rm ingest
# Uzun sürecekse ayrılabilir çalıştırın:
DATA_DIR=/opt/siraj/data docker compose --profile ingest up -d ingest
docker compose logs -f ingest
```

~235.000 chunk'ı bge-m3 ile embed'leyip pgvector'e yükler (CPU'da TEI ile bir gecelik iş;
tekrar çalıştırılabilir, ON CONFLICT ile günceller). Bitince getirimi kontrol edin:

```bash
docker compose exec backend python -m ingest.check_retrieval
```

Örnek soruların ilk pasajları konuyla ilgiliyse sohbet hazırdır.

---

## Manuel kurulum (Docker'sız, VPS'te)

Ingest için Postgres + embedding servisi + JSONL verisi aynı yerde olmalı. En basit
topoloji: her şeyi VPS'e koyun, `data/*.jsonl` dosyalarını oraya kopyalayın.

### 1) Embedding modeli: **BAAI/bge-m3** (kurulum ve erişim)

`bge-m3` seçildi çünkü Türkçe + Arapça karışık dini metinde en güçlü çok-dilli embedding'lerden
biri (1024 boyut, 8192 bağlam, önek gerektirmez).

**Önce kavram:** LiteLLM model *çalıştırmaz*, sadece *yönlendirir*. gemma'nız da aslında
LiteLLM'in arkasında bir sunucuda koşuyor. bge-m3 için de bir çalıştırıcı gerekir — en uygunu
**HF Text Embeddings Inference (TEI)**. TEI'yi çalıştırıp backend'e iki şekilde eriştirebilirsiniz.

**TEI'yi çalıştırın** (bge-m3, ~2.3GB RAM; ilk açılışta modeli indirir):
```bash
# CPU (sunucunuz CPU olduğu için; imaj amd64):
docker run -d --name tei -p 8080:80 -v $PWD/tei-data:/data \
  ghcr.io/huggingface/text-embeddings-inference:cpu-1.5 --model-id BAAI/bge-m3
# GPU olsaydı: --gpus all + imaj ':1.5'
```
> `docker-compose.yml` kullanıyorsanız `embeddings` servisi bunu zaten yapar; ayrıca çalıştırmayın.

**Erişim — iki yol:**

| | Nasıl | Ne zaman |
|---|---|---|
| **A. Doğrudan TEI** | `.env`: `EMBEDDING_MODE=tei`, `EMBEDDING_URL=http://embeddings:80` | En basit ve en hızlı. Backend TEI'ye compose ağı üzerinden özel erişir; dışarı açmanıza gerek yok. **Ingest için ideal** (LiteLLM hop'u yok). Compose varsayılanı budur. |
| **B. LiteLLM üzerinden** | TEI'yi LiteLLM'e kaydedin, `.env`: `EMBEDDING_MODE=openai`, `EMBEDDING_BASE_URL=<litellm>/v1`, `EMBEDDING_MODEL=bge-m3`, `EMBEDDING_API_KEY=<anahtar>` | bge-m3'ü gemma ile **aynı kapı ve anahtardan** erişmek, tek yerden loglama/limit istiyorsanız. |

**B için LiteLLM `config.yaml`'a ekleme** (TEI OpenAI-uyumlu `/v1/embeddings` sunar):
```yaml
model_list:
  - model_name: bge-m3
    litellm_params:
      model: openai/bge-m3
      api_base: http://TEI_HOST:8080/v1   # TEI'nin çalıştığı adres
      api_key: "none"
```
Kaydedip LiteLLM'i yeniden başlatın; artık `POST <litellm>/v1/embeddings` (model: `bge-m3`,
mevcut Bearer anahtarınız) çalışır.

> Öneri: Sorgu anındaki tek embedding için B (birleşik kapı) hoş; ama **ingest 235.000 çağrı
> yapar** — orada A (doğrudan TEI) hem daha hızlı hem LiteLLM loglarını şişirmez. İkisini
> karıştırabilirsiniz: ingest'i `EMBEDDING_MODE=tei` ile, backend'i `openai` ile çalıştırın.

> Alternatif model: `intfloat/multilingual-e5-large` (1024 boyut). Kullanırsanız `.env`'de
> `EMBEDDING_USE_E5_PREFIX=true` yapın — e5 `query:`/`passage:` önekleri ister, bge-m3 istemez.

### 2) Postgres + pgvector

Mevcut Postgres'inizi kullanabilirsiniz.

```bash
# pgvector eklentisi (Debian/Ubuntu, PG 16 örneği):
sudo apt install postgresql-16-pgvector
# Şema:
psql "$DATABASE_URL" -f schema.sql
```

### 3) Backend

```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env        # DATABASE_URL, EMBEDDING_URL, LLM_* değerlerini doldurun
```

### 4) Veriyi yükle (ingest)

```bash
# Önce küçük bir denemeyle boru hattını doğrulayın:
python -m ingest.ingest --data-dir ../data --source fetva --limit 50

# Tümü (~235.000 chunk; embedding hızına bağlı olarak GPU'da ~30-60 dk, CPU'da saatler):
python -m ingest.ingest --data-dir ../data
```

Bölümleme kaynağa göre yapılır: fetva bütün (Soru/Cevap), meal ayet ayet, tefsir/hadis/dia
~300 token'lık pencerelere bölünür (dia'nın şablon başlık/dipnotu ve sekmeleri temizlenir).

### 5) Getirim kalitesini kontrol edin (LLM'e geçmeden)

```bash
python -m ingest.check_retrieval
```

Örnek soruların ilk pasajları konuyla ilgiliyse devam edin. "Bugünün duası nedir?" bir
uygulama-durumu sorusudur; alakasız gelmesi/boş kalması normaldir — sohbet bunu nazikçe
reddeder.

### 6) Sunucuyu başlat

```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000
# systemd/pm2 ile kalıcılaştırın; TLS için nginx arkasına alın (SSE için buffering kapalı).
```

Sağlık kontrolü: `curl localhost:8000/health` → `{"status":"ok","chunks": N}`

---

## API

`POST /v1/chat` — `Authorization: Bearer <API_TOKEN>` (ayarlanmışsa)

```json
{ "messages": [ {"role":"user","content":"Namaz nasıl kılınır?"} ] }
```

Yanıt `text/event-stream`, her satır `data: {json}`:

| type | alanlar | anlamı |
|---|---|---|
| `sources` | `sources:[{n,source,label,title,url}]` | getirilen kaynaklar (üretimden önce, bir kez) |
| `delta` | `text` | üretilen metin parçası (çok kez) |
| `done` | — | tamamlandı |
| `error` | `message` | hata |

Mobil taraf `siraj/src/services/chatService.ts` içinde bu protokolü XHR ile akıtarak okur.

## Ayarlar (`.env`)

Yavaş donanımda gecikmeyi düşürmek için: `TOP_K` (4→3), `MAX_CHUNK_CHARS` (900→500),
`LLM_MAX_TOKENS` (512→256). Tümü `.env.example` içinde açıklanmıştır.

## Nginx notu (SSE)

```nginx
location /v1/chat {
    proxy_pass http://127.0.0.1:8000;
    proxy_buffering off;          # akışın anında geçmesi için şart
    proxy_read_timeout 600s;
}
```
