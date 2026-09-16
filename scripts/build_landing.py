"""Render landing/index.html (TR) and landing/en/index.html (EN) from one template so both stay in sync.
Run: python3 scripts/build_landing.py"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "landing"
SITE = "https://instilens.com"
APP = "https://app.instilens.com"

TEXT = {
    "tr": {
        "title": "InstiLens — Profesyonel para nereye gidiyor?",
        "desc": "Fonların ve kurumsal yatırımcıların hangi hisseleri topladığını, azalttığını, yeni pozisyon açtığını ve terk ettiğini tek ekrandan izle. KAP ve SEC 13F verisi, açıklanabilir skorlar, AI sabah brifingi. Yatırım tavsiyesi değildir.",
        "nav_product": "Ürün", "nav_how": "Nasıl çalışır", "nav_trust": "Güven", "nav_login": "Giriş yap",
        "h1": "Profesyonel para <em>nereye</em> gidiyor?",
        "lead": "Fonların hangi hisseleri topladığını, azalttığını, yeni pozisyon açtığını ve terk ettiğini tek ekrandan takip et. Her rakam KAP veya SEC kaynağına kadar izlenebilir.",
        "cta_app": "Uygulamaya git", "cta_wait": "Erken erişim iste", "cta_desktop": "Masaüstü uygulaması",
        "wait_ph": "e-posta adresin", "wait_btn": "Listeye katıl", "wait_hint": "Davetle açılıyor. Spam yok, sadece yerin açılınca yazarız.",
        "shot_cap": "Radar — BIST, gerçek KAP verisi. Skorlar deterministik ve açıklanabilir; \"Neden 87?\" sorusunun cevabı her skorun altında.",
        "f_h2": "Ne görürsün", "f_sub": "Fiyat grafiği değil, para akışı. Kimin, ne zaman, ne kadar aldığı.",
        "features": [
            ("KAP", "Canlı KAP Radar", "Fon pay alım-satım bildirimleri geldiği an normalize edilir: hisse, kurum, fon, lot, güven seviyesi."),
            ("PDF", "Portföy raporları", "Haftalık/aylık fon portföy raporları parse edilir; iki rapor arasındaki fark NEW / ADD / REDUCE / EXIT olur."),
            ("87", "Smart Money & Konsensüs skoru", "Genişlik, net akış, süreklilik, yeni pozisyon, conviction. Ağırlıklar açık; skor tıklanınca neden o olduğunu görürsün."),
            ("↗", "Sinyaller", "Birikim, dağıtım, pozitif/negatif ayrışma (fiyat düşerken toplayan fonlar), yeni pozisyon kümeleri."),
            ("13F", "Global: SEC 13F", "Berkshire, Bridgewater, Renaissance gibi kurumların çeyreklik pozisyon değişimleri aynı motorla."),
            ("AI", "Sabah brifingi + sesli anlatım", "Claude her sabah verinin özetini yazar; sayı uydurmaz, sadece tablodaki rakamları kullanır. TR/EN, kadın/erkek ses."),
        ],
        "h_h2": "Nasıl çalışır", "h_sub": "Kamuya açık bildirimlerden, tekrar üretilebilir bir zincirle.",
        "steps": [
            ("Topla", "KAP ve SEC EDGAR bildirimleri, fon portföy PDF'leri, kapanış fiyatları. Kaynak kimliği her satırda saklanır."),
            ("Normalize et", "Her işlem bir güven seviyesi alır: <span class='pill exact'>Exact</span> tek fon açık tutar, <span class='pill grouped'>Grouped</span> dağılım bilinmiyor, <span class='pill inferred'>Inferred</span> rapor farkından türetildi."),
            ("Hesapla", "Pozisyon farkları, skorlar ve sinyaller deterministik; aynı veri aynı sonucu verir. Düzeltme bildirimleri eskisini geçersiz kılar."),
            ("Anlat", "AI katmanı yalnızca hesaplanmış veriyi özetler ve araç çağrılarını gösterir. Telegram, e-posta ve telefon bildirimi."),
        ],
        "t_h2": "Güven ve sınırlar",
        "trust": [
            "InstiLens bir <b>veri sunumudur</b>; yatırım tavsiyesi vermez, al/sat demez, fiyat tahmini yapmaz.",
            "Kaynaklar: KAP (Kamuyu Aydınlatma Platformu) kamuya açık bildirimler ve SEC EDGAR 13F dosyaları. Fon performansı ölçmez.",
            "Gecikme açık yazılır: portföy raporları ve 13F dosyaları doğası gereği geriden gelir; her ekranda \"veri tazeliği\" görünür.",
            "Grup bildirimlerinde tutar fonlara bölüştürülmez; bilinmeyen bilinmeyen olarak kalır.",
        ],
        "foot": "Veriler kamuya açık düzenleyici bildirimlerden (KAP, SEC) türetilmiştir; yatırım tavsiyesi değildir.",
        "foot_login": "Giriş", "foot_privacy": "İstanbul · 2027",
    },
    "en": {
        "title": "InstiLens — See where smart money moves.",
        "desc": "Track which stocks funds and institutions accumulate, reduce, enter and abandon — on one screen. KAP and SEC 13F data, explainable scores, an AI morning brief. Not investment advice.",
        "nav_product": "Product", "nav_how": "How it works", "nav_trust": "Trust", "nav_login": "Sign in",
        "h1": "See <em>where</em> smart money moves.",
        "lead": "Track which stocks funds accumulate, reduce, enter and abandon — on one screen. Every number is traceable to its KAP or SEC source.",
        "cta_app": "Open the app", "cta_wait": "Request early access", "cta_desktop": "Desktop app",
        "wait_ph": "your e-mail", "wait_btn": "Join the list", "wait_hint": "Invite-only for now. No spam — we only write when your seat opens.",
        "shot_cap": "Radar — BIST, real KAP data. Scores are deterministic and explainable; the answer to \"Why 87?\" sits under every score.",
        "f_h2": "What you see", "f_sub": "Not a price chart — money flow. Who bought, when, how much.",
        "features": [
            ("KAP", "Live KAP Radar", "Fund share-transaction disclosures are normalised the moment they land: stock, institution, fund, lots, confidence."),
            ("PDF", "Portfolio reports", "Weekly/monthly fund portfolio reports are parsed; the diff between two reports becomes NEW / ADD / REDUCE / EXIT."),
            ("87", "Smart Money & Consensus score", "Breadth, net flow, persistence, new positions, conviction. Weights are open; click a score to see why."),
            ("↗", "Signals", "Accumulation, distribution, positive/negative divergence (funds buying while price falls), new-position clusters."),
            ("13F", "Global: SEC 13F", "Quarterly position changes of Berkshire, Bridgewater, Renaissance and others — same engine."),
            ("AI", "Morning brief + narration", "Claude writes a summary every morning; it never invents numbers, only uses what's in the tables. TR/EN, female/male voice."),
        ],
        "h_h2": "How it works", "h_sub": "From public disclosures, through a reproducible chain.",
        "steps": [
            ("Collect", "KAP and SEC EDGAR filings, fund portfolio PDFs, closing prices. The source id is kept on every row."),
            ("Normalise", "Every transaction gets a confidence level: <span class='pill exact'>Exact</span> single fund, explicit amount; <span class='pill grouped'>Grouped</span> allocation unknown; <span class='pill inferred'>Inferred</span> derived from a report diff."),
            ("Compute", "Position diffs, scores and signals are deterministic — same data, same result. Corrections supersede the original filing."),
            ("Explain", "The AI layer only summarises computed data and shows its tool calls. Telegram, e-mail and phone notifications."),
        ],
        "t_h2": "Trust and limits",
        "trust": [
            "InstiLens is a <b>data presentation</b>; it gives no investment advice, no buy/sell calls, no price forecasts.",
            "Sources: KAP (Turkey's Public Disclosure Platform) public filings and SEC EDGAR 13F files. It does not measure fund performance.",
            "Delay is stated: portfolio reports and 13F files lag by nature; \"data freshness\" is visible on every screen.",
            "Grouped filings are never split across funds; unknown stays unknown.",
        ],
        "foot": "Data is derived from public regulatory disclosures (KAP, SEC); not investment advice.",
        "foot_login": "Sign in", "foot_privacy": "Istanbul · 2027",
    },
}


def page(lang: str) -> str:
    t = TEXT[lang]
    a = "" if lang == "tr" else "../"  # asset prefix from /en/
    other = "en" if lang == "tr" else "tr"
    url = f"{SITE}/" if lang == "tr" else f"{SITE}/en/"
    feats = "".join(f'<div class="card"><div class="ic">{ic}</div><h3>{h}</h3><p>{p}</p></div>' for ic, h, p in t["features"])
    steps = "".join(f'<div class="card"><h3>{h}</h3><p>{p}</p></div>' for h, p in t["steps"])
    trust = "".join(f"<li>{x}</li>" for x in t["trust"])
    ld = {
        "@context": "https://schema.org", "@type": "SoftwareApplication", "name": "InstiLens", "applicationCategory": "FinanceApplication",
        "operatingSystem": "Web", "url": SITE, "description": t["desc"], "inLanguage": lang, "offers": {"@type": "Offer", "price": "0", "priceCurrency": "TRY", "availability": "https://schema.org/PreOrder"},
        "publisher": {"@type": "Organization", "name": "InstiLens", "url": SITE, "logo": f"{SITE}/assets/icon-512.png"},
    }
    import json
    return f"""<!doctype html>
