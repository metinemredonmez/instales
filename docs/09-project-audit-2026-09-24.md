# InstiLens proje denetimi — 24 Eylül 2026

İncelenen commit: `1755838`. Kapsam: backend, veri motorları, AI çıkarımı, API/auth, admin, frontend, abonelikler, bildirimler, migration/test düzeni, PM2/container dağıtımı, yedekleme ve masaüstü güncellemeleri.

**Değerlendirme:** Ürün kapsamı ve katman ayrımı güçlü; üretime güvenle çıkmak için önce veri doğruluğu, hesap izolasyonu ve operasyonel güvenilirlik sorunları giderilmeli. Testlerin yeşil olması bu alanların güvence altında olduğunu henüz göstermiyor. Aşağıdaki sonuçlar yerel kod incelemesine ve izole deneylere dayanıyor; canlı sunucuda bu hataların gerçekleştiği iddia edilmiyor.

Uygulama koduna düzeltme yapılmadı. İnceleme sonucunda yalnızca bu rapor eklendi. Dış servislere bildirim, ödeme veya AI isteği gönderilmedi.

## Doğrulama sonuçları ve sınırlar

| Kontrol | Sonuç |
|---|---|
| Backend mevcut testleri | **269 geçti**, 45,97 saniye |
| Backend Ruff | **Başarılı** |
| Frontend TypeScript + Vite build | **Başarılı** |
| Frontend mevcut testleri | **31 dosyada 241 test geçti** |
| Frontend lint | Başarılı çıkış kodu; React uyarıları var |
| Ek backend deneyleri | İzole SQLite, sahte HTTP göndericileri ve doğrudan servis çağrılarıyla aşağıdaki örnekler doğrulandı |
| Ek frontend deneyleri | Hesap değiştirme, piyasa karışması ve admin hata görünümü için 3 tekrar üretme testi geçti |

`uv sync` bu makinenin kısıtlı ortamında önce cache erişimi, ardından `uv` macOS system-configuration panic hatası verdi; offline denemesi de aynı panic ile sonlandı. Backend test/lint mevcut `.venv` üzerinden çalıştırıldı. Dolayısıyla temiz bağımlılık kurulumu doğrulanmış sayılmıyor. Test uyarılarındaki kısa JWT anahtarı test/dev konfigürasyonuna ait; production başlangıcında uzunluk kontrolü mevcut.

Canlı PostgreSQL, sağlayıcı entegrasyonları, gerçek ödeme, gerçek push teslimi, Docker/PM2 üzerinde uçtan uca dağıtım ve masaüstü kurulum testi yapılmadı. Güncel bağımlılık açıklarının tam taraması bu incelemenin sonucu değil. CI ayarları ayrıca değerlendirildi.

P1: yayına çıkmadan ele alınması gereken yüksek öncelik. P2: doğruluk, güvenlik veya işletim sorunları; sonraki düzeltme grubuna alınmalı. “Deney” kontrollü tekrar üretmeyi; “kod” ilgili akışın koddan doğrulanmasını belirtir.

## P1 — Öncelikli sorunlar

### 1. Hesap değişiminde önceki kullanıcının özel önbelleği kalıyor — deney

[auth.tsx:112](/Users/emre/Desktop/instales/frontend/src/lib/auth.tsx:112), [main.tsx:12](/Users/emre/Desktop/instales/frontend/src/main.tsx:12), [NotifySettingsCard.tsx:18](/Users/emre/Desktop/instales/frontend/src/components/domain/NotifySettingsCard.tsx:18).

Logout yalnızca oturumu sıfırlıyor; ortak QueryClient korunuyor. Kullanıcı ayarları, portföyler ve diğer özel sorguların anahtarlarında kullanıcı kimliği yok. A çıkış yapıp B giriş yaptığında **B başlığının altında A e-postası ve Telegram chat ID'si** gösterildi. 30 saniyelik staleTime içinde ikinci settings GET yapılmadı. Kaydetmek A'nın chat ayarını B hesabına gönderebildi.

