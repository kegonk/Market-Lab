"""Market Lab v0.9 — synthetic market viability, policy re-entry and regimes.

Entirely offline, predeclared, resumable. Re-entry is *only* of existing
policy-retired funded wallets, never a risk stop, and never mints assets.
Distinct markets have endogenous prices: market-wide contrasts are not
causal, strategy-level same-price execution estimates.
"""
from __future__ import annotations

import argparse
import json
from hashlib import sha256
from html import escape
from pathlib import Path
from statistics import mean

from .interactive import SessionConfig, SyntheticMarket
from .research import _fingerprint
from .research_v07 import interval, parse_seed_range
from .research_v08 import (_atomic_bytes, _canonical, _csv_bytes, _digest,
                           parse_horizons, shadow_rules)

SCHEMA = 'MARKET_LAB_V09_VIABILITY_PLAN_1'
RESULT_SCHEMA = 'MARKET_LAB_V09_VIABILITY_RESULT_1'
DEFAULT_SEEDS = tuple(range(201, 211))
DEFAULT_HORIZONS = (320, 1280)
METRICS = ('relative_vs_hold_vcu','fees_vcu','trades','volume','active_agents','active_agent_tick_exposure',
           'reentries','currently_retired','policy_exits','risk_exits',
           'trade_rate_per_1000_active_ticks','active_survival_percent',
           'two_sided_depth_percent','trading_windows_percent',
           'mean_spread_ticks','spread_coverage_percent','last_price_vcu','viable')

# Not an optimized grid: the entire set is frozen in plan.json before execution.
ALL_CELLS = (
    {'id':'legacy_baseline', 'regime':'LEGACY','reentry':False,'no_policy_turnover':False},
    {'id':'legacy_reentry',  'regime':'LEGACY','reentry':True, 'no_policy_turnover':False},
    {'id':'legacy_no_turnover', 'regime':'LEGACY','reentry':False,'no_policy_turnover':True},
    {'id':'bull_reentry', 'regime':'BULL','reentry':True,'no_policy_turnover':False},
    {'id':'bear_reentry', 'regime':'BEAR','reentry':True,'no_policy_turnover':False},
    {'id':'sideways_reentry', 'regime':'SIDEWAYS','reentry':True,'no_policy_turnover':False},
    {'id':'thin_reentry', 'regime':'THIN','reentry':True,'no_policy_turnover':False},
)
# Intervals have 20-tick windows, enough to test turnover/market health.
HEALTH_EVERY = 20
THRESHOLDS = {'min_active_survival_percent':60, 'min_two_sided_depth_percent':70,
              'min_trading_windows_percent':80,'min_depth_per_side':5}


def make_plan(seeds: tuple[int,...]=DEFAULT_SEEDS,
              horizons: tuple[int,...]=DEFAULT_HORIZONS,
              profile: str='full') -> dict:
    if (type(seeds) not in (tuple,list) or not 2 <= len(seeds) <= 100
            or len(set(seeds))!=len(seeds)
            or any(type(s) is not int or not 201<=s<=2**31 for s in seeds)):
        raise ValueError('V09_REQUIRES_2..100_NEW_SEEDS_AT_LEAST_201')
    if (type(horizons) not in (tuple,list) or not 1<=len(horizons)<=5
            or len(set(horizons))!=len(horizons)
            or any(type(h) is not int or not 40<=h<=2000 for h in horizons)):
        raise ValueError('V09_REQUIRES_DISTINCT_HORIZONS_40..2000')
    if profile not in ('core','full') or type(profile) is not str:
        raise ValueError('V09_PROFILE_CORE_OR_FULL')
    cells = list(ALL_CELLS if profile=='full' else ALL_CELLS[:3])
    body={
        'schema':SCHEMA,'source':'SIMULATED','role':'PREDECLARED_VIABILITY',
        'seeds':list(seeds),'horizons':list(horizons),'profile':profile,
        'cells':cells,'interval_ticks':HEALTH_EVERY,'thresholds':dict(THRESHOLDS),
        'settings':{'events_per_tick':6, 'waiting_agents':100,
                    'evolution_review_ticks':20,'weak_reviews_to_exit':3,
                    'max_joins_per_review':10,'maker_fee_bps':10,'taker_fee_bps':20,
                    'min_strategy_hold_ticks':40,'bankruptcy_threshold_percent':20,
                    'stop_denials':6,'maker_caution_ticks':35,'shock_tick':160,
                    'shock_enabled':True,'cancel_percent':85,'sell_qty':26,
                    'reentry_cooldown_ticks':40},
        'primary_endpoint':'active_survival_percent',
        'methodology':('Offline synthetic coupled market: seed is statistical unit. '
          'Baseline vs re-entry and no-policy-turnover are separate worlds with different '
          'endogenous prices; risk stops still apply to all. No-policy-turnover is NOT '
          'an exactly fixed agent-count control, so report active exposure separately. '
          'Only POLICY exits are eligible for re-entry after cooldown if funded and safe. '
          'Viability thresholds fixed before experiments. Regimes are deterministic '
          'directional agent-flow perturbations, not guaranteed price trends. '
          'Four shadow rules share the frozen price tape within each cell; simplified '
          'hypothetical fills are not full exchange executions. '
          'Secondary comparisons are exploratory without multiplicity correction.'),
    }
    for cell in cells:
        for arm in ('frozen','adaptive'):
            _config(body, cell, seeds[0], horizons[0], arm)
    return {**body,'sha256':_digest(body)}


