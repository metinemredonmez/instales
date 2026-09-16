#!/usr/bin/env bash
# Public landing site (instilens.com + www) served by nginx from /opt/instilens/landing, with /api proxied
# to the backend for the waitlist form. Usage (server, as root, after the A records for @ and www point here):
#   bash infra/pm2/landing-setup.sh instilens.com you@example.com
set -euo pipefail
DOMAIN="${1:?domain, e.g. instilens.com}"
EMAIL="${2:?e-mail for Lets Encrypt}"
ROOT="${ROOT:-/opt/instilens}"
command -v certbot >/dev/null || apt-get install -y -qq certbot python3-certbot-nginx >/dev/null

cat > "/etc/nginx/sites-available/$DOMAIN" <<NGINX
server {
    listen 80;
    server_name www.$DOMAIN;
    return 301 https://$DOMAIN\$request_uri;
}
server {
    listen 80;
    server_name $DOMAIN;
    root $ROOT/landing;
    index index.html;
    add_header X-Content-Type-Options nosniff;
    add_header X-Frame-Options DENY;
    add_header Referrer-Policy strict-origin-when-cross-origin;
    location /api/v1/public/ { proxy_pass http://127.0.0.1:8010/api/v1/public/; proxy_http_version 1.1; proxy_set_header Host \$host; proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for; proxy_set_header X-Forwarded-Proto \$scheme; }
    location /api/ { return 404; }
    location /assets/ { expires 7d; add_header Cache-Control "public"; }
    location / { try_files \$uri \$uri/ =404; }
}
NGINX
ln -sf "/etc/nginx/sites-available/$DOMAIN" "/etc/nginx/sites-enabled/$DOMAIN"
nginx -t && systemctl reload nginx
certbot --nginx -d "$DOMAIN" -d "www.$DOMAIN" -m "$EMAIL" --agree-tos --non-interactive --redirect
echo "✓ https://$DOMAIN  (www → apex redirect, /api/v1/public proxied for the waitlist)"
