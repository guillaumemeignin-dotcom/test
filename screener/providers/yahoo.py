"""Yahoo Finance provider (via the ``yfinance`` package). Free, no API key.

Yahoo is an unofficial source: field coverage varies by market and it rate-limits
aggressively, so every call degrades to ``None``/empty rather than raising.
"""
from __future__ import annotations

import logging
import math
import time
from datetime import date, datetime, timedelta
from functools import lru_cache

from .base import (AnnualFinancials, DataProvider, InsiderActivity, Listing, Market,
                   PricePoint, Profile)

log = logging.getLogger(__name__)

# Currencies Yahoo quotes in minor units: price is in pence/cents, we want major units.
MINOR_UNITS = {"GBp": "GBP", "GBX": "GBP", "ZAc": "ZAR", "ILA": "ILS"}
PAGE_SIZE = 250


def normalise_currency(ccy: str | None) -> tuple[str | None, float]:
    """Return (major currency, factor to multiply minor-unit prices by)."""
    if ccy in MINOR_UNITS:
        return MINOR_UNITS[ccy], 0.01
    return (ccy.upper() if ccy else None), 1.0


def _num(value) -> float | None:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def _row(frame, col, *names) -> float | None:
    for name in names:
        if name in frame.index:
            v = _num(frame.at[name, col])
            if v is not None:
                return v
    return None


