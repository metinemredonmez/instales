# fixtures/kap_public — real kap.org.tr pages and attachments

Everything here is a real disclosure, fetched once through the prototype public adapter's own client
(`ingestion/kap/public_adapter.KapPublicAdapter`: its User-Agent, its retry/backoff, `delay_seconds` between requests)
and saved byte for byte — the detail page as `GET /tr/Bildirim/{index}` returned it (Next.js RSC payload included) and
the attachment as `GET /tr/api/file/download/{objId}` returned it (KAP's Java-serialised wrapper around the PDF, which
`pdr_pdf.pdf_bytes` strips). Nothing is edited or invented; the tests replay these files through the adapter's real
parsing code (`tests/test_kap_public.py`, `tests/test_kap_insiders.py`).

## PYŞ share transactions and fund portfolio reports (the pipeline's ingest path)

| File | What it is | Source |
|---|---|---|
| `1662606.html` | Tera Portföy (PYŞ) — "Pay Alım Satım Bildirimi": fund DOH bought 10.313.894 MARTI on 2026-09-10, table with before/after ratios (4,754492 % → 5,442085 %) | https://www.kap.org.tr/tr/Bildirim/1662606 |
| `1662620.html` | Marmara Capital Portföy (PYŞ) — the same subject with the numbers in prose + PDF only (funds MAC, MAS on GSDHO): the prose fallback | https://www.kap.org.tr/tr/Bildirim/1662620 |
| `VPS_pdr_2026w36.pdf` | Vega Portföy VPS fund — weekly "Portföy Dağılım Raporu" PDF (week 36, as of 2026-09-11) | attachment of the fund's report disclosure |

## Insider filings — persons and shareholders (`services/insiders.refresh_kap`)

The same subject filed for a director, executive or shareholder rather than a PYŞ. Two shapes exist on kap.org.tr and
both are covered: the issuer's own ODA page (party and role in the prose, numbers in the standard table) and the page
MKK publishes under "KAMUYU AYDINLATMA PLATFORMU" on the person's behalf, whose page names only the sender and whose
SPK form ("SÜREKLİ BİLGİLERE İLİŞKİN ÖZEL DURUM AÇIKLAMASI") is the PDF attachment. Chosen for one ALIŞ and one SATIŞ
by natural persons with a stated role, one issuer-page filing, and one legal-entity shareholder (the form's "Tüzel Kişi
Adına Bildirimi Yapanın Adı Soyadı" is filled, so its "Görevi" is the signatory's job, not the entity's relationship).
No company buyback was filed under this subject in the windows read (BIST issuers file buybacks under "Payların Geri
Alınmasına İlişkin Bildirim", a different subject), so the buyback rule is exercised on an in-memory payload in the tests.

| Index | Files | What it is | Page / attachment |
|---|---|---|---|
| 1654800 | `1654800.html`, `1654800_GIPTA_55984.pdf` | GIPTA — Mehmet Sönmez (Görevi: GENEL MÜDÜR), **SATIŞ** 60.000 nominal at 103,2000 TL on 2026-08-25, stake after 0,0758 %. Relayed by MKK; published 2026-08-25 14:52 | https://www.kap.org.tr/tr/Bildirim/1654800 · `/tr/api/file/download/4028328c9f52dc3f01a038c2687f265a` |
| 1662850 | `1662850.html`, `1662850_BURVA_56128.pdf` | BURVA — Ümit Gümüş (Görevi: Yönetim Kurulu Başkanı), **ALIŞ** 30.000 nominal in the 594 – 600 TL range on 2026-09-15, stake after 55,14 %. Relayed by MKK; published 2026-09-15 13:23 | https://www.kap.org.tr/tr/Bildirim/1662850 · `/tr/api/file/download/4028328da09bf08e01a0a494fe4f4031` |
| 1664326 | `1664326.html` | ATSYH — Atlantis Yatırım Holding's own page: "Yönetim Kurulu Başkanı Sayın Süleyman Yıldırım tarafından … 31.879 adet alış" in the 103,90 – 104,00 TL range on 2026-09-16, 1,6394 % → 2,0379 %. Issuer ODA page; published 2026-09-16 19:35 | https://www.kap.org.tr/tr/Bildirim/1664326 |
| 1664407 | `1664407.html`, `1664407_AKSA_56154.pdf` | AKSA — Akkök Holding A.Ş. (legal entity; signatory Ayberk Büyükbayram, Mali İşler ve Vergi Yönetimi Direktörü), **ALIŞ** 1.750.000 nominal in the 10.41 – 10.69 TL range on 2026-09-16, stake after 40,22 % (40,2186 % in the prose). Relayed by MKK; published 2026-09-17 09:22 | https://www.kap.org.tr/tr/Bildirim/1664407 · `/tr/api/file/download/4028328ca09bee8f01a0ade645751699` |
| — | `byCriteria_share_transactions.json` | The listing rows of the six disclosures above, exactly as `POST /tr/api/disclosure/members/byCriteria` returns them (trimmed to these six): the two PYŞ rows and the four insider rows, so the tests replay both list filters | the byCriteria endpoint, windows 2026-08-24..28 and 2026-09-12..17 |

Recording a new page: instantiate `KapPublicAdapter(pys_only=False)` and save `fetch_detail_html(index)` as
`<index>.html`; for a relayed filing also save `_download(objId)` (objId from `attachments_of(decode_rsc(page))`) as
`<index>_<fileName>`; add the listing row from `list_insider_disclosures` to the JSON; document the row here.
