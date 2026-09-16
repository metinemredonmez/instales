#!/usr/bin/env bash
# Stop serving the app on http://IP:8088 once the domain works. Keeps only the HTTPS site.
set -euo pipefail
sed -i '/listen 8088;/d' /etc/nginx/sites-available/instilens
nginx -t && systemctl reload nginx
ufw status | grep -q active && ufw delete allow 8088/tcp >/dev/null 2>&1 || true
echo "8088 closed; app is https-only. Remove TCP 8088 from the Hetzner Cloud Firewall too."
