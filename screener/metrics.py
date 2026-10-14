"""Turn normalised provider data into the per-stock metrics the screen uses."""
from __future__ import annotations

from dataclasses import dataclass, field
from statistics import median

from .providers.base import AnnualFinancials, InsiderActivity, Profile

DEFAULT_TAX_RATE = 0.25


@dataclass
class YearMetrics:
    fiscal_year: int
    revenue: float | None
    ebit: float | None
    ebit_margin: float | None
    net_income: float | None
    free_cash_flow: float | None
    roic: float | None
    net_debt_to_ebitda: float | None
    ev_ebit: float | None
    fcf_yield: float | None


@dataclass
class StockMetrics:
    symbol: str
    name: str
    region: str
    exchange: str
    country: str | None
    sector: str | None
    industry: str | None
    currency: str
    reporting_currency: str | None
    description: str
    website: str | None
    market_cap_eur: float | None
    analysts: int | None
    net_income: float | None = None
    free_cash_flow: float | None = None
    roic: float | None = None
    net_debt: float | None = None
    ebitda: float | None = None
    net_debt_to_ebitda: float | None = None
    enterprise_value: float | None = None
    ev_ebit: float | None = None
    fcf_yield: float | None = None
    ev_ebit_hist_median: float | None = None
    fcf_yield_hist_median: float | None = None
    history_years: int = 0
    insider_held_pct: float | None = None
    insider_buys: int = 0
    insider_sells: int = 0
    insider_net_shares: float | None = None
    insider_window_months: int = 6
    years: list[YearMetrics] = field(default_factory=list)


def tax_rate(f: AnnualFinancials) -> float:
    if f.income_tax is None or not f.pretax_income or f.pretax_income <= 0:
        return DEFAULT_TAX_RATE
    rate = f.income_tax / f.pretax_income
    return rate if 0 <= rate <= 0.5 else DEFAULT_TAX_RATE


def roic(f: AnnualFinancials) -> float | None:
    """NOPAT / (debt + equity - cash)."""
    if f.ebit is None or f.total_equity is None:
        return None
    invested = (f.total_debt or 0) + f.total_equity - (f.cash or 0)
    if invested <= 0:
        return None
    return f.ebit * (1 - tax_rate(f)) / invested


def net_debt(f: AnnualFinancials) -> float | None:
    if f.total_debt is None and f.cash is None:
        return None
    return (f.total_debt or 0) - (f.cash or 0)


def net_debt_to_ebitda(nd: float | None, ebitda: float | None) -> float | None:
    """Negative = net cash. Positive debt with non-positive EBITDA is infinitely levered."""
    if nd is None:
        return None
    if nd <= 0:
        return nd / ebitda if ebitda and ebitda > 0 else 0.0 if nd == 0 else -1.0
    if not ebitda or ebitda <= 0:
        return float("inf")
    return nd / ebitda


def enterprise_value(market_cap: float | None, f: AnnualFinancials) -> float | None:
    nd = net_debt(f)
    if market_cap is None or nd is None:
        return None
    return market_cap + nd + (f.minority_interest or 0)


def ev_ebit(ev: float | None, ebit: float | None) -> float | None:
    if ev is None or not ebit or ebit <= 0 or ev <= 0:
        return None
    return ev / ebit


def fcf_yield(fcf: float | None, market_cap: float | None) -> float | None:
    if fcf is None or not market_cap or market_cap <= 0:
        return None
    return fcf / market_cap


def year_metrics(f: AnnualFinancials, market_cap: float | None) -> YearMetrics:
    ev = enterprise_value(market_cap, f)
    return YearMetrics(
        fiscal_year=f.fiscal_year, revenue=f.revenue, ebit=f.ebit,
        ebit_margin=f.ebit / f.revenue if f.ebit is not None and f.revenue else None,
        net_income=f.net_income, free_cash_flow=f.free_cash_flow, roic=roic(f),
        net_debt_to_ebitda=net_debt_to_ebitda(net_debt(f), f.ebitda),
        ev_ebit=ev_ebit(ev, f.ebit), fcf_yield=fcf_yield(f.free_cash_flow, market_cap),
    )


def _median(values) -> float | None:
    vals = [v for v in values if v is not None]
    return median(vals) if vals else None


def compute(profile: Profile, region: str, financials: list[AnnualFinancials], analysts: int | None,
            insider: InsiderActivity | None, fx_to_eur: float | None, fx_to_reporting: float | None,
            min_history_years: int = 3) -> StockMetrics:
    """``financials`` newest first. FX rates convert from the trading currency."""
    m = StockMetrics(
        symbol=profile.symbol, name=profile.name, region=region, exchange=profile.exchange,
        country=profile.country, sector=profile.sector, industry=profile.industry,
        currency=profile.currency, reporting_currency=financials[0].currency if financials else None,
        description=profile.description, website=profile.website,
        market_cap_eur=profile.market_cap * fx_to_eur if profile.market_cap and fx_to_eur else None,
        analysts=analysts,
    )
    if insider:
        m.insider_held_pct = insider.held_pct
        m.insider_buys, m.insider_sells = insider.buys, insider.sells
        m.insider_net_shares = insider.net_shares
        m.insider_window_months = insider.window_months
    if not financials:
        return m

    latest = financials[0]
    mcap_rep = profile.market_cap * fx_to_reporting if profile.market_cap and fx_to_reporting else None
    current = year_metrics(latest, mcap_rep)
    m.net_income, m.free_cash_flow, m.roic = latest.net_income, latest.free_cash_flow, current.roic
    m.net_debt, m.ebitda = net_debt(latest), latest.ebitda
    m.net_debt_to_ebitda = current.net_debt_to_ebitda
    m.enterprise_value = enterprise_value(mcap_rep, latest)
    m.ev_ebit, m.fcf_yield = current.ev_ebit, current.fcf_yield

    # Own history: prior fiscal years valued at their own year-end market caps.
    past = [year_metrics(f, f.market_cap) for f in financials[1:]]
    m.years = [current] + past
    m.history_years = sum(1 for y in past if y.ev_ebit is not None or y.fcf_yield is not None)
    if sum(1 for y in past if y.ev_ebit is not None) >= min_history_years:
        m.ev_ebit_hist_median = _median(y.ev_ebit for y in past)
    if sum(1 for y in past if y.fcf_yield is not None) >= min_history_years:
        m.fcf_yield_hist_median = _median(y.fcf_yield for y in past)
    return m


@dataclass
class SectorStats:
    ev_ebit_median: float | None
    fcf_yield_median: float | None
    peers: int


def sector_stats(stocks: list[StockMetrics], min_peers: int) -> dict[str, SectorStats]:
    by_sector: dict[str, list[StockMetrics]] = {}
    for s in stocks:
        if s.sector:
            by_sector.setdefault(s.sector, []).append(s)
    out = {}
    for sector, group in by_sector.items():
        ev = [s.ev_ebit for s in group if s.ev_ebit is not None]
        fy = [s.fcf_yield for s in group if s.fcf_yield is not None]
        out[sector] = SectorStats(
            ev_ebit_median=median(ev) if len(ev) >= min_peers else None,
            fcf_yield_median=median(fy) if len(fy) >= min_peers else None,
            peers=len(group),
        )
    return out
