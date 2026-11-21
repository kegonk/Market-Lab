# Market Lab v1.1 — Adaptive Liquidity Control

**Simulated only, offline, VCOIN/VCU.** This is an independent research project, **not** Kairos AGI and **not** a real exchange integration. No APIs, real data, credentials, external deposits, debt, minting or discretionary exchange liquidity.

## Hypotheses, with preregistered criteria

v1.0 showed that extra passive orders can improve an inadequate order book, but more orders did not reliably preserve two-sided depth during BULL / BEAR / THIN demand imbalances. We now compare (a) no controller, (b) symmetrical control (target 5 VCOIN on both sides), (c) separate side-aware control (the weak side's target can double, to at most 50 VCOIN). We test the impact of quote size 1/3/7 and wallet exposure limits 10%/20%, without using experimental results to change the plan.

**Primary**: percentage of individual simulation ticks with both BID and ASK present and at least 5 virtual coins in each side's top eight price levels (`two_sided_tick_percent`). Secondary, *unadjusted-for-multiplicity*: executed trades, percentage of ticks containing trades, total PnL relative to each world's own Buy & Hold, exchange fees, spread, independent wallet survival, controller quote fill/cancel quantities, and viability flag. At 1280 ticks, evaluate effects as paired differences over **10 independent new seeds 401–410**; 320/1280 horizons reuse the same seed and must not be pooled as independent observations.

Full plan: 17 predeclared cells × 2 arms (`frozen`, `adaptive`) × 10 seeds × 2 horizons = **680 simulations / 340 pairs**. Core profile: six LEGACY cells, 240 simulations under the same default seeds/horizons. Profiles are immutable after plan creation. Both arms use the same control mode and the same initial wallets. Adaptation is the additional *policy* distinction; within-arm same-seed policy contrasts isolate scenario differences more directly, though price trajectories still differ.

## Mechanism and accounting

The feature is **opt-in** via `SessionConfig.liquidity_v11_mode`: `OFF` (default), `SYMMETRIC`, or `SIDE_AWARE`. It requires `evolution_enabled=True` and cannot coexist with v1.0 `quote_refresh_mode != 'OFF'`. v1.0/v0.9/v0.8/v0.7 remain unchanged when controller mode is OFF.

At each tick after ordinary participant lifecycle and v1.0 refresh steps, the controller:

1. Observes both sides of the **same pre-controller book** to avoid iteration-order skew, and computes per-side depth targets, at most doubling the deficient side if the other side holds at least twice its depth.
2. For each deficient side, creates at most two limit orders per tick, each at most the configured quote size, at a passive price strictly inside or outside the spread, never crossing existing opposite orders.
3. Selects only currently active, eligible MAKER-strategy wallets, using deterministic rotation with **no extra RNG draws**. No halted or waiting wallet is eligible.
4. Limits each wallet to three currently tracked controller orders and checks its *total* already reserved cash plus mark-valued reserved inventory against 20% (or a separately tested 10%) of current mark-to-market equity. The authoritative economy's `reserve(...)` then holds the exact limit amount plus rounded maker fee for BUY, or coin quantity for SELL. Zero inventory / cap violations result in **no order**, not fabricated liquidity.
5. Counts each submitted order's initial, matched, cancelled, and still-open quantity; observes fills from actual matched order IDs and remaining quantity after the lifecycle step, so review exits, shock cancellation and regime withdrawal reconcile. **Quantity ledger:** `posted_qty = filled_qty + cancelled_qty + remaining_qty` for each side. Closed order lifetime counts ticks from placement to depletion; orders still open at horizon are right-censored.

All existing economy checks (money, coins, FIFO lots, maker/taker fees, reservation funding and order quantity conservation) execute every step. During experimentation no external wallet is credited. The controller can **improve** depth and still increase fee leakage or lower participant performance; no benefit is presumed.

## Plan and CLI (PowerShell, Windows, Python 3.12)

```powershell
cd D:\dev\Market-Lab
py -3.12 -m unittest discover -s tests -v

py -3.12 -m market_lab.research_v11 plan `
  --seeds 401:410 `
  --horizons 320,1280 `
  --profile full `
  --output artifacts/research-v11/plan-001.json

py -3.12 -m market_lab.research_v11 run `
  --plan artifacts/research-v11/plan-001.json `
  --output artifacts/research-v11/results-001

# View progress in a second PowerShell session:
py -3.12 -m market_lab.research_v11 status `
  --plan artifacts/research-v11/plan-001.json `
  --output artifacts/research-v11/results-001

# Resume after interruption:
py -3.12 -m market_lab.research_v11 run `
  --plan artifacts/research-v11/plan-001.json `
  --output artifacts/research-v11/results-001 `
  --resume
```

Do not reuse earlier seed ranges (1–20, 101–120, 201–210, 301–310). The plan is canonical, SHA-256 sealed, non-overwritable, and checked for in-flight changes. Per-arm checkpoint is written atomically and SHA-256 validated. Completed outputs are verified bytewise against `complete.json`; `--resume` reuses verified checkpoints and refuses modified ones.

Results: `summary.json` contains **aggregates only**, `runs.csv`, `paired.csv`, `statistics.csv`, `policy_comparisons.csv`, `windows.csv` (20-tick rows), `report.html`, and `complete.json`. This avoids embedding every per-tick event in the summary; every tick still contributes to window totals and primary rates. Run checkpoints include their own compact window aggregates; the full 680-run research may take considerable CPU time, but interruption is safe.

## Interpretation boundaries

- `frozen` means adaptation of trader strategies is switched off; the optional v1.1 quote controller is **not** switched off except in a policy scenario explicitly named `*_off`. Treat these as different experiment dimensions.
- `makers_relative_to_hold_vcu` measures the **observed** final score of wallets that ever posted controller quotes, not an attribution of PnL to controller orders; selection and price changes are endogenous.
- An order can have both a fill and a partial cancellation; the order-level counts overlap by design, whereas filled / cancelled **quantities** do not.
- Missing liquidity, observed stock-outs, stop-outs and cancellations may overlap and are **not** causally identified explanations.
- For symmetry analysis, compare `legacy_sideaware - legacy_symmetric`; for size/cap ablations compare predeclared direct contrasts. Compare BULL, BEAR, THIN within each scenario's matched seeds, not to unrelated market conditions.
- Open orders are right-censored, so closed lifetime averages omit still-open orders; inspect `*_remaining_qty` alongside lifetimes.
- Limits, 95% confidence intervals and sign tests describe this artificial model with only ten seeds; there is no claim of real-world profit, stability or applicability to real trading venues.