def verify_plan(plan:dict)->None:
    if type(plan) is not dict or type(plan.get('sha256')) is not str:
        raise ValueError('PLAN_SCHEMA_INVALID')
    body={k:v for k,v in plan.items() if k!='sha256'}
    if plan['sha256']!=_digest(body):
        raise ValueError('PLAN_SHA256_MISMATCH')
    if plan != make_plan(tuple(plan['seeds']),tuple(plan['horizons']),plan['profile']):
        raise ValueError('PLAN_NOT_CANONICAL')


def _config(plan:dict,cell:dict,seed:int,horizon:int,arm:str)->SessionConfig:
    if arm not in ('frozen','adaptive'):
        raise ValueError('ARM_INVALID')
    kwargs=dict(plan['settings'])
    kwargs['shock_tick']=min(kwargs['shock_tick'],horizon)
    kwargs['synthetic_regime']=cell['regime']
    kwargs['allow_retired_reentry']=cell['reentry'] and arm=='adaptive'
    kwargs['allow_population_exit']=not cell['no_policy_turnover']
    kwargs['allow_population_join']=not cell['no_policy_turnover']
    return SessionConfig(seed=seed,ticks=horizon,evolution_enabled=True,
                         adaptation_enabled=(arm=='adaptive'),**kwargs)


def _observation(m:SyntheticMarket,prev:dict,tick:int,thresholds:dict)->dict:
    p=m.population
    bid=m.book.best('BUY');ask=m.book.best('SELL')
    bid_depth=m.book.depth('BUY',8);ask_depth=m.book.depth('SELL',8)
    net_trades=m.trades-prev['trades'];net_volume=m.total_traded_qty-prev['volume']
    net_fees=round(m.economy.wallets['EXCHANGE'].cash_ticks/10 - prev['fees'],5)
    span=tick-prev['tick']
    return {
        'tick':tick,'span_ticks':span,'active':len(p.active), 'waiting':len(p.waiting),
        'currently_retired':len(p.retired),'joins':p.joins,'reentries':p.reentries,
        'policy_exits':p.exit_by_reason['policy'],'risk_exits':p.exit_by_reason['risk'],
        'bid_depth':bid_depth,'ask_depth':ask_depth,
        'spread_ticks':(ask-bid) if bid is not None and ask is not None else None,
        'two_sided_liquid':int(bid is not None and ask is not None
                  and bid_depth>=thresholds['min_depth_per_side']
                  and ask_depth>=thresholds['min_depth_per_side']),
        'trades':net_trades,'volume':net_volume,'fees_vcu':net_fees,
        'quote_withheld':m.maker_withheld-prev['withheld'],
        'price_vcu':m.book.last_trade_ticks/10,
        'regime_cancelled_qty':m.regime_cancelled_qty,
    }


