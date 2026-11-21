# Market Lab v1.0 — Liquidity & Population Dynamics

## Scope and guarantees

This is an **offline-only, synthetic VCOIN/VCU exchange laboratory**. It is **independent of Kairos AGI** and has no real exchanges, funds, prices, APIs or accounts. The only sources of cash and coins are the initially funded, closed set of wallets. There is **no minting, top-up, borrowing, privilege for a maker, or import of outside liquidity**. All reservations, fills, fees and FIFO are handled by the original tested economy and book. Asset conservation is enforced after each simulation tick.

v1.0 runs **alongside**, not instead of, the v0.9 and earlier study modules. Its new settings default to OFF, so previous experiment configurations retain their old paths. A liquidity-policy experiment requires `evolution_enabled=True`.

## New optional quote replenishment

`SessionConfig.quote_refresh_mode` has three values:

| Mode | Existing wallets allowed to post extra passive quotes |
|---|---|
| `OFF` (default) | None — v0.9 behavior |
| `MAKER_ONLY` | Active, funded members whose **current** rule is `MAKER` |
| `ALL_FUNDED` | Any active, funded member (explicit alternative behavioural assumption) |

`quote_refresh_min_depth=5` is the minimum target per side in the first eight book levels. After ordinary agent actions, the artificial regime's cancellations and population review, the policy tests each side separately. If a side is below the target, it attempts **at most one extra passive limit order per side and tick**, of at most three existing VCOIN units. BUY fully reserves owner cash **including maker fee**; SELL fully reserves owner coins. Quotes cannot cross the opposite best order. A wallet with insufficient free inventory is skipped without risking a denial-induced halt. A stopped wallet, reserved inventory, or nonparticipant is never used. No RNG draw is consumed by quote replenishment, so the original event-tape generator is unchanged until the markets diverge.

This is **not** a free liquidity engine. The quotes use finite holdings and can later execute or disappear. Replenishment may lower wallet performance or become ineffective when usable funds/coins run out. It is a hypothesis about agent participation, not a proof that agents would voluntarily quote in a real market.

## Pre-registered study

A v1.0 `plan.json` fixes 10 scenarios, two arms (frozen and adaptive), all seeds, horizons, and the criteria **before** execution. Default new seed range: **301–310**; defaults horizons: **320, 1280** ticks. This yields **400 simulation arms and 200 paired comparisons**. `--profile core` restricts the experiment to six legacy-policy scenarios (240 arms, 120 pairs). Independent seed sets should be used for future confirmation rather than selecting favorable ones post hoc.

| Scenario | Regime | Re-entry | Reserve join | Policy exit | Quote refresh |
|---|---|---|---|---|---|
| `legacy_baseline` | LEGACY | off | on | on | OFF |
| `legacy_reentry` | LEGACY | on | on | on | OFF |
| `legacy_maker_refresh` | LEGACY | on | on | on | MAKER_ONLY |
| `legacy_all_refresh` | LEGACY | on | on | on | ALL_FUNDED |
| `legacy_no_inflow` | LEGACY | off | off | on | MAKER_ONLY |
| `legacy_no_exit` | LEGACY | off | off | off | MAKER_ONLY |
| `bull_maker_refresh` | BULL | on | on | on | MAKER_ONLY |
| `bear_maker_refresh` | BEAR | on | on | on | MAKER_ONLY |
| `sideways_maker_refresh` | SIDEWAYS | on | on | on | MAKER_ONLY |
| `thin_maker_refresh` | THIN | on | on | on | MAKER_ONLY |

Frozen arms still use the *specified quote-refresh and population-policy options*, but never adapt rules; they are matched within their own scenario. Direct comparisons of two adaptive policies on the **same seed and horizon** are exported separately as `policy_comparisons.csv`; prices are **endogenous** and differ, so these are not strict individual-level causal effects.

## Every-tick observations