<html lang="{lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{t['title']}</title>
<meta name="description" content="{t['desc']}">
<link rel="canonical" href="{url}">
<link rel="alternate" hreflang="tr" href="{SITE}/">
<link rel="alternate" hreflang="en" href="{SITE}/en/">
<link rel="alternate" hreflang="x-default" href="{SITE}/">
<meta property="og:type" content="website">
<meta property="og:site_name" content="InstiLens">
<meta property="og:title" content="{t['title']}">
<meta property="og:description" content="{t['desc']}">
<meta property="og:url" content="{url}">
<meta property="og:image" content="{SITE}/assets/og.png">
<meta property="og:locale" content="{'tr_TR' if lang == 'tr' else 'en_US'}">
<meta name="twitter:card" content="summary_large_image">
<meta name="theme-color" content="#0f1118">
<link rel="icon" type="image/png" sizes="32x32" href="{a}assets/favicon-32.png">
<link rel="icon" type="image/png" sizes="16x16" href="{a}assets/favicon-16.png">
<link rel="apple-touch-icon" href="{a}assets/apple-touch-icon.png">
<link rel="stylesheet" href="{a}site.css">
<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script>
</head>
<body>
<header class="top"><div class="wrap">
  <a class="brand" href="{a or './'}" aria-label="InstiLens"><img class="mark" src="{a}assets/mark-gradient-light.png" alt=""><img class="wm" src="{a}assets/wordmark-gradient-light.png" alt="InstiLens"></a>
  <nav class="links">
    <a class="hide-sm" href="#product">{t['nav_product']}</a><a class="hide-sm" href="#how">{t['nav_how']}</a><a class="hide-sm" href="#trust">{t['nav_trust']}</a>
    <span class="lang"><a href="{'./' if lang == 'tr' else '../'}" class="{'on' if lang == 'tr' else ''}">TR</a><a href="{'en/' if lang == 'tr' else './'}" class="{'on' if lang == 'en' else ''}">EN</a></span>
    <a class="btn" href="{APP}">{t['nav_login']}</a>
  </nav>
