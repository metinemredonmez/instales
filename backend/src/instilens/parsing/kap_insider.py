"""KAP 'Pay Alım Satım Bildirimi' of a person / shareholder → NormalizedInsiderFiling.

The adapter hands over the party as filed ("Ad Soyad / Ticaret Ünvanı", the "… tarafından" of the prose) and the
Turkish role text ("Görevi", "Şirketimizin ana ortağı"); this is where both become the vocabulary the insider
tables already use for Form 4: `roles` director / officer / shareholder / other (KAP's counterpart of the Form 4
flags; "shareholder" is what the filing states — a relationship word such as ortağı / pay sahibi / hissedar, or a
stated stake of 5 % or more, the line at which II-15.1 makes a holder file — and asserts no stake size by itself, so
the UI labels it "Pay sahibi", never "%5+"), a stable
`party_key` for a party KAP identifies by name only, and code P for ALIŞ / S for SATIŞ — KAP has no grant,
exercise or withholding codes. Confidence is EXACT throughout: every row is the party's own report of their own
transaction. A company trading its own shares gets `roles = "issuer"` and `is_issuer`: services/insiders lists it
and leaves it out of every buyer / seller count and the cluster.
"""

from __future__ import annotations

import hashlib
import re
from decimal import Decimal

from instilens.domain.enums import Confidence
from instilens.domain.schemas import (
    KapInsiderPayload,
    NormalizedInsiderFiling,
    NormalizedInsiderRow,
    RawDisclosure,
)

SHAREHOLDER_THRESHOLD_PCT = Decimal(5)  # II-15.1 art. 12: crossing 5 % of capital or votes makes a holder file
ISSUER_ROLE = "issuer"  # `roles` of a row the company itself traded (a buyback or a treasury-share sale)
CODE_OF_SIDE = {"ALIS": "P", "SATIS": "S"}
_FOLD = str.maketrans("çğıöşüâîû", "cgiosuaiu")

# Turkish titles as they appear on KAP (the fixtures' "Yönetim Kurulu Başkanı", "GENEL MÜDÜR", "ana ortağı",
# "İmtiyazlı pay sahibi", "Mali İşler ve Vergi Yönetimi Direktörü") plus their usual variants, matched on the folded
# lower-case text. Order matters: a director pattern's span is removed before the officer patterns run, so
# "Yönetim Kurulu Başkan Yardımcısı" (a vice chairman) is a director, not an officer.
ROLE_TITLES: tuple[tuple[str, str], ...] = (
    (r"yonetim kurulu (?:baskan|uye)\w*|yon\.? ?kur\.? ?(?:bsk|baskan|uye)\w*|\byk (?:baskan|uye|bsk)\w*|murahhas (?:uye|aza)|bagimsiz uye|board (?:member|chair)\w*|\bchairman\b|\bdirector\b(?! of)", "director"),
    (r"genel mudur\w*|icra kurulu \w+|icra baskani|\bceo\b|\bcfo\b|\bcoo\b|\bcio\b|\bcto\b|direktor\w*|\bmudur\w*|koordinator\w*|genel sekreter\w*|baskan yardimcisi|grup baskani|chief \w+ officer|\bpresident\b|\bmanager\b|idari sorumlu\w*|yonetici\w*", "officer"),
    (r"ortag\w*|\bortak\b|pay sahibi|hissedar\w*|shareholder|kurucu\w*|\bsahibi\b", "shareholder"),
)


def fold(text: str) -> str:
    """Lower-case ASCII of a Turkish string ("Yönetim Kurulu BAŞKANI" → "yonetim kurulu baskani"): Python's
    lower() turns "İ" into "i̇" (i + combining dot), which no pattern would match, so the dotted letters go first."""
    return re.sub(r"\s+", " ", text.replace("İ", "i").replace("I", "i").lower().translate(_FOLD)).strip()


def party_key(name: str) -> str:
    """A stable 10-character key for a party KAP names but never numbers: "k" + 9 hex of the folded name, so
    "MEHMET SÖNMEZ" on the form and "Mehmet Sönmez" on the page are one insider, and no key ever looks like a CIK."""
    folded = re.sub(r"[^a-z0-9]", "", fold(re.sub(r"\([^)]*\)", " ", name)))
    return "k" + hashlib.sha1(folded.encode()).hexdigest()[:9]


def roles_from_title(text: str | None) -> list[str]:
    """The roles a Turkish title carries, in the vocabulary order director, officer, shareholder."""
    remaining = fold(text or "")
    out: list[str] = []
    for pattern, role in ROLE_TITLES:
        if re.search(pattern, remaining):
            out.append(role)
            remaining = re.sub(pattern, " ", remaining)
    return out


def roles_of(payload: KapInsiderPayload) -> str:
    """`roles` of the filing's rows: "issuer" for the company's own shares; for a person the title's roles plus
    shareholder from a stated stake of SHAREHOLDER_THRESHOLD_PCT or more; for a legal entity only the relationship
    words of the prose ("ana ortağı") and the stake — the form's "Görevi" is the signatory's job, not the entity's
    relationship; "other" when nothing else is known."""
    if payload.party_is_issuer:
        return ISSUER_ROLE
    roles = roles_from_title(payload.role_text)
    if payload.post_pct_stake is not None and payload.post_pct_stake >= SHAREHOLDER_THRESHOLD_PCT and "shareholder" not in roles:
        roles.append("shareholder")
    return ",".join(roles) if roles else "other"


def parse_insider_filing(raw: RawDisclosure) -> NormalizedInsiderFiling:
    """Validate the adapter's payload (a filing whose party or numbers could not be read fails here, with the
    reason) and map it onto the stored vocabulary."""
    payload = KapInsiderPayload.model_validate(raw.payload)
    rows = [
        NormalizedInsiderRow(
            transaction_date=r.transaction_date, code=CODE_OF_SIDE[r.side], nominal=r.nominal,
            price=r.price, price_low=r.price_low, price_high=r.price_high, post_pct_stake=r.post_pct_stake,
        )
        for r in payload.rows
    ]
    person = payload.party_kind == "person" and not payload.party_is_issuer
    return NormalizedInsiderFiling(
        market=raw.market, source=raw.source, source_id=raw.source_id,
        subject_symbol=payload.subject_symbol.strip().upper(), subject_name=payload.subject_name,
        party_name=payload.party_name.strip(), party_key=party_key(payload.party_name),
        party_kind=payload.party_kind, roles=roles_of(payload), title=(payload.role_text or None) if person else None,
        is_issuer=payload.party_is_issuer, rows=rows, post_pct_stake=payload.post_pct_stake,
        published_at=raw.published_at, is_correction=payload.is_correction, amends_source_id=payload.amends_source_id,
        confidence=Confidence.EXACT,
    )
