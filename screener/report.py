"""Write the ranked CSV/HTML, per-name tear sheets and the new-entrants list."""
from __future__ import annotations

import base64
import csv
import io
import json
import math
from datetime import date
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .pipeline import Row, RunResult
from .providers.base import DataProvider, PricePoint

TEMPLATES = Path(__file__).parent / "templates"
STATE_FILE = "state.json"

COLUMNS = ["rank", "symbol", "name", "score", "new_entry", "region", "exchange", "country", "sector",
           "market_cap_eur_m", "analysts", "roic", "net_debt_to_ebitda", "ev_ebit", "ev_ebit_own_median",
           "ev_ebit_sector_median", "fcf_yield", "fcf_yield_own_median", "fcf_yield_sector_median",
           "insider_held_pct", "insider_net_shares", "score_coverage", "score_quality",
           "score_balance_sheet", "score_value_vs_history", "score_value_vs_sector", "score_insider",
           "reasons"]


def _r(x, nd=4):
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return None
    if isinstance(x, float) and math.isinf(x):
        return "inf" if x > 0 else "-inf"
    return round(x, nd)


def csv_record(rank: int, row: Row) -> dict:
    s, e, sec = row.metrics, row.evaluation, row.sector
    rec = {
        "rank": rank, "symbol": s.symbol, "name": s.name, "score": e.score, "new_entry": row.new_entry,
        "region": s.region, "exchange": s.exchange, "country": s.country, "sector": s.sector,
        "market_cap_eur_m": _r(s.market_cap_eur / 1e6 if s.market_cap_eur else None, 1),
        "analysts": s.analysts, "roic": _r(s.roic), "net_debt_to_ebitda": _r(s.net_debt_to_ebitda, 2),
        "ev_ebit": _r(s.ev_ebit, 2), "ev_ebit_own_median": _r(s.ev_ebit_hist_median, 2),
        "ev_ebit_sector_median": _r(sec.ev_ebit_median if sec else None, 2),
        "fcf_yield": _r(s.fcf_yield), "fcf_yield_own_median": _r(s.fcf_yield_hist_median),
        "fcf_yield_sector_median": _r(sec.fcf_yield_median if sec else None),
        "insider_held_pct": _r(s.insider_held_pct), "insider_net_shares": s.insider_net_shares,
        "reasons": "; ".join(e.reasons),
    }
    for k in ("coverage", "quality", "balance_sheet", "value_vs_history", "value_vs_sector", "insider"):
        rec[f"score_{k}"] = _r(e.components.get(k), 3)
    return rec


def write_csv(rows: list[Row], path: Path) -> None:
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for i, row in enumerate(rows, 1):
            w.writerow(csv_record(i, row))


# -- new entrants ------------------------------------------------------------
def load_previous(out_root: Path) -> dict | None:
    try:
        return json.loads((out_root / STATE_FILE).read_text())
    except (OSError, ValueError):
        return None


def mark_new_entries(rows: list[Row], previous: dict | None) -> list[Row]:
    """Flag rows not in the previous run. No previous run = baseline, nothing is 'new'."""
    if previous is None:
        return []
    before = set(previous.get("symbols", []))
    new = [r for r in rows if r.metrics.symbol not in before]
    for r in new:
        r.new_entry = True
    return new


def save_state(out_root: Path, rows: list[Row], run_date: str) -> None:
    (out_root / STATE_FILE).write_text(json.dumps(
        {"run_date": run_date, "symbols": [r.metrics.symbol for r in rows]}, indent=2))


# -- formatting helpers for templates ------------------------------------------
def fmt_pct(x):
    return "–" if x is None else f"{x * 100:.1f}%"


def fmt_x(x):
    if x is None:
        return "–"
    if x == float("inf"):
        return "n/m"
    return "net cash" if x < 0 else f"{x:.1f}x"


def fmt_money(x):
    if x is None:
        return "–"
    for div, suf in ((1e9, "bn"), (1e6, "m"), (1e3, "k")):
        if abs(x) >= div:
            return f"{x / div:,.1f}{suf}"
    return f"{x:,.0f}"


def price_chart(points: list[PricePoint], title: str) -> str | None:
    """Base64 PNG so each tear sheet is a single self-contained file."""
    if len(points) < 2:
        return None
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(7.2, 2.4), dpi=110)
    ax.plot([p.day for p in points], [p.close for p in points], color="#1f4e79", linewidth=1.2)
    ax.set_title(title, fontsize=9, loc="left")
    ax.grid(alpha=0.25)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.tick_params(labelsize=8)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return base64.b64encode(buf.getvalue()).decode()


def _env() -> Environment:
    env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))
    env.filters.update(pct=fmt_pct, x=fmt_x, money=fmt_money)
    return env


def write_reports(result: RunResult, provider: DataProvider, cfg: dict, run_date: date | None = None) -> Path:
    run_date = run_date or date.today()
    out_root = Path(cfg["output_dir"])
    out = out_root / run_date.isoformat()
    (out / "tearsheets").mkdir(parents=True, exist_ok=True)

    previous = load_previous(out_root)
    new = mark_new_entries(result.passed, previous)

    write_csv(result.passed, out / "screen.csv")
    write_csv(new, out / "new_entrants.csv")

    env = _env()
    top = result.passed[: cfg["report"]["top_n_tearsheets"]]
    tear_tpl = env.get_template("tearsheet.html")
    years = cfg["valuation"]["history_years"]
    for rank, row in enumerate(top, 1):
        s = row.metrics
        chart = price_chart(provider.price_history(s.symbol, years),
                            f"{s.symbol} – {years}y share price ({s.currency})")
        (out / "tearsheets" / f"{safe_name(s.symbol)}.html").write_text(
            tear_tpl.render(row=row, s=s, e=row.evaluation, sec=row.sector, rank=rank, chart=chart,
                            run_date=run_date.isoformat()))

    tear_symbols = {r.metrics.symbol for r in top}
    (out / "screen.html").write_text(env.get_template("screen.html").render(
        rows=result.passed, new=new, previous=previous, run_date=run_date.isoformat(),
        tear_symbols=tear_symbols, safe_name=safe_name, result=result, cfg=cfg))

    save_state(out_root, result.passed, run_date.isoformat())
    return out


def safe_name(symbol: str) -> str:
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in symbol)
