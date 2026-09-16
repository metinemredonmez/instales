#!/usr/bin/env bash
# Update the server from GitHub: pull → backend deps → migrations → frontend build → nginx → pm2 reload.
# Usage on the server:  bash /opt/instilens/infra/pm2/deploy.sh
set -euo pipefail
ROOT=/opt/instilens
cd "$ROOT"
echo "== git pull"
git fetch --quiet origin
git reset --hard origin/main --quiet     # server never edits code; always match GitHub main
git log --oneline -1
# Pre-flight: production refuses to boot with a short JWT secret, so fix it here instead of after the restart.
ENV="$ROOT/backend/.env"
SECRET="$(grep -E '^INSTILENS_JWT_SECRET=' "$ENV" 2>/dev/null | cut -d= -f2- || true)"
if [ "${#SECRET}" -lt 32 ]; then
  NEW="$(openssl rand -hex 32)"
  if grep -q '^INSTILENS_JWT_SECRET=' "$ENV" 2>/dev/null; then sed -i "s|^INSTILENS_JWT_SECRET=.*|INSTILENS_JWT_SECRET=$NEW|" "$ENV"; else echo "INSTILENS_JWT_SECRET=$NEW" >> "$ENV"; fi
  echo "!! INSTILENS_JWT_SECRET was shorter than 32 chars — rotated (everyone must sign in again)"
fi
bash "$ROOT/infra/pm2/server-setup.sh"   # idempotent: deps, db init, frontend build, nginx, pm2
echo "== done"; pm2 list | grep instilens