Unlike the 20-tick snapshots in v0.9, `tick_health.csv` has **one record per simulated tick** for each market arm. `health.csv` additionally summarizes every 20-tick interval. Every record includes presence/absence of BID/ASK, accessible first-eight-level depth, last-trade price and spread (null if one side is missing), filled trades, volume and actual exchange fees, active population, maker quote withholding, model cancellations, shock cancellations, and attempted/accepted/refused quote refresh.

`maker_bid_stockout` or `maker_ask_stockout` is **1** only when a side is below the fixed depth threshold and there is no active `MAKER` wallet with enough *unreserved* holdings to post a one-unit order at the current passive quote price. This is a **stock-out indicator**, not proof that inventory shortage caused missing liquidity. Cancellation, adverse price change, withdrawals of quotes, too-few maker actions, and insufficient funds can coincide. v1.0 deliberately does **not** assign mutually exclusive or causal reason labels. Experimentation with targeted interventions is needed for causal attribution.

Statistics include share of ticks with quotes on each side, with both sides sufficiently deep, with a trade, and without quotes; maximal consecutive no-bid/no-ask/no-trade streaks; mean spread over *observed* two-sided ticks, plus a separate percentage of ticks where spread is observable. Reports never treat missing spread as zero. Active-agent exposure, fees and result relative to passive Hold are reported separately to avoid concealing inactivity behind profitability. All original 1000 prefunded wallets remain in the PnL benchmark.

## Pre-declared viability heuristic

A simulated run is `viable=1` only if **all** of the following hold at the end:

- At least **60%** of the original 900 active agents remain active (not necessarily the same identities).
- Adequate two-sided depth (at least **5** available VCOIN per side in the top eight levels) exists on at least **70% of all ticks**.
- At least one matched trade occurs on at least **35% of ticks**.
- Longest consecutive interval without either best BID or ASK does not exceed **25 ticks**.

These cutoffs are fixed in the SHA-256-protected plan and are **arbitrary laboratory heuristics**, not assurances of stability or real-market liquidity. Conservation is a mandatory independent gate for every run.

## Run on Windows PowerShell / Python 3.12

```powershell
cd D:\dev\Market-Lab
py -3.12 -m unittest discover -s tests -v

py -3.12 -m market_lab.research_v10 plan `
  --seeds 301:310 `
  --horizons 320,1280 `
  --profile full `
  --output artifacts/research-v10/plan-001.json

py -3.12 -m market_lab.research_v10 run `
  --plan artifacts/research-v10/plan-001.json `
  --output artifacts/research-v10/results-001
```

To monitor from a **separate** PowerShell session:

```powershell
py -3.12 -m market_lab.research_v10 status `
  --plan artifacts/research-v10/plan-001.json `
  --output artifacts/research-v10/results-001
```

After a crash/interruption use the same `run` with `--resume`. Its per-arm checkpoint files are written atomically and each is independently checked by SHA-256. Completed outputs are sealed with SHA-256 of all CSV, JSON and HTML files, so the final report cannot be silently modified. Only one writer may operate on an output directory at a time; `status` is read-only. Do not modify the plan while a run is in progress. To conduct a new experiment, create a fresh plan and output directory; there is no automatic overwriting.

The output directory contains `plan.json`, `checkpoints/`, `summary.json`, `runs.csv`, `paired.csv`, `statistics.csv`, `policy_comparisons.csv`, `tick_health.csv`, `health.csv`, `report.html` and `complete.json`. `summary.json` includes all per-tick observations and can be relatively large for full experiments; prefer CSV for filtering/plotting.

## Interpretation limits

This is a **synthetic economic experiment**, not a simulated live-exchange strategy or financial performance prediction. An accepted limit quote is not an execution. Exchange fees are transferred to the existing EXCHANGE wallet and are counted as real economic transfers in the closed model. Observational labels do not identify causes. Different policy arms affect subsequent order flow and hence prices. Reusing the same seed across 320 and 1280 ticks does not make those observations statistically independent. Intervals treat **seed**, not individual agents or ticks, as the statistical unit. Do not treat unadjusted multiple exploratory confidence intervals as independent discoveries.
