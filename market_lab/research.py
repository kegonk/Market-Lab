"""Paired synthetic research: frozen population vs adaptive population.

No live market feeds, credentials, forecasting or outside connectivity.
Each arm starts with identical pre-funded wallets and RNG seed. The only
initial intervention is whether strategy switching and admissions are enabled.
Subsequent trajectories are endogenous, not identical order tapes.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from hashlib import sha256
from html import escape
import json
from pathlib import Path
from statistics import mean, median

from .interactive import SessionConfig, SyntheticMarket

FIELDS = (
    'relative_vs_hold_vcu', 'mean_relative_per_agent_vcu',
    'ahead_of_hold', 'behind_hold', 'fees_vcu', 'trades', 'volume',
    'last_price_vcu', 'active_agents', 'retired_agents',
    'joins', 'strategy_switches', 'blocked_switches', 'switches_per_1000_agent_ticks',
    'noise_survival_pct', 'maker_survival_pct',
    'trend_survival_pct', 'reversion_survival_pct',
)


def parse_seeds(value: str) -> tuple[int, ...]:
    try:
        seeds = tuple(int(piece.strip()) for piece in value.split(','))
    except ValueError as exc:
        raise ValueError('seeds must be comma-separated integers') from exc
    if not 1 <= len(seeds) <= 20 or len(set(seeds)) != len(seeds):
        raise ValueError('provide 1..20 unique seeds')
    if any(abs(s) > 2**31 for s in seeds):
        raise ValueError('seed outside supported range')
    return seeds


def _fingerprint(market: SyntheticMarket) -> str:
    """Account-level and book-level state; omit mode/config from comparison."""
    content = {
        'book': [(oid, o.agent, o.side, o.price_ticks, o.remaining)
                 for oid, o in sorted(market.book.orders.items())],
        'wallets': [(agent, w.cash_ticks, w.coin_qty, w.reserved_cash_ticks,
                     w.reserved_coin_qty, w.status, w.traded_qty)
                    for agent, w in sorted(market.economy.wallets.items())],
        'price_history': market.price_history,
        'trades': market.trades,
        'volume': market.total_traded_qty,
        'active_ids': sorted(market.population.active),
        'waiting_ids': list(market.population.waiting),
    }
    return sha256(json.dumps(content, sort_keys=True, separators=(',', ':'))
                  .encode('utf-8')).hexdigest()


def _run_arm(config: SessionConfig) -> tuple[dict, str, str]:
    market = SyntheticMarket(config)
    initial = _fingerprint(market)
    pre_review = None
    for _ in range(config.ticks):
        market.step(capture_state=False)  # avoid full dashboard serialization per tick
        if market.tick == min(config.ticks, config.evolution_review_ticks - 1):
            pre_review = _fingerprint(market)
    snap = market.state()
    pop = snap['population']
    if not (snap['economy_conservation_pass'] and snap['conservation_pass']
            and snap['economy']['pnl_identity_pass'] and pop['conservation_pass']):
        raise AssertionError('RESEARCH_INVARIANT_FAILED')
    r = pop['relative_to_hold']
    cohorts = pop['initial_cohort_survival']
    row = {
        'seed': config.seed,
        'arm': 'adaptive' if config.adaptation_enabled else 'frozen',
        'relative_vs_hold_vcu': r['total_relative_vcu'],
        'mean_relative_per_agent_vcu': r['mean_relative_vcu'],
        'ahead_of_hold': r['ahead'], 'behind_hold': r['behind'],
        'fees_vcu': snap['economy']['fees_total_vcu'],
        'trades': snap['total_trades'], 'volume': snap['volume'],
        'last_price_vcu': snap['market']['last_price'],
        'active_agents': pop['active'], 'retired_agents': pop['retired'],
        'joins': pop['admitted'], 'strategy_switches': pop['strategy_switches'],
        'blocked_switches': pop['blocked_switches'],
        'switches_per_1000_agent_ticks': pop['switches_per_1000_agent_ticks'],
        'noise_survival_pct': cohorts['NOISE']['survival_percent'],
        'maker_survival_pct': cohorts['MAKER']['survival_percent'],
        'trend_survival_pct': cohorts['TREND']['survival_percent'],
        'reversion_survival_pct': cohorts['REVERSION']['survival_percent'],
    }
    return row, initial, pre_review


def paired_experiment(seeds: tuple[int, ...], *, ticks: int = 320,
                      shock_tick: int | None = None, review_every: int = 20,
                      min_hold_ticks: int = 40, waiting_agents: int = 100,
                      weak_reviews_to_exit: int = 3,
                      maker_fee_bps: int = 10, taker_fee_bps: int = 20,
                      shock_enabled: bool = True) -> dict:
    seeds = tuple(seeds)
    if not seeds:
        raise ValueError('at least one seed required')
    # Validate even when called from Python, not only through argparse.
    if len(seeds) > 20 or len(set(seeds)) != len(seeds):
        raise ValueError('provide 1..20 unique seeds')
    if any(type(x) is not int or abs(x) > 2**31 for x in seeds):
        raise ValueError('invalid seeds')
    baseline = SessionConfig(seed=seeds[0], ticks=ticks,
                             shock_tick=(min(160, ticks) if shock_tick is None else shock_tick),
                             evolution_enabled=True, adaptation_enabled=False,
                             evolution_review_ticks=review_every,
                             min_strategy_hold_ticks=min_hold_ticks,
                             waiting_agents=waiting_agents,
                             weak_reviews_to_exit=weak_reviews_to_exit,
                             maker_fee_bps=maker_fee_bps, taker_fee_bps=taker_fee_bps,
                             shock_enabled=shock_enabled)
    runs, pairs = [], []
    for seed in seeds:
        base = {**asdict(baseline), 'seed': seed}
        frozen, before0, pre0 = _run_arm(SessionConfig(**base))
        adaptive, before1, pre1 = _run_arm(SessionConfig(**{**base, 'adaptation_enabled': True}))
        if before0 != before1 or pre0 != pre1:
            raise AssertionError('PAIRED_BASELINE_MISMATCH')
        runs.extend((frozen, adaptive))
        pair = {'seed': seed, 'initial_state_match': True,
                'pre_first_review_match': True}
        pair.update({f'delta_{f}': (adaptive[f] - frozen[f]
                                   if frozen[f] is not None and adaptive[f] is not None else None)
                     for f in FIELDS})
        pairs.append(pair)
    aggregates = {}
    for field in FIELDS:
        values = [pair[f'delta_{field}'] for pair in pairs if pair[f'delta_{field}'] is not None]
        aggregates[field] = {
            'mean_delta': round(mean(values), 5) if values else None,
            'median_delta': round(median(values), 5) if values else None,
            'positive_seeds': sum(v > 0 for v in values),
            'negative_seeds': sum(v < 0 for v in values),
            'equal_seeds': sum(v == 0 for v in values),
        }
    return {
        'project': 'Kairos Market Lab', 'version': '0.6', 'source': 'SIMULATED',
        'experiment': 'PAIRED_FROZEN_VS_ADAPTIVE',
        'description': ('Same seed, initial book, funded wallets and reserve membership; '
                        'adaptive and frozen policies differ only in strategy switching/exit/admission. '
                        'After first review, market order flow and price paths may diverge.'),
        'interpretation': ('All-agent after-fee equity minus buy-and-hold of each original cash/coin '
                           'position, evaluated at its own arm\'s final synthetic price. '
                           'Paired differences are descriptive in-model observations, not '
                           'forecasts, statistical significance or live-market returns.'),
        'configuration': {k: v for k, v in asdict(baseline).items() if k != 'seed'},
        'seeds': list(seeds), 'runs': runs, 'paired_differences': pairs,
        'aggregate_differences': aggregates,
        'integrity': {'all_initial_states_match': True,
                      'all_pre_first_review_states_match': True,
                      'each_run_conservation_pass': True},
    }


def _write_csv(path: Path, fields: list[str], rows: list[dict]) -> None:
    with path.open('w', newline='', encoding='utf-8-sig') as file:
        writer = csv.DictWriter(file, fieldnames=fields, extrasaction='ignore')
        writer.writeheader()
        writer.writerows(rows)


def render_report(result: dict) -> str:
    metrics = [('relative_vs_hold_vcu', 'Результат против Hold, VCU'),
               ('mean_relative_per_agent_vcu', 'На одного агента против Hold, VCU'),
               ('fees_vcu', 'Комиссии биржи, VCU'),
               ('strategy_switches', 'Смены стратегий'),
               ('blocked_switches', 'Заблокировано смен (min hold)'),
               ('switches_per_1000_agent_ticks', 'Смен / 1000 агент-тиков'),
               ('retired_agents', 'Ушедшие'),
               ('active_agents', 'Активные'),
               ('trades', 'Исполнения'),
               ('last_price_vcu', 'Последняя цена, VCU')]
    head = ''.join(f'<th>{escape(label)}</th>' for _, label in metrics)
    rows = []
    for pair in result['paired_differences']:
        seed = pair['seed']
        frozen = next(r for r in result['runs'] if r['seed'] == seed and r['arm'] == 'frozen')
        adaptive = next(r for r in result['runs'] if r['seed'] == seed and r['arm'] == 'adaptive')
        for arm in (frozen, adaptive):
            vals = ''.join(f'<td>{arm[field]:,.2f}</td>' if isinstance(arm[field], float)
                           else f'<td>{arm[field]:,}</td>' for field, _ in metrics)
            rows.append(f'<tr><td>{seed}</td><td>{arm["arm"]}</td>{vals}</tr>')
        vals = ''.join(f'<td>{pair["delta_" + field]:+,.2f}</td>' if isinstance(pair['delta_'+field], float)
                       else f'<td>{pair["delta_"+field]:+,}</td>' for field, _ in metrics)
        rows.append(f'<tr class="delta"><td>{seed}</td><td>Δ adaptive − frozen</td>{vals}</tr>')
    cohort_table = []
    cohort_names = {'noise_survival_pct':'NOISE',
                    'maker_survival_pct':'MAKER',
                    'trend_survival_pct':'TREND',
                    'reversion_survival_pct':'REVERSION'}
    for pair in result['paired_differences']:
        for field, strategy in cohort_names.items():
            seed = pair['seed']
            frozen = next(r for r in result['runs'] if r['seed']==seed and r['arm']=='frozen')
            adaptive = next(r for r in result['runs'] if r['seed']==seed and r['arm']=='adaptive')
            d = pair['delta_' + field]
            cohort_table.append(f'<tr><td>{seed}</td><td>{strategy}</td>'
                                f'<td>{frozen[field]:.1f}%</td><td>{adaptive[field]:.1f}%</td>'
                                f'<td>{d:+.1f} п.п.</td></tr>')
    aggregate = result['aggregate_differences']
    body = ''.join(f'<tr><td>{escape(label)}</td><td>{aggregate[field]["mean_delta"]}</td>'
                   f'<td>{aggregate[field]["median_delta"]}</td>'
                   f'<td>{aggregate[field]["positive_seeds"]} / {aggregate[field]["negative_seeds"]} / '
                   f'{aggregate[field]["equal_seeds"]}</td></tr>' for field, label in metrics)
    return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Market Lab v0.6 · Paired Research</title>
<style>body{{font-family:system-ui,sans-serif;background:#0b1620;color:#e6f2fa;padding:2rem;line-height:1.5}}h1{{color:#74e8cb}}h2{{margin-top:2rem}}.tag{{color:#9fbece;letter-spacing:.1em}}.panel{{background:#132535;border:1px solid #385063;border-radius:12px;padding:1rem;overflow-x:auto}}table{{border-collapse:collapse;width:100%;white-space:nowrap;font-size:.83rem}}th,td{{padding:.65rem;text-align:right;border-bottom:1px solid #324656}}td:first-child,td:nth-child(2),th:first-child,th:nth-child(2){{text-align:left}}.delta{{background:#173b37}}.note{{border-left:3px solid #79d3c1;padding:1rem;background:#112b30}}code{{color:#b4e9de}}</style></head><body>
<div class="tag">SIMULATED · SYNTHETIC ONLY · NO EXTERNAL DATA · v0.6</div><h1>Kairos Market Lab · парное сравнение эволюции</h1><p>Seeds: {escape(', '.join(map(str,result['seeds'])))}. Тиков в опыте: {result['configuration']['ticks']}. Минимальное удержание стратегии: {result['configuration']['min_strategy_hold_ticks']} тиков.</p>
<div class="note"><strong>Метод.</strong> В обеих ветках одинаковые seed, исходный стакан, виртуальные кошельки и резерв агентов. Контрольная популяция остаётся «замороженной», экспериментальная может менять стратегии и участников. После первых проверок торговые события и конечные цены могут различаться.<p>Сравниваем <strong>результат против Buy &amp; Hold после комиссий</strong> в каждом мире по цене своего мира. В расчёт включены все 1000 агентов, в том числе резервные и ушедшие — без исключения проигравших. Сводные разности не являются доказательством статистической значимости или прогнозом реального рынка.</p></div>
<h2>Все запуски и парные разницы</h2><div class="panel"><table><thead><tr><th>Seed</th><th>Режим</th>{head}</tr></thead><tbody>{''.join(rows)}</tbody></table></div>
<h2>Выживаемость исходных когорт (без новичков)</h2>
<div class="panel"><table><thead><tr><th>Seed</th><th>Исходная стратегия</th><th>Frozen</th><th>Adaptive</th><th>Разница</th></tr></thead><tbody>{''.join(cohort_table)}</tbody></table></div>
<h2>Сводка разностей (adaptive − frozen)</h2><div class="panel"><table><thead><tr><th>Метрика</th><th>Средняя Δ</th><th>Медианная Δ</th><th>Seeds + / − / 0</th></tr></thead><tbody>{body}</tbody></table></div>
<p>Проверка совпадения исходных состояний и до первой оценки: <strong>PASS</strong>. Проверка сохранения виртуальных активов после каждого тика во всех запусках: <strong>PASS</strong>.</p><p>Сырые данные: <code>summary.json</code>, <code>runs.csv</code> и <code>paired.csv</code>. Исходники не обращаются к реальным биржам.</p></body></html>'''


def save_experiment(result: dict, output: Path, *, overwrite: bool = False) -> list[Path]:
    if output.exists() and any((output / name).exists() for name in
                              ('summary.json', 'runs.csv', 'paired.csv', 'report.html')) and not overwrite:
        raise FileExistsError('Research files already exist. Choose another --output or pass --overwrite.')
    output.mkdir(parents=True, exist_ok=True)
    targets = [output/name for name in ('summary.json', 'runs.csv', 'paired.csv', 'report.html')]
    targets[0].write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)+'\n', encoding='utf-8')
    _write_csv(targets[1], ['seed', 'arm', *FIELDS], result['runs'])
    _write_csv(targets[2], ['seed', 'initial_state_match', 'pre_first_review_match',
                            *(f'delta_{f}' for f in FIELDS)], result['paired_differences'])
    targets[3].write_text(render_report(result), encoding='utf-8')
    return targets


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description='Fully synthetic paired population comparison (offline)')
    parser.add_argument('--seeds', default='42,43,44', help='1..20 distinct integer seeds')
    parser.add_argument('--ticks', type=int, default=320)
    parser.add_argument('--shock-tick', type=int, default=None)
    parser.add_argument('--review-every', type=int, default=20)
    parser.add_argument('--min-hold', type=int, default=40)
    parser.add_argument('--waiting-agents', type=int, default=100)
    parser.add_argument('--weak-reviews-to-exit', type=int, default=3)
    parser.add_argument('--maker-fee-bps', type=int, default=10)
    parser.add_argument('--taker-fee-bps', type=int, default=20)
    parser.add_argument('--no-shock', action='store_true')
    parser.add_argument('--output', default=None, help='Output directory (default: artifacts/research-v06/<stable experiment id>)')
    parser.add_argument('--overwrite', action='store_true', help='Replace existing experiment files')
    args = parser.parse_args(argv)
    try:
        seeds = parse_seeds(args.seeds)
        params = dict(ticks=args.ticks, shock_tick=args.shock_tick,
                      review_every=args.review_every, min_hold_ticks=args.min_hold,
                      waiting_agents=args.waiting_agents,
                      weak_reviews_to_exit=args.weak_reviews_to_exit,
                      maker_fee_bps=args.maker_fee_bps, taker_fee_bps=args.taker_fee_bps,
                      shock_enabled=not args.no_shock)
        # Validate arguments before simulation and write any output.
        SessionConfig(seed=seeds[0], ticks=args.ticks,
                      shock_tick=args.shock_tick if args.shock_tick is not None else min(160,args.ticks),
                      evolution_enabled=True,
                      evolution_review_ticks=args.review_every,
                      min_strategy_hold_ticks=args.min_hold,
                      waiting_agents=args.waiting_agents,
                      weak_reviews_to_exit=args.weak_reviews_to_exit,
                      maker_fee_bps=args.maker_fee_bps, taker_fee_bps=args.taker_fee_bps)
        label = sha256(json.dumps({'seeds': seeds, **params}, sort_keys=True).encode('utf8')).hexdigest()[:12]
        output = Path(args.output) if args.output else Path('artifacts/research-v06') / f'experiment-{label}'
        if output.exists() and any((output / name).exists() for name in
                                   ('summary.json', 'runs.csv', 'paired.csv', 'report.html')) and not args.overwrite:
            raise FileExistsError('Output already exists. Use --overwrite or change --output.')
        result = paired_experiment(seeds, **params)
        paths = save_experiment(result, output, overwrite=args.overwrite)
    except (ValueError, FileExistsError) as exc:
        parser.error(str(exc))
    print(f'PAIRED_RESEARCH=PASS seeds={len(seeds)} runs={2*len(seeds)}')
    print('INVARIANTS=PASS INITIAL_PAIR=PASS PRE_REVIEW_PAIR=PASS')
    for path in paths:
        print(f'FILE={path}')


if __name__ == '__main__':
    main()
