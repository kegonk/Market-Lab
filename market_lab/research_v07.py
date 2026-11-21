"""v0.7: pre-registered, paired synthetic ablations and distributional analysis.

CLI: python -m market_lab.research_v07 plan ...; ... run ...
Offline only. No connections, forecasts, credentials or real orders.
"""
from __future__ import annotations

import argparse
import csv
from dataclasses import asdict
from hashlib import sha256
from html import escape
import json
from math import comb, sqrt
from pathlib import Path
from statistics import mean, median, stdev

from .interactive import SessionConfig, SyntheticMarket
from .research import _fingerprint

SCHEMA = 'MARKET_LAB_V07_PLAN_1'
# Prespecified primary outcome; secondary metrics are exploratory.
PRIMARY = 'relative_vs_hold_vcu'
METRICS = (
    'relative_vs_hold_vcu', 'mean_relative_per_agent_vcu',
    'median_relative_vcu', 'p10_relative_vcu', 'p90_relative_vcu',
    'ahead_percent', 'fees_vcu', 'trades', 'volume', 'last_price_vcu',
    'strategy_switches', 'retired_agents', 'joins', 'active_agents',
)
# Two-sided 95% Student-t quantiles (df 1..30); normal approximation with
# Cornish-Fisher terms for df > 30 avoids a third-party SciPy requirement.
T95 = (0, 12.7062, 4.3027, 3.1824, 2.7764, 2.5706, 2.4469,
       2.3646, 2.3060, 2.2622, 2.2281, 2.2010, 2.1788, 2.1604,
       2.1448, 2.1314, 2.1199, 2.1098, 2.1009, 2.0930, 2.0860,
       2.0796, 2.0739, 2.0687, 2.0639, 2.0595, 2.0555, 2.0518,
       2.0484, 2.0452, 2.0423)


def _critical(df: int) -> float:
    if df < 1:
        raise ValueError('at least two observations required')
    if df <= 30:
        return T95[df]
    z = 1.959963984540054
    return (z + (z**3 + z) / (4 * df)
            + (5*z**5 + 16*z**3 + 3*z) / (96 * df**2)
            + (3*z**7 + 19*z**5 + 17*z**3 - 15*z) / (384 * df**3))


def _sign_p(values: list[float]) -> float | None:
    nonzero = [v for v in values if v != 0]
    if not nonzero:
        return None
    k = min(sum(v > 0 for v in nonzero), sum(v < 0 for v in nonzero))
    return round(min(1.0, 2 * sum(comb(len(nonzero), i) for i in range(k + 1)) / 2**len(nonzero)), 9)


def interval(values: list[float]) -> dict:
    """Paired-seed mean interval, not a cross-sectional (1000-agent) CI."""
    if not values or any(type(x) not in (float, int) for x in values):
        raise ValueError('finite nonempty numeric observations required')
    if any(not (-float('inf') < v < float('inf')) for v in values):
        raise ValueError('nonfinite observation')
    n = len(values)
    avg = mean(values)
    margin = _critical(n - 1) * stdev(values) / sqrt(n) if n > 1 else None
    return {'n': n, 'mean': round(avg, 6), 'median': round(median(values), 6),
            'ci95_low': round(avg-margin, 6) if margin is not None else None,
            'ci95_high': round(avg+margin, 6) if margin is not None else None,
            'positive': sum(v > 0 for v in values),
            'negative': sum(v < 0 for v in values),
            'zero': sum(v == 0 for v in values),
            'sign_p_two_sided': _sign_p(values)}


