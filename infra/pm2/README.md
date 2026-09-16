# Deploy on the Ubuntu box (pm2 + nginx, no Docker)

## 1. Ship the code — via GitHub (preferred)
```bash
# server, once (private repo → add the server's public key as a Deploy Key on GitHub)
git clone git@github.com:metinemredonmez/instales.git /opt/instilens
```
Every update afterwards: `bash /opt/instilens/infra/pm2/deploy.sh` (pull + build + migrate + reload + health check).
Two things can go wrong, and both roll back:

- `server-setup.sh` itself fails (deps, a migration, the frontend build, `nginx -t`), or
- the API never answers `http://127.0.0.1:8010/health` within 60 s after the pm2 reload.

Either way deploy.sh prints the last API log lines and `== ROLLBACK → <sha>`, then **first** runs
`alembic downgrade <previous revision>` — while the new commit's migration files are still checked out, because the
old tree cannot resolve a revision it does not contain — and only then resets the checkout to the commit that was
running before, rebuilds, reloads and exits 1. The run always ends with one of `== done`, `== rolled back` or
`!! still unhealthy …`, so the log says what happened. If the downgrade itself fails (an irreversible migration) the
schema is left where it is and the message says so.

## 1b. Ship the code via rsync (alternative)
```bash
rsync -az --delete \
  --exclude .venv --exclude node_modules --exclude dist --exclude '*.db' --exclude .env \
  --exclude src-tauri/target --exclude .pytest_cache --exclude .ruff_cache \
  ~/Desktop/instales/ root@SERVER_IP:/opt/instilens/
```
Repeat the same command for every update; then on the server: `bash /opt/instilens/infra/pm2/server-setup.sh`
(it is idempotent: re-syncs deps, rebuilds the frontend, migrates, reloads pm2 + nginx).

## 2. Server env (once)
Every setting, with a one-line comment, is in `backend/.env.example` (it boots in development as copied;
production refuses to start until `INSTILENS_JWT_SECRET` is replaced). The production minimum:
```bash
cat > /opt/instilens/backend/.env <<'EOF2'
INSTILENS_ENVIRONMENT=production
POSTGRES_PASSWORD=...                 # any strong value; setup script creates the role with it
INSTILENS_DATABASE_URL=postgresql+psycopg://instilens:SAME_PASSWORD@127.0.0.1:5432/instilens
INSTILENS_JWT_SECRET=...              # openssl rand -hex 32 (≥32 chars or the API refuses to boot)
INSTILENS_PUBLIC_URL=https://app.instilens.com
INSTILENS_CORS_ORIGINS=["https://app.instilens.com","tauri://localhost","http://tauri.localhost"]
INSTILENS_ALLOW_REGISTRATION=true
INSTILENS_KAP_ADAPTER=public
INSTILENS_SEC_ADAPTER=edgar
INSTILENS_SEC_USER_AGENT=InstiLens you@mail.com
ANTHROPIC_API_KEY=sk-ant-...
# desktop releases (see below): INSTILENS_RELEASE_UPLOAD_KEY, INSTILENS_DESKTOP_UPDATER_PUBKEY
# backups (see below):          INSTILENS_BACKUP_PASSPHRASE, INSTILENS_BACKUP_REMOTE
EOF2
chmod 600 /opt/instilens/backend/.env
```
Before the domain exists use `http://SERVER_IP:8088` in `INSTILENS_PUBLIC_URL` / `INSTILENS_CORS_ORIGINS`; `domain-setup.sh` rewrites both.

## 3. Install + start
```bash
bash /opt/instilens/infra/pm2/server-setup.sh
```
Then open `http://SERVER_IP:8088` (open TCP 8088 in the Hetzner firewall). The script installs Node 22 from
NodeSource whenever the box has no `node` or an older major (Vite 7 and the frontend need ≥ 22); pm2 keeps running.

## 4. When you buy a domain (one command)
```bash
# DNS: A record  app.instilens.com → 91.99.183.64  (wait until `dig +short app.instilens.com` shows the IP)
bash /opt/instilens/infra/pm2/domain-setup.sh app.instilens.com you@mail.com
```
This writes the nginx site, gets a Let's Encrypt certificate, switches the app to https, enables Web Push (VAPID keys) and production mode.

