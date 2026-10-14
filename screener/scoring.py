"""Hard filters, composite score and human-readable reasons. Pure functions only."""
from __future__ import annotations

from dataclasses import dataclass, field

from .metrics import SectorStats, StockMetrics


@dataclass
class Evaluation:
    passed: bool
    score: float | None
    components: dict[str, float] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)
    failures: list[str] = field(default_factory=list)


def linear(x: float, lo: float, hi: float) -> float:
    """Map x from [lo, hi] onto [0, 1], clipped."""
    if hi == lo:
        return 1.0 if x >= hi else 0.0
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def plural(n: int, word: str) -> str:
    return f"{n} {word}{'s' * (n != 1)}"


def discount(current: float | None, reference: float | None) -> float | None:
    """How much cheaper than the reference on EV/EBIT (0.3 = 30% below)."""
    if current is None or not reference or reference <= 0:
        return None
    return 1 - current / reference


def _mean(values: list[float]) -> float | None:
    return sum(values) / len(values) if values else None


def valuation_component(cur_ev_ebit, ref_ev_ebit, cur_fcf, ref_fcf, cfg) -> float | None:
    parts = []
    d = discount(cur_ev_ebit, ref_ev_ebit)
    if d is not None:
        parts.append(linear(d, *cfg["ev_ebit_discount_range"]))
    if cur_fcf is not None and ref_fcf is not None:
        parts.append(linear(cur_fcf - ref_fcf, *cfg["fcf_yield_spread_range"]))
    return _mean(parts)


def insider_component(s: StockMetrics, cfg) -> float | None:
    parts = []
    if s.insider_held_pct is not None:
        parts.append(linear(s.insider_held_pct, 0, cfg["insider_held_full"]))
    if s.insider_buys or s.insider_sells or s.insider_net_shares is not None:
        net = s.insider_net_shares or 0
        parts.append(1.0 if net > 0 else 0.5 if net == 0 else 0.2)
    return _mean(parts)


def components(s: StockMetrics, sector: SectorStats | None, cfg: dict) -> dict[str, float]:
    f, sc = cfg["filters"], cfg["scoring"]
    out: dict[str, float] = {}
    max_analysts = f["max_analysts"] if f["max_analysts"] is not None else 10
    out["coverage"] = max(0.0, 1 - (s.analysts or 0) / (max_analysts + 1))
    if s.roic is not None:
        out["quality"] = linear(s.roic, f["min_roic"] or 0, sc["roic_excellent"])
    if s.net_debt_to_ebitda is not None:
        cap = f["max_net_debt_to_ebitda"] or 3.0
        out["balance_sheet"] = 1.0 if s.net_debt_to_ebitda <= 0 else linear(-s.net_debt_to_ebitda, -cap, 0)
    hist = valuation_component(s.ev_ebit, s.ev_ebit_hist_median, s.fcf_yield, s.fcf_yield_hist_median, sc)
    if hist is not None:
        out["value_vs_history"] = hist
    if sector:
        sect = valuation_component(s.ev_ebit, sector.ev_ebit_median, s.fcf_yield, sector.fcf_yield_median, sc)
        if sect is not None:
            out["value_vs_sector"] = sect
    ins = insider_component(s, sc)
    if ins is not None:
        out["insider"] = ins
    return out


def composite(comps: dict[str, float], weights: dict[str, float]) -> float | None:
    used = {k: w for k, w in weights.items() if k in comps and w > 0}
    total = sum(used.values())
    if not total:
        return None
    return round(100 * sum(comps[k] * w for k, w in used.items()) / total, 1)


