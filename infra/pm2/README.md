# Deploy on the Ubuntu box (pm2 + nginx, no Docker)

## 1. Ship the code (from your Mac)
```bash
rsync -az --delete \
  --exclude .venv --exclude node_modules --exclude dist --exclude '*.db' --exclude .env \
  --exclude src-tauri/target --exclude .pytest_cache --exclude .ruff_cache \
  ~/Desktop/instales/ root@SERVER_IP:/opt/instilens/
```
Repeat the same command for every update; then on the server: `bash /opt/instilens/infra/pm2/server-setup.sh`
(it is idempotent: re-syncs deps, rebuilds the frontend, migrates, reloads pm2 + nginx).

## 2. Server env (once)
```bash
cat > /opt/instilens/backend/.env <<'EOF2'
POSTGRES_PASSWORD=...                 # any strong value; setup script creates the role with it
INSTILENS_DATABASE_URL=postgresql+psycopg://instilens:SAME_PASSWORD@127.0.0.1:5432/instilens
INSTILENS_JWT_SECRET=...              # openssl rand -hex 32
INSTILENS_CORS_ORIGINS=["http://SERVER_IP:8088","tauri://localhost","http://tauri.localhost"]
INSTILENS_ALLOW_REGISTRATION=true
INSTILENS_KAP_ADAPTER=public
INSTILENS_SEC_ADAPTER=edgar
INSTILENS_SEC_USER_AGENT=InstiLens you@mail.com
ANTHROPIC_API_KEY=sk-ant-...
EOF2
chmod 600 /opt/instilens/backend/.env
```

## 3. Install + start
```bash
bash /opt/instilens/infra/pm2/server-setup.sh
```
Then open `http://SERVER_IP:8088` (open TCP 8088 in the Hetzner firewall).

## 4. When you buy a domain
Edit `/etc/nginx/sites-available/instilens`: `listen 80; server_name app.yourdomain.com;`
then `certbot --nginx -d app.yourdomain.com`, and add `https://app.yourdomain.com` to
`INSTILENS_CORS_ORIGINS` + `pm2 restart instilens-api --update-env`.

## Ops
- `pm2 logs instilens-scheduler` — ingest cadence and errors
- `pm2 restart instilens-api instilens-scheduler --update-env` after changing `.env`
- Ports used: 8010 (api, localhost only), 8088 (nginx). Change both files if they collide.
