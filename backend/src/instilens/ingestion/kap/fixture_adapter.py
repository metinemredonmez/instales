"""Prototype adapter: reads canonical disclosure JSON files from a directory.

File name convention: `<source_id>.json`. Payload must already be in canonical shape
(see domain/schemas.py). This is what we use until the official KAP API contract is signed;
it is also the replay mechanism for tests and backfills.
"""

import json
from pathlib import Path

from instilens.domain.schemas import RawDisclosure


class KapFixtureAdapter:
    name = "kap-fixture"

    def __init__(self, directory: Path) -> None:
        self.directory = directory

    def fetch(self, since_source_id: str | None = None) -> list[RawDisclosure]:
        items: list[RawDisclosure] = []
        for path in sorted(self.directory.glob("*.json")):
            raw = RawDisclosure.model_validate(json.loads(path.read_text(encoding="utf-8")))
            if since_source_id is not None and _numeric(raw.source_id) <= _numeric(since_source_id):
                continue
            items.append(raw)
        items.sort(key=lambda r: (_numeric(r.source_id), r.published_at))
        return items


def _numeric(source_id: str) -> int:
    return int(source_id) if source_id.isdigit() else 0
