import pytest

from screener import metrics as M
from screener.providers.base import Profile

from .conftest import fin, make_stock


def test_roic_uses_after_tax_ebit_over_invested_capital():
    f = fin(2025, ebit=20e6, debt=30e6, cash=10e6, equity=80e6)
    assert M.roic(f) == pytest.approx(20e6 * 0.75 / 100e6)


def test_roic_none_when_invested_capital_not_positive():
    assert M.roic(fin(2025, debt=0, cash=100e6, equity=50e6)) is None


def test_tax_rate_falls_back_on_odd_values():
    f = fin(2025)
    f.income_tax, f.pretax_income = 5e6, -1e6
    assert M.tax_rate(f) == M.DEFAULT_TAX_RATE


@pytest.mark.parametrize("nd, ebitda, expected", [
    (10, 5, 2.0), (-10, 5, -2.0), (0, 5, 0.0), (10, -5, float("inf")), (-10, -5, -1.0), (None, 5, None),
])
def test_net_debt_to_ebitda(nd, ebitda, expected):
    assert M.net_debt_to_ebitda(nd, ebitda) == expected


def test_ev_ebit_and_fcf_yield_guard_bad_inputs():
    assert M.ev_ebit(100, 10) == 10
    assert M.ev_ebit(100, 0) is None and M.ev_ebit(-5, 10) is None
    assert M.fcf_yield(5, 100) == 0.05 and M.fcf_yield(5, 0) is None


def _profile(mcap=200e6, ccy="GBP"):
    return Profile(symbol="X", name="X", exchange="LSE", country="GB", sector="Industrials", industry=None,
                   currency=ccy, price=1, market_cap=mcap)


def test_compute_current_vs_history_and_fx():
    # Reporting in EUR, trading in GBP at 1.15 EUR/GBP.
    fins = [fin(2025, ebit=20e6, fcf=12e6, debt=10e6, cash=30e6)] + \
           [fin(y, ebit=15e6, fcf=9e6, mcap=180e6) for y in (2024, 2023, 2022, 2021)]
    m = M.compute(_profile(mcap=200e6), "UK", fins, analysts=2, insider=None,
                  fx_to_eur=1.15, fx_to_reporting=1.15, min_history_years=3)
    assert m.market_cap_eur == pytest.approx(230e6)
    assert m.enterprise_value == pytest.approx(230e6 - 20e6)
    assert m.ev_ebit == pytest.approx(210e6 / 20e6)
    assert m.fcf_yield == pytest.approx(12e6 / 230e6)
    assert m.ev_ebit_hist_median == pytest.approx((180e6 + 5e6) / 15e6)
    assert m.fcf_yield_hist_median == pytest.approx(9e6 / 180e6)
    assert m.history_years == 4 and len(m.years) == 5


def test_compute_requires_min_history():
    fins = [fin(2025), fin(2024), fin(2023)]
    m = M.compute(_profile(), "UK", fins, 0, None, 1.0, 1.0, min_history_years=3)
    assert m.ev_ebit_hist_median is None and m.history_years == 2


def test_sector_stats_need_min_peers():
    stocks = [make_stock(symbol=str(i), ev_ebit=float(i)) for i in (6, 8, 10)] + \
             [make_stock(symbol="t", sector="Technology")]
    stats = M.sector_stats(stocks, min_peers=3)
    assert stats["Industrials"].ev_ebit_median == 8.0
    assert stats["Technology"].ev_ebit_median is None
