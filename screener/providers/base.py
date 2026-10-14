"""Provider-agnostic data model and the interface every data provider implements.

The rest of the screener only ever sees these dataclasses, so plugging in a new
data source means writing one class that fills them in (see ``fmp.py``).
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date


@dataclass
class Listing:
    """A candidate returned by the provider's universe query."""
    symbol: str
    name: str
    exchange: str
    country: str | None = None
    sector: str | None = None
    market_cap: float | None = None   # in ``currency``, if the universe query returns it
    currency: str | None = None


@dataclass
class Profile:
    symbol: str
    name: str
    exchange: str
    country: str | None
    sector: str | None
    industry: str | None
    currency: str            # trading currency, normalised to a major unit (GBp -> GBP)
    price: float | None      # in ``currency``
    market_cap: float | None  # in ``currency``
    description: str = ""
    website: str | None = None
    is_fund: bool = False


@dataclass
class AnnualFinancials:
    """One fiscal year. All money values are in ``currency`` (the reporting currency)."""
    fiscal_year: int
    period_end: date
    currency: str
    revenue: float | None = None
    ebit: float | None = None
    ebitda: float | None = None
    net_income: float | None = None
    pretax_income: float | None = None
    income_tax: float | None = None
    operating_cash_flow: float | None = None
    capex: float | None = None           # negative = cash out
    free_cash_flow: float | None = None
    total_debt: float | None = None
    cash: float | None = None
    total_equity: float | None = None
    minority_interest: float | None = None
    market_cap: float | None = None      # at period end, converted to the reporting currency


@dataclass
class InsiderActivity:
    held_pct: float | None = None        # fraction of shares held by insiders
    buys: int = 0                         # number of purchase transactions in the window
    sells: int = 0                        # number of sale transactions in the window
    net_shares: float | None = None       # shares bought minus shares sold in the window
    window_months: int = 6


@dataclass
class PricePoint:
    day: date
    close: float


@dataclass
class Market:
    """One market to scan: a provider-specific region code plus allowed (primary) exchanges."""
    region: str
    code: str
    exchanges: list[str] = field(default_factory=list)


class DataProvider(ABC):
    """Interface for market-data back ends. Return ``None``/empty when data is unavailable."""

    name: str = "abstract"

    @abstractmethod
    def list_universe(self, market: Market) -> list[Listing]:
        """All listed common stocks in ``market`` (market caps are checked later from profiles)."""

    @abstractmethod
    def profile(self, symbol: str) -> Profile | None: ...

    @abstractmethod
    def analyst_count(self, symbol: str) -> int | None:
        """Number of analysts covering the next fiscal year (0 if no estimates exist)."""

    @abstractmethod
    def financials(self, symbol: str, years: int) -> list[AnnualFinancials]:
        """Annual financials, newest first."""

    @abstractmethod
    def insider_activity(self, symbol: str) -> InsiderActivity | None: ...

    @abstractmethod
    def price_history(self, symbol: str, years: int) -> list[PricePoint]:
        """Daily closes, oldest first."""

    @abstractmethod
    def fx_rate(self, from_ccy: str, to_ccy: str) -> float | None:
        """Units of ``to_ccy`` per one unit of ``from_ccy``."""

