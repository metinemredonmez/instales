"""Parse a KAP 'Portföy Dağılım Raporu' PDF attachment into a canonical portfolio-report payload.

The report's "III-FON PORTFÖY DEĞERİ TABLOSU" lists every holding; in layout-preserving text each
equity row starts with the ticker and carries: unit price, total value, weights, …, quantity. Column
order is unreliable across PDFs, so we identify the quantity by the invariant quantity × price ≈ value.
Only the equity block (HİSSE SENETLERİ) is taken — bonds, repo, cash are out of scope for the MVP.
"""

from __future__ import annotations

import calendar
import io
import re
from datetime import date, datetime
from decimal import Decimal

import pypdf

MONTHS = {m: i for i, m in enumerate(["Ocak", "Şubat", "Mart", "Nisan", "Mayıs", "Haziran", "Temmuz", "Ağustos", "Eylül", "Ekim", "Kasım", "Aralık"], start=1)}

TICKER_LINE = re.compile(r"^([A-Z][A-Z0-9]{2,5})\s{2,}TL\b(.*)$")
NUM = re.compile(r"-?\d{1,3}(?:\.\d{3})*(?:,\d+)?|-?\d+,\d+")
# Block headers that follow the equity block in the fund-portfolio table.
SECTION_END_PREFIXES = (
    "BORSA PARA", "TERS REPO", "REPO", "DEVLET TAHV", "HAZİNE BONO", "ÖZEL SEKTÖR", "VADELİ", "YATIRIM FONU",
    "BORSA YATIRIM", "MEVDUAT", "KATILMA HESA", "TEMİNAT", "DİĞER", "TAAHHÜTLÜ", "KİRA SERTİF", "BORÇLANMA",
    "OPSİYON", "VARANT", "VAAD", "FİNANSMAN BONO", "DEĞERLİ MADEN", "ALTIN", "TOPLAM", "GENEL TOPLAM", "IV-",
)


def _dec(s: str) -> Decimal:
    return Decimal(s.replace(".", "").replace(",", "."))


def _numbers(rest: str) -> list[Decimal]:
    """Numbers on a row; a 2-decimal figure glued to the next number ("6,14134.359,00") is split."""
    out: list[Decimal] = []
    for tok in rest.split():
        tok = re.sub(r"[^\d.,-]", " ", tok).strip()
        for piece in tok.split():
            if piece.count(",") > 1:
                head, tail = piece[: piece.index(",") + 3], piece[piece.index(",") + 3 :]
                pieces = [head, tail]
            else:
                pieces = [piece]
            for pc in pieces:
                if NUM.fullmatch(pc):
                    out.append(_dec(pc))
    return out


def pdf_bytes(blob: bytes) -> bytes:
    """KAP wraps attachments in a Java-serialized byte array; strip to the %PDF header."""
    i = blob.find(b"%PDF")
    return blob[i:] if i >= 0 else blob


def parse_pdr_pdf(blob: bytes, fund_code: str | None = None, fund_name: str | None = None, fallback_as_of: date | None = None) -> dict:
    """Weekly ("dd.mm.yyyy-dd.mm.yyyy Tarihleri Arası …") and monthly ("Ağustos-2026") report variants.
    Raises ValueError for PDFs without extractable text (scanned) — those need OCR, out of scope."""
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes(blob)))
    plain = "\n".join(p.extract_text() or "" for p in reader.pages)
    layout = "\n".join(p.extract_text(extraction_mode="layout") or "" for p in reader.pages)
    if len(plain.strip()) < 50:
        raise ValueError("no extractable text (scanned PDF)")

    m = re.search(r"^([A-Z0-9]{2,5})-(.+?)$", plain, flags=re.M)
    if m and not fund_code:
        fund_code, fund_name = m.group(1), m.group(2).strip()
    elif m and not fund_name:
        fund_name = m.group(2).strip()
    if not fund_code:
        raise ValueError("fund code not found")

    period_end = fallback_as_of
    w = re.search(r"(\d{2}\.\d{2}\.\d{4})-(\d{2}\.\d{2}\.\d{4}) Tarihleri Arası", plain)
    mo = re.search(r"\b(Ocak|Şubat|Mart|Nisan|Mayıs|Haziran|Temmuz|Ağustos|Eylül|Ekim|Kasım|Aralık)-(\d{4})\b", plain)
    if w:
        period_end = datetime.strptime(w.group(2), "%d.%m.%Y").date()
    elif mo:
        y, mnum = int(mo.group(2)), MONTHS[mo.group(1)]
        period_end = date(y, mnum, calendar.monthrange(y, mnum)[1])
    if period_end is None:
        raise ValueError("report period not found")
    f = re.search(r"Kurucunun Ünvanı\s*:\s*(\S.+)", plain)
    founder = f.group(1).strip() if f else None
    if not founder:
        f2 = re.search(r"([A-ZÇĞİÖŞÜ][A-ZÇĞİÖŞÜ .&-]+PORTFÖY YÖNETİMİ A\.Ş\.)", plain)
        founder = f2.group(1).strip() if f2 else None
    tv = re.search(r"Toplam Değer/Net Varlık Değeri\s*:\s*([\d.]+,\d+)", plain)
    total_value = _dec(tv.group(1)) if tv else None

    holdings: list[dict] = []
    in_block = False
    for line in layout.splitlines():
        stripped = line.strip()
        if "HİSSE SENETLERİ" in stripped:
            in_block = True
            continue
        if in_block and stripped.upper().startswith(SECTION_END_PREFIXES):
            break
        if not in_block:
            continue
        lm = TICKER_LINE.match(line.lstrip())
        if not lm:
            continue
        symbol, rest = lm.group(1), lm.group(2)
        nums = _numbers(rest)
        if len(nums) < 3:
            continue
        price, value = nums[0], nums[1]
        weight = nums[3] if len(nums) > 3 else None  # "TOPLAM (FPD GÖRE)"
        qty = next((n for n in nums[2:] if price and value and abs(n * price - value) <= abs(value) * Decimal("0.02")), None)
        if qty is None or value <= 0:
            continue
        holdings.append({"symbol": symbol, "quantity": int(qty), "market_value": str(value.quantize(Decimal("0.01"))), "weight_pct": str(weight) if weight is not None else None})

    if not holdings:
        raise ValueError("no equity holdings parsed")
    return {
        "fund_code": fund_code, "fund_name": fund_name or fund_code, "member_name": founder, "member_oid": None,
        "as_of": period_end.isoformat(), "total_value": str(total_value) if total_value is not None else None, "holdings": holdings,
    }


def merge_reports(parts: list[dict]) -> dict:
    """Multi-part monthly reports (PHK_2026.08.1.pdf, .2.pdf): union of holdings, first part's header."""
    base = dict(parts[0])
    seen = {h["symbol"] for h in base["holdings"]}
    for extra in parts[1:]:
        for h in extra["holdings"]:
            if h["symbol"] not in seen:
                base["holdings"].append(h)
                seen.add(h["symbol"])
        base["total_value"] = base["total_value"] or extra.get("total_value")
    return base
