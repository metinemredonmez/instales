#!/usr/bin/env bash
# One-shot (idempotent) install on Ubuntu 22.04/24.04 with pm2 + nginx already present.
# Run as root on the server AFTER the repo is at /opt/instilens (see infra/pm2/README.md).
set -euo pipefail
ROOT=/opt/instilens
cd "$ROOT"

echo "== system packages"
apt-get update -qq
apt-get install -y -qq postgresql nginx curl git >/dev/null
command -v uv >/dev/null || (curl -LsSf https://astral.sh/uv/install.sh | sh && ln -sf "$HOME/.local/bin/uv" /usr/local/bin/uv)
command -v node >/dev/null || { curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && apt-get install -y nodejs; }
command -v pm2 >/dev/null || npm i -g pm2

echo "== postgres role + db"
DB_PASS="$(grep -E '^POSTGRES_PASSWORD=' backend/.env | cut -d= -f2- | tr -d '"')"
[ -n "$DB_PASS" ] || { echo "POSTGRES_PASSWORD missing in backend/.env"; exit 1; }
sudo -u postgres psql -tc "SELECT 1 FROM pg_roles WHERE rolname='instilens'" | grep -q 1 || sudo -u postgres psql -c "CREATE ROLE instilens LOGIN PASSWORD '$DB_PASS';"
sudo -u postgres psql -tc "SELECT 1 FROM pg_database WHERE datname='instilens'" | grep -q 1 || sudo -u postgres psql -c "CREATE DATABASE instilens OWNER instilens;"

echo "== backend"
cd "$ROOT/backend"
uv sync --frozen --no-dev --extra postgres
.venv/bin/instilens db init     # alembic upgrade head

echo "== frontend build"
cd "$ROOT/frontend"
npm ci --silent
npm run build --silent

echo "== nginx site"
cp "$ROOT/infra/pm2/nginx-instilens.conf" /etc/nginx/sites-available/instilens
ln -sf /etc/nginx/sites-available/instilens /etc/nginx/sites-enabled/instilens
nginx -t && systemctl reload nginx

echo "== backup cron"
chmod +x "$ROOT/infra/pm2/backup.sh"
( crontab -l 2>/dev/null | grep -v 'instilens/infra/pm2/backup.sh'; echo "15 3 * * * /bin/bash $ROOT/infra/pm2/backup.sh >> /var/log/instilens-backup.log 2>&1" ) | crontab -

echo "== pm2"
pm2 startOrReload "$ROOT/infra/pm2/ecosystem.config.cjs" --update-env
pm2 save >/dev/null

IP=$(hostname -I | awk '{print $1}')
echo
echo "web  → http://$IP:8088      api docs → http://$IP:8088/docs"
echo "admin: cd $ROOT/backend && .venv/bin/instilens users create you@mail.com --name You --role ADMIN --plan PRO_PLUS"
echo "first data: cd $ROOT/backend && .venv/bin/instilens run   (then the scheduler keeps it fresh)"
echo "firewall: open TCP 8088 (Hetzner Cloud Firewall / ufw allow 8088)"
