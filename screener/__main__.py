"""CLI: ``python -m screener [--config screen.yaml] [--max-symbols N] [--refresh]``."""
from __future__ import annotations

import argparse
import logging
import sys

from .cache import DiskCache
from .config import load_config
from .pipeline import run
from .providers import get_provider
from .providers.cached import CachedProvider
from .report import write_reports


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="screener", description="ML Capital small/mid-cap screen")
    ap.add_argument("--config", default="screen.yaml")
    ap.add_argument("--provider", help="override provider.name from the config")
    ap.add_argument("--max-symbols", type=int, help="process at most N symbols (quick test runs)")
    ap.add_argument("--output-dir", help="override output_dir")
    ap.add_argument("--refresh", action="store_true", help="ignore cached responses (still writes the cache)")
    ap.add_argument("--no-cache", action="store_true", help="neither read nor write the cache")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    overrides = {}
    if args.max_symbols:
        overrides["max_symbols"] = args.max_symbols
    if args.output_dir:
        overrides["output_dir"] = args.output_dir
    cfg = load_config(args.config, overrides)

    pcfg = cfg["provider"]
    inner = get_provider(args.provider or pcfg["name"], **(pcfg.get("options") or {}))
    cache = DiskCache(cfg["cache"]["dir"], enabled=not args.no_cache, refresh=args.refresh)
    provider = CachedProvider(inner, cache, cfg["cache"].get("ttl_days"))

    result = run(provider, cfg)
    if result.universe_size == 0:
        logging.error("Provider returned no listings - check network access to the data source.")
        return 1
    out = write_reports(result, provider, cfg)
    logging.info("%d passed, %d failed filters, pre-screen drops: %s",
                 len(result.passed), len(result.failed), result.rejected)
    logging.info("Reports written to %s", out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
