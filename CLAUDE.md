# CLAUDE.md — InstiLens hatırlatma notu

Bu dosya projeye girince ilk okunan nottur. Kurallar `AGENTS.md`'de (9 yasa + çalışma kuralları);
burası *durum, alışkanlık ve bekleyen işler* içindir. Ana dizindeki Prime Lab `CLAUDE.md`/`AGENTS.md`
bu repoya uygulanmaz.

## Ürün tek cümlede
"Profesyonel para nereye gidiyor?" — KAP (Türkiye) + SEC 13F / Form 4 (Global) açıklamalarından
fon/kurum hareketleri, pozisyon motoru, sinyaller, deterministik skorlar, tool-grounded AI araştırma.
Dil **betimleyici**, asla tavsiye ("12 fon artırdı", asla "al").

## Yapı
| Parça | Yer | Nasıl çalışır |
|---|---|---|
| Backend (FastAPI, SQLAlchemy, Alembic) | `backend/` | `cd backend && uv sync && uv run pytest -q && uv run ruff check src tests` |
| Frontend (Vite + React + TS, shadcn) | `frontend/` | `cd frontend && npm run build` (tsc + Vite) · testler `npm test` |
| Tauri 2 masaüstü | `frontend/src-tauri` | `npm run desktop:dev` · sürüm: `scripts/desktop-release.sh` (Emre çalıştırır) |
| Landing (TR/EN, waitlist) | `landing/` | `scripts/build_landing.py` |
| Dağıtım | `infra/pm2/` (deploy.sh, harden-nginx.sh, backup.sh, ecosystem.config.cjs) · `infra/compose.yml` (Apple container) | aşağıdaki sunucu kuralına bak |
| Dokümanlar | `docs/01…10` | kararlar `06-decisions.md`, güvenlik `07-security.md`, API/webhook `10-api-and-webhooks.md` |

- Frontend testleri **Node 22** ister (`~/.nvm/versions/node/v22.18.0`); Emre'nin kabuğu Node 20 açar.
- Testler ağsız kalır; Claude motoru tool'lar üzerinden test edilir, canlı çağrı yok.
- Para `Decimal`, adet `int`; enum'lar `domain/enums.py`'den, DB'de string (`==` ile karşılaştır).
- Şema değişikliği yalnızca Alembic autogenerate ile; `render_as_batch` kalır (SQLite dev).
- KAP adaptörü **public site** (`INSTILENS_KAP_ADAPTER=public`), yavaş/throttle'lı: cap'leri küçük tut.
  Resmî VYK API sözleşmesi bilinçli olarak ertelendi (kurumsal kimlik gerekiyor); istenmedikçe açma.
- Anahtarlar `backend/.env`'de; sohbete asla yazma.

## Emre ile çalışma
- Türkçe, kısa, net fikir + tam uygulama. "Sen olsan ne yapardın" sorulunca önce görüş, sonra kod.
- **Sunucuya (Hetzner 91.99.183.64) asla ssh/rsync yapma.** Komutları "Mac'te" / "Sunucuda" blokları
  halinde ver, o çalıştırır.
- Bu repoda bazen ikinci bir Claude oturumu çalışır: `git add -A` öncesi `git status` bak, dokunmadığın
  dosyayı stage'leme. Commit/push yalnızca istenince.
- Bu klasöre yanlışlıkla başka projenin (ör. duvar kağıdı sipariş uygulaması) hatası gelebilir;
  InstiLens ile ilgisi yoksa hiçbir şey yapmadan sor.

## Üretim
- `https://app.instilens.com` (pm2 + nginx + certbot + Postgres, kayıt kapalı), landing `https://instilens.com`.
- Canlı katman SSE (`/events/stream`), WebSocket yok (karar). Web push OneSignal, external_id `instilens-<id>`.
- Fiyat: Yahoo `PriceProvider` arkasında; Matriks için yalnız config yuvası var (dokümanlar bekleniyor).
- Ödeme: Stripe adaptörü resmî SDK üstünde, anahtar yokken 503; plan zorlaması `plans_enforced` ile kapalı.

## Bekleyen işler (2026-11-03 itibarıyla)
1. **24 Eylül denetimi** (`docs/09-project-audit-2026-09-24.md`): 11 P1 + 12 P2 bulgu, denetimde kod
   düzeltilmedi; sonraki iki commit (Swagger/webhook, şifre kurtarma) bunları kapsamıyor. Önce P1'ler:
   hesap değişiminde önbellek sızıntısı, push endpoint SSRF, snapshot çift sayımı, karışık alış/satış
   net akışı, AI doğrulama, düzeltme akışı, bakış-önü verisi, admin ayar tutarlılığı, scheduler kilidi,
   yedek saklama, container kalıcılığı.
2. OWASP ASVS 5.0 güvenlik denetimi — Emre'nin PDF'leri `~/Desktop/siber/` (bellek: project-security-review-asvs).
3. Matriks adaptörü (ürün seçimi + API dokümanı bekliyor), Stripe canlı anahtarlar, masaüstü sürümü.
4. Dış girdiler: 12 aylık TR backfill, lisanslı fiyat akışı.

Son durum: `main` temiz ve `origin/main` ile eşit (dcf8dd6, 2026-11-03'te doğrulandı).
Bu listeyi iş bitince güncelle; tarihleri mutlak yaz.
