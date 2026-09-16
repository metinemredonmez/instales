from datetime import UTC, datetime

from instilens.domain.models import NewsItem, NewsRule
from instilens.services.news_rules import apply_rules, rule_matches, seed_default_rules


def _rule(**kw):
    base = {"name": "r", "market_code": "TR", "query": "", "exclusion": "", "only_sources": "", "remove_sources": "", "max_age_days": 3, "symbols": []}
    base.update(kw)
    return NewsRule(**base)


def test_rule_matching_semantics():
    now = datetime.now(UTC).replace(tzinfo=None)
    r = _rule(query="banka*, faiz+artış", exclusion="magazin", only_sources="AA Ekonomi")
    assert rule_matches(r, "Merkez Bankası faiz kararını açıkladı", "AA Ekonomi", now)          # 'banka' as substring at word start... "Bankası" starts with banka → match
    assert rule_matches(r, "Faiz artış beklentisi güçlendi", "AA Ekonomi", now)                 # a+b both present
    assert not rule_matches(r, "Faiz beklentisi", "AA Ekonomi", now)                            # only one of a+b
    assert not rule_matches(r, "Banka hisseleri magazin gündeminde", "AA Ekonomi", now)         # exclusion
    assert not rule_matches(r, "Banka hisseleri yükseldi", "Dünya", now)                        # source filter
    assert not rule_matches(_rule(query="kap"), "Proje kapsamında açıklama", "AA Ekonomi", now)   # whole word, no wildcard


def test_apply_rules_tags_and_symbols(session):
    from instilens.services.entities import EntityResolver

    EntityResolver(session).ensure_markets()
    assert seed_default_rules(session) == 6
    item = NewsItem(market_code="TR", source="Bloomberg HT", title="Aselsan savunma ihracatında rekor kırdı", url="https://x/1", published_at=datetime.now(UTC).replace(tzinfo=None), symbols=[], tags=[])
    apply_rules(session, item)
    assert "Savunma" in item.tags and "ASELS" in item.symbols