def evaluate(s: StockMetrics, sector: SectorStats | None, cfg: dict) -> Evaluation:
    f = cfg["filters"]
    reasons: list[str] = []
    failures: list[str] = []

    def gate(ok: bool, reason: str, failure: str):
        (reasons if ok else failures).append(reason if ok else failure)

    n = s.analysts or 0
    if f["max_analysts"] is not None:
        gate(n <= f["max_analysts"],
             "No analyst coverage" if n == 0 else f"{plural(n, 'analyst')} (≤{f['max_analysts']})",
             f"{n} analysts > {f['max_analysts']}")

    if f["require_profitable_or_fcf_positive"]:
        profitable = s.net_income is not None and s.net_income > 0
        fcf_pos = s.free_cash_flow is not None and s.free_cash_flow > 0
        label = " and ".join(x for x, ok in (("Profitable", profitable), ("FCF positive", fcf_pos)) if ok)
        gate(profitable or fcf_pos, label, "Loss-making and FCF negative (or data missing)")

    if f["min_roic"] is not None:
        if s.roic is None:
            failures.append("ROIC unavailable")
        else:
            gate(s.roic > f["min_roic"], f"ROIC {pct(s.roic)} > {pct(f['min_roic'])}",
                 f"ROIC {pct(s.roic)} ≤ {pct(f['min_roic'])}")

    if f["max_net_debt_to_ebitda"] is not None:
        nd = s.net_debt_to_ebitda
        if nd is None:
            failures.append("Net debt/EBITDA unavailable")
        elif nd <= 0:
            reasons.append("Net cash")
        else:
            gate(nd < f["max_net_debt_to_ebitda"], f"Net debt/EBITDA {nd:.1f}x",
                 "Net debt with negative EBITDA" if nd == float("inf")
                 else f"Net debt/EBITDA {nd:.1f}x ≥ {f['max_net_debt_to_ebitda']}x")

    # Valuation: always explained when cheap, gated only if configured.
    d_hist = discount(s.ev_ebit, s.ev_ebit_hist_median)
    if d_hist is not None and d_hist > 0:
        reasons.append(f"EV/EBIT {s.ev_ebit:.1f}x vs own median {s.ev_ebit_hist_median:.1f}x ({pct(-d_hist)})")
    if f["max_ev_ebit_vs_history"] is not None:
        if d_hist is None:
            failures.append("No EV/EBIT history")
        elif 1 - d_hist > f["max_ev_ebit_vs_history"]:
            failures.append(f"EV/EBIT {s.ev_ebit:.1f}x above own median {s.ev_ebit_hist_median:.1f}x")

    d_sect = discount(s.ev_ebit, sector.ev_ebit_median if sector else None)
    if d_sect is not None and d_sect > 0:
        reasons.append(f"EV/EBIT {s.ev_ebit:.1f}x vs sector median {sector.ev_ebit_median:.1f}x ({pct(-d_sect)})")
    if f["max_ev_ebit_vs_sector"] is not None:
        if d_sect is None:
            failures.append("No sector EV/EBIT comparison")
        elif 1 - d_sect > f["max_ev_ebit_vs_sector"]:
            failures.append(f"EV/EBIT {s.ev_ebit:.1f}x above sector median {sector.ev_ebit_median:.1f}x")

    if s.fcf_yield is not None:
        refs = []
        if s.fcf_yield_hist_median is not None and s.fcf_yield > s.fcf_yield_hist_median:
            refs.append(f"own median {pct(s.fcf_yield_hist_median)}")
        if sector and sector.fcf_yield_median is not None and s.fcf_yield > sector.fcf_yield_median:
            refs.append(f"sector {pct(sector.fcf_yield_median)}")
        if refs:
            reasons.append(f"FCF yield {pct(s.fcf_yield)} vs " + ", ".join(refs))
    if f["min_fcf_yield"] is not None:
        if s.fcf_yield is None or s.fcf_yield < f["min_fcf_yield"]:
            failures.append(f"FCF yield below {pct(f['min_fcf_yield'])}")

    if s.insider_held_pct is not None and s.insider_held_pct >= 0.10:
        reasons.append(f"Insiders own {pct(s.insider_held_pct)}")
    net = s.insider_net_shares
    if net is not None and net > 0:
        reasons.append(f"Insider net buying ({plural(s.insider_buys, 'buy')} / "
                       f"{plural(s.insider_sells, 'sell')}, {s.insider_window_months}m)")
    if f["require_insider_buying"] and not (net is not None and net > 0):
        failures.append("No insider net buying")

    comps = components(s, sector, cfg)
    return Evaluation(passed=not failures, score=composite(comps, cfg["scoring"]["weights"]),
                      components=comps, reasons=reasons, failures=failures)
