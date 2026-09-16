#!/usr/bin/env bash
# Put InstiLens on a domain with HTTPS (Let's Encrypt) and enable Web Push.
# Usage (server, as root, after DNS A record points to this box):  bash infra/pm2/domain-setup.sh app.example.com you@example.com
set -euo pipefail
DOMAIN="${1:?domain}"; EMAIL="${2:?email for Let's Encrypt}"
ROOT=/opt/instilens; ENV="$ROOT/backend/.env"
command -v certbot >/dev/null || apt-get install -y -qq certbot python3-certbot-nginx >/dev/null

cat > /etc/nginx/sites-available/instilens <<CONF
server {
    listen 80;
    server_name $DOMAIN;
    root $ROOT/frontend/dist;
    index index.html;
    location /api/ { proxy_pass http://127.0.0.1:8010/api/; proxy_http_version 1.1; proxy_set_header Host \$host; proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for; proxy_set_header X-Forwarded-Proto \$scheme; proxy_buffering off; proxy_read_timeout 3600s; }
    location /health { proxy_pass http://127.0.0.1:8010/health; }
    location /docs   { proxy_pass http://127.0.0.1:8010/docs; }
    location /openapi.json { proxy_pass http://127.0.0.1:8010/openapi.json; }
    location /sw.js { add_header Cache-Control "no-cache"; add_header Service-Worker-Allowed "/"; try_files \$uri =404; }
    location / { try_files \$uri \$uri/ /index.html; }
    add_header X-Content-Type-Options nosniff; add_header X-Frame-Options DENY;
    gzip on; gzip_types text/css application/javascript application/json image/svg+xml;
}
CONF
ln -sf /etc/nginx/sites-available/instilens /etc/nginx/sites-enabled/instilens
nginx -t && systemctl reload nginx
certbot --nginx -d "$DOMAIN" -m "$EMAIL" --agree-tos --non-interactive --redirect

# env: public URL, CORS, VAPID keys (generated once), production mode
sed -i "/^INSTILENS_PUBLIC_URL=/d; /^INSTILENS_CORS_ORIGINS=/d; /^INSTILENS_ENVIRONMENT=/d" "$ENV"
{
  echo "INSTILENS_PUBLIC_URL=https://$DOMAIN"
  echo "INSTILENS_CORS_ORIGINS=[\"https://$DOMAIN\",\"tauri://localhost\",\"http://tauri.localhost\"]"
  echo "INSTILENS_ENVIRONMENT=production"
} >> "$ENV"
if ! grep -q '^INSTILENS_VAPID_PUBLIC_KEY=' "$ENV"; then
  "$ROOT/backend/.venv/bin/instilens" vapid-keys | grep -v alembic >> "$ENV"
  sed -i "s|^INSTILENS_VAPID_SUBJECT=.*||" "$ENV"; echo "INSTILENS_VAPID_SUBJECT=mailto:$EMAIL" >> "$ENV"
fi
pm2 restart instilens-api instilens-scheduler --update-env >/dev/null
echo "✓ https://$DOMAIN  (push enabled; 8088 site can be removed later: rm /etc/nginx/sites-enabled/instilens-8088 if you kept one)"
