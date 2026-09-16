#!/usr/bin/env bash
# Nightly backup, 14-day retention (local + off-site). Installed by server-setup.sh as a cron job (03:15).
#   Postgres dump        → /var/backups/instilens/instilens-YYYY-MM-DD.dump
#   desktop installers   → releases-YYYY-MM-DD.tar.gz   (backend/media/releases; media/tts is a regenerable cache → skipped)
#   backend/.env         → env-YYYY-MM-DD.enc           (openssl aes-256-cbc -pbkdf2, passphrase = INSTILENS_BACKUP_PASSPHRASE
#                                                        from .env; without it the .env is NOT backed up — never stored plain)
# The installers rarely change, so an unchanged release tree is hard-linked to the previous archive instead of being
# re-packed (and copied server-side off-site instead of re-uploaded): one stored copy, 14 dated names.
# Off-site copy when INSTILENS_BACKUP_REMOTE is set in .env: an rsync/scp target such as user@host:/backups, or an
# rclone remote such as r2:bucket/path when rclone is installed. Both sides keep 14 days, and both sides only ever
# prune our own three file patterns — never anything else that happens to live under the destination.
set -euo pipefail
ROOT="${ROOT:-/opt/instilens}"
ENV="$ROOT/backend/.env"
DIR="${INSTILENS_BACKUP_DIR:-/var/backups/instilens}"; mkdir -p "$DIR"; chmod 700 "$DIR"
STAMP=$(date +%F)
KEEP_DAYS=14
OURS=( -name 'instilens-*.dump' -o -name 'releases-*.tar.gz' -o -name 'env-*.enc' )   # the only files we ever delete
envval() { sed -n "s/^$1=//p" "$ENV" 2>/dev/null | head -1 | tr -d '"\r'; }
PASSPHRASE="$(envval INSTILENS_BACKUP_PASSPHRASE)"
REMOTE="$(envval INSTILENS_BACKUP_REMOTE)"
RC=0
REL="$DIR/releases-$STAMP.tar.gz"
REL_LINKED_FROM=""        # set when tonight's archive is a hard link to an older one (tree unchanged)

echo "== db"
sudo -u postgres pg_dump -Fc instilens > "$DIR/instilens-$STAMP.dump"
echo "   $DIR/instilens-$STAMP.dump ($(du -h "$DIR/instilens-$STAMP.dump" | cut -f1))"

echo "== releases (backend/media/releases)"
if [ -d "$ROOT/backend/media/releases" ]; then
  # fingerprint = every file's path, size and mtime; identical fingerprint ⇒ identical installers ⇒ reuse the archive
  FP="$(find "$ROOT/backend/media/releases" -type f -printf '%P %s %T@\n' 2>/dev/null | sort | sha256sum | cut -d' ' -f1)"
  PREV_REL="$(ls -1t "$DIR"/releases-*.tar.gz 2>/dev/null | head -1 || true)"
  if [ -n "$PREV_REL" ] && [ "$PREV_REL" != "$REL" ] && [ -n "$FP" ] && [ "$FP" = "$(cat "$PREV_REL.fp" 2>/dev/null || true)" ] \
     && ln -f "$PREV_REL" "$REL" 2>/dev/null; then   # same inode: no extra disk, and `find -mtime` still sees today's name
    REL_LINKED_FROM="$(basename "$PREV_REL")"
    echo "   unchanged since ${REL_LINKED_FROM} → hard link (no re-pack)"
  else
    tar -C "$ROOT/backend/media" -czf "$REL" releases
    echo "   $REL ($(du -h "$REL" | cut -f1))"
  fi
  printf '%s\n' "$FP" > "$REL.fp"
else
  echo "   no backend/media/releases yet — skipped"
fi

echo "== backend/.env (encrypted)"
if [ -n "$PASSPHRASE" ]; then
  # the passphrase reaches openssl through the environment (-pass env:), never the command line / ps
  INSTILENS_BACKUP_PASSPHRASE="$PASSPHRASE" openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:INSTILENS_BACKUP_PASSPHRASE \
    -in "$ENV" -out "$DIR/env-$STAMP.enc"
  chmod 600 "$DIR/env-$STAMP.enc"
  echo "   $DIR/env-$STAMP.enc"
else
  echo "   !! INSTILENS_BACKUP_PASSPHRASE not set in $ENV — .env NOT backed up (it is never stored unencrypted)"
  RC=1
fi

find "$DIR" -maxdepth 1 \( "${OURS[@]}" \) -mtime +$KEEP_DAYS -delete
find "$DIR" -maxdepth 1 -name 'releases-*.tar.gz.fp' -mtime +$KEEP_DAYS -delete

