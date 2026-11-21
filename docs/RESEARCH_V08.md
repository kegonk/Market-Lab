# Market Lab v0.8 — holdout, fixed-price shadow replay, checkpoint/resume

**Everything is synthetic.** This repository has no connections to any live exchange, real financial instruments, trading credentials, external price source or Kairos AGI. Python standard library only. The module `market_lab.research_v08` is a separate, **offline** research lane; it does not change v0.1–v0.7 behavior or the localhost UI.

## Why v0.8 exists

v0.7 found a positive mean difference between evolving and frozen economies in 19/20 seeds, but those seeds (`1..20`) were reused in model development and may contain selection bias. Two endogenous markets also diverge in price, order flow, fills and liquidity. v0.8 therefore predeclares previously unused seeds `101..120`, examines horizons `320`, `640`, `1280`, and adds a **distinct** common-price test that is not confused with actual order-book execution.

The primary result remains full-economy aggregate **after-fee equity of all 1000 originally funded virtual agents minus passive Hold**, valued at each endogenous arm's *own* final synthetic price. The paired difference is **adaptive − frozen**. This is a *policy/economy* outcome, not a clean individual causal effect. Individual traders or horizons are not separate independent samples for inferential calculations; **seed** is the unit, and the same seeds recur across horizons. Confidence intervals use seed-level Student-t estimates and secondary comparisons are exploratory and uncorrected for multiplicity.

## Procedure in PowerShell (no ZIP, all updates through GitHub)

First verify the checkout and tests on the new v0.8 GitHub branch. Then from `D:\dev\Market-Lab`:

```powershell
py -3.12 -m market_lab.research_v08 plan `
  --seeds 101:120 --horizons 320,640,1280 `
  --output artifacts/research-v08/plan-001.json

py -3.12 -m market_lab.research_v08 run `
  --plan artifacts/research-v08/plan-001.json `
  --output artifacts/research-v08/results-001
```

The tool prints `ARM_SAVED=X/120 SEED=... TICKS=... MODE=...` after **every completed simulation arm**, then prints `RESEARCH_PASS=YES INTEGRITY_PASS=YES PAIRS=60 ARMS=120` when complete. All 120 simulation arms include per-tick accounting invariants. Runtime scales with the number of ticks; this is more substantial than the v0.6 exploratory run. Keep your computer awake for a long run.

In a **second terminal**, inspect progress without touching the running process:

```powershell
py -3.12 -m market_lab.research_v08 status `
  --plan artifacts/research-v08/plan-001.json `
  --output artifacts/research-v08/results-001
```

After interruption, restart with the **same** plan and output folder:

```powershell
py -3.12 -m market_lab.research_v08 run `
  --plan artifacts/research-v08/plan-001.json `
  --output artifacts/research-v08/results-001 --resume
```

Each completed `(seed, horizon, arm)` is written atomically to an individually SHA-256-checked JSON checkpoint under `results-001/checkpoints/`. `--resume` verifies and reuses completed arms. A crash *during* one arm loses **only that in-progress arm**; the simulator **does not** resume from an arbitrary intra-arm tick. SHA-256 detects accidental corruption, **not malicious rewriting** (no digital signature). A wrong plan, altered checkpoint, or unexpected conflicting result refuses reuse. Existing completed outputs are only checked and never overwritten by default.

For a deliberate short initial batch, `run --max-new-arms 2` writes two complete arms and pauses, then `run --resume` continues. This is also tested automatically. Plan input is canonical and checksummed; seed choices must be >=101, distinct, 2–100 total, and not from prior experiments (`1..20`, `42..44`). Default values are the predeclared holdout configuration. A new plan requires a **new file path**; no edits in place are permitted.

## Common-price shadow test — limitations are essential

Each frozen economy produces one **synthetic** end-of-tick price trajectory. Four entirely separate small shadow wallets (one per strategy *proxy*) receive that **same immutable price tape**, identical one-action-per-five-ticks decision opportunities, fixed initial holdings, and synthetic fees. Strategy rule proxies:

- `NOISE` random BUY/SELL.
- `TREND` direction of 8-tick price change.
- `REVERSION` inverse of 8-tick price change.
- `MAKER` submits a passive quote 2 price ticks away, filled **on the next tick only if the reference tape crosses the quote**. This is a deliberately simplified maker proxy, not price-time matching in the real synthetic book.

For the other three proxies a hypothetical taker trade fills at the reference price +/- 1 tick. Each proxy has 1000 VCU and 10 VCOIN at start; prices and cash are kept in integer 0.1 VCU increments, fees charged to the independent proxy. Every result is compared to passive Hold of that proxy's **own initial holdings** at the same terminal price. The shadow test never injects orders into the actual limit order book and therefore cannot change that tape. It deliberately omits real queues, slippage, market impact, inventory financing and the actual agents' limit-order routing. **Do not interpret shadow results as literal outcomes of the original market strategies, causal identification, or trading performance.** The shadow comparison helps isolate a *simplified rule signal under common prices*, not a full matching-counterfactual execution.

## Cohort tracking and first-change panels

All 1000 initial wallets enter terminal cohort statistics:

- `original_NOISE`, `original_MAKER`, `original_TREND`, `original_REVERSION`: 900 initial active agents in total.
- `admitted_reserve`: initially inactive, later admitted.
- `remaining_reserve`: never admitted, including ineligible reserves.

An original active trader who later retires **remains in its original cohort**; a reserve trader who later retires **remains in admitted_reserve**. No survivor-only filtering.

For each individual who switches at least once, `agent_changes.csv` records **the first strategy change only**, its synthetic return vs Hold immediately after that review, and the same wallet's return **20 ticks later**, including if retired. Changes with no 20-tick follow-up before the horizon are **explicitly censored**. This analysis is descriptive, uses the evolving endogenous market price and is not proof that the switch caused the subsequent return. To estimate cause, an experimental counterfactual with independent execution opportunities would be needed.

## Outputs and audit trail

`plan.json` (immutable original plan copy), `checkpoints/` (atomic per-arm evidence), `summary.json`, `runs.csv`, `paired.csv`, `statistics.csv`, `cohorts.csv`, `agent_changes.csv`, `shadow.csv`, `shadow_statistics.csv`, `report.html`, `complete.json` (SHA-256 of each final file). The final autonomous HTML has no remote scripts, CDN or HTTP calls and is suitable for offline review. The `complete.json` seal verifies every finished file on subsequent `--resume` calls. `status` checks plan and each checkpoint without modifying them.

### Basic acceptance

```powershell
py -3.12 -m unittest discover -s tests -v
py -3.12 -m market_lab.research_v08 plan --seeds 101:102 --horizons 40 --output artifacts/research-v08/smoke-plan.json
py -3.12 -m market_lab.research_v08 run --plan artifacts/research-v08/smoke-plan.json --output artifacts/research-v08/smoke --max-new-arms 1
py -3.12 -m market_lab.research_v08 status --plan artifacts/research-v08/smoke-plan.json --output artifacts/research-v08/smoke
py -3.12 -m market_lab.research_v08 run --plan artifacts/research-v08/smoke-plan.json --output artifacts/research-v08/smoke --resume
```

The smoke test uses a separate path; running it again without changing paths is intentionally rejected to prevent replacing evidence.
