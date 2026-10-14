"""Orchestrates a screen run: universe -> per-stock data -> metrics -> filters -> ranking."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from . import metrics as M
from .config import markets
from .providers.base import DataProvider, Listing
from .scoring import Evaluation, evaluate

log = logging.getLogger(__name__)

# Quote market caps move intraday; keep a margin at the listing stage and apply
# the exact bounds once the profile is fetched.
LISTING_TOLERANCE = 0.2


@dataclass
class Row:
    metrics: M.StockMetrics
    evaluation: Evaluation
    sector: M.SectorStats | None = None
    new_entry: bool = False


@dataclass
class RunResult:
    passed: list[Row]
    failed: list[Row]
    rejected: dict[str, int] = field(default_factory=dict)  # pre-screen drop counts by reason
    universe_size: int = 0


class FxCache:
    def __init__(self, provider: DataProvider):
        self.provider = provider
        self.rates: dict[tuple[str, str], float | None] = {}

    def __call__(self, a: str | None, b: str | None) -> float | None:
        if not a or not b:
            return None
        if a == b:
            return 1.0
        if (a, b) not in self.rates:
            self.rates[(a, b)] = self.provider.fx_rate(a, b)
        return self.rates[(a, b)]


def build_universe(provider: DataProvider, cfg: dict, fx: FxCache) -> tuple[list[tuple[str, Listing]], int]:
    lo, hi = cfg["universe"]["market_cap_eur"]["min"], cfg["universe"]["market_cap_eur"]["max"]
    seen: dict[str, tuple[str, Listing]] = {}
    total = 0
    for market in markets(cfg):
        listings = provider.list_universe(market)
        log.info("%s/%s: %d listings", market.region, market.code, len(listings))
        total += len(listings)
        for l in listings:
            if l.symbol in seen:
                continue
            if l.market_cap is not None and l.currency:
                rate = fx(l.currency, "EUR")
                if rate:
                    eur = l.market_cap * rate
                    if not lo * (1 - LISTING_TOLERANCE) <= eur <= hi * (1 + LISTING_TOLERANCE):
                        continue
            seen[l.symbol] = (market.region, l)
    return list(seen.values()), total


def analyse(provider: DataProvider, cfg: dict, fx: FxCache, region: str, listing: Listing
            ) -> tuple[M.StockMetrics | None, str | None]:
    """Fetch everything for one symbol. Returns (metrics, None) or (None, rejection reason).

    Cheapest, most selective checks run first so excluded names cost few calls.
    """
    f, u = cfg["filters"], cfg["universe"]
    analysts = provider.analyst_count(listing.symbol)
    if f["max_analysts"] is not None and (analysts or 0) > f["max_analysts"]:
        return None, "analyst coverage"
    profile = provider.profile(listing.symbol)
    if profile is None or profile.is_fund:
        return None, "no profile / not an equity"
    if profile.sector in set(u["exclude_sectors"] or []):
        return None, "excluded sector"
    to_eur = fx(profile.currency, "EUR")
    mcap_eur = profile.market_cap * to_eur if profile.market_cap and to_eur else None
    if mcap_eur is None or not u["market_cap_eur"]["min"] <= mcap_eur <= u["market_cap_eur"]["max"]:
        return None, "market cap"
    years = cfg["valuation"]["history_years"]
    financials = provider.financials(listing.symbol, years)
    if not financials:
        return None, "no financials"
    insider = provider.insider_activity(listing.symbol)
    return M.compute(profile, region, financials, analysts, insider, to_eur,
                     fx(profile.currency, financials[0].currency),
                     cfg["valuation"]["min_history_years"]), None


def run(provider: DataProvider, cfg: dict) -> RunResult:
    fx = FxCache(provider)
    universe, total = build_universe(provider, cfg, fx)
    if cfg.get("max_symbols"):
        universe = universe[: cfg["max_symbols"]]
    log.info("%d candidates in market-cap range (of %d listings)", len(universe), total)

    rejected: dict[str, int] = {}
    stocks: list[M.StockMetrics] = []

    def task(item):
        region, listing = item
        try:
            return analyse(provider, cfg, fx, region, listing)
        except Exception:  # one bad symbol must not kill a multi-hour run
            log.exception("failed on %s", listing.symbol)
            return None, "error"

    with ThreadPoolExecutor(max_workers=max(1, cfg.get("workers") or 1)) as pool:
        for i, (stock, reason) in enumerate(pool.map(task, universe), 1):
            if stock:
                stocks.append(stock)
            else:
                rejected[reason] = rejected.get(reason, 0) + 1
            if i % 100 == 0:
                log.info("processed %d/%d", i, len(universe))

    sectors = M.sector_stats(stocks, cfg["valuation"]["min_sector_peers"])
    passed, failed = [], []
    for s in stocks:
        sector = sectors.get(s.sector or "")
        row = Row(metrics=s, evaluation=evaluate(s, sector, cfg), sector=sector)
        (passed if row.evaluation.passed else failed).append(row)
    passed.sort(key=lambda r: (-(r.evaluation.score or 0), r.metrics.symbol))
    return RunResult(passed=passed, failed=failed, rejected=rejected, universe_size=total)