Çözüm: Özel sorguları kullanıcı kimliğiyle anahtarlamak; logout, 401 ve hesap değiştirmede devam eden sorguları iptal edip özel cache'i temizlemek. Kabul testi hem görünürlüğü hem yanlış hesaba yazmayı sınamalı.

### 2. Push endpoint alanı sunucudan iç ağa istek gönderebiliyor — deney

[userdata.py:140](/Users/emre/Desktop/instales/backend/src/instilens/api/routes/userdata.py:140), [notify.py:104](/Users/emre/Desktop/instales/backend/src/instilens/services/notify.py:104).

`endpoint` yalnızca uzunluğu sınırlanmış bir string. `http://127.0.0.1:9999/audit-only` adresi **201** ile kaydedildi. Geçerli test VAPID/browser anahtarlarıyla gerçek pywebpush kodunun HTTP çağrısı, ağa çıkmadan yakalandı: hedef aynı iç adresdi; timeout da `None` idi. VAPID yapılandırılmışken abonelik oluşturabilen hesap bu yolu kullanabilir. Plans enforced kapalıyken ücretsiz hesap da erişebilir.

Çözüm: Destinasyon doğrulaması; HTTPS ve desteklenen push servisleri için izin politikası; loopback/private/link-local adresleri ve yönlendirmeler için koruma; kesin timeout. Testler özel IP, IPv6, DNS çözümleme ve redirect yollarını kapsamalı. `docs/07-security.md` içindeki “user-controlled URL yok” ifadesi güncellenmeli.

### 3. Çok tarihli bildirim snapshot ile tekrar sayılıyor — deney

[kap_share_transaction.py:57](/Users/emre/Desktop/instales/backend/src/instilens/parsing/kap_share_transaction.py:57), [pipeline.py:540](/Users/emre/Desktop/instales/backend/src/instilens/services/pipeline.py:540).

Bir bildirimdeki bütün işlemler en son işlem tarihi altında birleştiriliyor. Örnekte 20 Ağustos +100 ve 1 Eylül +10 var; 31 Ağustos snapshot'ı ilk +100'ü zaten içeriyor. Son tarih snapshot sonrasında olduğu için +110'un tamamı tekrar sayıldı: fiyat 10 iken akış **1.100 yerine 2.100** oldu. AGENTS.md'nin kaynaklar arası çift sayım yasağı ihlal ediliyor.

Çözüm: İşlemleri tarih bazında normalize etmek ve snapshot kapsam kontrolünü o düzeyde yapmak. GROUPED toplamları fonlara dağıtmama kuralı korunmalı.

### 4. Karışık alış/satışta net parasal akış yanlış hesaplanıyor — deney

[kap_share_transaction.py:33](/Users/emre/Desktop/instales/backend/src/instilens/parsing/kap_share_transaction.py:33).

Alış ve satış fiyatlarının ortak pozitif ağırlıklı ortalaması net adetle çarpılıyor. **100 × 10 alış, 90 × 20 satış** için gerçek alış eksi satış değeri **−800** iken `net_value` **+147,368** oldu. Miktar net pozitif olsa bile para akışının işareti farklı olabilir; tek ortalama bu farkı kaybediyor.

Çözüm: Yönlü satır tutarlarını toplamak. Eksik fiyatlı satır varsa hangi toplamın bilindiğini açıkça temsil etmek; eksik fiyatı diğer tarafın ortalamasıyla doldurmamak. Mevcut mixed-row testi net değeri doğrulamıyor.

### 5. AI doğrulaması kaynakta olmayan finansal alanları kabul ediyor — deney

[filing_extract.py:42](/Users/emre/Desktop/instales/backend/src/instilens/ai/filing_extract.py:42), [public_adapter.py:361](/Users/emre/Desktop/instales/backend/src/instilens/ingestion/kap/public_adapter.py:361).

Nominal doğrulaması tam sayı eşleşmesi yerine alt dize araması yapıyor. Kaynak “22.09.2026 tarihinde **1.234 adet pay satıldı**” iken **123 adet, 01.01.2020, ALIS, %99,9** çıktısı kabul edildi. Tarihin yalnızca biçimi/geçmişte olması kontrol ediliyor; yön ve oranların kaynak desteği aranmıyor. Tek fon olması bu sonucu parser'da `EXACT` yapabiliyor.

