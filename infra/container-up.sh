#!/usr/bin/env bash
# Run the stack with Apple's `container` CLI (what Berthly drives). No compose needed.
# Containers on Apple container do not resolve each other by name, so services are wired by IP.
# Usage: infra/container-up.sh            (build + start)
#        infra/container-up.sh down       (stop + remove containers; the pgdata volume is kept)
#        infra/container-up.sh logs NAME  (api | scheduler | web | db)
set -euo pipefail
cd "$(dirname "$0")"
NET=instilens
NAMES=(instilens-web instilens-scheduler instilens-api instilens-db)

ip_of() { container inspect "$1" | python3 -c "import sys,json; d=json.load(sys.stdin); d=d[0] if isinstance(d,list) else d; print(d['status']['networks'][0]['ipv4Address'].split('/')[0])"; }

case "${1:-up}" in
  down) for c in "${NAMES[@]}"; do container stop "$c" >/dev/null 2>&1 || true; container rm "$c" >/dev/null 2>&1 || true; done; echo "stopped"; exit 0 ;;
  logs) container logs "instilens-${2:-api}"; exit 0 ;;
esac

[ -f .env ] || { echo "create infra/.env from .env.example first"; exit 1; }
POSTGRES_PASSWORD="$(grep -E '^POSTGRES_PASSWORD=' .env | cut -d= -f2- | tr -d '"' | tr -d "'")"
[ -n "$POSTGRES_PASSWORD" ] || { echo "POSTGRES_PASSWORD missing in infra/.env"; exit 1; }
grep -qE '^ANTHROPIC_API_KEY=sk-ant-[A-Za-z0-9_-]{20,}$' .env || echo "warning: ANTHROPIC_API_KEY looks like a placeholder — AI Research will not work until you set it"

"$0" down >/dev/null
container network create "$NET" >/dev/null 2>&1 || true
container build -t instilens-api:local -f ../backend/Dockerfile ../backend
container build -t instilens-web:local -f ../frontend/Dockerfile ../frontend

# Postgres: the volume root carries lost+found, so PGDATA must be a subdirectory.
container run -d --name instilens-db --network "$NET" \
  -e POSTGRES_DB=instilens -e POSTGRES_USER=instilens -e POSTGRES_PASSWORD="$POSTGRES_PASSWORD" \
  -e PGDATA=/var/lib/postgresql/data/pgdata \
  -v instilens-pgdata:/var/lib/postgresql/data postgres:16-alpine >/dev/null
DB_IP="$(ip_of instilens-db)"
echo "postgres at $DB_IP — waiting for it to accept connections"
for i in $(seq 1 30); do
  if container exec instilens-db pg_isready -U instilens >/dev/null 2>&1; then break; fi
  sleep 2
done
DB_URL="postgresql+psycopg://instilens:${POSTGRES_PASSWORD}@${DB_IP}:5432/instilens"

container run -d --name instilens-api --network "$NET" --env-file .env -e INSTILENS_DATABASE_URL="$DB_URL" -p 8000:8000 instilens-api:local >/dev/null
API_IP="$(ip_of instilens-api)"
container run -d --name instilens-scheduler --network "$NET" --env-file .env -e INSTILENS_DATABASE_URL="$DB_URL" instilens-api:local instilens scheduler >/dev/null
container run -d --name instilens-web --network "$NET" -e API_UPSTREAM="${API_IP}:8000" -p 8080:80 instilens-web:local >/dev/null
WEB_IP="$(ip_of instilens-web)"

echo
echo "web       → http://${WEB_IP}     (or http://localhost:8080 if port publishing works on your container version)"
echo "api docs  → http://${API_IP}:8000/docs"
echo "first-time admin: container exec -it instilens-api instilens users create you@mail.com --name You --role ADMIN --plan PRO_PLUS"
echo "first data pull : container exec instilens-api instilens run        (or wait for the scheduler)"