def parse_seed_range(raw: str) -> tuple[int, ...]:
    """Accept '1:20' inclusive or '1,2,3'; 1..100 distinct seeds."""
    try:
        if ':' in raw and ',' not in raw:
            parts = raw.split(':')
            if len(parts) != 2:
                raise ValueError('bad range')
            start, end = (int(piece.strip()) for piece in parts)
            if end < start or end - start > 99:
                raise ValueError('range must be ascending and contain at most 100 seeds')
            seeds = tuple(range(start, end + 1))
        else:
            if any(not piece.strip() for piece in raw.split(',')):
                raise ValueError('empty seed')
            seeds = tuple(int(piece.strip()) for piece in raw.split(','))
    except (ValueError, TypeError) as exc:
        raise ValueError('use up to 100 unique seeds: 1:20 or 1,2,3') from exc
    if not 1 <= len(seeds) <= 100 or len(seeds) != len(set(seeds)) or any(abs(s) > 2**31 for s in seeds):
        raise ValueError('seeds must be 1..100 distinct ints <= 2^31')
    return seeds


def _cell(label: str, family: str, switch: bool, exit_: bool, join: bool,
          hold: int, maker: int, taker: int, shock: bool) -> dict:
    return {'id': label, 'family': family, 'switch': switch, 'exit': exit_,
            'join': join, 'min_hold': hold, 'maker_fee_bps': maker,
            'taker_fee_bps': taker, 'shock_enabled': shock}


def make_plan(seeds: tuple[int, ...], *, ticks: int = 320, shock_tick: int | None = None,
              review_every: int = 20, waiting_agents: int = 100,
              min_hold: int = 40, maker_fee_bps: int = 10,
              taker_fee_bps: int = 20, shock_enabled: bool = True) -> dict:
    if type(seeds) not in (tuple, list) or any(type(s) is not int for s in seeds):
        raise ValueError('seeds must be integer sequence')
    seeds = tuple(seeds)
    if not 1 <= len(seeds) <= 100 or len(set(seeds)) != len(seeds) or any(abs(s)>2**31 for s in seeds):
        raise ValueError('1..100 unique seeds required')
    if type(min_hold) is not int or min_hold not in (40, 80, 160):
        raise ValueError('base min-hold must be 40, 80 or 160')
    if type(shock_enabled) is not bool:
        raise ValueError('shock_enabled must be boolean')
    shock_tick = min(ticks, 160) if shock_tick is None else shock_tick
    common = {'ticks': ticks, 'shock_tick': shock_tick,
              'evolution_review_ticks': review_every, 'waiting_agents': waiting_agents,
              'events_per_tick': 6, 'cancel_percent': 85, 'sell_qty': 26,
              'weak_reviews_to_exit': 3, 'max_joins_per_review': 10,
              'bankruptcy_threshold_percent': 20, 'stop_denials': 6,
              'maker_caution_ticks': 35}
    cells = []
    for bits in range(8):
        s, e, j = bool(bits&4), bool(bits&2), bool(bits&1)
        cells.append(_cell(f'ablation_{int(s)}{int(e)}{int(j)}', 'ablation',
                           s, e, j, min_hold, maker_fee_bps, taker_fee_bps, shock_enabled))
    for hold in (40,80,160):
        if hold != min_hold:
            cells.append(_cell(f'hold_{hold}', 'holding_period', True, True, True,
                               hold, maker_fee_bps, taker_fee_bps, shock_enabled))
    # One-factor-at-a-time fee and shock sensitivity; each is compared with
    # its OWN matched frozen arm to isolate identical starting assumptions.
    for name, maker, taker in [('zero',0,0), ('double',2*maker_fee_bps,2*taker_fee_bps)]:
        if (maker,taker) != (maker_fee_bps,taker_fee_bps):
            cells.append(_cell(f'fees_{name}', 'fees', True, True, True,
                               min_hold, maker, taker, shock_enabled))
    cells.append(_cell('shock_toggle', 'shock', True, True, True,
                       min_hold, maker_fee_bps, taker_fee_bps, not shock_enabled))
    # Keep fee sensitivity within supported model rates.
    for c in cells:
        _config(common, c, seeds[0], adaptive=True)
        _config(common, c, seeds[0], adaptive=False)
    body = {'schema': SCHEMA, 'source': 'SIMULATED',
            'primary_endpoint': PRIMARY, 'seeds': list(seeds),
            'common': common, 'cells': cells,
            'methodology': ('Each cell uses a matched frozen arm with identical initial wallets/book/seed. '
                            'Cells are one-factor-at-a-time comparisons, not a factorial causal model. '
                            'All 1000 initially funded wallets enter distribution metrics. '
                            'Outcomes use each arm own final endogenous price. ' 
                            'Intervals use independent seeds as units and are exploratory outside the prespecified primary endpoint.')}
    return {**body, 'sha256': _digest(body)}


