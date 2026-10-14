import json

from screener.cache import DiskCache
from screener.config import load_config, markets
from screener.providers.yahoo import normalise_currency


def test_cache_roundtrip_and_ttl(tmp_path):
    c = DiskCache(tmp_path)
    k = c.key("p", "profile", {"symbol": "X"})
    assert c.get(k, 60) is None
    c.set(k, {"a": 1})
    assert c.get(k, 60) == {"a": 1}
    assert c.get(k, -1) is None
    assert DiskCache(tmp_path, refresh=True).get(k, 60) is None
    assert DiskCache(tmp_path, enabled=False).get(k, 60) is None


def test_secrets_never_reach_the_cache(tmp_path):
    c = DiskCache(tmp_path)
    k1 = c.key("p", "e", {"symbol": "X", "apikey": "SECRET"})
    assert k1 == c.key("p", "e", {"symbol": "X", "apikey": "OTHER"})
    c.set(k1, {"ok": True})
    for f in tmp_path.rglob("*.json"):
        assert "SECRET" not in f.read_text()


def test_minor_currency_units():
    assert normalise_currency("GBp") == ("GBP", 0.01)
    assert normalise_currency("eur") == ("EUR", 1.0)


def test_repo_config_loads():
    cfg = load_config("screen.yaml")
    assert cfg["filters"]["max_analysts"] == 3
    assert cfg["universe"]["market_cap_eur"] == {"min": 10_000_000, "max": 1_000_000_000}
    regions = {m.region for m in markets(cfg)}
    assert regions == {"US", "UK", "Canada", "Australia", "Japan", "Europe", "LatAm"}
    assert abs(sum(cfg["scoring"]["weights"].values()) - 1.0) < 1e-9
    json.dumps(cfg)  # plain data only


def test_unquoted_yaml_country_code_is_rejected(cfg):
    import pytest
    cfg["universe"]["regions"] = {"Europe": {False: ["OSL"]}}
    with pytest.raises(ValueError, match="quote it"):
        markets(cfg)
