"""Data provider registry. Add a new back end by implementing ``DataProvider`` and
registering a factory here; the rest of the screener is provider-agnostic."""
from __future__ import annotations

from typing import Callable

from .base import DataProvider

_REGISTRY: dict[str, Callable[..., DataProvider]] = {}


def register(name: str, factory: Callable[..., DataProvider]) -> None:
    _REGISTRY[name] = factory


def get_provider(name: str, **options) -> DataProvider:
    if name not in _REGISTRY:
        raise ValueError(f"unknown provider {name!r}; available: {', '.join(sorted(_REGISTRY))}")
    return _REGISTRY[name](**options)


def _yahoo(**options) -> DataProvider:
    from .yahoo import YahooProvider
    return YahooProvider(**options)


register("yahoo", _yahoo)
