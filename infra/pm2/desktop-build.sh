#!/usr/bin/env bash
# INSTILENS — DESKTOP PACKAGES ON THE (LINUX) SERVER: AppImage + .deb, and Windows via cargo-xwin when the
# tools exist. Uploads into the same draft the Mac build used. macOS cannot be built here.
#   bash infra/pm2/desktop-build.sh            # version from the store (open draft or next)
#   bash infra/pm2/desktop-build.sh 0.2.3      # explicit
# One-time setup (as root):  bash infra/pm2/desktop-build.sh --setup
set -euo pipefail
ROOT="${ROOT:-/opt/instilens}"
API="${INSTILENS_RELEASE_API:-http://127.0.0.1:8010/api/v1/public/desktop}"
ENV="$ROOT/backend/.env"

if [ "${1:-}" = "--setup" ]; then
  apt-get update -qq
  apt-get install -y -qq libwebkit2gtk-4.1-dev libappindicator3-dev librsvg2-dev patchelf libssl-dev build-essential curl file nsis lld llvm clang >/dev/null
  command -v cargo >/dev/null || curl -fsSL https://sh.rustup.rs | sh -s -- -y >/dev/null
  # shellcheck disable=SC1090
  source "$HOME/.cargo/env"; rustup target add x86_64-pc-windows-msvc >/dev/null; cargo install cargo-xwin --locked >/dev/null
  command -v node >/dev/null || { curl -fsSL https://deb.nodesource.com/setup_22.x | bash - >/dev/null && apt-get install -y -qq nodejs >/dev/null; }
  grep -q '^INSTILENS_RELEASE_UPLOAD_KEY=' "$ENV" || echo "INSTILENS_RELEASE_UPLOAD_KEY=$(openssl rand -hex 32)" >> "$ENV"
  [ -f "$ROOT/.tauri/instilens.key" ] || echo "!! copy the updater signing key to $ROOT/.tauri/instilens.key (scp ~/.tauri/instilens.key ...)"
  pm2 restart instilens-api --update-env >/dev/null
  echo "✓ setup done"; exit 0
fi
# shellcheck disable=SC1090
[ -f "$HOME/.cargo/env" ] && source "$HOME/.cargo/env"
KEY="$(sed -n 's/^INSTILENS_RELEASE_UPLOAD_KEY=//p' "$ENV" | head -1 | tr -d '"\r')"
[ -n "$KEY" ] || { echo "❌ INSTILENS_RELEASE_UPLOAD_KEY missing in $ENV (run --setup)"; exit 1; }
[ -f "$ROOT/.tauri/instilens.key" ] || { echo "❌ $ROOT/.tauri/instilens.key missing (updater signing key)"; exit 1; }
export TAURI_SIGNING_PRIVATE_KEY="$(cat "$ROOT/.tauri/instilens.key")" TAURI_SIGNING_PRIVATE_KEY_PASSWORD=""

VERSION="${1:-}"
[ -n "$VERSION" ] || VERSION="$(curl -fsS -X POST "$API/ci/next" -H "x-release-key: $KEY" | sed -n 's/.*"version":"\([0-9.]*\)".*/\1/p')"
[ -n "$VERSION" ] || { echo "❌ no version from the API"; exit 1; }
echo "▶ version $VERSION"

cd "$ROOT/frontend"
restore() { git -C "$ROOT" checkout -- frontend/src-tauri/tauri.conf.json frontend/src-tauri/Cargo.toml frontend/src-tauri/Cargo.lock 2>/dev/null || true; }
trap restore EXIT
python3 - "$VERSION" <<'PY'
import json,pathlib,re,sys
v=sys.argv[1]
p=pathlib.Path("src-tauri/tauri.conf.json"); c=json.loads(p.read_text()); c["version"]=v; p.write_text(json.dumps(c,indent=2)+"\n")
p=pathlib.Path("src-tauri/Cargo.toml"); p.write_text(re.sub(r'^version = "[^"]+"', f'version = "{v}"', p.read_text(), count=1, flags=re.M))
PY
npm ci --no-audit --no-fund >/dev/null
export VITE_API_BASE=https://app.instilens.com NO_STRIP=true
LIN="—"; WIN="—"
echo "▶ Linux…"
if npx tauri build >/tmp/il-linux.log 2>&1; then LIN="✓"; else LIN="✗ (/tmp/il-linux.log)"; tail -5 /tmp/il-linux.log; fi
if command -v cargo-xwin >/dev/null && command -v makensis >/dev/null; then
  echo "▶ Windows (cargo-xwin)…"
  if npx tauri build --runner cargo-xwin --target x86_64-pc-windows-msvc >/tmp/il-win.log 2>&1; then WIN="✓"; else WIN="✗ (/tmp/il-win.log)"; tail -5 /tmp/il-win.log; fi
else WIN="✗ tools missing (run --setup)"; fi

upload() { local f="$1" sig=""; [ -n "${2:-}" ] && [ -f "$2" ] && sig="$(cat "$2")"
  if curl -fsS -X POST "$API/ci/upload" -H "x-release-key: $KEY" -F "version=$VERSION" -F "file=@$f" ${sig:+-F "signature=$sig"} >/dev/null; then echo "   ↑ $(basename "$f")"; else echo "   ✗ $(basename "$f")"; fi; }
shopt -s nullglob
B="src-tauri/target/release/bundle"
for f in "$B"/appimage/*.AppImage; do upload "$f" "$f.sig"; done
for f in "$B"/deb/*.deb "$B"/rpm/*.rpm; do upload "$f"; done
W="src-tauri/target/x86_64-pc-windows-msvc/release/bundle"
for f in "$W"/nsis/*-setup.exe; do upload "$f" "$f.sig"; done
for f in "$W"/msi/*.msi; do upload "$f"; done
echo; echo "  Linux: $LIN   Windows: $WIN"; echo "Admin → Sürüm yönetimi → Yayımla."
