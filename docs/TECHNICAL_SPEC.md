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

## v0.2 — Browser-based synthetic market (implemented)

A standalone localhost-only HTTP simulator at `python -m market_lab.web`.
No external packages, prices, exchange endpoints, trading capital, or Kairos imports.
The UI displays mutable book levels, OHLCV bars derived from actual *synthetic*
executed fills, per-agent-type activity counters, execution tape, shock details,
and controls for play, pause, step, speed, and parameter reset.

### Determinism and agent behavior

- Integer price ticks, integer quantities and price/time FIFO within equal levels.
- One `random.Random(seed)` per independent session. No wall-clock input to matching.
- 1000 possible agent identifiers, default six decisions per simulation tick.
- NOISE (50%) alternates random market/passive orders; MAKER (20%) adds passive
  orders; TREND (20%) chooses the directional side of a recent price change;
  REVERSION (10%) chooses the opposite. These are simplified reactive rules,
  not wealthy, rational, independently capital-limited market participants.
- The forced synthetic SELL happens on the same configured tick in baseline
  and treatment. The treatment removes up to 85% (configurable) of top-eight
  BID-level quantity first, with the same rounding as LQ-001. Subsequent
  endogenous behavior can diverge: this is **not** the fixed-tape paired test.
- For candles without trades, an explicitly zero-volume flat OHLC is emitted;
  these zero-activity bars do not imply that a trade took place.
- Loopback server offers GET `/api/state` and JSON POST `/api/step`,
  POST `/api/reset`. No order-related endpoint exists. Request parameters,
  sizes, hostnames, origins and fetch-site context are bounded/validated.
- No external CDN, analytics, API key, exchange SDK, or LLM; no cross-origin
  sharing. Browser JS interacts only with the local Python server.

### v0.2 acceptance

1. `python -m unittest discover -s tests -v` passes both v0.1 and v0.2 tests.
2. Browser UI loads via `python -m market_lab.web`, play/step/pause/reset
   operate exclusively on synthetic local state.
3. Same seed/config gives identical final serialized state and trade tape.
4. Baseline and treatment have identical state before the forced sell.
5. After shock a CANCEL is applied to resting BID volume, followed by one
   aggressive SELL; unfilled quantity is explicit, never invented.
6. All statuses remain SIMULATED. No real market data or linked Kairos paths.

## v0.3 — Closed synthetic economy (implementation slice)

- `market_lab/economy.py` owns an **in-memory**, zero-credit, zero-fee ledger of 1,000 synthetic agent wallets plus `MAKER` (initial depth) and `STRESS` (the forced sale).
- Unit accounting uses *integer* quote ticks (one tick = 0.1 VCU) and whole VCOIN. The dashboard converts to displayed VCU only; no floating-point cash mutation occurs.
- Each wallet has owned cash/coin plus reserved cash/coin supporting resting passive orders. A BUY limit locks `price_ticks × quantity`, a SELL limit locks quantity. Insufficient collateral rejects the order.
- A market SELL is bounded by free VCOIN; a market BUY is matched only to quantity affordable at each ask level using free VCU. Self-matching is prevented; maker/taker transfers are recorded from matched resting-order identities.
- Trade settlement updates both counterparties, releases filled maker reservations, and cannot create cash or VCOIN. The liquidity-withdrawal shock returns exact cancelled BID reserves to original agents.
- Simulation verifies after *each tick* (1) cash total unchanged, (2) coin total unchanged, (3) wallet balances nonnegative, (4) every wallet's locked collateral equals remaining resting orders, and (5) order-book quantity conservation.
- Leaderboard mark-to-market PnL is `cash + coin × last_trade_price − initial_wealth`; aggregate marked wealth changes when the **price** changes despite conservation of assets. This is a valuation, not realized profit, and is not a source of real trading alpha.
- UI reports wallet counts, settled fills, rejected attempts, aggregate marked PnL, leaders and laggards. Synthetic cash/coin held by MAKER/STRESS are included in conservation but excluded from individual agent leaderboards.
- The existing v0.1 paired deterministic tape and v0.2 chart controls remain available. `python -m unittest discover -s tests -v` exercises original tests plus accounting-specific cases.

### Explicit non-goals for v0.3

No outside data, exchange connectivity, paid models, live orders, real balances, borrowing, margin calls, liquidation, bankruptcies, fees, agent deposit/withdrawal, durable order ledger, or reliable profit forecasting. A negative marked PnL does not imply bankruptcy; future versions may define insolvency rules separately. This isolated research code is **not** the Kairos AGI `PAPER` or `SHADOW` runtime.