echo "== off-site"
is_rclone_remote() {  # "r2:bucket/path" → yes; "user@host:/backups" / "host:/backups" → no (ssh)
  case "$1" in
    *@*)  return 1 ;;   # user@host:… is always ssh
    *:/*) return 1 ;;   # host:/absolute/path is ssh
    ?*:*) return 0 ;;   # name:bucket/path → rclone, and then rclone must really be there
    *)    return 1 ;;
  esac
}
RCLONE_REMOTE=false; if is_rclone_remote "$REMOTE"; then RCLONE_REMOTE=true; fi
if [ -z "$REMOTE" ]; then
  echo "   INSTILENS_BACKUP_REMOTE not set — local copy only"
elif [ "$RCLONE_REMOTE" = true ]; then
  NAME="${REMOTE%%:*}"
  if ! command -v rclone >/dev/null; then
    echo "   !! INSTILENS_BACKUP_REMOTE='$REMOTE' looks like an rclone remote but rclone is not installed (apt-get install rclone && rclone config)"; RC=1
  elif ! rclone listremotes 2>/dev/null | grep -qx "$NAME:"; then
    echo "   !! rclone remote '$NAME:' is not configured (rclone config; rclone listremotes)"; RC=1
  else
    OK=0
    # tonight's files, minus the releases archive when it is only a rename of one already up there (server-side copy)
    INC=(--include "instilens-$STAMP.dump" --include "env-$STAMP.enc")
    if [ -n "$REL_LINKED_FROM" ] && rclone copyto "$REMOTE/$REL_LINKED_FROM" "$REMOTE/releases-$STAMP.tar.gz" 2>/dev/null; then
      echo "   releases-$STAMP.tar.gz copied server-side from $REL_LINKED_FROM"
    else
      INC+=(--include "releases-$STAMP.tar.gz")
    fi
    if rclone copy --max-depth 1 "${INC[@]}" "$DIR" "$REMOTE"; then OK=1; fi
    # prune ONLY our own three patterns, only at the top of the destination — never whatever else lives there
    if [ "$OK" = 1 ] && rclone delete --min-age "${KEEP_DAYS}d" --max-depth 1 \
         --include "instilens-*.dump" --include "releases-*.tar.gz" --include "env-*.enc" "$REMOTE"; then
      echo "   rclone → $REMOTE ok"
    else
      echo "   !! rclone → $REMOTE failed"; RC=1
    fi
  fi
else
  # ssh target (user@host:/backups): rsync when available, scp otherwise; retention is applied on the remote side
  HOST="${REMOTE%%:*}"; RPATH="${REMOTE#*:}"
  mapfile -t TODAY < <(find "$DIR" -maxdepth 1 -name "*-$STAMP.*" ! -name '*.fp' -type f)
  if [ -n "$REL_LINKED_FROM" ]; then
    # unchanged installers: hard-link them over there too instead of sending the whole archive again
    if ssh -o BatchMode=yes -o ConnectTimeout=15 "$HOST" \
         "mkdir -p '$RPATH' && cd '$RPATH' && [ -f '$REL_LINKED_FROM' ] && ln -f '$REL_LINKED_FROM' 'releases-$STAMP.tar.gz'" 2>/dev/null; then
      echo "   releases-$STAMP.tar.gz hard-linked on $HOST from $REL_LINKED_FROM"
      mapfile -t TODAY < <(find "$DIR" -maxdepth 1 -name "*-$STAMP.*" ! -name '*.fp' ! -name 'releases-*' -type f)
    fi
  fi
  if ssh -o BatchMode=yes -o ConnectTimeout=15 "$HOST" "mkdir -p '$RPATH'" \
     && { if command -v rsync >/dev/null; then rsync -az "${TODAY[@]}" "$HOST:$RPATH/"; else scp -q "${TODAY[@]}" "$HOST:$RPATH/"; fi; } \
     && ssh -o BatchMode=yes "$HOST" "find '$RPATH' -maxdepth 1 \( -name 'instilens-*.dump' -o -name 'releases-*.tar.gz' -o -name 'env-*.enc' \) -mtime +$KEEP_DAYS -delete"; then
    echo "   ${TODAY[*]##*/} → $REMOTE ok"
  else
    echo "   !! copy → $REMOTE failed (ssh key for $HOST installed? BatchMode, no password prompts)"; RC=1
  fi
fi

[ "$RC" = 0 ] && echo "backup ok: $STAMP" || echo "backup finished with warnings: $STAMP"
exit $RC
# restore:
#   db        sudo -u postgres pg_restore -d instilens --clean --if-exists /var/backups/instilens/instilens-YYYY-MM-DD.dump
#   releases  tar -C /opt/instilens/backend/media -xzf /var/backups/instilens/releases-YYYY-MM-DD.tar.gz
#   .env      openssl enc -d -aes-256-cbc -pbkdf2 -in /var/backups/instilens/env-YYYY-MM-DD.enc -out /opt/instilens/backend/.env  (asks for the passphrase)