Çözüm: Miktar, tarih, yön, oran ve kimlikler için alan bazında kaynak kanıtı; sayı token sınırları; doğrulanamayan satırı inceleme kuyruğuna alma. Bu sonuç “model sayı uyduramaz” garantisinin mevcut validator ile sağlanmadığını gösteriyor.

### 6. Düzeltme akışı eski veriyi erken devreden çıkarıyor ve geçmiş satırları siliyor — deney + kod

[pipeline.py:122](/Users/emre/Desktop/instales/backend/src/instilens/services/pipeline.py:122), [pipeline.py:135](/Users/emre/Desktop/instales/backend/src/instilens/services/pipeline.py:135), [pipeline.py:243](/Users/emre/Desktop/instales/backend/src/instilens/services/pipeline.py:243).

Düzeltme ham veri olarak gelir gelmez önceki bildirim/işlemler superseded oluyor; snapshot ve bağlı değişimler siliniyor. Yeni bildirim parse edilemezse eski veri geri gelmiyor. Geçerli işlem ardından `rows=[]` düzeltmesinde **eski superseded, yeni FAILED, kullanılabilir işlem sayısı 0** oldu. Eski normalize snapshot'ların fiziksel silinmesi ayrıca “supersede, never overwrite; keep old rows” kuralına aykırı.

Çözüm: Düzeltmenin etkinleşmesini başarılı normalizasyonla atomik yapmak, önceki versiyonları saklamak. Geçersiz düzeltmede eski verinin güncelliğinin şüpheli olduğu açıkça gösterilmeli; sessiz veri kaybı olmamalı.

SEC ek holding düzeltmesinde de kaynak kaybı var: [pipeline.py:267](/Users/emre/Desktop/instales/backend/src/instilens/services/pipeline.py:267) ek satırları eski snapshot'a ekliyor/değiştiriyor; snapshot'ın `disclosure_id` değeri orijinal belgeyi göstermeyi sürdürüyor. Holding düzeyinde ek bildirimi gösterecek lineage yok. Versiyonlama ve satır kaynak bağlantısı birlikte tasarlanmalı; şema değişikliği Alembic ile yapılmalı.

### 7. Tarihsel hesaplar sonradan yayımlanan veriyi kullanıyor — deney

[pipeline.py:529](/Users/emre/Desktop/instales/backend/src/instilens/services/pipeline.py:529), [pipeline.py:427](/Users/emre/Desktop/instales/backend/src/instilens/services/pipeline.py:427).

“O tarihte bilinen” snapshot sorgusu yalnızca rapor dönemini filtreliyor; yayın zamanını kontrol etmiyor. **10 Eylül'de yayımlanan 31 Ağustos raporu, 1 Eylül hesaplamasında** mevcut kabul edildi. Tarihsel skor, backfill ve performans ölçümleri gelecekte öğrenilmiş veriyi kullanabilir.

Çözüm: Rapor tarihi ile bilgiye erişim zamanını ayırmak; snapshot, işlem ve düzeltme versiyonu seçimini `published_at` kesitiyle tutarlı yapmak. Sonradan düzeltilmiş tarihsel görünüm gerekiyorsa ayrı bir semantik olarak sunmak.

### 8. Admin erişim ayarları API worker'ları arasında tutarlı uygulanmıyor — deney + kod

[runtime_settings.py:140](/Users/emre/Desktop/instales/backend/src/instilens/services/runtime_settings.py:140), [auth.py:79](/Users/emre/Desktop/instales/backend/src/instilens/api/routes/auth.py:79), [ecosystem.config.cjs:10](/Users/emre/Desktop/instales/infra/pm2/ecosystem.config.cjs:10).

PM2 iki API worker başlatıyor. Ayar değişimi bir process'in `settings` nesnesine uygulanıyor; diğer process yalnızca başlangıçta veya belirli quotes/provider/candles yollarında yeniliyor. Auth ve plan kontrolü öncesinde ortak yenileme yok. Başka worker'ın DB'ye yazmasını temsil eden deneyde DB'de `allow_registration=false` olmasına rağmen eski process ayarıyla kayıt **201** döndü.

