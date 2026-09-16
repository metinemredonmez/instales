from typing import Protocol

from instilens.domain.schemas import RawDisclosure


class SourceAdapter(Protocol):
    """Anything that can yield regulatory disclosures. One per (market, source)."""

    name: str

    def fetch(self, since_source_id: str | None = None) -> list[RawDisclosure]:
        """Return disclosures newer than `since_source_id`, oldest first."""
        ...
