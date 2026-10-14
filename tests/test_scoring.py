import pytest

from screener.config import deep_merge
from screener.metrics import SectorStats
from screener.scoring import components, composite, discount, evaluate, linear

from .conftest import make_stock

SECTOR = SectorStats(ev_ebit_median=12.0, fcf_yield_median=0.045, peers=10)


def test_linear_clips_and_scales():
    assert linear(5, 0, 10) == 0.5
    assert linear(-1, 0, 10) == 0.0
    assert linear(20, 0, 10) == 1.0
    assert linear(3, 3, 3) == 1.0


def test_discount():
    assert discount(8, 10) == pytest.approx(0.2)
    assert discount(12, 10) == pytest.approx(-0.2)
    assert discount(None, 10) is None
    assert discount(8, None) is None


def test_good_stock_passes_with_reasons(cfg):
    e = evaluate(make_stock(), SECTOR, cfg)
    assert e.passed, e.failures
    text = " | ".join(e.reasons)
    assert "1 analyst (≤3)" in text
    assert "ROIC 18.0% > 10.0%" in text
    assert "Net debt/EBITDA 0.2x" in text
    assert "vs own median 11.0x" in text and "vs sector median 12.0x" in text
    assert "FCF yield 7.0% vs own median 5.0%, sector 4.5%" in text
    assert "Insiders own 20.0%" in text
    assert "Insider net buying" in text
    assert 0 < e.score <= 100


def test_zero_coverage_reason(cfg):
    assert "No analyst coverage" in evaluate(make_stock(analysts=0), SECTOR, cfg).reasons


@pytest.mark.parametrize("change, failure", [
    ({"analysts": 4}, "4 analysts > 3"),
    ({"net_income": -1e6, "free_cash_flow": -2e6}, "Loss-making and FCF negative"),
    ({"roic": 0.10}, "ROIC 10.0% ≤ 10.0%"),
    ({"roic": None}, "ROIC unavailable"),
    ({"net_debt_to_ebitda": 2.0}, "Net debt/EBITDA 2.0x ≥ 2.0x"),
    ({"net_debt_to_ebitda": float("inf")}, "Net debt with negative EBITDA"),
    ({"net_debt_to_ebitda": None}, "Net debt/EBITDA unavailable"),
])
def test_hard_filters(cfg, change, failure):
    e = evaluate(make_stock(**change), SECTOR, cfg)
    assert not e.passed
    assert any(f.startswith(failure) for f in e.failures), e.failures


def test_profitable_or_fcf_positive_is_an_or(cfg):
    assert evaluate(make_stock(net_income=-1e6, free_cash_flow=5e6), SECTOR, cfg).passed
    assert evaluate(make_stock(net_income=5e6, free_cash_flow=-1e6), SECTOR, cfg).passed


def test_net_cash_passes_and_maxes_balance_sheet(cfg):
    e = evaluate(make_stock(net_debt_to_ebitda=-0.5), SECTOR, cfg)
    assert e.passed and "Net cash" in e.reasons
    assert e.components["balance_sheet"] == 1.0


def test_filters_can_be_disabled(cfg):
    cfg = deep_merge(cfg, {"filters": {"max_analysts": None, "min_roic": None}})
    assert evaluate(make_stock(analysts=12, roic=None), SECTOR, cfg).passed


def test_optional_valuation_gates(cfg):
    cfg = deep_merge(cfg, {"filters": {"max_ev_ebit_vs_history": 1.0, "max_ev_ebit_vs_sector": 1.0,
                                       "min_fcf_yield": 0.06}})
    assert evaluate(make_stock(), SECTOR, cfg).passed
    expensive = evaluate(make_stock(ev_ebit=15.0, fcf_yield=0.03), SECTOR, cfg)
    assert {f.split(" ")[0] for f in expensive.failures} == {"EV/EBIT", "FCF"}
    assert len(expensive.failures) == 3
    no_hist = evaluate(make_stock(ev_ebit_hist_median=None), SECTOR, cfg)
    assert "No EV/EBIT history" in no_hist.failures


def test_require_insider_buying(cfg):
    cfg = deep_merge(cfg, {"filters": {"require_insider_buying": True}})
    assert evaluate(make_stock(), SECTOR, cfg).passed
    assert not evaluate(make_stock(insider_net_shares=-100), SECTOR, cfg).passed


def test_coverage_component_rewards_fewer_analysts(cfg):
    scores = [components(make_stock(analysts=n), SECTOR, cfg)["coverage"] for n in range(4)]
    assert scores == sorted(scores, reverse=True)
    assert scores[0] == 1.0


def test_missing_components_are_reweighted_not_penalised(cfg):
    weights = cfg["scoring"]["weights"]
    assert composite({"coverage": 1.0, "quality": 0.5}, weights) == pytest.approx(
        100 * (0.25 * 1.0 + 0.20 * 0.5) / 0.45, abs=0.05)
    assert composite({}, weights) is None
    # A stock without insider data isn't scored as if insiders were bad.
    full = evaluate(make_stock(), SECTOR, cfg)
    no_insider = evaluate(make_stock(insider_held_pct=None, insider_buys=0, insider_sells=0,
                                     insider_net_shares=None), SECTOR, cfg)
    assert "insider" not in no_insider.components
    assert no_insider.score is not None and abs(no_insider.score - full.score) < 15


def test_cheaper_better_quality_ranks_higher(cfg):
    good = evaluate(make_stock(analysts=0, roic=0.30, ev_ebit=6.0, fcf_yield=0.10), SECTOR, cfg)
    mid = evaluate(make_stock(), SECTOR, cfg)
    weak = evaluate(make_stock(analysts=3, roic=0.11, ev_ebit=14.0, fcf_yield=0.03,
                               net_debt_to_ebitda=1.8, insider_net_shares=-1000), SECTOR, cfg)
    assert good.score > mid.score > weak.score


def test_valuation_without_sector_peers(cfg):
    e = evaluate(make_stock(), None, cfg)
    assert e.passed
    assert "value_vs_sector" not in e.components


def test_weights_are_configurable(cfg):
    only_value = deep_merge(cfg, {"scoring": {"weights": {"value_vs_history": 1.0}}})
    cheap = evaluate(make_stock(ev_ebit=5.5, fcf_yield=0.10), SECTOR, only_value)
    assert cheap.score == 100.0