Çözüm: Güvenlik/plan ayarları için request öncesi sürüm kontrollü ortak yenileme veya merkezi okuma. Kabul testi iki gerçek process üzerinde kayıt kapatma ve plan enforcement değişimini doğrulamalı.

### 9. Scheduler ve admin hesaplamaları aynı ortak kilidi kullanmıyor — tetiklenme deneyi + kod

[scheduler.py:208](/Users/emre/Desktop/instales/backend/src/instilens/scheduler.py:208), [admin.py:282](/Users/emre/Desktop/instales/backend/src/instilens/api/routes/admin.py:282), [pipeline.py:296](/Users/emre/Desktop/instales/backend/src/instilens/services/pipeline.py:296).

`max_instances=1` yalnızca her job'ın kendi tekrarını önlüyor. `kap_rush` ve `kap_insiders` için CronTrigger deneyi **24 Eylül 17:15 İstanbul** tetiklenmesinin aynı olduğunu doğruladı; ikisi de compute yoluna gidiyor. Admin'in DB pipeline kilidini scheduler almıyor. Pozisyon ve günlük skorların silinip yeniden yazıldığı bu işlerde eşzamanlı çalışma çakışma/rollback riski oluşturuyor. Canlı veri bozulması gözlenmedi.

Çözüm: Scheduler, admin ve CLI girişlerinin aynı DB kilidini kullanması; kritik yazımların transaction sınırlarının netleştirilmesi. PostgreSQL üzerinde eşzamanlı başlatma testi gerekli.

### 10. Yedek saklama kuralı bugün adlı release arşivini silebiliyor — deney

[backup.sh:35](/Users/emre/Desktop/instales/infra/pm2/backup.sh:35), [backup.sh:60](/Users/emre/Desktop/instales/infra/pm2/backup.sh:60).

Değişmeyen release arşivi eski dosyaya hard link verilerek yeniden kullanılıyor. Hard link aynı mtime'ı taşıyor; `find -mtime +14 -delete` bugünün dosya adına bakmıyor. 20 günlük arşivden bugün adlı link oluşturulan deneyde **bugünkü link de silme filtresine girdi**. SSH hedefindeki saklama mantığında da aynı sorun var.

Çözüm: Saklama yaşını snapshot tarihinden hesaplamak veya arşiv tasarımını değiştirmek. Yalnızca yedek oluşturma değil, retention sonrası geri yükleme testi de yapılmalı.

### 11. Container kurulumunda release ve warehouse dosyaları kalıcı/ortak değil — kod

[compose.yml:13](/Users/emre/Desktop/instales/infra/compose.yml:13), [container-up.sh:42](/Users/emre/Desktop/instales/infra/container-up.sh:42), [releases.py:113](/Users/emre/Desktop/instales/backend/src/instilens/services/releases.py:113).

API ve scheduler için ortak kalıcı media volume yok. API'ye yüklenen installer dosyaları container yeniden oluşturulduğunda kaybolabilir; DB kaydı kalır ve indirme 404 olur. Scheduler'ın oluşturduğu warehouse dosyası da API container'ının diskinde bulunmaz. Bu bulgu container dağıtımı için geçerli; PM2 diskiyle aynı durum olduğu iddia edilmiyor.

Çözüm: Her iki sürece bağlanan kalıcı media deposu; yeniden oluşturma ve indirilebilirlik testi; bu deponun yedek kapsamına alınması.

## P2 — Diğer doğrulanmış sorunlar

### 12. Aynı sembol farklı piyasalarda yanlış enstrümana bağlanıyor — deney

[pipeline.py:297](/Users/emre/Desktop/instales/backend/src/instilens/services/pipeline.py:297), [pipeline.py:374](/Users/emre/Desktop/instales/backend/src/instilens/services/pipeline.py:374).

DB kimliği piyasa+sembol iken rebuild/fiyat sözlükleri yalnızca sembol kullanıyor. TR ve US `SAME` örneğinde TR fonunun hareketi US instrument_id ile yazıldı; değer **1.000 yerine 100.000** çıktı. Canlı veri setinde böyle bir çakışma bulunduğu iddia edilmiyor. Anahtar `instrument_id` veya `(market, symbol)` olmalı.

