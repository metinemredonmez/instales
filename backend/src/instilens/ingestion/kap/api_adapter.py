"""Official KAP Veri Yayın Servisi adapter (production).

Access requires a Borsa İstanbul data-distribution agreement, an API key and IP whitelisting.
The endpoint names below (disclosures, disclosureDetail, lastDisclosureIndex, ...) follow the
public service description; the exact request/response fields MUST be verified against the
contract documentation before this adapter is enabled. Until then `INSTILENS_KAP_ADAPTER=fixture`.

Responsibility split: this class does transport + mapping to the canonical payload only.
No business logic here — that belongs to `instilens.parsing`.
"""

from datetime import datetime

import httpx

from instilens.domain.enums import DisclosureKind, Market, Source
from instilens.domain.schemas import RawDisclosure

# KAP disclosure template names → our kinds. Anything else is stored as UNSUPPORTED and kept raw.
TEMPLATE_KINDS: dict[str, DisclosureKind] = {
    "Pay Alım Satım Bildirimi": DisclosureKind.KAP_SHARE_TRANSACTION,
    "Portföy Dağılım Raporu": DisclosureKind.KAP_PORTFOLIO_REPORT,
}


class KapApiAdapter:
    name = "kap-api"

    def __init__(self, base_url: str, api_key: str, client: httpx.Client | None = None) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(
            base_url=self.base_url, headers={"Authorization": f"Bearer {api_key}"}, timeout=30
        )

    def fetch(self, since_source_id: str | None = None) -> list[RawDisclosure]:
        params = {"fromIndex": since_source_id} if since_source_id else {}
        listing = self.client.get("/disclosures", params=params).raise_for_status().json()
        out: list[RawDisclosure] = []
        for item in listing:
            kind = TEMPLATE_KINDS.get(item.get("templateName", ""))
            if kind is None:
                continue
            detail = (
                self.client.get(f"/disclosureDetail/{item['disclosureIndex']}")
                .raise_for_status()
                .json()
            )
            out.append(
                RawDisclosure(
                    market=Market.TR,
                    source=Source.KAP,
                    source_id=str(item["disclosureIndex"]),
                    kind=kind,
                    published_at=datetime.fromisoformat(item["publishDate"]),
                    raw_uri=f"{self.base_url}/disclosureDetail/{item['disclosureIndex']}",
                    payload=self.map_detail(kind, item, detail),
                )
            )
        return out

    @staticmethod
    def map_detail(kind: DisclosureKind, item: dict, detail: dict) -> dict:
        """Map vendor JSON → canonical payload. Field names are placeholders pending the contract."""
        raise NotImplementedError(
            "KAP API field mapping is not verified yet; use the fixture adapter. "
            "See docs/02-architecture.md#kap-integration."
        )