def _digest(body: dict) -> str:
    return sha256(json.dumps(body, sort_keys=True, ensure_ascii=True,
                             separators=(',', ':')).encode('utf-8')).hexdigest()


def _config(common: dict, cell: dict, seed: int, *, adaptive: bool) -> SessionConfig:
    return SessionConfig(**common, seed=seed, evolution_enabled=True,
                         adaptation_enabled=adaptive,
                         allow_strategy_switch=cell['switch'],
                         allow_population_exit=cell['exit'],
                         allow_population_join=cell['join'],
                         min_strategy_hold_ticks=cell['min_hold'],
                         maker_fee_bps=cell['maker_fee_bps'],
                         taker_fee_bps=cell['taker_fee_bps'],
                         shock_enabled=cell['shock_enabled'])


def verify_plan(plan: dict) -> bool:
    if not isinstance(plan, dict) or set(plan) != {'schema','source','primary_endpoint',
             'seeds','common','cells','methodology','sha256'}:
        raise ValueError('unknown plan schema or fields')
    if plan['schema'] != SCHEMA or plan['source'] != 'SIMULATED' or plan['primary_endpoint'] != PRIMARY:
        raise ValueError('unsupported research plan')
    if type(plan['sha256']) is not str or _digest({k:v for k,v in plan.items() if k!='sha256'}) != plan['sha256']:
        raise ValueError('PLAN_HASH_MISMATCH')
    seeds=plan['seeds']
    if type(seeds) is not list or not 1<=len(seeds)<=100 or any(type(s) is not int or abs(s)>2**31 for s in seeds) or len(seeds)!=len(set(seeds)):
        raise ValueError('invalid seeds')
    # Require the exact canonical scenario list and all fixed parameters.
    regenerated=make_plan(tuple(seeds), ticks=plan['common']['ticks'],
        shock_tick=plan['common']['shock_tick'],
        review_every=plan['common']['evolution_review_ticks'],
        waiting_agents=plan['common']['waiting_agents'],
        min_hold=plan['cells'][0]['min_hold'],
        maker_fee_bps=plan['cells'][0]['maker_fee_bps'],
        taker_fee_bps=plan['cells'][0]['taker_fee_bps'],
        shock_enabled=plan['cells'][0]['shock_enabled'])
    if plan != regenerated:
        raise ValueError('noncanonical or modified plan')
    return True


def _arm(cfg: SessionConfig) -> tuple[dict, str, str]:
    m = SyntheticMarket(cfg)
    before = _fingerprint(m)
    pre = ''
    for _ in range(cfg.ticks):
        m.step(capture_state=False)
        if m.tick == min(cfg.ticks, cfg.evolution_review_ticks - 1):
            pre = _fingerprint(m)
    if not m.economy.check(m.book) or not m.population.check() or not m.book.check_conservation():
        raise AssertionError('ECONOMY_OR_POPULATION_CONSERVATION_FAILED')
    snap = m.state()
    if not (snap['economy_conservation_pass'] and snap['conservation_pass']
            and snap['economy']['pnl_identity_pass'] and snap['population']['conservation_pass']):
        raise AssertionError('ENDPOINT_CONSERVATION_FAILED')
    pop = snap['population']
    rel = pop['relative_to_hold']
    row = {'relative_vs_hold_vcu':rel['total_relative_vcu'],
           'mean_relative_per_agent_vcu':rel['mean_relative_vcu'],
           'median_relative_vcu':rel['median_relative_vcu'],
           'p10_relative_vcu':rel['p10_relative_vcu'],
           'p90_relative_vcu':rel['p90_relative_vcu'],
           'ahead_percent':rel['ahead_percent'],
           'fees_vcu':snap['economy']['fees_total_vcu'],
           'trades':snap['total_trades'], 'volume':snap['volume'],
           'last_price_vcu':snap['market']['last_price'],
           'strategy_switches':pop['strategy_switches'],
           'retired_agents':pop['retired'], 'joins':pop['admitted'],
           'active_agents':pop['active']}
    return row,before,pre