def run_arm(plan:dict,cell:dict,seed:int,horizon:int,arm:str)->dict:
    config=_config(plan,cell,seed,horizon,arm)
    m=SyntheticMarket(config)
    initial=_fingerprint(m)
    before_review=None
    prev={'trades':0,'volume':0,'fees':0.,'tick':0,'withheld':0}
    health=[]
    for _ in range(horizon):
        m.step(capture_state=False)
        if m.tick==min(horizon,m.config.evolution_review_ticks-1):
            before_review=_fingerprint(m)
        if m.tick%plan['interval_ticks']==0 or m.tick==horizon:
            health.append(_observation(m,prev,m.tick,plan['thresholds']))
            prev={'trades':m.trades,'volume':m.total_traded_qty,
                  'fees':m.economy.wallets['EXCHANGE'].cash_ticks/10,
                  'tick':m.tick,'withheld':m.maker_withheld}
    if not (m.economy.check(m.book) and m.population.check() and m.book.check_conservation()):
        raise AssertionError('ECONOMY_OR_POPULATION_CONSERVATION_FAILED')
    snap=m.state()
    if not (snap['economy_conservation_pass'] and snap['conservation_pass']
            and snap['economy']['pnl_identity_pass'] and snap['population']['conservation_pass']):
        raise AssertionError('ENDPOINT_CONSERVATION_FAILED')
    t=plan['thresholds'];p=m.population
    initial_active=len(p.initial_active_ids)
    active_survival=round(100*len(p.active)/initial_active,5)
    liquid=round(100*sum(x['two_sided_liquid'] for x in health)/len(health),5)
    traded=round(100*sum(x['trades']>0 for x in health)/len(health),5)
    spread=[x['spread_ticks'] for x in health if x['spread_ticks'] is not None]
    metrics={
        'relative_vs_hold_vcu':snap['population']['relative_to_hold']['total_relative_vcu'],
        'fees_vcu':snap['economy']['fees_total_vcu'],
        'trades':m.trades,'volume':m.total_traded_qty,
        'active_agents':len(p.active),'active_agent_tick_exposure':p.active_agent_tick_exposure,
        'reentries':p.reentries,
        'currently_retired':len(p.retired),
        'policy_exits':p.exit_by_reason['policy'],'risk_exits':p.exit_by_reason['risk'],
        'trade_rate_per_1000_active_ticks':round(1000*m.trades/p.active_agent_tick_exposure,5)
             if p.active_agent_tick_exposure else 0.,
        'active_survival_percent':active_survival,
        'two_sided_depth_percent':liquid,'trading_windows_percent':traded,
        'mean_spread_ticks':round(mean(spread),5) if spread else -1.,
        'spread_coverage_percent':round(100*len(spread)/len(health),5),
        'last_price_vcu':m.book.last_trade_ticks/10,
        'viable':int(active_survival>=t['min_active_survival_percent'] and
                    liquid>=t['min_two_sided_depth_percent'] and
                    traded>=t['min_trading_windows_percent']),
    }
    all_ids=p.relative_metrics(m.book.last_trade_ticks)['all_agents']
    if all_ids!=1000:
        raise AssertionError('ORIGINAL_WALLET_LOSS')
    return {'seed':seed,'horizon':horizon,'cell':cell['id'],'arm':arm,
            'initial_fingerprint':initial,'pre_review_fingerprint':before_review,
            'metrics':metrics,'health':health,
            'price_tape_ticks':m.price_history if arm=='frozen' else None,
            'all_wallets':all_ids,'conservation':True,
            'identity':{'initial_agents':initial_active,'initial_cash_ticks':m.economy.initial_cash_ticks,
                        'initial_coins':m.economy.initial_coins},
            'reentry_by_agent':p.reentry_by_agent,'regime_cancelled_qty':m.regime_cancelled_qty}


def _key(seed:int,horizon:int,cell:str,arm:str)->str:
    return f'seed-{seed}-ticks-{horizon}-{cell}-{arm}.json'