class YahooProvider(DataProvider):
    name = "yahoo"

    def __init__(self, retries: int = 4, backoff_seconds: float = 2.0, min_interval: float = 0.0):
        import yfinance  # imported lazily so tests/other providers don't need it
        self.yf = yfinance
        self.retries = retries
        self.backoff = backoff_seconds
        self.min_interval = min_interval
        self._last_call = 0.0

    # -- plumbing ---------------------------------------------------------
    def _call(self, what: str, fn):
        for attempt in range(self.retries + 1):
            wait = self.min_interval - (time.monotonic() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.monotonic()
            try:
                return fn()
            except Exception as exc:  # yfinance raises a zoo of exception types
                rate_limited = "rate" in type(exc).__name__.lower() or "429" in str(exc)
                if attempt < self.retries and rate_limited:
                    time.sleep(self.backoff * 2 ** attempt)
                    continue
                log.debug("yahoo %s failed: %s", what, exc)
                return None
        return None

    @lru_cache(maxsize=4096)
    def _ticker(self, symbol: str):
        return self.yf.Ticker(symbol)

    @lru_cache(maxsize=4096)
    def _info(self, symbol: str) -> dict:
        return self._call(f"info {symbol}", lambda: self._ticker(symbol).info) or {}

    # -- interface --------------------------------------------------------
    def list_universe(self, market: Market) -> list[Listing]:
        EQ = self.yf.EquityQuery
        clauses = [EQ("eq", ["region", market.code]), EQ("gt", ["intradaymarketcap", 0])]
        if market.exchanges:
            clauses.append(EQ("is-in", ["exchange", *market.exchanges]))
        query = EQ("and", clauses)
        out: list[Listing] = []
        offset = 0
        while True:
            page = self._call(f"screen {market.code}@{offset}", lambda: self.yf.screen(
                query, offset=offset, size=PAGE_SIZE, sortField="ticker", sortAsc=True))
            quotes = (page or {}).get("quotes") or []
            for q in quotes:
                if q.get("quoteType", "EQUITY") != "EQUITY":
                    continue
                ccy, factor = normalise_currency(q.get("currency"))
                mcap = _num(q.get("marketCap"))
                price, shares = _num(q.get("regularMarketPrice")), _num(q.get("sharesOutstanding"))
                if factor != 1.0 and price and shares:
                    mcap = price * factor * shares
                out.append(Listing(symbol=q["symbol"], name=q.get("longName") or q.get("shortName") or q["symbol"],
                                   exchange=q.get("exchange", ""), country=market.code.upper(),
                                   market_cap=mcap, currency=ccy))
            offset += len(quotes)
            if not quotes or offset >= (page or {}).get("total", 0):
                break
        return out

    def profile(self, symbol: str) -> Profile | None:
        info = self._info(symbol)
        if not info:
            return None
        ccy, factor = normalise_currency(info.get("currency"))
        price = _num(info.get("currentPrice") or info.get("regularMarketPrice"))
        price = price * factor if price is not None else None
        shares = _num(info.get("sharesOutstanding"))
        mcap = _num(info.get("marketCap"))
        if factor != 1.0 and price and shares:
            mcap = price * shares
        return Profile(
            symbol=symbol, name=info.get("longName") or info.get("shortName") or symbol,
            exchange=info.get("exchange", ""), country=info.get("country"),
            sector=info.get("sector"), industry=info.get("industry"), currency=ccy or "USD",
            price=price, market_cap=mcap, description=info.get("longBusinessSummary") or "",
            website=info.get("website"), is_fund=info.get("quoteType", "EQUITY") != "EQUITY",
        )

    def analyst_count(self, symbol: str) -> int | None:
        info = self._info(symbol)
        if not info:
            return None
        n = _num(info.get("numberOfAnalystOpinions"))
        if n is None:
            est = self._call(f"estimates {symbol}", lambda: self._ticker(symbol).earnings_estimate)
            if est is not None and not est.empty and "numberOfAnalysts" in est.columns:
                n = _num(est["numberOfAnalysts"].max())
        return int(n or 0)

    def financials(self, symbol: str, years: int) -> list[AnnualFinancials]:
        t = self._ticker(symbol)
        inc = self._call(f"income {symbol}", lambda: t.income_stmt)
        bal = self._call(f"balance {symbol}", lambda: t.balance_sheet)
        cf = self._call(f"cashflow {symbol}", lambda: t.cashflow)
        if inc is None or inc.empty:
            return []
        info = self._info(symbol)
        reporting = (info.get("financialCurrency") or info.get("currency") or "USD").upper()
        trading, _ = normalise_currency(info.get("currency"))
        fx = self.fx_rate(trading, reporting) if trading and trading != reporting else 1.0
        prices = self.price_history(symbol, years + 1)
        splits = self._call(f"splits {symbol}", lambda: t.splits)

        rows: list[AnnualFinancials] = []
        for col in list(inc.columns)[:years]:
            end = col.date() if hasattr(col, "date") else col
            b = bal if bal is not None and col in getattr(bal, "columns", []) else None
            c = cf if cf is not None and col in getattr(cf, "columns", []) else None
            ocf = _row(c, col, "Operating Cash Flow") if c is not None else None
            capex = _row(c, col, "Capital Expenditure") if c is not None else None
            fcf = _row(c, col, "Free Cash Flow") if c is not None else None
            if fcf is None and ocf is not None and capex is not None:
                fcf = ocf + capex
            shares = _row(b, col, "Ordinary Shares Number", "Share Issued") if b is not None else None
            mcap = self._market_cap_at(end, shares, prices, splits)
            rows.append(AnnualFinancials(
                fiscal_year=end.year, period_end=end, currency=reporting,
                revenue=_row(inc, col, "Total Revenue", "Operating Revenue"),
                ebit=_row(inc, col, "EBIT", "Operating Income"),
                ebitda=_row(inc, col, "EBITDA", "Normalized EBITDA"),
                net_income=_row(inc, col, "Net Income Common Stockholders", "Net Income"),
                pretax_income=_row(inc, col, "Pretax Income"),
                income_tax=_row(inc, col, "Tax Provision"),
                operating_cash_flow=ocf, capex=capex, free_cash_flow=fcf,
                total_debt=_row(b, col, "Total Debt") if b is not None else None,
                cash=_row(b, col, "Cash Cash Equivalents And Short Term Investments",
                          "Cash And Cash Equivalents") if b is not None else None,
                total_equity=_row(b, col, "Stockholders Equity", "Common Stock Equity") if b is not None else None,
                minority_interest=_row(b, col, "Minority Interest") if b is not None else None,
                market_cap=mcap * fx if mcap is not None and fx else None,
            ))
        return rows

    @staticmethod
    def _market_cap_at(end: date, shares, prices: list[PricePoint], splits) -> float | None:
        """Split-adjusted close at fiscal year end x split-adjusted share count."""
        if not shares or not prices:
            return None
        close = next((p.close for p in reversed(prices) if p.day <= end), None)
        if close is None or (end - prices[0].day).days < 0:
            return None
        factor = 1.0
        if splits is not None and len(splits):
            for when, ratio in splits.items():
                if when.date() > end and ratio:
                    factor *= float(ratio)
        return close * shares * factor

    def insider_activity(self, symbol: str) -> InsiderActivity | None:
        info = self._info(symbol)
        held = _num(info.get("heldPercentInsiders")) if info else None
        act = InsiderActivity(held_pct=held)
        frame = self._call(f"insiders {symbol}", lambda: self._ticker(symbol).insider_purchases)
        if frame is not None and not frame.empty:
            label = frame.columns[0]
            idx = {str(v).strip().lower(): i for i, v in frame[label].items()}

            def get(row, col):
                i = idx.get(row)
                return _num(frame.at[i, col]) if i is not None and col in frame.columns else None

            act.buys = int(get("purchases", "Trans") or 0)
            act.sells = int(get("sales", "Trans") or 0)
            act.net_shares = get("net shares purchased (sold)", "Shares")
        if held is None and frame is None:
            return None
        return act

    @lru_cache(maxsize=1024)
    def _history(self, symbol: str, years: int):
        return self._call(f"history {symbol}", lambda: self._ticker(symbol).history(
            period=f"{years}y", interval="1d", auto_adjust=False, actions=False))

    def price_history(self, symbol: str, years: int) -> list[PricePoint]:
        hist = self._history(symbol, years)
        if hist is None or hist.empty:
            return []
        _, factor = normalise_currency(self._info(symbol).get("currency"))
        return [PricePoint(day=ts.date(), close=float(v) * factor)
                for ts, v in hist["Close"].items() if _num(v) is not None]

    @lru_cache(maxsize=256)
    def fx_rate(self, from_ccy: str, to_ccy: str) -> float | None:
        from_ccy, f1 = normalise_currency(from_ccy)
        to_ccy, f2 = normalise_currency(to_ccy)
        if from_ccy == to_ccy:
            return 1.0
        direct = self._pair(from_ccy, to_ccy)
        if direct:
            return direct
        inverse = self._pair(to_ccy, from_ccy)
        if inverse:
            return 1.0 / inverse
        a, b = self._pair(from_ccy, "USD"), self._pair("USD", to_ccy)
        return a * b if a and b else None

    def _pair(self, a: str, b: str) -> float | None:
        if a == b:
            return 1.0
        hist = self._call(f"fx {a}{b}", lambda: self._ticker(f"{a}{b}=X").history(period="5d"))
        if hist is None or hist.empty:
            return None
        return _num(hist["Close"].dropna().iloc[-1]) if not hist["Close"].dropna().empty else None
