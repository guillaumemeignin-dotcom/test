from __future__ import annotations

import copy
from datetime import date, timedelta

import pytest

from screener.config import DEFAULTS, deep_merge
from screener.metrics import StockMetrics
from screener.providers.base import (AnnualFinancials, DataProvider, InsiderActivity, Listing, Market,
                                     PricePoint, Profile)


@pytest.fixture
def cfg():
    return copy.deepcopy(DEFAULTS)


def make_stock(**kw) -> StockMetrics:
    base = dict(symbol="TEST", name="Test Co", region="UK", exchange="LSE", country="GB",
                sector="Industrials", industry="Machinery", currency="GBP", reporting_currency="GBP",
                description="", website=None, market_cap_eur=200e6, analysts=1, net_income=10e6,
                free_cash_flow=12e6, roic=0.18, net_debt=5e6, ebitda=20e6, net_debt_to_ebitda=0.25,
                ev_ebit=8.0, fcf_yield=0.07, ev_ebit_hist_median=11.0, fcf_yield_hist_median=0.05,
                insider_held_pct=0.2, insider_buys=2, insider_sells=0, insider_net_shares=10_000)
    base.update(kw)
    return StockMetrics(**base)


def fin(year, *, revenue=100e6, ebit=15e6, ebitda=20e6, net_income=11e6, fcf=10e6, debt=10e6,
        cash=5e6, equity=60e6, mcap=150e6, ccy="EUR") -> AnnualFinancials:
    return AnnualFinancials(
        fiscal_year=year, period_end=date(year, 12, 31), currency=ccy, revenue=revenue, ebit=ebit,
        ebitda=ebitda, net_income=net_income, pretax_income=ebit, income_tax=ebit * 0.25,
        operating_cash_flow=fcf + 3e6, capex=-3e6, free_cash_flow=fcf, total_debt=debt, cash=cash,
        total_equity=equity, minority_interest=0, market_cap=mcap)


class FakeProvider(DataProvider):
    """In-memory provider: proves the pipeline only depends on the DataProvider interface."""
    name = "fake"

    def __init__(self, stocks: dict[str, dict]):
        self.stocks = stocks
        self.calls: list[tuple] = []

    def list_universe(self, market: Market):
        self.calls.append(("universe", market.code))
        return [Listing(symbol=s, name=d["profile"].name, exchange=d["profile"].exchange,
                        market_cap=d["profile"].market_cap, currency=d["profile"].currency)
                for s, d in self.stocks.items() if d["market"] == market.code]

    def profile(self, symbol):
        self.calls.append(("profile", symbol))
        return self.stocks[symbol]["profile"]

    def analyst_count(self, symbol):
        self.calls.append(("analysts", symbol))
        return self.stocks[symbol]["analysts"]

    def financials(self, symbol, years):
        self.calls.append(("financials", symbol))
        return self.stocks[symbol]["financials"][:years]

    def insider_activity(self, symbol):
        return self.stocks[symbol].get("insider")

    def price_history(self, symbol, years):
        start = date(2021, 1, 1)
        return [PricePoint(start + timedelta(days=7 * i), 10 + i * 0.1) for i in range(52 * years)]

    def fx_rate(self, a, b):
        rates = {"EUR": 1.0, "GBP": 1.15, "USD": 0.92}
        return rates[a] / rates[b]


def fake_stock(symbol, market="gb", ccy="GBP", mcap=200e6, analysts=1, sector="Industrials",
               financials=None, insider=None):
    return {
        "market": market,
        "analysts": analysts,
        "profile": Profile(symbol=symbol, name=f"{symbol} plc", exchange="LSE", country="GB", sector=sector,
                           industry="Machinery", currency=ccy, price=1.0, market_cap=mcap,
                           description=f"{symbol} makes things."),
        "financials": financials if financials is not None else [fin(y, ccy=ccy) for y in range(2025, 2020, -1)],
        "insider": insider or InsiderActivity(held_pct=0.15, buys=1, sells=0, net_shares=5000),
    }


@pytest.fixture
def fake_cfg(cfg, tmp_path):
    return deep_merge(cfg, {
        "output_dir": str(tmp_path / "out"),
        "workers": 2,
        "universe": {"regions": {"UK": {"gb": ["LSE"]}, "US": {"us": []}},
                     "exclude_sectors": ["Financial Services"]},
        "valuation": {"min_sector_peers": 2},
    })