def _seal(entry:dict)->dict:
    return {**entry,'content_sha256':_digest(entry)}


def _load_checkpoint(path:Path,plan:dict,seed:int,horizon:int,cell:str,arm:str)->dict:
    obj=json.loads(path.read_bytes())
    if type(obj) is not dict or set(obj)!={'plan_sha256','seed','horizon','cell','arm','data','content_sha256'}:
        raise ValueError(f'CHECKPOINT_SCHEMA_ERROR {path}')
    content={k:v for k,v in obj.items() if k!='content_sha256'}
    if obj['content_sha256']!=_digest(content) or (
      obj['plan_sha256'],obj['seed'],obj['horizon'],obj['cell'],obj['arm']) != (
      plan['sha256'],seed,horizon,cell,arm):
        raise ValueError(f'CHECKPOINT_SHA256_MISMATCH {path}')
    data=obj['data']
    if (data.get('conservation') is not True or data.get('all_wallets')!=1000
            or (data.get('seed'),data.get('horizon'),data.get('cell'),data.get('arm'))!=(seed,horizon,cell,arm)
            or len(data.get('health',[]))!=(horizon+plan['interval_ticks']-1)//plan['interval_ticks']):
        raise ValueError(f'CHECKPOINT_INVALID_DATA {path}')
    if data['identity']['initial_agents']!=900:
        raise ValueError(f'CHECKPOINT_INITIAL_COHORT_FAILED {path}')
    return data


def _aggregate(plan:dict,records:list[dict])->dict:
    by={(r['seed'],r['horizon'],r['cell'],r['arm']):r for r in records}
    runs=[];pairs=[];health=[];shadow=[]
    for horizon in plan['horizons']:
        for cell in plan['cells']:
            for seed in plan['seeds']:
                fr=by[(seed,horizon,cell['id'],'frozen')]
                ad=by[(seed,horizon,cell['id'],'adaptive')]
                if (fr['initial_fingerprint'],fr['pre_review_fingerprint']) != (
                      ad['initial_fingerprint'],ad['pre_review_fingerprint']):
                    raise AssertionError(f'PAIR_INITIAL_FINGERPRINT_MISMATCH {seed} {cell["id"]}')
                if fr['identity']!=ad['identity']:
                    raise AssertionError('PAIR_INITIAL_ASSETS_MISMATCH')
                if len(fr['price_tape_ticks'])!=horizon+1:
                    raise AssertionError('PRICE_TAPE_MISMATCH')
                for obj in (fr,ad):
                    runs.append({'seed':seed,'horizon':horizon,'cell':cell['id'],
                                 'arm':obj['arm'],**obj['metrics']})
                    health.extend({'seed':seed,'horizon':horizon,'cell':cell['id'],
                                  'arm':obj['arm'],**item} for item in obj['health'])
                pairs.append({'seed':seed,'horizon':horizon,'cell':cell['id'],
                              **{f'delta_{m}':ad['metrics'][m]-fr['metrics'][m] for m in METRICS}})
                shadow.extend({'seed':seed,'horizon':horizon,'cell':cell['id'],**row}
                              for row in shadow_rules(fr['price_tape_ticks'],seed,
                               maker_fee_bps=plan['settings']['maker_fee_bps'],
                               taker_fee_bps=plan['settings']['taker_fee_bps']))
    stats=[]
    for horizon in plan['horizons']:
        for cell in plan['cells']:
            for metric in METRICS:
                for group in ('frozen','adaptive','delta'):
                    vals=([x[f'delta_{metric}'] for x in pairs if x['horizon']==horizon and x['cell']==cell['id']]
                          if group=='delta' else
                          [x[metric] for x in runs if x['horizon']==horizon and x['cell']==cell['id'] and x['arm']==group])
                    stats.append({'horizon':horizon,'cell':cell['id'],'metric':metric,'group':group,**interval(vals)})
    # Compare the same *seed* across policy modes, not unpaired cell means.
    policy=[]
    for horizon in plan['horizons']:
        for target in ('legacy_reentry','legacy_no_turnover'):
            if target not in {c['id'] for c in plan['cells']}:
                continue
            for metric in METRICS:
                vals=[]
                for seed in plan['seeds']:
                    a=by[(seed,horizon,target,'adaptive')]['metrics'][metric]
                    b=by[(seed,horizon,'legacy_baseline','adaptive')]['metrics'][metric]
                    vals.append(a-b)
                policy.append({'horizon':horizon,'comparison':target+' - legacy_baseline',
                               'metric':metric,**interval(vals)})
    shadow_stats=[]
    for horizon in plan['horizons']:
        for cell in plan['cells']:
            for strategy in ('NOISE','MAKER','TREND','REVERSION'):
                vals=[v['vs_hold_vcu'] for v in shadow if (v['horizon'],v['cell'],v['strategy']) ==
                      (horizon,cell['id'],strategy)]
                shadow_stats.append({'horizon':horizon,'cell':cell['id'],'strategy':strategy,**interval(vals)})
    return {'schema':RESULT_SCHEMA,'source':'SIMULATED','plan_sha256':plan['sha256'],
            'runs':runs,'pairs':pairs,'health':health,'statistics':stats,
            'policy_comparisons':policy,'shadow':shadow,'shadow_statistics':shadow_stats,
            'integrity':{'all_assets_conserved':True,'paired_state_aligned':True,
                         'all_1000_initial_wallets_included':True},
            'limitations':plan['methodology']}


def render_report(plan:dict,result:dict)->str:
    header='''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Market Lab v0.9 — Viability</title><style>body{font:15px system-ui,sans-serif;background:#0b1620;color:#e6f2fa;max-width:1200px;margin:2rem auto;padding:1rem}h1{color:#78dfc3}table{border-collapse:collapse;display:block;overflow:auto;max-width:100%}th,td{border-bottom:1px solid #345;padding:.55rem .7rem;text-align:right}th:first-child,td:first-child{text-align:left}section{border:1px solid #345;border-radius:8px;padding:1rem;margin:1rem 0}</style></head><body>'''
    def fmt(v):return '—' if v is None else f'{v:.3f}' if isinstance(v,float) else str(v)
    def row(h,case,metric,group='delta'):
        s=next(x for x in result['statistics'] if x['horizon']==h and x['cell']==case
               and x['metric']==metric and x['group']==group)
        return '<tr>'+''.join(f'<td>{escape(fmt(v))}</td>' for v in
           [h,case,fmt(s['mean']),fmt(s['ci95_low']),fmt(s['ci95_high']),s['positive'],s['negative']])+'</tr>'
    items=[header,'<p>SIMULATED ONLY / OFFLINE / NO EXCHANGE CONNECTIONS</p>',
           '<h1>Market Lab v0.9 — жизнеспособность искусственного рынка</h1>',
           f'<p>Plan SHA256: <code>{plan["sha256"]}</code>. Seeds: {escape(str(plan["seeds"]))}. Horizons: {escape(str(plan["horizons"]))}. Profile: {plan["profile"]}.</p>']
    for metric,label in [('active_survival_percent','Сохранение активных участников, %'),
                         ('relative_vs_hold_vcu','Доходность адаптация − контроль, VCU'),
                         ('trade_rate_per_1000_active_ticks','Сделок на 1000 активных агент-тиков'),
                         ('two_sided_depth_percent','Периоды с двухсторонней глубиной, %'),
                         ('viable','Доля выполнения всех критериев жизнеспособности')]:
        items.append(f'<section><h2>{escape(label)}</h2><table><tr><th>Тики</th><th>Режим</th><th>Mean Δ</th><th>95% low</th><th>95% high</th><th>+</th><th>−</th></tr>')
        for h in plan['horizons']:
            for c in plan['cells']:
                items.append(row(h,c['id'],metric))
        items.append('</table><p>Δ = adaptive − frozen внутри одного синтетического сценария. Не означает причинный эффект.</p></section>')
    if result['policy_comparisons']:
        items.append('<section><h2>Изменение политики: повторный допуск и без ротации</h2><table><tr><th>Тики</th><th>Сравнение</th><th>Метрика</th><th>Среднее Δ</th><th>95% low</th><th>95% high</th></tr>')
        selected=('active_survival_percent','relative_vs_hold_vcu','trades','active_agent_tick_exposure','viable')
        for r in result['policy_comparisons']:
            if r['metric'] in selected:
                items.append('<tr>'+''.join(f'<td>{escape(fmt(v))}</td>' for v in
                   (r['horizon'],r['comparison'],r['metric'],r['mean'],r['ci95_low'],r['ci95_high']))+'</tr>')
        items.append('</table><p>Прямое парное сравнение на одинаковых seed, но эндогенные рыночные цены расходятся.</p></section>')
    items.append('<section><h2>Четыре правила на общей искусственной ценовой ленте</h2><table><tr><th>Тики</th><th>Режим</th><th>Правило</th><th>Среднее vs Hold, VCU</th><th>95% low</th><th>95% high</th></tr>')
    for r in result['shadow_statistics']:
        items.append('<tr>'+''.join(f'<td>{escape(fmt(v))}</td>' for v in
                     (r['horizon'],r['cell'],r['strategy'],r['mean'],r['ci95_low'],r['ci95_high']))+'</tr>')
    items.append('</table><p>Симуляция упрощённых сделок, без полного стакана и воздействия на цену. Не результаты агентов основной модели.</p></section>')
    items.append('<section><h2>Рынок на старте и в конце: временные окна</h2><table><tr><th>Тики</th><th>Режим</th><th>Arm</th><th>Активных (первое окно)</th><th>Активных (последнее окно)</th><th>Доля окон с двухсторонней ликвидностью</th></tr>')
    for horizon in plan['horizons']:
        for cell in plan['cells']:
            for arm in ('frozen','adaptive'):
                relevant=[x for x in result['health'] if x['horizon']==horizon and x['cell']==cell['id'] and x['arm']==arm]
                first=mean(x['active'] for x in relevant if x['tick']==min(plan['interval_ticks'],horizon))
                last=mean(x['active'] for x in relevant if x['tick']==horizon)
                liquidity=100*mean(x['two_sided_liquid'] for x in relevant)
                items.append('<tr>'+''.join(f'<td>{escape(fmt(v))}</td>' for v in
                             (horizon,cell['id'],arm,first,last,liquidity))+'</tr>')
    items.append('</table><p>Полный временной ряд: health.csv. Спред без обеих сторон отсутствует, не считается нулевым.</p></section>')
    items.append('<section><h2>Критерии жизнеспособности</h2><p>'+escape(str(plan['thresholds']))+'</p><p>Все критерии должны выполняться; сохранение денег и монет проверяется отдельно.</p></section>')
    items.append('<section><h2>Файлы</h2><p>summary.json · runs.csv · paired.csv · statistics.csv · policy_comparisons.csv · health.csv · shadow.csv · shadow_statistics.csv · complete.json</p></section>')
    items.append('<section><h2>Ограничения</h2><p>'+escape(result['limitations'])+'</p></section></body></html>')
    return ''.join(items)


def _finalize(plan:dict,result:dict,output:Path)->list[Path]:
    files={
      'summary.json':_canonical(result),
      'runs.csv':_csv_bytes(result['runs'],['seed','horizon','cell','arm',*METRICS]),
      'paired.csv':_csv_bytes(result['pairs'],['seed','horizon','cell',*(f'delta_{m}' for m in METRICS)]),
      'statistics.csv':_csv_bytes(result['statistics'],['horizon','cell','metric','group','n','mean','median','ci95_low','ci95_high','positive','negative','zero','sign_p_two_sided']),
      'policy_comparisons.csv':_csv_bytes(result['policy_comparisons'],['horizon','comparison','metric','n','mean','median','ci95_low','ci95_high','positive','negative','zero','sign_p_two_sided']),
      'health.csv':_csv_bytes(result['health'],['seed','horizon','cell','arm','tick','span_ticks','active','waiting','currently_retired','joins','reentries','policy_exits','risk_exits','bid_depth','ask_depth','spread_ticks','two_sided_liquid','trades','volume','fees_vcu','quote_withheld','price_vcu','regime_cancelled_qty']),
      'shadow.csv':_csv_bytes(result['shadow'],['seed','horizon','cell','strategy','tape_sha256','vs_hold_vcu','fills','opportunities','fees_vcu']),
      'shadow_statistics.csv':_csv_bytes(result['shadow_statistics'],['horizon','cell','strategy','n','mean','median','ci95_low','ci95_high','positive','negative','zero','sign_p_two_sided']),
      'report.html':render_report(plan,result).encode('utf-8'),
    }
    for name,data in files.items():
        path=output/name
        if path.exists() and path.read_bytes()!=data:
            raise FileExistsError(f'FINAL_CONFLICT {path}')
    for name,data in files.items():
        if not (output/name).exists():
            _atomic_bytes(output/name,data)
    seal={'plan_sha256':plan['sha256'],'result_sha256':_digest(result),
          'file_sha256':{name:sha256(data).hexdigest() for name,data in files.items()}}
    _atomic_bytes(output/'complete.json',_canonical(seal))
    return [output/n for n in files]+[output/'complete.json']


def _items(plan:dict):
    for horizon in plan['horizons']:
        for cell in plan['cells']:
            for seed in plan['seeds']:
                for arm in ('frozen','adaptive'):
                    yield horizon,cell,seed,arm


def _prepare(plan:dict,output:Path,resume:bool)->None:
    verify_plan(plan)
    if resume and not output.exists():
        raise ValueError('RESUME_DIRECTORY_MISSING')
    if output.exists() and not resume:
        raise FileExistsError('RESULT_DIRECTORY_ALREADY_EXISTS_USE_RESUME')
    output.mkdir(parents=True,exist_ok=True)
    path=output/'plan.json'
    if path.exists():
        if path.read_bytes()!=_canonical(plan):
            raise ValueError('OUTPUT_PLAN_MISMATCH')
    elif resume and any(output.iterdir()):
        raise ValueError('OUTPUT_PLAN_MISSING')
    else:
        _atomic_bytes(path,_canonical(plan))


def _verify_final(plan:dict,output:Path)->dict|None:
    sealpath=output/'complete.json'
    if not sealpath.exists():return None
    seal=json.loads(sealpath.read_bytes())
    if seal.get('plan_sha256')!=plan['sha256'] or type(seal.get('file_sha256')) is not dict:
        raise ValueError('FINAL_PLAN_MISMATCH')
    for file,hexhash in seal['file_sha256'].items():
        path=output/file
        if not path.is_file() or sha256(path.read_bytes()).hexdigest()!=hexhash:
            raise ValueError(f'FINAL_HASH_MISMATCH {file}')
    result=json.loads((output/'summary.json').read_bytes())
    if _digest(result)!=seal.get('result_sha256'):
        raise ValueError('FINAL_RESULT_HASH_MISMATCH')
    return result


def execute(plan:dict,output:Path,*,resume:bool=False,max_new_arms:int|None=None,
            plan_path:Path|None=None)->dict|None:
    if max_new_arms is not None and (type(max_new_arms) is not int or max_new_arms<1):
        raise ValueError('MAX_NEW_ARMS_INVALID')
    _prepare(plan,output,resume)
    completed=_verify_final(plan,output)
    if completed is not None:
        print('RESEARCH_ALREADY_COMPLETE=YES VERIFIED_FILES=YES',flush=True)
        return completed
    items=list(_items(plan));records=[];new=0
    for horizon,cell,seed,arm in items:
        path=output/'checkpoints'/_key(seed,horizon,cell['id'],arm)
        if path.is_file():
            record=_load_checkpoint(path,plan,seed,horizon,cell['id'],arm)
            label='REUSED'
        else:
            record=run_arm(plan,cell,seed,horizon,arm)
            if plan_path is not None and plan_path.read_bytes()!=_canonical(plan):
                raise ValueError('PLAN_CHANGED_DURING_RUN')
            _atomic_bytes(path,_canonical(_seal({'plan_sha256':plan['sha256'],
                'seed':seed,'horizon':horizon,'cell':cell['id'],'arm':arm,'data':record})))
            label='SAVED';new+=1
        records.append(record)
        print(f'ARM_{label}={len(records)}/{len(items)} SEED={seed} TICKS={horizon} CELL={cell["id"]} ARM={arm}',flush=True)
        if max_new_arms is not None and new>=max_new_arms and len(records)<len(items):
            print(f'RESEARCH_PAUSED=YES COMPLETED={len(records)}/{len(items)} RESUME=--resume',flush=True)
            return None
    result=_aggregate(plan,records)
    if plan_path is not None and plan_path.read_bytes()!=_canonical(plan):
        raise ValueError('PLAN_CHANGED_DURING_RUN')
    files=_finalize(plan,result,output)
    print(f'RESEARCH_PASS=YES INTEGRITY_PASS=YES PAIRS={len(result["pairs"])} ARMS={len(result["runs"])}',flush=True)
    for path in files:print(f'FILE={path}',flush=True)
    return result


def progress_status(plan:dict,output:Path)->dict:
    verify_plan(plan)
    if not (output/'plan.json').is_file() or (output/'plan.json').read_bytes()!=_canonical(plan):
        raise ValueError('OUTPUT_PLAN_MISMATCH')
    missing=[];verified=0
    for horizon,cell,seed,arm in _items(plan):
        path=output/'checkpoints'/_key(seed,horizon,cell['id'],arm)
        if path.is_file():
            _load_checkpoint(path,plan,seed,horizon,cell['id'],arm);verified+=1
        else:missing.append((seed,horizon,cell['id'],arm))
    complete=_verify_final(plan,output)
    print(f'PROGRESS_VERIFIED={verified}/{verified+len(missing)} NEXT={missing[0] if missing else "complete"} FINALIZED={complete is not None}',flush=True)
    return {'verified':verified,'total':verified+len(missing),'finalized':complete is not None}


def main(argv:list[str]|None=None)->None:
    p=argparse.ArgumentParser(description='Market Lab v0.9 OFFLINE synthetic viability research')
    sub=p.add_subparsers(dest='action',required=True)
    plan=sub.add_parser('plan');plan.add_argument('--seeds',default='201:210')
    plan.add_argument('--horizons',default='320,1280')
    plan.add_argument('--profile',choices=('core','full'),default='full')
    plan.add_argument('--output',required=True)
    run=sub.add_parser('run');run.add_argument('--plan',required=True)
    run.add_argument('--output',required=True);run.add_argument('--resume',action='store_true')
    run.add_argument('--max-new-arms',type=int,default=None)
    status=sub.add_parser('status');status.add_argument('--plan',required=True)
    status.add_argument('--output',required=True)
    args=p.parse_args(argv)
    try:
        if args.action=='plan':
            path=Path(args.output)
            if path.exists():raise FileExistsError('PLAN_ALREADY_EXISTS')
            plan_obj=make_plan(parse_seed_range(args.seeds),parse_horizons(args.horizons),args.profile)
            _atomic_bytes(path,_canonical(plan_obj))
            print(f'PLAN_SHA256={plan_obj["sha256"]} SEEDS={len(plan_obj["seeds"])} HORIZONS={plan_obj["horizons"]} CELLS={len(plan_obj["cells"])}',flush=True)
        else:
            path=Path(args.plan);data=path.read_bytes();plan_obj=json.loads(data)
            verify_plan(plan_obj)
            if data!=_canonical(plan_obj):raise ValueError('PLAN_FILE_NONCANONICAL')
            print(f'PLAN_VERIFIED={plan_obj["sha256"]} SEEDS={len(plan_obj["seeds"])} HORIZONS={plan_obj["horizons"]} CELLS={len(plan_obj["cells"])}',flush=True)
            if args.action=='status':progress_status(plan_obj,Path(args.output))
            else:execute(plan_obj,Path(args.output),resume=args.resume,max_new_arms=args.max_new_arms,plan_path=path)
    except (ValueError,FileExistsError,TypeError,KeyError,OSError) as exc:
        p.error(str(exc))


if __name__=='__main__':
    main()
