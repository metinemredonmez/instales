#!/usr/bin/env bash
# INSTILENS — DESKTOP RELEASE FROM A MAC. Builds macOS (this arch; Intel too if the target is installed),
# tries Windows via cargo-xwin, signs the updater bundles, uploads everything to our own release store.
#
#   bash scripts/desktop-release.sh                    # version number comes from the server
#   bash scripts/desktop-release.sh 0.2.3 "YENİ — …"   # explicit version (+ notes)
#
# Needs: ~/.tauri/instilens.key (updater signing key; NEVER in git), and the upload key —
# either INSTILENS_RELEASE_UPLOAD_KEY in the environment or readable over SSH from the server
# (INSTILENS_RELEASE_SSH=host, default: instilens). Nothing secret is printed, and neither the key nor a
# signature ever appears in a command line (local or remote `ps`): curl reads the key from a 0600 header
# file, signatures from their .sig files, and the SSH fallback receives the key on stdin.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API="${INSTILENS_RELEASE_API:-https://app.instilens.com/api/v1/public/desktop}"
SSH_HOST="${INSTILENS_RELEASE_SSH:-root@91.99.183.64}"
VERSION="${1:-}"; NOTES="${2:-}"

KEY="${INSTILENS_RELEASE_UPLOAD_KEY:-}"
if [ -z "$KEY" ] && ssh -o ConnectTimeout=8 -o BatchMode=yes "$SSH_HOST" true 2>/dev/null; then
  KEY="$(ssh "$SSH_HOST" "sed -n 's/^INSTILENS_RELEASE_UPLOAD_KEY=//p' /opt/instilens/backend/.env | head -1 | tr -d '\"\\r'")"
fi
[ -n "$KEY" ] || { echo "❌ upload key: set INSTILENS_RELEASE_UPLOAD_KEY or make 'ssh $SSH_HOST' work"; exit 1; }
[ -f "$HOME/.tauri/instilens.key" ] || { echo "❌ ~/.tauri/instilens.key missing (updater signing key)"; exit 1; }
export TAURI_SIGNING_PRIVATE_KEY="$(cat "$HOME/.tauri/instilens.key")" TAURI_SIGNING_PRIVATE_KEY_PASSWORD=""
# curl -H @file: the key stays out of argv. Removed together with the version stamp on exit (see restore()).
HDR="$(mktemp "${TMPDIR:-/tmp}/il-hdr.XXXXXX")"; chmod 600 "$HDR"; printf 'x-release-key: %s\n' "$KEY" > "$HDR"

if [ -z "$VERSION" ]; then
  VERSION="$(curl -fsS -X POST "$API/ci/next" -H "@$HDR" | sed -n 's/.*"version":"\([0-9.]*\)".*/\1/p')"
fi
[ -n "$VERSION" ] || { echo "❌ server gave no version"; exit 1; }
echo "▶ version $VERSION"

cd "$ROOT/frontend"
# version is written into tauri.conf.json + Cargo.toml for the build, then restored (tracked files).
restore() { rm -f "$HDR"; git -C "$ROOT" checkout -- frontend/src-tauri/tauri.conf.json frontend/src-tauri/Cargo.toml frontend/src-tauri/Cargo.lock 2>/dev/null || true; }
trap restore EXIT
python3 - "$VERSION" <<'PY'
import json,pathlib,re,sys
v=sys.argv[1]
p=pathlib.Path("src-tauri/tauri.conf.json"); c=json.loads(p.read_text()); c["version"]=v; p.write_text(json.dumps(c,indent=2)+"\n")
p=pathlib.Path("src-tauri/Cargo.toml"); p.write_text(re.sub(r'^version = "[^"]+"', f'version = "{v}"', p.read_text(), count=1, flags=re.M))
PY

export VITE_API_BASE="${VITE_API_BASE:-https://app.instilens.com}"
MAC="—"; MACX="—"; WIN="—"
echo "▶ macOS (aarch64)…"
if npx tauri build --target aarch64-apple-darwin >/tmp/il-mac.log 2>&1; then MAC="✓"; else MAC="✗ (/tmp/il-mac.log)"; tail -5 /tmp/il-mac.log; fi
if rustup target list --installed | grep -q x86_64-apple-darwin; then
  echo "▶ macOS (Intel)…"
  if npx tauri build --target x86_64-apple-darwin >/tmp/il-macx.log 2>&1; then MACX="✓"; else MACX="✗ (/tmp/il-macx.log)"; fi
fi
if command -v cargo-xwin >/dev/null; then
  # Homebrew's makensis crashes on Apple silicon; the shim in scripts/bin compiles the installer in a Linux container.
  if command -v container >/dev/null && container images list 2>/dev/null | grep -q "^nsis-linux"; then export PATH="$ROOT/scripts/bin:$PATH"; fi
  echo "▶ Windows (cargo-xwin)…"
  if npx tauri build --runner cargo-xwin --target x86_64-pc-windows-msvc >/tmp/il-win.log 2>&1; then WIN="✓"; else WIN="✗ (/tmp/il-win.log)"; tail -5 /tmp/il-win.log; fi
