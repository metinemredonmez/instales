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
bash "$ROOT/infra/pm2/server-setup.sh"   # idempotent: deps, db init, frontend build, nginx, pm2
echo "== done"; pm2 list | grep instilens