</div></header>

<section class="hero"><div class="glow"></div><div class="wrap">
  <img class="wordmark" src="{a}assets/wordmark-gradient-light.png" alt="InstiLens">
  <div class="tag">See where smart money moves.</div>
  <h1>{t['h1']}</h1>
  <p class="lead">{t['lead']}</p>
  <form class="wait" id="wait" novalidate>
    <input type="email" name="email" placeholder="{t['wait_ph']}" autocomplete="email" required>
    <button class="btn primary" type="submit">{t['wait_btn']}</button>
    <div class="msg">{t['wait_hint']}</div>
  </form>
  <div class="cta"><a class="btn" href="{APP}">{t['cta_app']} →</a><span id="desktop-dl" hidden></span></div>
  <figure class="shot"><img src="{a}assets/radar.webp" width="1400" height="780" alt="InstiLens Radar" loading="eager"><figcaption>{t['shot_cap']}</figcaption></figure>
</div></section>

<section id="product"><div class="wrap">
  <h2>{t['f_h2']}</h2><p class="sub">{t['f_sub']}</p>
  <div class="grid">{feats}</div>
</div></section>

<section id="how"><div class="wrap">
  <h2>{t['h_h2']}</h2><p class="sub">{t['h_sub']}</p>
  <div class="steps">{steps}</div>
</div></section>

<section id="trust"><div class="wrap">
  <h2>{t['t_h2']}</h2>
  <div class="trust"><ul>{trust}</ul></div>
</div></section>

<footer><div class="wrap">
  <span>© InstiLens · {t['foot_privacy']}</span><span>{t['foot']}</span>
  <span class="right"><a href="{APP}">{t['foot_login']}</a><a href="{'en/' if lang == 'tr' else '../'}">{other.upper()}</a></span>
</div></footer>
<script src="{a}site.js" defer></script>
</body>
</html>
"""


if __name__ == "__main__":
    (ROOT / "index.html").write_text(page("tr"), encoding="utf-8")
    (ROOT / "en").mkdir(exist_ok=True)
    (ROOT / "en" / "index.html").write_text(page("en"), encoding="utf-8")
    print("ok", ROOT)
