"""Load ``screen.yaml`` on top of built-in defaults."""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from .providers.base import Market

DEFAULTS: dict[str, Any] = {
    "provider": {"name": "yahoo", "options": {}},
    "cache": {"dir": ".cache", "ttl_days": {}},
    "output_dir": "output",
    "workers": 4,
    "max_symbols": None,
    "universe": {
        "market_cap_eur": {"min": 10_000_000, "max": 1_000_000_000},
        "exclude_sectors": [],
        "regions": {},
    },
    "filters": {
        "max_analysts": 3,
        "require_profitable_or_fcf_positive": True,
        "min_roic": 0.10,
        "max_net_debt_to_ebitda": 2.0,
        "max_ev_ebit_vs_history": None,
        "max_ev_ebit_vs_sector": None,
        "min_fcf_yield": None,
        "require_insider_buying": False,
    },
    "valuation": {"history_years": 5, "min_history_years": 3, "min_sector_peers": 5},
    "scoring": {
        "weights": {"coverage": 0.25, "quality": 0.20, "balance_sheet": 0.10,
                    "value_vs_history": 0.15, "value_vs_sector": 0.20, "insider": 0.10},
        "roic_excellent": 0.30,
        "ev_ebit_discount_range": [-0.25, 0.50],
        "fcf_yield_spread_range": [-0.02, 0.05],
        "insider_held_full": 0.30,
    },
    "report": {"top_n_tearsheets": 20},
}


def deep_merge(base: dict, override: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict) and k != "weights":
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def load_config(path: str | Path | None = None, overrides: dict | None = None) -> dict:
    data = {}
    if path:
        with open(path) as fh:
            data = yaml.safe_load(fh) or {}
    return deep_merge(deep_merge(DEFAULTS, data), overrides or {})


def markets(cfg: dict) -> list[Market]:
    out = []
    for region, codes in cfg["universe"]["regions"].items():
        for code, exchanges in (codes or {}).items():
            if not isinstance(code, str):
                raise ValueError(f"region code {code!r} under {region} must be a string - quote it in "
                                 f"YAML (e.g. \"no\" for Norway, which YAML otherwise reads as false)")
            out.append(Market(region=region, code=code, exchanges=list(exchanges or [])))
    return out
