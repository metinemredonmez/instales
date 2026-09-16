import json
from pathlib import Path

from instilens.domain.schemas import RawDisclosure


class SecFixtureAdapter:
    """Replays canonical SEC_13F disclosures from JSON files (same shape EdgarClient produces)."""

    name = "sec-fixture"

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def fetch(self, since_source_id: str | None = None) -> list[RawDisclosure]:
        items = [RawDisclosure.model_validate(json.loads(p.read_text(encoding="utf-8"))) for p in sorted(self.directory.glob("*.json"))]
        return sorted(items, key=lambda r: r.published_at)