### 13. Sinyal devam ettikçe performans başlangıcı değişiyor — deney

[outcomes.py:23](/Users/emre/Desktop/instales/backend/src/instilens/services/outcomes.py:23), [pipeline.py:510](/Users/emre/Desktop/instales/backend/src/instilens/services/pipeline.py:510).

Devam eden sinyalin `window_end` değeri ileri taşınıyor; outcome aynı alanı başlangıç kabul ediyor. Aynı sinyalin baz fiyatı **100→120**, 7 günlük getirisi **%20→%25** oldu. İlk tespit tarihi ile son görülme tarihi ayrılmalı; ilk tespit bazlı performans sabit tutulmalı.

### 14. Başarısız girişler admin audit tablosuna kalıcı yazılmıyor — deney

[auth.py:117](/Users/emre/Desktop/instales/backend/src/instilens/services/auth.py:117), [db/session.py:35](/Users/emre/Desktop/instales/backend/src/instilens/db/session.py:35).

Başarısız auth işlemi audit satırı ekleyip exception fırlatıyor; request session'ı rollback yapıyor. Gerçek transaction yaşam döngüsünü kullanan deneyde yanlış şifre **401**, kalıcı audit satırı **0** idi. Uygulama loguna yazılan mesaj bundan ayrı; bulgu admin'in okuduğu DB kaydına ilişkin. Bazı mevcut testlerin session'ı doğrudan döndürmesi gerçek rollback'i modellemiyor.

Çözüm: Güvenlik olayları için bağımsız, güvenilir kayıt işlemi; başarısız istekten sonra yeni session ile audit sorgulayan test.

### 15. MFA kapatma denemelerine hesap/IP limiti uygulanmıyor — deney + kod

[auth.py:287](/Users/emre/Desktop/instales/backend/src/instilens/services/auth.py:287), [routes/auth.py:173](/Users/emre/Desktop/instales/backend/src/instilens/api/routes/auth.py:173).

Login MFA doğrulamasındaki limit, enable/disable yollarında yok. TOTP doğrulayıcısının yanlış kod döndürdüğü kontrollü deneyde 20 disable isteğinin tamamı **400**, hiçbiri **429** verdi. Risk geçerli oturumu elinde bulunduran kişi için geçerli. Hesap ve IP bazlı limit ile hassas işlem için yeniden doğrulama uygulanmalı.

### 16. Normal kullanıcı tüm kullanıcıların alarm değerlendirmesini başlatabiliyor — deney

[userdata.py:77](/Users/emre/Desktop/instales/backend/src/instilens/api/routes/userdata.py:77), [alerts.py:73](/Users/emre/Desktop/instales/backend/src/instilens/services/alerts.py:73).

Dev/ops diye açıklanan `/alerts/evaluate` yalnızca current_user istiyor ve global evaluate çağırıyor. Normal USER isteği **200** verdi; servis çağrısına owner filtresi aktarılmadı. Bu veri sızması kanıtı değil; yetki kapsamından geniş iş ve diğer sahiplerin bildirim satırlarını oluşturma yetkisi. Admin'e sınırlandırılmalı veya owner bazında değerlendirmeli.

### 17. Abonelik silme olayı önce gelirse daha eski olay planı yeniden açıyor — deney

[billing.py:522](/Users/emre/Desktop/instales/backend/src/instilens/services/billing.py:522).

Henüz yerelde bilinmeyen subscription için silme olayı yok sayılıyor. Deneyde timestamp 200 olan deleted önce, timestamp 100 olan created sonra uygulandı; hesap **PRO/ACTIVE** kaldı. Eski olay koruması yalnızca mevcut subscription satırı üzerinde çalışıyor. Bilinmeyen kimlikler için de son olay durumu korunmalı veya sağlayıcının güncel durumu ile uzlaştırılmalı.

### 18. Süresi biten manuel üst plan, ücretli alt plana dönemiyor — deney

