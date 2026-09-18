# CLAUDE.md

## Code language: English

In this app **function names must be in English**, without exception. **Comments
and docstrings are in English** too. No exceptions.

The scope is the code itself: variable, function, class and module names, and every
explanation inside the code. These are out of scope:

- User-facing text (store notes, interface translations, `tr.json`).
- Project documents kept in Turkish, such as `DAILY.md` and `CHANGELOG.md`.
- Data from the corpus and religious terms themselves (names such as `sirâc`,
  `tefsir`, `meal` stay as they are when they stand for a concept).

## Ingest: always on the local machine, never on the server

When asked to "run an ingest", the **default and only method** is this: embeddings are
produced on the development Mac with `EMBEDDING_MODE=mps` (Apple Silicon GPU), and the
writes go to the VPS Postgres over an **SSH tunnel**.

Do not offer to run the ingest on the server. The VPS has no GPU; a CPU-only embed takes
hours. The `docker compose --profile ingest` flow in the README describes the server
side and **does not apply** to this project.

### Flow

1. **Connect to the server:** `ssh root@49.13.156.121` (Coolify setup). The same access
   also exists as the `ravey` alias in `~/.zshrc`, but the alias does not resolve in a
   non-interactive shell, so always write the command out in full.

2. **Point the tunnel at the DB container's IP, NOT the host's published port.**
   On a tunnel to `127.0.0.1:5432` (docker-proxy), connections randomly drop with
   "Connection reset by peer" and nothing shows up in the Postgres log. The container
   IP works fine:

   ```bash
   DBIP=$(ssh root@49.13.156.121 'docker inspect db-aoqjjh7jto93og1mpi2ncsvi \
     --format "{{range .NetworkSettings.Networks}}{{.IPAddress}}{{end}}"')
   ssh -f -N -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
     -L 5433:$DBIP:5432 root@49.13.156.121
   ```

3. **Point `.env` at the tunnel.** The DB password is `POSTGRES_PASSWORD` in the
   container environment. To write it without printing the password:

   ```bash
   ssh root@49.13.156.121 'docker exec db-aoqjjh7jto93og1mpi2ncsvi printenv POSTGRES_PASSWORD' \
     | awk '{print "DATABASE_URL=postgresql://siraj:"$1"@localhost:5433/siraj"}' > .env
   ```

4. **Run two checks before the ingest - they cannot be skipped:**
   - `SELECT count(*) FROM chunks` over the tunnel must return the expected size. If it
     returns `0`, the tunnel points to the wrong place and an empty corpus gets built
     on the Mac.
   - Are the local MPS vectors in the same space as the corpus: re-embed one chunk's
     `content` from the DB and compare it with the stored `embedding` by cosine
     similarity - it must be `1.000000`. Do not ask the remote embedding service; there
     is no key in `.env` (it returns 401).

5. **Run:**

   ```bash
   python -m ingest.ingest --data-dir ../data                 # everything
   python -m ingest.ingest --data-dir ../data --source fetva --limit 50   # trial run
   python -m ingest.check_retrieval                           # check afterwards
   ```

### Ingest does not ship code changes

Ingest only writes data. Labels, prompts and retrieval code live inside the image; if
they changed, a separate deploy is needed:

```bash
docker buildx build --platform linux/amd64 -t metehancelik/siraj-backend:latest --push .
```

then redeploy from Coolify. A redeploy can change the published port.
