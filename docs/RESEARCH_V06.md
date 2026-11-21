# Market Lab v0.6 — pre-registered synthetic paired experiment

This is a **synthetic-only** in-model comparison. It is not a calibrated financial
experiment, a profitable strategy demonstration, or evidence about live markets.
No real-world data, exchange clients, credentials, or paid API calls are used.

## Question

Does allowing a synthetic pre-funded population to change strategies and replace
persistently weak traders improve the cohort-wide after-fee result relative to
passively holding the original synthetic assets, compared with the *same*
pre-funded population with strategy changes/replacements frozen?

## Two arms per seed

- **Frozen control:** `evolution_enabled=True`, `adaptation_enabled=False`.
  All 1,000 wallets exist at startup; by default 900 agents are eligible to
  trade and 100 are in a pre-funded reserve. The policy rules remain fixed.
  Risk stops can still apply; there are no adaptive replacements.
- **Adaptive treatment:** `evolution_enabled=True`, `adaptation_enabled=True`.
  Identical wallets, reserve membership, initial order book, RNG seed, fees,
  shock configuration, and matching engine. Weak-review decisions may change
  rules, retire accounts and admit pre-funded replacements.

The model verifies identical **account/order state at initialization** and
identical **account/order state through the tick preceding the first review**.
After adaptation begins, differing strategies can result in different RNG
consumption, orders, executions, prices and fees. This is **not** an
identical-action-tape mechanical test (that is v0.1 LQ-001).

## Measures

For each agent `A0000`..`A0999`, compute at the final price of its own arm:

```
relative_vs_hold_ticks = (final_cash_ticks + final_coin_qty * final_price_ticks)
                         - (initial_cash_ticks + initial_coin_qty * final_price_ticks)
```

- Holding benchmark is the agent's own **original** cash and coin holdings,
  valued at the **same end price** as the policy's final balance.
- The result is **after fees** because fee payments have already reduced wallet
  cash (or proceeds) and are transferred to the `EXCHANGE` wallet.
- Sum across **all 1,000 agent wallets**: active, waiting, and retired.
  This prevents excluding unsuccessful agents (survivorship bias).
- Include per-agent mean, counts ahead/behind Hold, gross trading activity,
  total exchange fees, price, active and retired agents, number of strategy
  switches, number of blocked switches, switches per 1,000 active-agent-ticks,
  and original-cohort survival rates.
- Survival by original strategy uses the **initially active 900 agents** as
  denominator; newcomers are not included in the original cohort numerator.
  Therefore it is **not** a strategy-specific causal treatment estimate.

The aggregate after-fee agent score need not sum to zero, because the model also
contains service participants `MAKER` and `STRESS` and the `EXCHANGE` fee wallet.
A positive score for one strategy or arm is not evidence of an edge in a real
market. End prices can differ across the arms; interpret paired differences
as descriptive *within-model* results, not independent investment returns.

## Strategy dwell

`min_strategy_hold_ticks` prevents switching again until at least that many
simulation ticks have elapsed since admission/last switch. It does **not**
block risk stops or retirement after configured consecutive weak reviews.
The default is 40 ticks. `0` reproduces earlier unconstrained switching
behavior for the adaptation policy (subject to other v0.6 changes).

## Repeatable Windows PowerShell run

```powershell
cd D:\dev\Market-Lab
py -3.12 -m market_lab.research --seeds 42,43,44 --ticks 320 --review-every 20 --min-hold 40 --output artifacts/research-v06/experiment-001
Start-Process .\artifacts\research-v06\experiment-001\report.html
```

Files saved locally; `artifacts/` is Git-ignored and is **not** uploaded to
GitHub. All output files are UTF-8; CSV includes a BOM for Excel compatibility.

- `summary.json` — complete configuration and outcome data for both arms,
  seed-level deltas and mean/median deltas across seeds.
- `runs.csv` — one row per seed/arm with raw comparable outcomes.
- `paired.csv` — one row per seed with treatment-minus-control differences.
- `report.html` — readable offline summary, no remote CSS/JS dependencies.

To prevent accidental loss of results, writing to an existing experiment folder
fails unless explicitly given `--overwrite`; choose another `--output` for new
experiments. Outputs are deterministic for identical arguments; no timestamps.

`--seeds` accepts 1 to 20 distinct integer seeds. The default list is
`42,43,44`. The number of seeds is small for a quick demonstration, **not**
adequate for claiming statistical significance or a generalizable improvement.
For follow-up work, preselect larger seed sets, tune only on separate seeds,
report uncertainty and control multiple comparisons. Current code does not
implement a confidence interval or multi-parameter grid search.

## Safety and verification

Run `py -3.12 -m unittest discover -s tests -v` before comparing.
The experiment verifies money/coin conservation, FIFO accounting identity and
population integrity after every tick, and the paired initialization/pre-review
identity for each seed. It does not contact any remote endpoint. Browser UI is
bound to 127.0.0.1, and changes in its settings require starting a new run.