[plans.py:82](/Users/emre/Desktop/instales/backend/src/instilens/services/plans.py:82), [billing.py:448](/Users/emre/Desktop/instales/backend/src/instilens/services/billing.py:448).

Manuel PRO_PLUS varken aktif ücretli PRO kaydı oluşabiliyor; yüksek manuel hak korunuyor. Süresi bitince `own_plan` doğrudan FREE dönüyor ve canlı ücretli aboneliği çözmüyor. Deney sonucu **PRO_PLUS → FREE**, canlı ücretli kayıt ise **PRO**. Bir sonraki uygun webhook'a kadar ücretli erişim kaybolabilir. Hak çözümü tüm etkin kaynakları birlikte değerlendirmeli.

### 19. Takip listesi ve portföy bağlantıları yanlış piyasayı kullanıyor — deney

[WatchlistPage.tsx:50](/Users/emre/Desktop/instales/frontend/src/pages/WatchlistPage.tsx:50), [PortfolioPage.tsx:212](/Users/emre/Desktop/instales/frontend/src/pages/PortfolioPage.tsx:212).

Takip listesi tüm piyasaları getiriyor; satırın piyasası yerine header piyasası grafik/para birimine veriliyor. TR seçiliyken US NVDA satırı **₺100** ve TR grafik parametresiyle gösterildi. Hisse bağlantısı da global TR ile açılıyor. Satır piyasası tutar, grafik ve navigasyona taşınmalı; URL piyasa bilgisini korumalı.

### 20. Admin veri inceleme hatası boş kuyruk gibi gösteriliyor — deney

[AdminReviewPage.tsx:21](/Users/emre/Desktop/instales/frontend/src/pages/admin/AdminReviewPage.tsx:21), [AdminSettingsPage.tsx:15](/Users/emre/Desktop/instales/frontend/src/pages/admin/AdminSettingsPage.tsx:15).

Review isteği 503 ile reddedildiğinde üç inceleme listesi **“None.”** gösterdi, hata göstermedi. Admin settings hata halinde süresiz `…` gösterebiliyor. Doğrulama/kayıt mutasyonlarında da hata sunumu eksik. Loading, başarılı boş sonuç ve hata durumları ayrı olmalı; yeniden deneme ve mutasyon geri bildirimi eklenmeli.

### 21. VAPID cihaz aboneliği logout'ta önceki hesaba bağlı kalıyor — kod

[ProfileMenu.tsx:115](/Users/emre/Desktop/instales/frontend/src/components/layout/ProfileMenu.tsx:115), [push.ts:72](/Users/emre/Desktop/instales/frontend/src/lib/push.ts:72), [userdata.py:164](/Users/emre/Desktop/instales/backend/src/instilens/api/routes/userdata.py:164).

Logout OneSignal'ı ayırıyor; VAPID kaydını kaldırmıyor. Sonraki kullanıcı tarayıcı aboneliği olduğu için push açık sanabilir; endpoint başka hesaba kayıtlı olduğundan yeniden bağlama 409 verir. Eski hesap bildirimleri cihaza gönderilmeye devam edebilir. Canlı push gönderilmedi. Token kaldırılmadan cihaz kaydını ayıran ortak logout akışı gerekli.

### 22. Deploy health kontrolü veri katmanının hazır olduğunu doğrulamıyor — deney

[main.py:85](/Users/emre/Desktop/instales/backend/src/instilens/api/main.py:85), [deploy.sh:16](/Users/emre/Desktop/instales/infra/pm2/deploy.sh:16).

Migration yapılmamış geçici DB ile `/health` **200 ok** döndü. Başlangıçta runtime settings DB hatası da yalnızca loglanıyor. Endpoint liveness için kullanılabilir; deploy başarı/rollback kararı için yetersiz. Ayrı readiness kontrolü DB erişimi ve migration seviyesini doğrulamalı.

### 23. Container dağıtımı bağımsız quote-feed sürecini başlatmıyor — kod

[compose.yml](/Users/emre/Desktop/instales/infra/compose.yml), [ecosystem.config.cjs:29](/Users/emre/Desktop/instales/infra/pm2/ecosystem.config.cjs:29), [feed.py:103](/Users/emre/Desktop/instales/backend/src/instilens/services/feed.py:103).

