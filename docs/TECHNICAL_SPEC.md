# Technical Specification — Kairos Market Lab

Status: v0.1 standalone prototype scope. Scope owner: `kegonk/Market-Lab`. Research only.

## Purpose and non-goals

Build a standalone, entirely synthetic market matching simulation for visually inspecting the mechanics of price discovery, bid/ask liquidity, spreads, market impact, order cancellations and the behavior of different *fictional* trader types. The market has no exchange connection or relationship to live/paper trading rights.

## LQ-001 hypothesis

**H1:** For identical sell market-order quantity, greater removal of near-touch resting bid liquidity increases sell VWAP slippage relative to the pre-cancellation best bid, assuming the order fills. **H0:** there is no measurable increase. We report both cases with the identical exogenous action tape and pre-event order book. One synthetic run demonstrates a mechanical consequence of a designed model; it is not statistical evidence about real exchanges.

### Baseline vs treatment

1. Generate a 120-tick order tape using `random.Random(seed=20261120)`. Tick 60 is a forced synthetic market SELL for 26 virtual units.
2. Initialize each book with 40 price levels per side, 8 virtual units per level, initial mid 100.0 VCU, tick size 0.1 VCU. Each other tick includes one market or passive limit action from a random ID in a pool of 1,000.
3. Baseline: at tick 60 submit the stress SELL without prior cancellation.
4. Treatment: at tick 60 cancel 85% (integer-rounded per resting order) of bid volume at top eight occupied bid price levels, then submit exactly the same stress SELL.
5. Compare `filled_qty`, `unfilled_qty`, execution VWAP, price slippage in bps, depth on the eight pre-shock best bid price levels, and post-event last-trade price. Resting order price-time priority applies. Any missing liquidity yields a partial fill and explicit `unfilled_qty`, not a fabricated fill.
6. No lookahead, no future market prices, and no real OHLCV are involved.

### Calculations

`VWAP_ticks = SUM(executed_price_ticks * executed_qty) / SUM(executed_qty)`

For a sell:

`slippage_bps = 10000 * (pre_shock_best_bid_ticks - VWAP_ticks) / pre_shock_best_bid_ticks`

`delta_slippage_bps = treatment_slippage_bps - baseline_slippage_bps`

All order matching quantities and prices use integer virtual ticks/units. Presentation VWAP is floating point derived from exact integer trade totals; **there is no financial-accounting ledger in this version**.

### Reproducibility and tests

- Input tape and `Config` have a stable SHA-256 calculated over sorted JSON; no wall-clock timestamp or external source influences replay.
- Identical seed/config must yield byte-identical sorted JSON.
- Same action tape and market state through tick 59 in both scenarios.
- Order book conservation: submitted passive units = remaining units + cancelled units + consumed resting units.
- Priority: better bid/ask before worse; earlier same-price orders before later.
- Unfilled market order residual must be reported, not placed automatically or treated as executed.
- Baseline vs treatment is a *paired deterministic experiment*, not two independently sampled simulations.

## Proposed next versions (not implemented in v0.1)

**v0.2 — Interactive synthetic market:** parameter panels, pause/step/run, candlesticks OHLCV derived only from trades, online bid/ask order-book depth, trader-agent counters, experiment controls. Local browser only. Prefer simple FastAPI and minimal JavaScript after proving the core.

**v0.3 — Agent-based market:** separate market makers/noise/trend/mean-reverting/large-volume agents with synthetic budgets and position conservation, order cancellations, adaptive decisions. Scenario tape identity now requires common random numbers for random streams and documented divergence from endogenous reactions.

**v0.4 — Research quality:** multiple independent seeds, parameter sweeps, confidence intervals, stylized-facts sanity checks, profiling, test coverage, documented limitations, benchmark data generated *within* the synthetic system.

No exchange, external market data, live keys, actual money, paid models, or Kairos AGI dependency is in scope. Future export is research artifacts only and must be separately approved if desired.

## Safety acceptance

1. `python -m market_lab` succeeds entirely offline.
2. `python -m unittest discover -s tests -v` passes.
3. Identical reruns yield identical `results.json` content.
4. No import, network operation, credentials, or exchange routing exists in the application.
5. UI and artifacts mark all outputs `SIMULATED`.
6. A change to the existing production or PAPER trading runtime is not part of this project.