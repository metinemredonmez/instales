from instilens.domain.schemas import RawDisclosure
from instilens.ingestion.sec.edgar_client import EdgarClient


class SecEdgarAdapter:
    """Live adapter: pulls the latest 13F-HR filings for a configured list of filer CIKs."""

    name = "sec-edgar"

    def __init__(self, user_agent: str, ciks: list[str], filings_per_filer: int = 4) -> None:
        self.client = EdgarClient(user_agent)
        self.ciks = ciks
        self.limit = filings_per_filer

    def fetch(self, since_source_id: str | None = None) -> list[RawDisclosure]:
        out: list[RawDisclosure] = []
        for cik in self.ciks:
            out.extend(self.client.fetch_filer(cik, self.limit))
        return sorted(out, key=lambda r: r.published_at)
