# CLAUDE.md

## Ingest: her zaman yerel makinede, sunucuda değil

"Ingest yap" dendiğinde **varsayılan ve tek yöntem** şudur: embedding'ler
geliştirme Mac'inde `EMBEDDING_MODE=mps` ile (Apple Silicon GPU) üretilir, yazma
işlemi VPS Postgres'e bir **SSH tüneli** üzerinden yapılır.

Sunucuda ingest çalıştırma seçeneği sunma. VPS'te GPU yok; CPU-only embed
saatler sürüyor. README'deki `docker compose --profile ingest` akışı sunucu
tarafını anlatır ve bu proje için **geçerli değildir**.

### Akış

1. **Sunucuya bağlan:** `ssh root@49.13.156.121` (Coolify kurulumu). Aynı erişim
   `~/.zshrc`'de `ravey` alias'ı olarak da var, ama alias etkileşimsiz kabukta
   çözülmediği için komutu her zaman açık yaz.

2. **Tüneli DB container'ının IP'sine kur, host'un yayınlanan portuna DEĞİL.**
   `127.0.0.1:5432`'ye (docker-proxy) kurulan tünelde bağlantılar rastgele
   "Connection reset by peer" ile düşüyor; Postgres log'unda hata olmuyor.
   Container IP'si ile sorun yok:

   ```bash
   DBIP=$(ssh root@49.13.156.121 'docker inspect db-aoqjjh7jto93og1mpi2ncsvi \
     --format "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"')
   ssh -f -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
     -L 5433:$DBIP:5432 root@49.13.156.121
   ```

3. **`.env`'i tünele yönlendir.** DB parolası container ortamında
   `POSTGRES_PASSWORD`. Parolayı ekrana düşürmeden yazmak için:

   ```bash
   ssh root@49.13.156.121 'docker exec db-aoqjjh7jto93og1mpi2ncsvi printenv POSTGRES_PASSWORD' \
     | awk '{print "DATABASE_URL=postgresql://siraj:"$1"@localhost:5433/siraj"}' > .env
   ```

4. **Ingest'ten önce iki doğrulamayı yap — atlanamaz:**
   - Tünelden `SELECT count(*) FROM chunks` beklenen büyüklüğü dönmeli. `0`
     dönüyorsa tünel yanlış yere gidiyordur ve Mac'te boş bir korpüs kurulur.
   - Yerel MPS vektörleri korpüsle aynı uzayda mı: DB'den bir chunk'ın
     `content`'ini yeniden embed edip saklı `embedding` ile kosinüs benzerliğine
     bak — `1.000000` çıkmalı. Uzak embedding servisine sorma, `.env`'de anahtar
     yok (401 döner).

5. **Çalıştır:**

   ```bash
   python -m ingest.ingest --data-dir ../data                 # hepsi
   python -m ingest.ingest --data-dir ../data --source fetva --limit 50   # deneme
   python -m ingest.check_retrieval                           # sonrasında kontrol
   ```

### Ingest kod değişikliğini yayına almaz

Ingest yalnızca veriyi yazar. Etiketler, promptlar ve retrieval kodu imajın
içindedir; değiştiyseler ayrıca deploy gerekir:

```bash
docker buildx build --platform linux/amd64 -t metehancelik/siraj-backend:latest --push .
```

sonra Coolify'dan redeploy. Redeploy yayınlanan portu değiştirebiliyor.