else
  WIN="✗ tools missing → cargo install cargo-xwin && rustup target add x86_64-pc-windows-msvc && container build -t nsis-linux scripts/nsis-image"
fi

# macOS dmg: Tauri's bundle_dmg.sh needs Finder automation; fall back to hdiutil.
for T in aarch64-apple-darwin x86_64-apple-darwin; do
  B="src-tauri/target/$T/release/bundle"
  [ -d "$B/macos/InstiLens.app" ] || continue
  ARCH="${T%%-*}"
  if ! ls "$B"/dmg/*.dmg >/dev/null 2>&1; then
    rm -rf /tmp/il-dmg && mkdir -p /tmp/il-dmg "$B/dmg" && cp -R "$B/macos/InstiLens.app" /tmp/il-dmg/ && ln -s /Applications /tmp/il-dmg/Applications
    hdiutil create -volname "InstiLens" -srcfolder /tmp/il-dmg -ov -format UDZO "$B/dmg/InstiLens_${VERSION}_${ARCH}.dmg" >/dev/null
  fi
  # updater bundle name must carry the arch so the store can classify it
  if [ -f "$B/macos/InstiLens.app.tar.gz" ]; then cp "$B/macos/InstiLens.app.tar.gz" "$B/macos/InstiLens_${VERSION}_${ARCH}.app.tar.gz"; cp "$B/macos/InstiLens.app.tar.gz.sig" "$B/macos/InstiLens_${VERSION}_${ARCH}.app.tar.gz.sig"; fi
done

echo "▶ upload…"
SSH_OK=0; if ssh -o ConnectTimeout=8 -o BatchMode=yes "$SSH_HOST" true 2>/dev/null; then SSH_OK=1; fi
# Remote side of the SSH fallback: the key header arrives on stdin (curl -H @-), the signature is a temp file
# next to the artifact, so nothing secret is in any command line over there.
# $1 = file name in /tmp, $2 = version, $3 = notes (may be empty).
REMOTE_UPLOAD='f="/tmp/$1"; s="$f.sig"; extra=(); [ -f "$s" ] && extra=(-F "signature=<$s"); [ -n "${3:-}" ] && extra+=(-F "notes=$3")
  curl -fsS -X POST http://127.0.0.1:8010/api/v1/public/desktop/ci/upload -H @- -F "version=$2" -F "file=@$f" "${extra[@]}" >/dev/null
  rc=$?; rm -f "$f" "$s"; exit $rc'
# base64 so the remote LOGIN shell only ever sees one plain token (printf %q would emit bash-only $'…' quoting),
# and single-quoting that works in sh as well as bash for the arguments.
REMOTE_UPLOAD_B64="$(printf '%s' "$REMOTE_UPLOAD" | base64 | tr -d '\n')"
shq() { printf "'%s'" "$(printf '%s' "${1:-}" | sed "s/'/'\\\\''/g")"; }
upload() {  # file [sigfile] — HTTPS first; if nginx refuses (413 etc.) and SSH works, copy to the server and post locally there
  local f="$1" sig="" name; name="$(basename "$f")"; if [ -n "${2:-}" ] && [ -f "$2" ]; then sig="$2"; fi
  # -F "signature=<file": curl reads the field value from the .sig file (never in argv)
  if curl -fsS -X POST "$API/ci/upload" -H "@$HDR" -F "version=$VERSION" -F "file=@$f" ${sig:+-F "signature=<$sig"} ${NOTES:+-F "notes=$NOTES"} >/dev/null 2>&1; then
    echo "   ↑ $name"; return
  fi
  if [ "$SSH_OK" = "1" ]; then
    if scp -q "$f" "$SSH_HOST:/tmp/$name" && { [ -z "$sig" ] || scp -q "$sig" "$SSH_HOST:/tmp/$name.sig"; } \
       && ssh "$SSH_HOST" "bash -c \"\$(printf %s $REMOTE_UPLOAD_B64 | base64 -d)\" _ $(shq "$name") $(shq "$VERSION") $(shq "$NOTES")" < "$HDR"; then
      echo "   ↑ $name (ssh)"; return
    fi
  fi
  echo "   ✗ $name"
}
shopt -s nullglob
for T in aarch64-apple-darwin x86_64-apple-darwin; do
  B="src-tauri/target/$T/release/bundle"; ARCH="${T%%-*}"
  for f in "$B"/dmg/*.dmg; do upload "$f"; done
  f="$B/macos/InstiLens_${VERSION}_${ARCH}.app.tar.gz"; if [ -f "$f" ]; then upload "$f" "$f.sig"; fi
done
W="src-tauri/target/x86_64-pc-windows-msvc/release/bundle"
for f in "$W"/nsis/*-setup.exe; do upload "$f" "$f.sig"; done
for f in "$W"/msi/*.msi; do upload "$f"; done

echo
echo "  macOS Apple Silicon : $MAC"
echo "  macOS Intel         : $MACX"
echo "  Windows             : $WIN"
echo "  Linux               : sunucuda → bash infra/pm2/desktop-build.sh $VERSION"
echo "Sonra Admin → Sürüm yönetimi → Yayımla."
