#!/usr/bin/env bash
# Nightly Postgres dump, 14-day retention. Installed by server-setup.sh as a cron job (03:15).
set -euo pipefail
DIR=/var/backups/instilens; mkdir -p "$DIR"
STAMP=$(date +%F)
sudo -u postgres pg_dump -Fc instilens > "$DIR/instilens-$STAMP.dump"
find "$DIR" -name 'instilens-*.dump' -mtime +14 -delete
echo "backup ok: $DIR/instilens-$STAMP.dump ($(du -h "$DIR/instilens-$STAMP.dump" | cut -f1))"
# restore: sudo -u postgres pg_restore -d instilens --clean --if-exists /var/backups/instilens/instilens-YYYY-MM-DD.dump
