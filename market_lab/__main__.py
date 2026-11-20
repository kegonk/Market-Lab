"""CLI: python -m market_lab [--output DIR] [--seed 20261120]."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .report import write_csv, write_html
from .simulation import Config, run_experiment


def main() -> None:
    parser = argparse.ArgumentParser(description="Offline synthetic liquidity shock experiment LQ-001")
    parser.add_argument("--output", type=Path, default=Path("artifacts/lq001"))
    parser.add_argument("--seed", type=int, default=20261120)
    parser.add_argument("--ticks", type=int, default=120)
    parser.add_argument("--shock-tick", type=int, default=60)
    parser.add_argument("--cancel-percent", type=float, default=85.0)
    parser.add_argument("--cancel-levels", type=int, default=8)
    parser.add_argument("--sell-qty", type=int, default=26)
    args = parser.parse_args()
    cfg = Config(seed=args.seed, ticks=args.ticks, shock_tick=args.shock_tick,
                 cancel_fraction=args.cancel_percent / 100, cancel_levels=args.cancel_levels,
                 shock_sell_qty=args.sell_qty)
    result = run_experiment(cfg)
    args.output.mkdir(parents=True, exist_ok=True)
    (args.output / "results.json").write_text(json.dumps(result, indent=2, ensure_ascii=False,
                                                       sort_keys=True) + "\n", encoding="utf-8")
    write_csv(result, args.output / "timeseries.csv")
    write_html(result, args.output / "report.html")
    b, s = result["baseline"]["shock_trade"], result["shock"]["shock_trade"]
    print(f"Experiment LQ-001 (synthetic): seed={cfg.seed}, tape_sha256={result['tape_sha256']}")
    print(f"Baseline: filled={b['filled_qty']}, slippage={b['slippage_bps']} bps")
    print(f"Shock:    filled={s['filled_qty']}, slippage={s['slippage_bps']} bps")
    print(f"Delta:    {result['comparison']['slippage_increase_bps']} bps")
    print(f"Report:   {args.output / 'report.html'}")


if __name__ == "__main__":
    main()
