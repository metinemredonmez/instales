#!/usr/bin/env bash
# Update the server from GitHub: pull → backend deps → migrations → frontend build → nginx → pm2 reload → health check.
# Anything that fails (deps, migration, frontend build, nginx, or the API not answering /health) restores the previous
# commit: the new commit's migrations are downgraded first — while its migration files are still on disk — then the
# checkout goes back, rebuilds and reloads. The output always ends with what happened (== done / == rolled back / !!).
# Usage on the server:  bash /opt/instilens/infra/pm2/deploy.sh
set -euo pipefail
ROOT="${ROOT:-/opt/instilens}"
HEALTH_URL=http://127.0.0.1:8010/health
HEALTH_TRIES="${HEALTH_TRIES:-12}"; HEALTH_DELAY="${HEALTH_DELAY:-5}"   # up to 60 s — uvicorn workers + first DB connection
cd "$ROOT"

healthy() {  # poll /health until it answers 200 or the tries run out
  local i
  for i in $(seq 1 "$HEALTH_TRIES"); do
    if curl -fsS --max-time 5 "$HEALTH_URL" >/dev/null 2>&1; then return 0; fi
    sleep "$HEALTH_DELAY"
  done
  return 1
}

db_revision() {  # alembic revision the DB is at right now ("" when it has never been migrated)
  [ -x "$ROOT/backend/.venv/bin/alembic" ] || return 0
  # `alembic current` prints "<rev> (head)" — or nothing at all when alembic_version does not exist yet
  ( cd "$ROOT/backend" && .venv/bin/alembic current 2>/dev/null | tail -1 | awk '{print $1}' | grep -E '^[A-Za-z0-9_]+$' ) || true
}

echo "== git pull"
git fetch --quiet origin
PREV="$(git rev-parse HEAD)"              # remembered before the reset so a broken deploy can go back to it
PREV_REV="$(db_revision)"                 # …and so its migrations can be downgraded away
git reset --hard origin/main --quiet     # server never edits code; always match GitHub main
NEW="$(git rev-parse HEAD)"
git log --oneline -1
# Pre-flight: production refuses to boot with a short JWT secret, so fix it here instead of after the restart.
ENV="$ROOT/backend/.env"
SECRET="$(grep -E '^INSTILENS_JWT_SECRET=' "$ENV" 2>/dev/null | cut -d= -f2- || true)"
if [ "${#SECRET}" -lt 32 ]; then
  NEW_SECRET="$(openssl rand -hex 32)"
  if grep -q '^INSTILENS_JWT_SECRET=' "$ENV" 2>/dev/null; then sed -i "s|^INSTILENS_JWT_SECRET=.*|INSTILENS_JWT_SECRET=$NEW_SECRET|" "$ENV"; else echo "INSTILENS_JWT_SECRET=$NEW_SECRET" >> "$ENV"; fi
  echo "!! INSTILENS_JWT_SECRET was shorter than 32 chars — rotated (everyone must sign in again)"
fi

rollback() {  # $1 = why the deploy failed. Never aborts early: it must always reach a running API and a clear verdict.
  set +e
  echo "!! $1"
  pm2 logs instilens-api --nostream --lines 30
  if [ "$PREV" = "$NEW" ]; then
    echo "!! nothing to roll back to (HEAD was already ${PREV:0:8}) — fix the .env / logs above and re-run deploy.sh"
    pm2 list | grep instilens; exit 1
  fi
  echo "== ROLLBACK → ${PREV:0:8} ($(git log --oneline -1 "$PREV"))"
  # Downgrade BEFORE the checkout moves: only the new tree still has the revision files the DB now points at.
  NOW_REV="$(db_revision)"
  if [ -n "$PREV_REV" ] && [ -n "$NOW_REV" ] && [ "$NOW_REV" != "$PREV_REV" ]; then
    echo "== alembic downgrade ${NOW_REV:0:12} → ${PREV_REV:0:12}"
    if ! ( cd "$ROOT/backend" && .venv/bin/alembic downgrade "$PREV_REV" ); then
      echo "!! downgrade failed — the DB stays at ${NOW_REV:0:12} while the code goes back to ${PREV:0:8};"
      echo "   ${PREV:0:8} usually still runs on a newer schema, but fix it by hand if it does not."
    fi
  elif [ -z "$PREV_REV" ] && [ -n "$NOW_REV" ]; then
    echo "   (DB had no alembic_version before this deploy — leaving the schema at ${NOW_REV:0:12}, nothing is dropped automatically)"
  fi
  git reset --hard "$PREV" --quiet
  if ! bash "$ROOT/infra/pm2/server-setup.sh"; then
    echo "!! server-setup.sh also failed on ${PREV:0:8} — reloading pm2 with whatever is currently built"
    pm2 startOrReload "$ROOT/infra/pm2/ecosystem.config.cjs" --update-env
  fi
  if healthy; then
    echo "== rolled back: ${PREV:0:8} is up again; ${NEW:0:8} was NOT deployed."
    pm2 list | grep instilens; exit 1
  fi
  echo "!! still unhealthy after rolling back to ${PREV:0:8} — check pm2 logs instilens-api and backend/.env"
  pm2 list | grep instilens
  exit 1
}

# idempotent: deps, db init, frontend build, nginx, pm2 — a failure in any of them rolls back, it is not a bare abort
bash "$ROOT/infra/pm2/server-setup.sh" \
  || rollback "deploying ${NEW:0:8} failed inside server-setup.sh (deps / migration / frontend build / nginx — error above)"

echo "== health check ($HEALTH_URL)"
if healthy; then
  echo "== done: ${NEW:0:8} is up"; pm2 list | grep instilens || true; exit 0
fi
rollback "API did not answer $HEALTH_URL within $((HEALTH_TRIES * HEALTH_DELAY)) s after deploying ${NEW:0:8}"