def execute_plan(plan: dict) -> dict:
    verify_plan(plan)
    cache: dict[tuple, tuple] = {}
    runs=[];pairs=[]
    for seed in plan['seeds']:
        for cell in plan['cells']:
            results=[]
            for adaptive in (False,True):
                cfg = _config(plan['common'],cell,seed,adaptive=adaptive)
                key = tuple(sorted(asdict(cfg).items()))
                if key not in cache:
                    cache[key]=_arm(cfg)
                results.append(cache[key])
                runs.append({'seed':seed, 'cell':cell['id'],
                             'family':cell['family'],
                             'arm':'adaptive' if adaptive else 'frozen',
                             **cache[key][0]})
            (frozen,before0,pre0),(adapt,before1,pre1)=results
            if (before0,pre0)!=(before1,pre1):
                raise AssertionError(f'PAIRED_START_MISMATCH seed={seed} cell={cell["id"]}')
            pairs.append({'seed':seed,'cell':cell['id'],'family':cell['family'],
                          **{f'delta_{k}':adapt[k]-frozen[k] for k in METRICS}})
    stats=[]
    for cell in plan['cells']:
        for metric in METRICS:
            for group in ('frozen','adaptive','delta'):
                if group=='delta':
                    vals=[row[f'delta_{metric}'] for row in pairs if row['cell']==cell['id']]
                else:
                    vals=[row[metric] for row in runs if row['cell']==cell['id'] and row['arm']==group]
                stats.append({'cell':cell['id'],'family':cell['family'],
                              'metric':metric,'group':group,**interval(vals)})
    return {'schema':'MARKET_LAB_V07_RESULT_1', 'source':'SIMULATED',
            'plan_sha256':plan['sha256'], 'seeds':plan['seeds'],
            'scenario_count':len(plan['cells']), 'runs':runs,
            'paired_differences':pairs,'statistics':stats,
            'integrity':{'initial_pair_match':True,'pre_review_pair_match':True,
                         'conservation_all_runs':True},
            'notes': ('All cross-arm comparisons are within different endogenous market paths. '
                      'Participant distributions include all original funded wallets, even inactive/retired. '
                      'Intervals treat different seeds as units, not individual agents. '
                      'Many secondary comparisons: no multiple-testing correction; do not infer real-market validity.')}


def _write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer=csv.DictWriter(f,fieldnames=columns,extrasaction='raise')
        writer.writeheader();writer.writerows(rows)


def _fmt(v: object) -> str:
    if v is None:
        return 'n/a'
    if isinstance(v,float):
        return f'{v:,.3f}'
    return str(v)


