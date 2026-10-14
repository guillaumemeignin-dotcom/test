# ML Capital stock screener

Screens global small and mid caps for **under-followed, high-quality, cheap** companies:
market cap €10m–€1bn, listed in the US, Europe, UK, Canada, Australia, Japan or LatAm,
with **three or fewer analysts** covering them.

Each run produces:

| File | What it is |
|---|---|
| `output/<date>/screen.csv` | Ranked list with the composite score, every metric, per-component scores and the reasons each stock passed |
| `output/<date>/screen.html` | The same table as a sortable page (click a header), with new entrants highlighted |
| `output/<date>/tearsheets/<SYMBOL>.html` | One-page tear sheet for each of the top 20: business description, why it passed, valuation vs own history and sector, up to 5 years of financials and a share price chart. Prints to a single A4 page |
| `output/<date>/new_entrants.csv` | Names that passed this run but not the previous one |
| `output/state.json` | The last run's passing names, used to work out new entrants |

## Quick start

```bash
pip install -r requirements.txt
python -m screener                       # full run with screen.yaml
python -m screener --max-symbols 50      # quick trial on the first 50 candidates
python -m pytest                         # tests
```

Options: `--config PATH`, `--provider NAME`, `--output-dir DIR`, `--refresh` (ignore the cache
but still write to it), `--no-cache`, `-v`.

Data comes from **Yahoo Finance** via [`yfinance`](https://github.com/ranaroussi/yfinance):
free, no API key, global coverage. The machine running the screen needs outbound HTTPS to
`*.finance.yahoo.com` and `fc.yahoo.com`. Yahoo is an unofficial source meant for personal
and research use, so check its terms before using it more widely.

A first full run makes several requests per candidate and can take a few hours, because Yahoo
rate-limits. Responses are cached under `.cache/`, so reruns within the cache TTLs take seconds.
Raise `provider.options.min_interval` if you see rate-limit errors.

## How the screen works

1. **Universe.** For each region in `screen.yaml`, list every common stock on the configured
   primary exchanges. Using only primary exchanges keeps out duplicate cross-listings, such as
   Frankfurt copies of US stocks. Names outside the market-cap band (converted to EUR) are dropped.
2. **Coverage first.** The analyst count is fetched before anything else. Covered names
   are dropped at that point, so most of the universe costs a single request.
3. **Profile.** Market cap is checked exactly in EUR. Funds and excluded sectors are dropped
   (Financial Services and Real Estate by default, because ROIC and net debt/EBITDA don't suit them).
4. **Financials and insiders.** Annual statements, insider ownership and recent insider transactions.
5. **Metrics.**
   - ROIC = EBIT × (1 − effective tax rate) / (debt + equity − cash)
   - Net debt / EBITDA (net cash counts as a pass)
   - EV/EBIT and FCF yield, computed now from today's market cap and for each past year from
     that year-end's market cap
   - The 'own history' figure is the median of the prior years. The sector median is taken
     across all names that reach this stage.
6. **Filters and score.** Hard filters decide pass or fail. A composite score from 0 to 100 ranks the names that pass.

### Filters (`filters:` in `screen.yaml`; set any one to `null` to switch it off)

| Key | Default | Meaning |
|---|---|---|
| `max_analysts` | 3 | Analyst coverage ceiling: the core of the strategy |
| `require_profitable_or_fcf_positive` | true | Net income > 0 **or** FCF > 0 in the latest year |
| `min_roic` | 0.10 | ROIC must exceed this |
| `max_net_debt_to_ebitda` | 2.0 | Leverage must stay below this |
| `max_ev_ebit_vs_history` | null | e.g. `1.0` requires EV/EBIT at or below its own median |
| `max_ev_ebit_vs_sector` | null | e.g. `1.0` requires EV/EBIT at or below the sector median |
| `min_fcf_yield` | null | Minimum FCF yield |
| `require_insider_buying` | false | Require net insider buying |

A stock missing the data for an active filter fails it, because the screen can't confirm it passes.

### Composite score (`scoring:`)

Each component scores between 0 and 1 and is combined using the `weights` you set.

| Component | Score 1 at… | Score 0 at… |
|---|---|---|
| `coverage` | 0 analysts | (max + 1) analysts |
| `quality` | ROIC ≥ `roic_excellent` (30%) | ROIC = `min_roic` |
| `balance_sheet` | net cash | ND/EBITDA = limit |
| `value_vs_history` | EV/EBIT 50% below own median, FCF yield 5 pts above | 25% above, 2 pts below |
| `value_vs_sector` | the same, measured against the sector median | |
| `insider` | insiders own ≥ 30%, net buying | no ownership, net selling |

If a component has no data (for example no insider data, too little history, or too few
sector peers), it is left out and the remaining weights are rescaled. A stock isn't penalised
for a gap in the data source. Each stock's "why it passed" list records the filters it cleared
and every valuation or insider signal that worked in its favour.

## Swapping the data provider

Only `screener/providers/` knows about Yahoo. Everything else works with the plain
dataclasses in `screener/providers/base.py`. To add a source, such as FMP, Refinitiv or FactSet:

1. Subclass `DataProvider` and implement `list_universe`, `profile`, `analyst_count`,
   `financials`, `insider_activity`, `price_history` and `fx_rate`. Return values in the
   documented units: trading currency in major units for prices and market cap, and the
   reporting currency for statements.
2. Register it in `screener/providers/__init__.py`: `register("myprovider", factory)`.
3. Set `provider.name: myprovider` in `screen.yaml`. Read API keys from environment variables
   and never commit them.

Caching is a wrapper (`CachedProvider`) around any provider. It stores normalised results,
never raw HTTP responses or credentials, so a new provider gets caching for free.
`tests/conftest.py::FakeProvider` is a complete minimal example.

## Data caveats (Yahoo)

- **History depth.** Yahoo usually gives 4 annual periods, sometimes 5. Comparing a stock with
  its own history therefore uses about 3 prior years, and `valuation.min_history_years: 3` sets
  the minimum. A provider with deeper history gives the full 5 years with no code change.
- **Historical market cap** = split-adjusted year-end close × year-end share count (also
  split-adjusted), converted to the reporting currency at today's FX rate.
- **Analyst count** is Yahoo's `numberOfAnalystOpinions`. If no count is published, the stock
  is treated as uncovered.
- **Insiders.** Ownership (`heldPercentInsiders`) is widely available. Transaction data (the
  last 6 months) is mostly US-only, so non-US names score on ownership alone.
- **UK and South African prices** quoted in pence or cents (GBp, ZAc) are converted to pounds
  or rand, and market cap is recomputed from price × shares.
- The sector median is taken across the screened universe, so it is a peer group of under-followed
  small caps rather than the whole market. `valuation.min_sector_peers` sets the minimum peer count.

## Layout

```
screen.yaml               all thresholds, weights, regions and exchanges
screener/
  providers/base.py       data model + DataProvider interface
  providers/yahoo.py      Yahoo Finance implementation
  providers/cached.py     disk-caching wrapper for any provider
  cache.py                JSON disk cache
  metrics.py              ROIC, leverage, EV/EBIT, FCF yield, history & sector medians
  scoring.py              filters, composite score, reasons (pure functions)
  pipeline.py             orchestration
  report.py, templates/   CSV, HTML table, tear sheets, new entrants
tests/                    scoring, metrics, cache, pipeline and Yahoo-parsing tests (offline)
```