PM2 feed sürecini başlatırken Compose/Apple container yolu başlatmıyor. Böylece feed heartbeat ve ürettiği canlı quotes eventleri yok. HTTP polling ile fiyat görülebilmesi bu eksikliği kapatmaz. Container topolojisine feed eklenmeli ve heartbeat test edilmeli.

## Admin ve ürün iş akışlarında eksikler

| Alan | Mevcut durum | Tamamlanması gereken |
|---|---|---|
| Kullanıcı yönetimi | Rol, aktiflik, plan ve süre; kendini/son admin'i düşürme koruması var | Büyük kullanıcı listeleri için arama/sayfalama; gerçek request yaşam döngüsü ve erişim değişimi testleri |
| Enstrüman doğrulama | Yalnızca isim değişimi ve `is_verified=true` | CUSIP→ticker eşleme, kayıt birleştirme, bağımlı satırları koruma ve yeniden hesaplama |
| Başarısız parse | Hata metni listeleniyor | Kaynak/payload inceleme, tek kayıt yeniden parse, sonucu izleme |
| Review kuyruğu | İlk 50 kayıt gösteriliyor; kalanı yalnızca sayı | Arama, filtre ve sayfalama; 51. kayda doğrudan erişim |
| Haber kuralları | Oluşturma, aktiflik değiştirme, silme | Mevcut kuralın alanlarını düzenleme; eksik language/max_age_days/ai_summary alanları; US enrich kontrolü |
| Pipeline işletimi | Admin çalıştırma ve durum ekranı mevcut | Tüm entrypoint'lerde ortak kilit, job bazında görünür hata ve tekrar deneme |
| Hesap güvenliği | Argon2, JWT revocation, MFA ve reset akışları var | Başarısız audit kalıcılığı, MFA değişim limitleri, cache/push oturum temizliği |

Admin doğrulama davranışı: [admin.py:55](/Users/emre/Desktop/instales/backend/src/instilens/services/admin.py:55). Review sınırı: [AdminReviewPage.tsx:40](/Users/emre/Desktop/instales/frontend/src/pages/admin/AdminReviewPage.tsx:40). Haber kuralı işlemleri: [NewsRulesAdmin.tsx:19](/Users/emre/Desktop/instales/frontend/src/components/domain/NewsRulesAdmin.tsx:19).

## Test, entegrasyon ve sürdürülebilirlik boşlukları

- **Frontend testleri CI'da çalışmıyor.** [ci.yml:18](/Users/emre/Desktop/instales/.github/workflows/ci.yml:18) tsc/build yapıyor; mevcut 241 Vitest testi için adım yok.
- **PostgreSQL ve eşzamanlılık test hattı yok.** [conftest.py:17](/Users/emre/Desktop/instales/backend/tests/conftest.py:17) SQLite kullanıyor; CI Postgres servisi kurmuyor. SQLite testlerinin geçmesi production kilit/transaction/DDL davranışını doğrulamıyor.
- **Dependency audit yayın kapısı değil.** [ci.yml:17](/Users/emre/Desktop/instales/.github/workflows/ci.yml:17) ve `:28` komutları `|| true` ile devam ediyor. Bu mevcut açık bulunduğu anlamına gelmez; yüksek önem dereceli sonuç build'i durdurmaz.
- **Admin test dağılımı zayıf.** Releases testleri var; users/review/settings/news ve hesap değiştirme akışlarının kapsamı eksik. Her bulguya yönelik regresyon testleri eklenmeli.
- **İlk frontend paketi büyük.** Ana JS yaklaşık **1.230 kB, gzip 361 kB**. [App.tsx:1](/Users/emre/Desktop/instales/frontend/src/App.tsx:1) sayfaları eager import ediyor. Route bazlı lazy loading ve grafik/admin kodunu ayırmak uygun.
- **Matriks slotu çalışır entegrasyon değil.** [matriks.py:36](/Users/emre/Desktop/instales/backend/src/instilens/ingestion/prices/matriks.py:36) veri çağrılarını reddediyor. Anahtar tanımlamak yeterli değil; adapter ve sağlayıcı doğrulaması gerekiyor.
- **Resmî KAP adapteri var; doküman geride.** Roadmap/mimari skeleton anlatırken kod gerçek auth/fetch/parse içeriyor. “Hiç yazılmamış” demek yanlış olur; canlı sözleşme/kimlik/şema uyumu bu denetimde doğrulanmadı.
- **Desktop updater imzası ile OS imzası farklı.** Updater doğrulaması mevcut; [desktop.yml:127](/Users/emre/Desktop/instales/.github/workflows/desktop.yml:127) macOS/Windows imzasız dağıtım uyarılarını belgeliyor. Kod imzası ve notarization geniş dağıtım hazırlığı olarak ele alınmalı.
- **İş tarihinin timezone'u netleştirilmeli.** Scheduler İstanbul saatinde çalışıyor; [scheduler.py:41](/Users/emre/Desktop/instales/backend/src/instilens/scheduler.py:41) host `date.today()` kullanıyor. UTC hostta gece job'ının beklenen iş günü açıkça seçilmeli.
- **Dokümanların “tamamlandı” işaretleri yeniden kontrol edilmeli.** README test sayısı, roadmap admin eksikleri, güvenlikte SSRF/audit değerlendirmesi mevcut kodla tam örtüşmüyor.

