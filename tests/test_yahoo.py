"""Parsing tests for the Yahoo provider against stubbed yfinance objects (no network)."""
from types import SimpleNamespace

import pandas as pd
import pytest

from screener.providers.base import Market
from screener.providers.yahoo import YahooProvider

COLS = [pd.Timestamp("2025-12-31"), pd.Timestamp("2024-12-31"), pd.Timestamp("2023-12-31")]


def frame(rows: dict) -> pd.DataFrame:
    return pd.DataFrame(rows, index=COLS).T


class StubTicker:
    def __init__(self):
        self.info = {"longName": "Widget plc", "currency": "GBp", "financialCurrency": "EUR",
                     "currentPrice": 250.0, "sharesOutstanding": 40e6, "marketCap": 1e11,
                     "sector": "Industrials", "industry": "Machinery", "country": "United Kingdom",
                     "exchange": "LSE", "longBusinessSummary": "Makes widgets.", "quoteType": "EQUITY",
                     "numberOfAnalystOpinions": 2, "heldPercentInsiders": 0.31}
        self.income_stmt = frame({"Total Revenue": [100, 90, 80], "EBIT": [20, 18, float("nan")],
                                  "Operating Income": [19, 17, 15], "EBITDA": [25, 22, 19],
                                  "Net Income Common Stockholders": [14, 12, 10],
                                  "Pretax Income": [20, 18, 15], "Tax Provision": [5, 4, 3]})
        self.balance_sheet = frame({"Total Debt": [10, 12, 14],
                                    "Cash Cash Equivalents And Short Term Investments": [30, 20, 10],
                                    "Stockholders Equity": [60, 50, 45], "Ordinary Shares Number": [20e6, 20e6, 20e6]})
        self.cashflow = frame({"Operating Cash Flow": [18, 15, 12], "Capital Expenditure": [-4, -3, -3]})
        self.splits = pd.Series([2.0], index=[pd.Timestamp("2025-06-01")])
        self.insider_purchases = pd.DataFrame({
            "Insider Purchases Last 6m": ["Purchases", "Sales", "Net Shares Purchased (Sold)"],
            "Shares": [5000, 1000, 4000], "Trans": [3, 1, 4]})

    def history(self, **kw):
        idx = pd.date_range("2022-01-01", "2026-10-01", freq="7D")
        return pd.DataFrame({"Close": [200.0] * len(idx)}, index=idx)


@pytest.fixture
def provider():
    p = YahooProvider.__new__(YahooProvider)
    p.retries, p.backoff, p.min_interval, p._last_call = 0, 0, 0, 0
    stub = StubTicker()
    p._ticker = lambda symbol: stub
    p._info = lambda symbol: stub.info
    p._history = lambda symbol, years: stub.history()
    p.fx_rate = lambda a, b: {("GBP", "EUR"): 1.2}.get((a, b), 1.0)
    return p


def test_profile_converts_pence(provider):
    prof = provider.profile("WID.L")
    assert prof.currency == "GBP" and prof.price == 2.5
    assert prof.market_cap == pytest.approx(2.5 * 40e6)  # recomputed, not Yahoo's raw figure
    assert provider.analyst_count("WID.L") == 2


def test_financials_rows(provider):
    rows = provider.financials("WID.L", 5)
    assert [r.fiscal_year for r in rows] == [2025, 2024, 2023]
    latest, oldest = rows[0], rows[2]
    assert latest.currency == "EUR" and latest.ebit == 20 and oldest.ebit == 15  # falls back to Operating Income
    assert latest.free_cash_flow == 14  # OCF + capex when FCF row is missing
    assert latest.cash == 30 and latest.total_equity == 60
    # 2.00 GBP close x 20m shares, x2 for the post-period split only for 2024, then GBP->EUR.
    assert latest.market_cap == pytest.approx(2.0 * 20e6 * 1.2)
    assert rows[1].market_cap == pytest.approx(2.0 * 20e6 * 2 * 1.2)


def test_insider_activity(provider):
    act = provider.insider_activity("WID.L")
    assert act.held_pct == 0.31 and act.buys == 3 and act.sells == 1 and act.net_shares == 4000


def test_list_universe_paginates(provider):
    pages = [{"total": 3, "quotes": [{"symbol": "A.L", "longName": "A", "exchange": "LSE", "marketCap": 5e8,
                                      "currency": "GBp", "regularMarketPrice": 100.0, "sharesOutstanding": 1e6},
                                     {"symbol": "B.L", "shortName": "B", "exchange": "LSE", "marketCap": 1e8,
                                      "currency": "GBP"}]},
             {"total": 3, "quotes": [{"symbol": "F.L", "quoteType": "ETF", "exchange": "LSE"}]}]
    seen_offsets = []

    def screen(query, offset, size, sortField, sortAsc):
        seen_offsets.append(offset)
        return pages[len(seen_offsets) - 1]

    provider.yf = SimpleNamespace(EquityQuery=lambda op, args: (op, args), screen=screen)
    listings = provider.list_universe(Market(region="UK", code="gb", exchanges=["LSE"]))
    assert seen_offsets == [0, 2]
    assert [l.symbol for l in listings] == ["A.L", "B.L"]
    assert listings[0].market_cap == pytest.approx(1.0 * 1e6) and listings[0].currency == "GBP"
