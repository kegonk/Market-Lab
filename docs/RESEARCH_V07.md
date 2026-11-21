# Market Lab v0.7 — pre-registered synthetic ablations

Only artificial VCOIN/VCU, funded virtual participants and local files. No real data, exchange clients, Kairos AGI or credentials.

## Experimental unit and identification

The **seed**, not an individual trader, is the independent observation for inference.
Within each cell, frozen/adaptive arms share the same seed, initial inventory, book, policies until the first review and fixed scenario parameters. The treatment changes which population mechanisms are permitted. After treatment, quotes, trades and end prices can diverge endogenously: the paired delta is an **entire-economy outcome**, not an isolated trader effect.

Eight ablations use 3 binary switches: S=strategy switching, E=retirement after weak reviews, J=admission from pre-funded waitlist. `000` is frozen, `111` full adaptation. **J needs vacancies**: `001` and `101` can be behaviorally identical to modes without J unless independent risk stops create vacancies. Risk stops remain active in all arms. If exit is disabled, weak reviews do not trigger voluntary exit but may trigger switches.

Hold sensitivity includes baseline 40, plus 80 and 160; fee sensitivity includes zero, baseline 10/20 bps, and doubled 20/40 bps; shock sensitivity toggles 85% BUY-liquidity cancellation at the configured tick. The matched aggressive stress SELL order still occurs in both arms. Changing fee/shock means comparing an adaptive run with a **newly matched frozen run at that same scenario setting**, not recycling a baseline with incompatible assumptions. Parameter combinations are *one-factor-at-a-time*, not a full causal factorial decomposition.

## Protocol — lock the plan before simulation

```powershell
cd D:\dev\Market-Lab
py -3.12 -m market_lab.research_v07 plan --seeds 1:20 --ticks 320 --min-hold 40 --output artifacts/research-v07/plan-001.json
py -3.12 -m market_lab.research_v07 run --plan artifacts/research-v07/plan-001.json --output artifacts/research-v07/results-001
Start-Process .\artifacts\research-v07\results-001\report.html
```

For a quick check, choose `--seeds 1,2 --ticks 40` and a **different** plan/output path. To expand the confirmatory sample, set `--seeds 1:100` **before generating the plan**. The v0.7 matrix contains 13 paired cells per seed and may be computationally intensive for 100 seeds. The tool rejects replacing an existing plan. Results refuse overwrite by default.

The plan JSON declares the precise ordered list of treatments, baseline config and seed set; a canonical SHA-256 is verified both before the run and its bytes compared again after it. The outputs include that hash in all-run metadata and saved `plan.json` so results can be traced to their locked inputs.

## Endpoints and inference

Prespecified primary endpoint: difference in the aggregate **all-agent after-fee equity minus Buy & Hold** measured at each arm's own final synthetic price (VCU), `adaptive - frozen`, in the `ablation_111` cell. Includes 1000 original funded wallets, even inactive/retired. If the exchange charges a fee, it goes to the model exchange account and the comparison remains after-fee for the traders.

Secondary metrics: per-agent mean, median, P10, P90 and winner percentage relative to Hold, fees, executions, volume, final price, switching, retirements, admissions, and active agent count. Percentiles use linear rank interpolation on 1000 after-fee relative PnL amounts (VCU). P10 and P90 are **cross-sectional descriptive statistics**, not confidence interval bounds.

`statistics.csv` contains for **every** metric, cell and arm/delta: n, mean, median, and two-sided 95% Student-t confidence interval for the **mean across seeds** (df=n−1), with exact small-df t constants and standard asymptotic expansion after df 30. Also an unadjusted two-sided exact sign-test p value (ties omitted). For n=1 the confidence interval is unavailable. This analysis requires approximately independent representative seeds; post hoc selection of seeds, multiple comparisons and all other modeling choices restrict causal or inferential interpretation. Secondary tests are exploratory and NOT multiplicity adjusted. Hold, fee, shock and ablation comparisons are **not** proof of live-trading profitability.

## Output

- `plan.json` — locked plan and SHA-256
- `summary.json` — complete result and invariants
- `runs.csv` — each seed × cell × arm
- `paired.csv` — each matched pair delta
- `statistics.csv` — per-cell metric CI and exact sign test
- `report.html` — offline, self-contained human report

Built-in tests must pass. The CLI performs all per-tick conservation checks and validates matching initial/pre-review fingerprint for every pair. No external dependencies or HTTP calls are made by the research CLI.