def render_report(plan: dict, result: dict) -> str:
    """Deterministic local HTML; no scripts, CDNs or remote references."""
    primary = [r for r in result['statistics']
               if r['cell']=='ablation_111' and r['metric']==PRIMARY and r['group']=='delta'][0]
    primary_html=''.join(f'<p><strong>{escape(k)}:</strong> {escape(_fmt(primary[k]))}</p>'
                         for k in ('mean','median','ci95_low','ci95_high','positive','negative','sign_p_two_sided'))
    cells_rows=[]
    for cell in plan['cells']:
        stat=next(r for r in result['statistics'] if r['cell']==cell['id']
                  and r['metric']==PRIMARY and r['group']=='delta')
        cells_rows.append('<tr>'+''.join(f'<td>{escape(_fmt(x))}</td>' for x in (
            cell['id'],cell['family'],stat['mean'],stat['ci95_low'],
            stat['ci95_high'],stat['positive'],stat['negative']))+'</tr>')
    dist_rows=[]
    for cell in plan['cells']:
        for m in ('median_relative_vcu','p10_relative_vcu','p90_relative_vcu','ahead_percent','fees_vcu'):
            stat=next(x for x in result['statistics'] if x['cell']==cell['id']
                      and x['metric']==m and x['group']=='delta')
            dist_rows.append('<tr>'+''.join(f'<td>{escape(_fmt(x))}</td>' for x in (
                cell['id'],m,stat['mean'],stat['ci95_low'],stat['ci95_high']))+'</tr>')
    return f'''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Market Lab v0.7 — Ablation Research</title>
<style>body{{font:15px system-ui,sans-serif;background:#0b1620;color:#e6f2fa;padding:2rem;line-height:1.5}}h1{{color:#74e8cb}}table{{border-collapse:collapse;max-width:100%;display:block;overflow-x:auto}}th,td{{padding:.6rem 1rem;text-align:right;border-bottom:1px solid #345}}td:first-child,th:first-child{{text-align:left}}section{{border:1px solid #345;padding:1rem;border-radius:12px;margin:1rem 0}}code{{color:#74e8cb}}</style></head><body>
<p>SIMULATED ONLY / NO REAL DATA / NO EXCHANGE CONNECTIONS</p><h1>Market Lab v0.7 — абляции, чувствительность, статистика</h1><p>Зафиксированный SHA-256 плана: <code>{escape(plan['sha256'])}</code></p><p>Seed: {escape(', '.join(map(str,plan['seeds'])))}. Ячеек: {len(plan['cells'])}. Один seed — единица статистического анализа.</p>
<section><h2>Основной показатель: adaptive − matched frozen, VCU</h2>{primary_html}<p>95% t-интервал для среднего по seed; двусторонний точный тест знаков без поправки на множественные сравнения.</p></section>
<section><h2>Факторные сравнения (один фактор за раз)</h2><table><tr><th>Сценарий</th><th>Семейство</th><th>Средняя Δ</th><th>95% low</th><th>95% high</th><th>Положит.</th><th>Отрицат.</th></tr>{''.join(cells_rows)}</table></section>
<section><h2>Распределение результата по всем 1000 агентам: P10 / медиана / P90 / доля обогнавших Hold</h2><table><tr><th>Сценарий</th><th>Метрика</th><th>Средняя Δ</th><th>95% low</th><th>95% high</th></tr>{''.join(dist_rows)}</table></section>
<section><h2>Ограничения</h2><p>Сценарий 001 (только join) не создаёт вступлений, потому что без выходов нет вакансий; режимы без switch не используют min-hold. Frozen никогда не переключает стратегии и не заменяет участников, кроме независимых риск-стопов.</p><p>Сравнения являются исследовательскими внутри искусственной модели. После расхождения решений меняются сами цены и потоки заявок; сравнение на разных конечных ценах не выделяет индивидуальный причинный эффект стратегии. Вторичные метрики и десятки сценариев требуют отдельных подтверждающих экспериментов с контролем множественного тестирования.</p></section>
<p>Источники: plan.json, summary.json, runs.csv, paired.csv, statistics.csv — все локальные.</p></body></html>'''


