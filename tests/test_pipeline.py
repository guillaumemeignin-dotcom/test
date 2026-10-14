import csv
from datetime import date

from screener.cache import DiskCache
from screener.pipeline import run
from screener.providers.cached import CachedProvider
from screener.report import write_reports

from .conftest import FakeProvider, fake_stock, fin


def universe():
    return {
        "GOOD.L": fake_stock("GOOD.L", analysts=0),
        "OK.L": fake_stock("OK.L", analysts=2, financials=[fin(y, ccy="GBP", ebit=12e6, mcap=200e6)
                                                           for y in range(2025, 2020, -1)]),
        "COVERED.L": fake_stock("COVERED.L", analysts=9),
        "BIG.L": fake_stock("BIG.L", mcap=5e9),
        "TINY.L": fake_stock("TINY.L", mcap=1e6),
        "BANK.L": fake_stock("BANK.L", sector="Financial Services"),
        "LEVER.L": fake_stock("LEVER.L", financials=[fin(y, ccy="GBP", debt=200e6) for y in range(2025, 2020, -1)]),
        "USCO": fake_stock("USCO", market="us", ccy="USD", mcap=300e6),
    }


def test_pipeline_filters_and_ranks(fake_cfg):
    provider = FakeProvider(universe())
    result = run(provider, fake_cfg)
    passed = [r.metrics.symbol for r in result.passed]
    assert set(passed) == {"GOOD.L", "OK.L", "USCO"}
    assert passed[0] == "GOOD.L"
    assert [r.metrics.symbol for r in result.failed] == ["LEVER.L"]
    assert result.rejected == {"analyst coverage": 1, "excluded sector": 1}
    # Out-of-range market caps are dropped from the listing data, before any per-stock calls.
    assert not any(c[1] in ("BIG.L", "TINY.L") for c in provider.calls if c[0] != "universe")
    # Heavily covered names never trigger the expensive financials fetch.
    assert ("financials", "COVERED.L") not in provider.calls


def test_reports_and_new_entrants(fake_cfg):
    stocks = universe()
    write_reports(run(FakeProvider(stocks), fake_cfg), FakeProvider(stocks), fake_cfg, date(2026, 10, 1))
    out = fake_cfg["output_dir"]
    first = f"{out}/2026-10-01"
    rows = list(csv.DictReader(open(f"{first}/screen.csv")))
    assert [r["symbol"] for r in rows][0] == "GOOD.L"
    assert rows[0]["reasons"] and float(rows[0]["score"]) > 0
    assert list(csv.DictReader(open(f"{first}/new_entrants.csv"))) == []  # baseline run
    html = open(f"{first}/screen.html").read()
    assert "First run" in html and "GOOD.L" in html
    tear = open(f"{first}/tearsheets/GOOD.L.html").read()
    assert "GOOD.L makes things." in tear and "data:image/png;base64," in tear

    stocks["NEW.L"] = fake_stock("NEW.L", analysts=1)
    del stocks["OK.L"]
    write_reports(run(FakeProvider(stocks), fake_cfg), FakeProvider(stocks), fake_cfg, date(2026, 10, 8))
    new = list(csv.DictReader(open(f"{out}/2026-10-08/new_entrants.csv")))
    assert [r["symbol"] for r in new] == ["NEW.L"]
    assert "NEW" in open(f"{out}/2026-10-08/screen.html").read()


def test_cached_provider_makes_reruns_cheap(fake_cfg, tmp_path):
    inner = FakeProvider(universe())
    cache = DiskCache(tmp_path / "cache")
    first = run(CachedProvider(inner, cache), fake_cfg)
    n_calls = len(inner.calls)
    second = run(CachedProvider(inner, cache), fake_cfg)
    assert len(inner.calls) == n_calls  # everything served from disk
    assert [r.metrics.symbol for r in first.passed] == [r.metrics.symbol for r in second.passed]
    assert [r.evaluation.score for r in first.passed] == [r.evaluation.score for r in second.passed]

    run(CachedProvider(inner, DiskCache(tmp_path / "cache", refresh=True)), fake_cfg)
    assert len(inner.calls) > n_calls
