"""Wraps any DataProvider and caches its normalised results on disk.

Caching sits above the provider, so every back end gets cheap reruns for free and
the cache never sees raw HTTP traffic or credentials.
"""
from __future__ import annotations

import dataclasses
from datetime import date
from typing import Any

from ..cache import DiskCache
from .base import (AnnualFinancials, DataProvider, InsiderActivity, Listing, Market,
                   PricePoint, Profile)

DAY = 86400


def dump(obj: Any) -> Any:
    if dataclasses.is_dataclass(obj):
        return {f.name: dump(getattr(obj, f.name)) for f in dataclasses.fields(obj)}
    if isinstance(obj, list):
        return [dump(o) for o in obj]
    if isinstance(obj, date):
        return obj.isoformat()
    return obj


def load(cls, data: dict | None):
    if data is None:
        return None
    kwargs = {}
    for f in dataclasses.fields(cls):
        if f.name not in data:
            continue
        v = data[f.name]
        if isinstance(v, str) and f.name in ("period_end", "day"):
            v = date.fromisoformat(v)
        kwargs[f.name] = v
    return cls(**kwargs)


class CachedProvider(DataProvider):
    def __init__(self, inner: DataProvider, cache: DiskCache, ttl_days: dict[str, float] | None = None):
        self.inner = inner
        self.cache = cache
        self.name = inner.name
        self.ttl = {"universe": 7, "profile": 7, "analysts": 7, "financials": 30,
                    "insiders": 7, "prices": 1, "fx": 1, **(ttl_days or {})}

    def _cached(self, kind: str, args: list, fetch, encode=dump, decode=lambda d: d):
        key = self.cache.key(self.inner.name, kind, {"args": args})
        hit = self.cache.get(key, self.ttl[kind] * DAY)
        if hit is not None:
            return decode(hit["v"])
        value = fetch()
        # Don't cache failures/empties: a transient error shouldn't stick for days.
        if value is not None and value != []:
            self.cache.set(key, {"v": encode(value)}, meta={"kind": kind, "args": args})
        return value

    def list_universe(self, market: Market) -> list[Listing]:
        return self._cached("universe", [market.code, sorted(market.exchanges)],
                            lambda: self.inner.list_universe(market),
                            decode=lambda d: [load(Listing, x) for x in d])

    def profile(self, symbol):
        return self._cached("profile", [symbol], lambda: self.inner.profile(symbol),
                            decode=lambda d: load(Profile, d))

    def analyst_count(self, symbol):
        return self._cached("analysts", [symbol], lambda: self.inner.analyst_count(symbol))

    def financials(self, symbol, years):
        return self._cached("financials", [symbol, years], lambda: self.inner.financials(symbol, years),
                            decode=lambda d: [load(AnnualFinancials, x) for x in d])

    def insider_activity(self, symbol):
        return self._cached("insiders", [symbol], lambda: self.inner.insider_activity(symbol),
                            decode=lambda d: load(InsiderActivity, d))

    def price_history(self, symbol, years):
        return self._cached("prices", [symbol, years], lambda: self.inner.price_history(symbol, years),
                            decode=lambda d: [load(PricePoint, x) for x in d])

    def fx_rate(self, from_ccy, to_ccy):
        return self._cached("fx", [from_ccy, to_ccy], lambda: self.inner.fx_rate(from_ccy, to_ccy))