def save_results(plan:dict, result:dict, output:Path, *, overwrite:bool=False) -> list[Path]:
    verify_plan(plan)
    if result['plan_sha256']!=plan['sha256']:
        raise ValueError('RESULT_PLAN_MISMATCH')
    names=('plan.json','summary.json','runs.csv','paired.csv','statistics.csv','report.html')
    if output.exists() and any((output/n).exists() for n in names) and not overwrite:
        raise FileExistsError('Output exists. Pick fresh directory or use --overwrite.')
    output.mkdir(parents=True,exist_ok=True)
    paths=[output/n for n in names]
    paths[0].write_text(json.dumps(plan,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    paths[1].write_text(json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')
    _write_csv(paths[2],result['runs'],['seed','cell','family','arm',*METRICS])
    _write_csv(paths[3],result['paired_differences'],['seed','cell','family',*(f'delta_{k}' for k in METRICS)])
    _write_csv(paths[4],result['statistics'],['cell','family','metric','group','n','mean','median','ci95_low','ci95_high','positive','negative','zero','sign_p_two_sided'])
    paths[5].write_text(render_report(plan,result),encoding='utf-8')
    return paths


def main(argv: list[str] | None = None) -> None:
    parser=argparse.ArgumentParser(description='v0.7 offline pre-registered synthetic ablation research')
    commands=parser.add_subparsers(dest='action',required=True)
    p=commands.add_parser('plan',help='write immutable, checksummed research plan')
    p.add_argument('--seeds',default='1:20',help='1:20 or 1,2,3 (up to 100)')
    p.add_argument('--ticks',type=int,default=320)
    p.add_argument('--shock-tick',type=int,default=None)
    p.add_argument('--min-hold',type=int,default=40,choices=(40,80,160))
    p.add_argument('--maker-fee-bps',type=int,default=10)
    p.add_argument('--taker-fee-bps',type=int,default=20)
    p.add_argument('--no-shock',action='store_true')
    p.add_argument('--output',required=True,help='path of new plan.json')
    r=commands.add_parser('run',help='verify plan hash, execute and export')
    r.add_argument('--plan',required=True)
    r.add_argument('--output',required=True)
    r.add_argument('--overwrite',action='store_true')
    args=parser.parse_args(argv)
    try:
        if args.action=='plan':
            path=Path(args.output)
            if path.exists():
                raise FileExistsError('Plan exists: refusing overwrite')
            plan=make_plan(parse_seed_range(args.seeds),ticks=args.ticks,
                shock_tick=args.shock_tick,min_hold=args.min_hold,
                maker_fee_bps=args.maker_fee_bps,taker_fee_bps=args.taker_fee_bps,
                shock_enabled=not args.no_shock)
            path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps(plan,ensure_ascii=False,indent=2,sort_keys=True)+'\n',encoding='utf-8')
            print(f'PLAN_SHA256={plan["sha256"]} SEEDS={len(plan["seeds"])} CELLS={len(plan["cells"])} FILE={path}')
        else:
            plan_path=Path(args.plan)
            raw=plan_path.read_bytes()
            plan=json.loads(raw)
            verify_plan(plan)
            output=Path(args.output)
            if output.exists() and any((output/n).exists() for n in ('summary.json','runs.csv','paired.csv','statistics.csv','report.html')) and not args.overwrite:
                raise FileExistsError('Results directory already contains research results')
            print(f'PLAN_VERIFIED={plan["sha256"]} SEEDS={len(plan["seeds"])} CELLS={len(plan["cells"])}',flush=True)
            result=execute_plan(plan)
            if plan_path.read_bytes()!=raw:
                raise ValueError('PLAN_CHANGED_DURING_RUN')
            targets=save_results(plan,result,output,overwrite=args.overwrite)
            print(f'RESEARCH_PASS=YES INTEGRITY_PASS=YES PAIRS={len(result["paired_differences"])}')
            for target in targets:print(f'FILE={target}')
    except (ValueError,FileExistsError,KeyError,TypeError,OSError) as exc:
        parser.error(str(exc))

if __name__=='__main__':
    main()