## 4b. Manual notes
Edit `/etc/nginx/sites-available/instilens`: `listen 80; server_name app.instilens.com;`
then `certbot --nginx -d app.instilens.com`, and add `https://app.instilens.com` to
`INSTILENS_CORS_ORIGINS` + `pm2 restart instilens-api --update-env`.

## Ops
- `pm2 logs instilens-scheduler` — ingest cadence and errors
- `pm2 restart instilens-api instilens-scheduler --update-env` after changing `.env`
- Ports used: 8010 (api, localhost only), 8088 (nginx). Change both files if they collide.

## Notifications (optional)
Telegram: talk to @BotFather → `/newbot` → token → `.env`: `INSTILENS_TELEGRAM_BOT_TOKEN=...`; each user pastes their chat id in Alarmlar → Bildirim kanalları.
E-mail: `INSTILENS_SMTP_HOST`, `INSTILENS_SMTP_PORT=587`, `INSTILENS_SMTP_USER`, `INSTILENS_SMTP_PASSWORD`, `INSTILENS_SMTP_FROM=alerts@instilens.com`.
Links in messages use `INSTILENS_PUBLIC_URL` (`https://app.instilens.com`; `http://91.99.183.64:8088` before the domain).

## Backups
`infra/pm2/backup.sh` runs nightly at 03:15 (cron installed by `server-setup.sh`, log: `/var/log/instilens-backup.log`)
→ `/var/backups/instilens/`, 14-day retention. Each night writes three files:

| file | contents |
|---|---|
| `instilens-YYYY-MM-DD.dump` | Postgres (`pg_dump -Fc`) |
| `releases-YYYY-MM-DD.tar.gz` | `backend/media/releases` — the desktop installers (`media/tts` is a regenerable cache and is skipped) |
| `env-YYYY-MM-DD.enc` | `backend/.env`, encrypted with `openssl enc -aes-256-cbc -pbkdf2` and the passphrase in `INSTILENS_BACKUP_PASSPHRASE`. Without a passphrase `.env` is **not** backed up (never stored plain) and the run exits 1. |

The installers rarely change: when `backend/media/releases` is byte-for-byte what it was last night, the archive is
hard-linked instead of re-packed (one copy on disk, 14 dated names) and copied server-side off-site instead of
re-uploaded.

