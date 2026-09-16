#!/usr/bin/env bash
# Adds a Content-Security-Policy and friends to the app site (served by nginx, so the API middleware cannot
# set them for the SPA itself). Idempotent: writes a snippet and includes it in the app's HTTPS server block.
#   bash infra/pm2/harden-nginx.sh app.instilens.com
set -euo pipefail
DOMAIN="${1:?app domain, e.g. app.instilens.com}"
SITE="/etc/nginx/sites-available/$DOMAIN"
[ -f "$SITE" ] || { echo "no nginx site at $SITE"; exit 1; }
mkdir -p /etc/nginx/snippets
cat > /etc/nginx/snippets/instilens-app-headers.conf <<'CONF'
# InstiLens SPA hardening (ASVS 7.4 / 7.12). Script sources: self + OneSignal SDK. Styles: self + inline
# (Tailwind runtime + chart libraries set style attributes). Connections: self + OneSignal. No framing.
add_header Content-Security-Policy "default-src 'self'; base-uri 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'self'; script-src 'self' https://cdn.onesignal.com; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob: https://*.onesignal.com; font-src 'self' data:; media-src 'self' blob:; connect-src 'self' https://*.onesignal.com https://onesignal.com; worker-src 'self' https://cdn.onesignal.com; manifest-src 'self'; upgrade-insecure-requests" always;
add_header X-Content-Type-Options nosniff always;
add_header X-Frame-Options DENY always;
add_header Referrer-Policy strict-origin-when-cross-origin always;
add_header Permissions-Policy "camera=(), microphone=(), geolocation=(), payment=()" always;
add_header Strict-Transport-Security "max-age=63072000; includeSubDomains" always;
CONF
# include once, right after the 443 server's server_name line
if ! grep -q "instilens-app-headers.conf" "$SITE"; then
  awk -v inc="    include /etc/nginx/snippets/instilens-app-headers.conf;" '
    /listen 443/ {in443=1}
    {print}
    in443 && /server_name/ {print inc; in443=0}
  ' "$SITE" > "$SITE.tmp" && mv "$SITE.tmp" "$SITE"
fi
# tokens never appear in URLs any more, but keep query strings out of the access log anyway
if ! grep -q "log_format noquery" /etc/nginx/nginx.conf; then
  sed -i 's|http {|http {\n    log_format noquery '"'"'$remote_addr - $remote_user [$time_local] "$request_method $uri $server_protocol" $status $body_bytes_sent "$http_referer" "$http_user_agent"'"'"';|' /etc/nginx/nginx.conf
fi
grep -q "access_log .*noquery" "$SITE" || sed -i "0,/listen 443/s|listen 443|access_log /var/log/nginx/$DOMAIN.access.log noquery;\n    listen 443|" "$SITE"
nginx -t && systemctl reload nginx
echo "✓ headers + CSP active on https://$DOMAIN — open the app once and check the browser console for CSP reports"