## Korunması gereken güçlü taraflar

- API, servisler, veri motorları, ingestion ve AI katmanları ayrılmış; sistem genişletilebilir bir temel üzerinde.
- Veri router'larında authentication ve admin router'ında rol kontrolü mevcut; temel bir “admin herkese açık” sorunu görülmedi.
- Argon2 parola hash'i, JWT issuer/audience/scope kontrolleri, token_version ile iptal, production secret kontrolü ve DB tabanlı giriş limitleri mevcut.
- Parasal hesaplarda Decimal, GROUPED paylaştırma yasağı, snapshot baseline yaklaşımı ve açıklanabilir skor component'leri önemli doğru tercihler.
- Gerçek ve sentetik fixture ayrımı, ağsız test yaklaşımı, Alembic ve 510 mevcut test değerli bir temel oluşturuyor.
- Typed frontend API hataları, 402 plan sınırı akışı, i18n kapsam testi ve çeşitli domain component testleri mevcut.

## Önerilen düzeltme sırası ve kabul koşulları

1. **Hesap izolasyonu ve sunucu istek sınırı:** #1, #2, #8, #14–16, #21. A→B geçişinde A verisi/aboneliği kalmamalı; özel hedefe HTTP çıkışı yapılamamalı; erişim ayarları bütün worker'larda aynı olmalı; başarısız güvenlik olayları kalıcı olmalı.
2. **Veri doğruluğu ve geçmiş:** #3–7, #12–13. Çift sayım, mixed cashflow, yayın gecikmesi, düzeltme başarısızlığı, lineage ve piyasa çakışması için regresyon seti. Etkilenmiş eski verilerin yeniden işlenmesi gerekir; yalnızca yeni kodu deploy etmek eski sonuçları düzeltmez.
3. **Operasyon ve abonelikler:** #9–11, #17–18, #22–23. Postgres concurrency, retention sonrası restore, container recreate sonrası installer indirme, readiness ve ters sıralı webhook testleri.
4. **Admin'in günlük kullanımını tamamlama:** #19–20 ve admin tablosundaki iş akışları. Kaynak inceleme→düzeltme/eşleme→yeniden parse→yeniden hesaplama zinciri operatör tarafından tamamlanabilmeli.
5. **Yayın kapısı ve performans:** CI'da Vitest + Postgres; güvenlik taraması politikası; route splitting; dokümanların gerçek durumla eşitlenmesi.

Şema gerektiren düzeltmeler Alembic ile yapılmalı. Yeniden hesaplama öncesinde veri/yedek korunmalı; eski düzeltmeler ve kaynaklar silinmemeli. Bu sıranın amacı önce kullanıcı verisi ve finansal sonuçlara güveni sağlamak, ardından admin işletimini ve dağıtım sürekliliğini tamamlamak.