Off-site: set `INSTILENS_BACKUP_REMOTE` in `.env` to an SSH target (`user@host:/backups` or `host:/backups` — rsync when
installed, scp otherwise; the server's root key must be authorised there, no password prompts) or an rclone remote
(`r2:bucket/instilens` — anything without `@` and without an absolute path after the `:`; it needs `rclone` installed and
that remote in `rclone config`, and the script says so instead of silently trying ssh). Only that night's files are
copied, and both sides prune **only** `instilens-*.dump`, `releases-*.tar.gz` and `env-*.enc` older than 14 days at the
top of the destination — anything else living there is never touched (but give the backups their own prefix anyway).

```bash
# .env additions
INSTILENS_BACKUP_PASSPHRASE=$(openssl rand -hex 32)     # keep a copy in your password manager — the .enc is useless without it
INSTILENS_BACKUP_REMOTE=backup@backup-host:/backups/instilens
bash /opt/instilens/infra/pm2/backup.sh                 # run once by hand and read the output
```
Restore:
```bash
sudo -u postgres pg_restore -d instilens --clean --if-exists /var/backups/instilens/instilens-YYYY-MM-DD.dump
tar -C /opt/instilens/backend/media -xzf /var/backups/instilens/releases-YYYY-MM-DD.tar.gz
openssl enc -d -aes-256-cbc -pbkdf2 -in /var/backups/instilens/env-YYYY-MM-DD.enc -out /opt/instilens/backend/.env   # asks for the passphrase
```

## After the domain works
`bash infra/pm2/close-ip-port.sh` removes the plain-HTTP :8088 listener. Set `INSTILENS_ALLOW_REGISTRATION=false` in `.env` to make the app invite-only.

## Landing site (instilens.com)

Static pages in `landing/` (TR at `/`, EN at `/en/`), generated by `python3 scripts/build_landing.py`.
DNS at the registrar: `A @ → 91.99.183.64`, `A www → 91.99.183.64`. Then on the server:

```bash
bash infra/pm2/landing-setup.sh instilens.com you@example.com
```

The waitlist form posts to `/api/v1/public/waitlist` (proxied to the backend, rate-limited); signups show in
Admin → Users. `robots.txt` + `sitemap.xml` live in `landing/`; the app itself is `noindex`.

## Security hardening (after every deploy that touches nginx)

```bash
bash infra/pm2/harden-nginx.sh app.instilens.com   # CSP/HSTS/Permissions-Policy on the SPA, query-less access log
```
Findings and status: `docs/07-security.md`. Admin → Veri & pipeline shows the security event log.

## Desktop (macOS)

```bash
cd frontend && npm run desktop:build     # → src-tauri/target/release/bundle/dmg/InstiLens_<version>_aarch64.dmg
```
The desktop bundle talks to https://app.instilens.com (VITE_API_BASE). Unsigned: first launch needs
right-click → Open (or `xattr -d com.apple.quarantine`). Sign + notarize with an Apple Developer ID before wider distribution.

### Windows / Linux / Intel Mac installers

macOS cannot be built anywhere but a Mac (and Linux needs its own toolchain), so `.github/workflows/desktop.yml` builds on four GitHub runners (Node 22 everywhere)
and ships the installers (dmg ×2 + signed `.app.tar.gz` updater bundles, msi + signed setup.exe, AppImage + deb + rpm):

- **with the `INSTILENS_RELEASE_UPLOAD_KEY` secret** → straight into our release store (same `ci/next` + `ci/upload`
  protocol as `scripts/desktop-release.sh`); a `version` job asks the store for the open draft so all four builds
  land in the same draft. Then Admin → Sürüm yönetimi → **Yayımla**.
- **without it** → attached to a GitHub Release (tag name, or `desktop-latest` pre-release for manual runs).

Either way the files are also kept as workflow artifacts for 14 days. Secrets (once, from the Mac):

```bash
gh secret set TAURI_SIGNING_PRIVATE_KEY < ~/.tauri/instilens.key          # updater signing key — the build fails without it
gh secret set TAURI_SIGNING_PRIVATE_KEY_PASSWORD --body ""                 # "" when the key has no password
ssh instilens "sed -n 's/^INSTILENS_RELEASE_UPLOAD_KEY=//p' /opt/instilens/backend/.env" | gh secret set INSTILENS_RELEASE_UPLOAD_KEY
```
Start it by tagging, or by hand with an explicit version:

```bash
git tag v0.2.0 && git push origin v0.2.0            # version = tag without the v
gh workflow run desktop -f version=0.2.3            # or Actions → desktop → Run workflow → version (empty = store's next)
```
Private repos: macOS runner minutes count 10×, Windows 2× against the free monthly quota — a full matrix run costs
roughly 150–200 minutes of quota.

## Desktop release management (Sürüm yönetimi)

The store is our own server (Admin → Sürüm yönetimi): `releases` + `release_files`, files under
`backend/media/releases/<version>/`. The app checks `/api/v1/public/desktop/update/{target}/{arch}/{version}`
at launch and installs signed updates.

One-time:
```bash
# server (root): toolchain for Linux (+ Windows via cargo-xwin), upload key, restart API
bash infra/pm2/desktop-build.sh --setup
# Mac → server: the updater signing key (private half; never in git)
scp ~/.tauri/instilens.key root@91.99.183.64:/opt/instilens/.tauri/instilens.key
```
Every release — two commands, same version (or let CI do all four platforms, see above):
```bash
bash scripts/desktop-release.sh              # Mac: macOS (+Windows) → uploads, prints the version
ssh instilens 'bash /opt/instilens/infra/pm2/desktop-build.sh <version>'   # Linux (+Windows) → same draft
```
Neither script — nor the GitHub workflow — exposes a secret in a command line: the upload key goes to curl through a
0600 header file (`-H @file`; stdin, `-H @-`, on the far side of `desktop-release.sh`'s SSH fallback) and signatures are
read straight from their `.sig` files (`-F "signature=<file"`), so nothing shows up in `ps` on either box.
Then Admin → Sürüm yönetimi → check the four columns → **Yayımla**. Users see "Güncelle ve yeniden başlat".
