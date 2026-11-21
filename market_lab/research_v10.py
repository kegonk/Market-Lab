"""Market Lab v1.0: synthetic tick-by-tick liquidity and population dynamics.

Closed VCOIN/VCU economy. Mechanism labels are observational indicators,
not causal attribution. All runs are offline; no external data or connections.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from hashlib import sha256
from html import escape
from pathlib import Path
from statistics import mean

from .interactive import SessionConfig, SyntheticMarket
from .economy import fee_unit
from .research import _fingerprint
from .research_v07 import interval, parse_seed_range
from .research_v08 import (_atomic_bytes, _canonical, _csv_bytes, _digest,
                           parse_horizons)

SCHEMA = 'MARKET_LAB_V10_LIQUIDITY_PLAN_1'
RESULT_SCHEMA = 'MARKET_LAB_V10_LIQUIDITY_RESULT_1'
DEFAULT_SEEDS = tuple(range(301, 311))
DEFAULT_HORIZONS = (320, 1280)
HEALTH_EVERY = 20
THRESHOLDS = {'min_active_survival_percent': 60,
              'min_two_sided_tick_percent': 70,
              'min_nonzero_trade_tick_percent': 35,
              'max_no_quote_streak_ticks': 25,
              'min_depth_per_side': 5}
ALL_CELLS = (
    {'id':'legacy_baseline', 'regime':'LEGACY','reentry':False,'join':True,'exit':True,'refresh':'OFF'},
    {'id':'legacy_reentry', 'regime':'LEGACY','reentry':True,'join':True,'exit':True,'refresh':'OFF'},
    {'id':'legacy_maker_refresh', 'regime':'LEGACY','reentry':True,'join':True,'exit':True,'refresh':'MAKER_ONLY'},
    {'id':'legacy_all_refresh', 'regime':'LEGACY','reentry':True,'join':True,'exit':True,'refresh':'ALL_FUNDED'},
    {'id':'legacy_no_inflow', 'regime':'LEGACY','reentry':False,'join':False,'exit':True,'refresh':'MAKER_ONLY'},
    {'id':'legacy_no_exit', 'regime':'LEGACY','reentry':False,'join':False,'exit':False,'refresh':'MAKER_ONLY'},
    {'id':'bull_maker_refresh', 'regime':'BULL','reentry':True,'join':True,'exit':True,'refresh':'MAKER_ONLY'},
    {'id':'bear_maker_refresh', 'regime':'BEAR','reentry':True,'join':True,'exit':True,'refresh':'MAKER_ONLY'},
    {'id':'sideways_maker_refresh', 'regime':'SIDEWAYS','reentry':True,'join':True,'exit':True,'refresh':'MAKER_ONLY'},
    {'id':'thin_maker_refresh', 'regime':'THIN','reentry':True,'join':True,'exit':True,'refresh':'MAKER_ONLY'},
)
METRICS = (
    'active_survival_percent','relative_vs_hold_vcu','trades','volume','fees_vcu',
    'active_agent_tick_exposure','policy_exits','risk_exits','joins','reentries',
    'bid_present_ticks_percent','ask_present_ticks_percent','two_sided_tick_percent',
    'nonzero_trade_tick_percent','no_bid_ticks_percent','no_ask_ticks_percent',
    'bid_below_depth_ticks_percent','ask_below_depth_ticks_percent',
    'max_no_bid_streak','max_no_ask_streak','max_no_trade_streak',
    'maker_bid_stockout_ticks','maker_ask_stockout_ticks',
    'withheld_quote_ticks','regime_cancel_ticks','shock_cancel_ticks',
    'mean_spread_ticks','spread_coverage_percent',
    'quote_refresh_attempts','quote_refresh_posted','quote_refresh_quantity',
    'quote_refresh_no_eligible','viable'
)
TICK_COLUMNS = ('seed','horizon','cell','arm','tick','active','bid_present','ask_present',
                'bid_depth','ask_depth','spread_ticks','bid_below_depth','ask_below_depth',
                'two_sided_depth','trades','volume','fees_vcu',
                'maker_bid_stockout','maker_ask_stockout','quote_withheld',
                'regime_cancelled_qty','shock_cancelled_qty',
                'refresh_attempts','refresh_posted','refresh_quantity',
                'refresh_no_eligible','price_vcu')
WINDOW_COLUMNS=('seed','horizon','cell','arm','tick','span_ticks','active',
                'no_bid_ticks','no_ask_ticks','two_sided_depth_ticks','nonzero_trade_ticks',
                'trades','volume','fees_vcu','quote_withheld','regime_cancelled_qty',
                'refresh_posted','avg_bid_depth','avg_ask_depth')


def make_plan(seeds:tuple[int,...]=DEFAULT_SEEDS,
              horizons:tuple[int,...]=DEFAULT_HORIZONS,
              profile:str='full')->dict:
    if (type(seeds) not in (list,tuple) or not 2<=len(seeds)<=100
            or len(set(seeds))!=len(seeds)
            or any(type(s) is not int or not 301<=s<=2**31 for s in seeds)):
        raise ValueError('V10_REQUIRES_UNSEEN_SEEDS_301_OR_LATER')
    if (type(horizons) not in (list,tuple) or not 1<=len(horizons)<=5
            or len(set(horizons))!=len(horizons)
            or any(type(h) is not int or not 40<=h<=2000 for h in horizons)):
        raise ValueError('V10_INVALID_HORIZONS')
    if type(profile) is not str or profile not in ('core','full'):
        raise ValueError('V10_PROFILE_CORE_OR_FULL')
    cells = list(ALL_CELLS if profile=='full' else ALL_CELLS[:6])
    body = {
        'schema':SCHEMA,'source':'SIMULATED','role':'PREREGISTERED_LIQUIDITY',
        'seeds':list(seeds),'horizons':list(horizons),'profile':profile,
        'cells':cells,'interval_ticks':HEALTH_EVERY,'thresholds':dict(THRESHOLDS),
        'settings':{'events_per_tick':6,'waiting_agents':100,
                    'evolution_review_ticks':20,'weak_reviews_to_exit':3,
                    'max_joins_per_review':10,'maker_fee_bps':10,'taker_fee_bps':20,
                    'min_strategy_hold_ticks':40,'bankruptcy_threshold_percent':20,
                    'stop_denials':6,'maker_caution_ticks':35,'shock_tick':160,
                    'shock_enabled':True,'cancel_percent':85,'sell_qty':26,
                    'reentry_cooldown_ticks':40,
                    'quote_refresh_min_depth':5},
        'primary_endpoint':'two_sided_tick_percent',
        'methodology':(
          'All prices, wallets, agents, shocks and rules are simulated. '
          'Predeclared same-seed frozen/adaptive pairs and separate same-seed adaptive policy '
          'contrasts. Liquidity is measured each tick, with 20-tick diagnostics. '
          'Liquidity refresh is funded by pre-existing active wallets using full order '
          'reservations; no minting, debt, injection or revival of risk-stopped accounts. '
          'No-inflow keeps voluntary exits; no-exit keeps mandatory risk stops. '
          'Labels for missing quotes, inventory shortages, withholding and cancellations '
          'are overlapping observational signals, NOT mutually-exclusive causal reasons. '
          'Market prices diverge after different policies; cross-world contrasts are not '
          'isolated causal trader alpha. Different horizons reuse seeds. '
          'Secondary endpoint CIs do not adjust for multiple comparisons. '
          'Viability thresholds are predeclared heuristics, not market guarantees.'),
    }
    for cell in cells:
        for arm in ('frozen','adaptive'):
            _config(body,cell,seeds[0],horizons[0],arm)
    return {**body,'sha256':_digest(body)}


def verify_plan(plan:dict)->None:
    if type(plan) is not dict or type(plan.get('sha256')) is not str:
        raise ValueError('PLAN_SCHEMA_INVALID')
    body={k:v for k,v in plan.items() if k!='sha256'}
    if plan['sha256'] != _digest(body):
        raise ValueError('PLAN_SHA256_MISMATCH')
    if plan != make_plan(tuple(plan['seeds']),tuple(plan['horizons']),plan['profile']):
        raise ValueError('PLAN_NOT_CANONICAL')


def _config(plan:dict,cell:dict,seed:int,horizon:int,arm:str)->SessionConfig:
    if arm not in ('frozen','adaptive'):
        raise ValueError('ARM_INVALID')
    options=dict(plan['settings'])
    options['shock_tick']=min(options['shock_tick'],horizon)
    options.update(synthetic_regime=cell['regime'],
                   quote_refresh_mode=cell['refresh'],
                   allow_retired_reentry=cell['reentry'] and arm=='adaptive',
                   allow_population_join=cell['join'],
                   allow_population_exit=cell['exit'])
    return SessionConfig(seed=seed,ticks=horizon,evolution_enabled=True,
                         adaptation_enabled=arm=='adaptive',**options)


def _increment(now:dict,previous:dict)->dict:
    return {k:now[k]-previous[k] for k in now}


def _counter(m:SyntheticMarket)->dict:
    return {'trades':m.trades,'volume':m.total_traded_qty,
            'fees_vcu':m.economy.wallets['EXCHANGE'].cash_ticks / 10,
            'quote_withheld':m.maker_withheld,
            'regime_cancelled_qty':m.regime_cancelled_qty,
            'shock_cancelled_qty':m.shock['cancelled_qty'] if m.shock else 0,
            'refresh_attempts':sum(m.refresh_attempts.values()),
            'refresh_posted':sum(m.refresh_posted.values()),
            'refresh_quantity':sum(m.refresh_quantity.values()),
            'refresh_no_eligible':sum(m.refresh_no_eligible.values())}


def _maker_stockout(m:SyntheticMarket,side:str,price:int)->int:
    """1 means no *active, willing-inventory* MAKER can post one unit.

    This is inventory availability evidence, not proof that it caused missing depth.
    """
    for agent,member in m.population.active.items():
        if member.strategy != 'MAKER' or not m.economy.can_trade(agent):
            continue
        w=m.economy.wallets[agent]
        if side=='BUY' and w.free_cash >= price+fee_unit(price,m.economy.maker_fee_bps):
            return 0
        if side=='SELL' and w.free_coin >= 1:
            return 0
    return 1


def _tick_diagnostic(m:SyntheticMarket,previous:dict,threshold:int)->tuple[dict,dict]:
    bid=m.book.best('BUY');ask=m.book.best('SELL')
    bdepth=m.book.depth('BUY',8);adepth=m.book.depth('SELL',8)
    now=_counter(m);diff=_increment(now,previous)
    return ({
      'tick':m.tick,'active':len(m.population.active),
      'bid_present':int(bid is not None),'ask_present':int(ask is not None),
      'bid_depth':bdepth,'ask_depth':adepth,
      'spread_ticks':ask-bid if bid is not None and ask is not None else None,
      'bid_below_depth':int(bdepth<threshold),
      'ask_below_depth':int(adepth<threshold),
      'two_sided_depth':int(bid is not None and ask is not None and
                            bdepth>=threshold and adepth>=threshold),
      'maker_bid_stockout':_maker_stockout(m,'BUY',m._quote_price('BUY')) if bdepth<threshold else 0,
      'maker_ask_stockout':_maker_stockout(m,'SELL',m._quote_price('SELL')) if adepth<threshold else 0,
      'price_vcu':m.book.last_trade_ticks/10,
      **{k:round(v,5) if isinstance(v,float) else v for k,v in diff.items()}
    },now)


def _max_streak(values:list[bool])->int:
    best=run=0
    for condition in values:
        run=run+1 if condition else 0
        best=max(best,run)
    return best


def _windows(ticks:list[dict],length:int)->list[dict]:
    windows=[]
    for begin in range(0,len(ticks),length):
        chunk=ticks[begin:begin+length]
        windows.append({
            'tick':chunk[-1]['tick'],'span_ticks':len(chunk),
            'active':chunk[-1]['active'],
            'no_bid_ticks':sum(1-x['bid_present'] for x in chunk),
            'no_ask_ticks':sum(1-x['ask_present'] for x in chunk),
            'two_sided_depth_ticks':sum(x['two_sided_depth'] for x in chunk),
            'nonzero_trade_ticks':sum(x['trades']>0 for x in chunk),
            'trades':sum(x['trades'] for x in chunk),
            'volume':sum(x['volume'] for x in chunk),
            'fees_vcu':round(sum(x['fees_vcu'] for x in chunk),5),
            'quote_withheld':sum(x['quote_withheld'] for x in chunk),
            'regime_cancelled_qty':sum(x['regime_cancelled_qty'] for x in chunk),
            'refresh_posted':sum(x['refresh_posted'] for x in chunk),
            'avg_bid_depth':round(mean(x['bid_depth'] for x in chunk),5),
            'avg_ask_depth':round(mean(x['ask_depth'] for x in chunk),5),
        })
    return windows


def run_arm(plan:dict,cell:dict,seed:int,horizon:int,arm:str)->dict:
    m=SyntheticMarket(_config(plan,cell,seed,horizon,arm))
    initial=_fingerprint(m);pre_review=None
    previous=_counter(m);ticks=[]
    threshold=plan['thresholds']['min_depth_per_side']
    for _ in range(horizon):
        m.step(capture_state=False)
        if m.tick == min(horizon,m.config.evolution_review_ticks-1):
            pre_review=_fingerprint(m)
        row,previous=_tick_diagnostic(m,previous,threshold)
        ticks.append(row)
    if not (m.economy.check(m.book) and m.population.check() and m.book.check_conservation()):
        raise AssertionError('ECONOMY_OR_POPULATION_CONSERVATION_FAILED')
    snap=m.state()
    if not (snap['economy_conservation_pass'] and snap['conservation_pass'] and
            snap['economy']['pnl_identity_pass'] and snap['population']['conservation_pass']):
        raise AssertionError('ENDPOINT_CONSERVATION_FAILED')
    if m.population.relative_metrics(m.book.last_trade_ticks)['all_agents']!=1000:
        raise AssertionError('ORIGINAL_WALLET_LOSS')
    t=plan['thresholds'];p=m.population
    percent=lambda count:round(100*count/horizon,5)
    spread=[x['spread_ticks'] for x in ticks if x['spread_ticks'] is not None]
    active_survival=round(100*len(p.active)/len(p.initial_active_ids),5)
    both=percent(sum(x['two_sided_depth'] for x in ticks))
    traded=percent(sum(x['trades']>0 for x in ticks))
    no_bid=_max_streak([x['bid_present']==0 for x in ticks])
    no_ask=_max_streak([x['ask_present']==0 for x in ticks])
    metrics={
      'active_survival_percent':active_survival,
      'relative_vs_hold_vcu':snap['population']['relative_to_hold']['total_relative_vcu'],
      'trades':m.trades,'volume':m.total_traded_qty,
      'fees_vcu':snap['economy']['fees_total_vcu'],
      'active_agent_tick_exposure':p.active_agent_tick_exposure,
      'policy_exits':p.exit_by_reason['policy'],'risk_exits':p.exit_by_reason['risk'],
      'joins':p.joins,'reentries':p.reentries,
      'bid_present_ticks_percent':percent(sum(x['bid_present'] for x in ticks)),
      'ask_present_ticks_percent':percent(sum(x['ask_present'] for x in ticks)),
      'two_sided_tick_percent':both,
      'nonzero_trade_tick_percent':traded,
      'no_bid_ticks_percent':percent(sum(1-x['bid_present'] for x in ticks)),
      'no_ask_ticks_percent':percent(sum(1-x['ask_present'] for x in ticks)),
      'bid_below_depth_ticks_percent':percent(sum(x['bid_below_depth'] for x in ticks)),
      'ask_below_depth_ticks_percent':percent(sum(x['ask_below_depth'] for x in ticks)),
      'max_no_bid_streak':no_bid,'max_no_ask_streak':no_ask,
      'max_no_trade_streak':_max_streak([x['trades']==0 for x in ticks]),
      'maker_bid_stockout_ticks':sum(x['maker_bid_stockout'] for x in ticks),
      'maker_ask_stockout_ticks':sum(x['maker_ask_stockout'] for x in ticks),
      'withheld_quote_ticks':sum(x['quote_withheld']>0 for x in ticks),
      'regime_cancel_ticks':sum(x['regime_cancelled_qty']>0 for x in ticks),
      'shock_cancel_ticks':sum(x['shock_cancelled_qty']>0 for x in ticks),
      'mean_spread_ticks':round(mean(spread),5) if spread else -1.,
      'spread_coverage_percent':percent(len(spread)),
      'quote_refresh_attempts':sum(m.refresh_attempts.values()),
      'quote_refresh_posted':sum(m.refresh_posted.values()),
      'quote_refresh_quantity':sum(m.refresh_quantity.values()),
      'quote_refresh_no_eligible':sum(m.refresh_no_eligible.values()),
      'viable':int(active_survival>=t['min_active_survival_percent'] and
                  both>=t['min_two_sided_tick_percent'] and
                  traded>=t['min_nonzero_trade_tick_percent'] and
                  max(no_bid,no_ask)<=t['max_no_quote_streak_ticks']),
    }
    if set(metrics)!=set(METRICS):
        raise AssertionError('METRICS_COVERAGE_FAILED')
    if (sum(x['trades'] for x in ticks)!=m.trades or
        sum(x['volume'] for x in ticks)!=m.total_traded_qty or
        sum(x['refresh_posted'] for x in ticks)!=metrics['quote_refresh_posted']):
        raise AssertionError('PER_TICK_ACCOUNTING_FAILED')
    if abs(sum(x['fees_vcu'] for x in ticks)-metrics['fees_vcu'])>0.0001:
        raise AssertionError('FEE_TICK_ACCOUNTING_FAILED')
    return {'seed':seed,'horizon':horizon,'cell':cell['id'],'arm':arm,
            'initial_fingerprint':initial,'pre_review_fingerprint':pre_review,
            'metrics':metrics,'ticks':ticks,'health':_windows(ticks,plan['interval_ticks']),
            'price_tape_ticks':m.price_history if arm=='frozen' else None,
            'identity':{'initial_agents':len(p.initial_active_ids),
                        'initial_cash_ticks':m.economy.initial_cash_ticks,
                        'initial_coins':m.economy.initial_coins},
            'all_wallets':1000,'conservation':True}


def _key(seed:int,horizon:int,cell:str,arm:str)->str:
    return f'seed-{seed}-ticks-{horizon}-{cell}-{arm}.json'


def _seal(obj:dict)->dict:
    return {**obj,'content_sha256':_digest(obj)}


def _load_checkpoint(path:Path,plan:dict,seed:int,horizon:int,cell:str,arm:str)->dict:
    obj=json.loads(path.read_bytes())
    if type(obj) is not dict or set(obj)!={'plan_sha256','seed','horizon','cell','arm','data','content_sha256'}:
        raise ValueError('CHECKPOINT_SCHEMA_ERROR')
    content={k:v for k,v in obj.items() if k!='content_sha256'}
    if obj['content_sha256']!=_digest(content) or (
       obj['plan_sha256'],obj['seed'],obj['horizon'],obj['cell'],obj['arm']) != (
       plan['sha256'],seed,horizon,cell,arm):
        raise ValueError('CHECKPOINT_SHA256_MISMATCH')
    data=obj['data']
    if (data.get('conservation') is not True or data.get('all_wallets')!=1000 or
        (data.get('seed'),data.get('horizon'),data.get('cell'),data.get('arm'))!=(seed,horizon,cell,arm)
        or len(data.get('ticks',[]))!=horizon or
        len(data.get('health',[]))!=(horizon+HEALTH_EVERY-1)//HEALTH_EVERY or
        data.get('identity',{}).get('initial_agents')!=900):
        raise ValueError('CHECKPOINT_INVALID_DATA')
    return data


def _aggregate(plan:dict,records:list[dict])->dict:
    by={(r['seed'],r['horizon'],r['cell'],r['arm']):r for r in records}
    runs=[];pairs=[];ticks=[];health=[]
    for horizon in plan['horizons']:
        for cell in plan['cells']:
            for seed in plan['seeds']:
                fr=by[(seed,horizon,cell['id'],'frozen')]
                ad=by[(seed,horizon,cell['id'],'adaptive')]
                if (fr['initial_fingerprint'],fr['pre_review_fingerprint']) != (
                      ad['initial_fingerprint'],ad['pre_review_fingerprint']):
                    raise AssertionError(f'PAIR_FINGERPRINT_MISMATCH seed={seed} cell={cell["id"]}')
                if fr['identity']!=ad['identity']:
                    raise AssertionError('PAIR_INITIAL_ASSETS_MISMATCH')
                if len(fr['price_tape_ticks'])!=horizon+1:
                    raise AssertionError('PRICE_TAPE_MISMATCH')
                for obj in (fr,ad):
                    key={'seed':seed,'horizon':horizon,'cell':cell['id'],'arm':obj['arm']}
                    runs.append({**key,**obj['metrics']})
                    ticks.extend({**key,**item} for item in obj['ticks'])
                    health.extend({**key,**item} for item in obj['health'])
                pairs.append({'seed':seed,'horizon':horizon,'cell':cell['id'],
                              **{f'delta_{m}':ad['metrics'][m]-fr['metrics'][m] for m in METRICS}})
    stats=[]
    for horizon in plan['horizons']:
        for cell in plan['cells']:
            for metric in METRICS:
                for group in ('frozen','adaptive','delta'):
                    vals=([x[f'delta_{metric}'] for x in pairs if x['horizon']==horizon and x['cell']==cell['id']]
                          if group=='delta' else
                          [x[metric] for x in runs if x['horizon']==horizon and x['cell']==cell['id'] and x['arm']==group])
                    stats.append({'horizon':horizon,'cell':cell['id'],'metric':metric,'group':group,
                                  **interval(vals)})
    comparisons=(('legacy_reentry','legacy_baseline'),
                 ('legacy_maker_refresh','legacy_reentry'),
                 ('legacy_all_refresh','legacy_reentry'),
                 ('legacy_no_inflow','legacy_maker_refresh'),
                 ('legacy_no_exit','legacy_maker_refresh'))
    if plan['profile']=='core' or plan['profile']=='full':
        policy=[]
        for horizon in plan['horizons']:
            for target,control in comparisons:
                for metric in METRICS:
                    values=[by[(seed,horizon,target,'adaptive')]['metrics'][metric] -
                            by[(seed,horizon,control,'adaptive')]['metrics'][metric]
                            for seed in plan['seeds']]
                    policy.append({'horizon':horizon,'comparison':f'{target} - {control}',
                                   'metric':metric,**interval(values)})
    return {'schema':RESULT_SCHEMA,'source':'SIMULATED','plan_sha256':plan['sha256'],
            'runs':runs,'pairs':pairs,'tick_health':ticks,'health':health,
            'statistics':stats,'policy_comparisons':policy,
            'integrity':{'all_assets_conserved':True,'paired_state_aligned':True,
                         'all_1000_initial_wallets_included':True,
                         'per_tick_trades_and_fees_reconciled':True},
            'limitations':plan['methodology']}


def render_report(plan:dict,result:dict)->str:
    base='''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1"><title>Market Lab v1.0 — Liquidity</title><style>body{font:15px system-ui,sans-serif;background:#0b1620;color:#e7f3fa;max-width:1250px;margin:2rem auto;padding:1rem;line-height:1.5}h1{color:#73ddc8}section{border:1px solid #345;border-radius:12px;padding:1rem;margin:1rem 0}table{border-collapse:collapse;width:100%;display:block;overflow:auto}th,td{padding:.5rem .7rem;border-bottom:1px solid #345;white-space:nowrap;text-align:right}th:first-child,td:first-child{text-align:left}code{color:#73ddc8}</style></head><body>'''
    def fmt(v):
        return f'{v:.3f}' if isinstance(v,float) else str(v)
    items=[base,'<p>SIMULATED ONLY / OFFLINE / NO EXCHANGE CONNECTIONS</p>',
           '<h1>Market Lab v1.0 — динамика ликвидности и популяции</h1>',
           '<p>Plan SHA-256: <code>'+plan['sha256']+'</code> · seed '+escape(str(plan['seeds']))+
           ' · horizons '+escape(str(plan['horizons']))+' · '+plan['profile']+'</p>']
    display_metrics=(('two_sided_tick_percent','Доля тиков с достаточной двусторонней глубиной'),
                     ('active_survival_percent','Удержание активных участников'),
                     ('nonzero_trade_tick_percent','Доля тиков со сделками'),
                     ('relative_vs_hold_vcu','Результат против Hold, VCU'),
                     ('max_no_bid_streak','Самая длинная серия без BID'),
                     ('max_no_ask_streak','Самая длинная серия без ASK'))
    for metric,label in display_metrics:
        items.append('<section><h2>'+escape(label)+'</h2><table><tr><th>Тики</th><th>Сценарий</th><th>Средняя Δ adaptive−frozen</th><th>95% low</th><th>95% high</th></tr>')
        for h in plan['horizons']:
            for cell in plan['cells']:
                s=next(x for x in result['statistics'] if x['horizon']==h and
                       x['cell']==cell['id'] and x['metric']==metric and x['group']=='delta')
                vals=(h,cell['id'],s['mean'],s['ci95_low'],s['ci95_high'])
                items.append('<tr>'+''.join(f'<td>{escape(fmt(v))}</td>' for v in vals)+'</tr>')
        items.append('</table></section>')
    items.append('<section><h2>Прямые сравнения политики (adaptive против adaptive)</h2><table><tr><th>Тики</th><th>Контраст</th><th>Метрика</th><th>Mean Δ</th><th>95% low</th><th>95% high</th></tr>')
    pick={'two_sided_tick_percent','nonzero_trade_tick_percent','active_survival_percent',
          'relative_vs_hold_vcu','quote_refresh_posted','viable'}
    for s in result['policy_comparisons']:
        if s['metric'] in pick:
            items.append('<tr>'+''.join(f'<td>{escape(fmt(v))}</td>' for v in (
                s['horizon'],s['comparison'],s['metric'],s['mean'],s['ci95_low'],s['ci95_high']))+'</tr>')
    items.append('</table><p>Разные миры формируют разные цены. Это не чистый причинный эффект стратегий.</p></section>')
    items.append('<section><h2>Как читать диагностику</h2><p><code>tick_health.csv</code> содержит каждую симуляционную отметку: BID/ASK, глубину, спред, сделки, состояние активности, отсутствие свободного инвентаря у MAKER, отмены и пропущенные котировки. Признаки могут накладываться; они НЕ доказывают единственную причину потери ликвидности. <code>health.csv</code> агрегирует по 20 тикам. <code>statistics.csv</code> — интервалы по независимым seed.</p></section>')
    items.append('<section><h2>Предварительно фиксированные критерии</h2><p>'+escape(str(plan['thresholds']))+'</p><p>Инварианты денег, монет, FIFO и резервов проверяются отдельно на каждом тике.</p></section>')
    items.append('<section><h2>Ограничения</h2><p>'+escape(plan['methodology'])+'</p></section></body></html>')
    return ''.join(items)


def _finalize(plan:dict,result:dict,output:Path)->list[Path]:
    files={
      'summary.json':_canonical(result),
      'runs.csv':_csv_bytes(result['runs'],['seed','horizon','cell','arm',*METRICS]),
      'paired.csv':_csv_bytes(result['pairs'],['seed','horizon','cell',*(f'delta_{m}' for m in METRICS)]),
      'statistics.csv':_csv_bytes(result['statistics'],['horizon','cell','metric','group','n','mean','median','ci95_low','ci95_high','positive','negative','zero','sign_p_two_sided']),
      'policy_comparisons.csv':_csv_bytes(result['policy_comparisons'],['horizon','comparison','metric','n','mean','median','ci95_low','ci95_high','positive','negative','zero','sign_p_two_sided']),
      'tick_health.csv':_csv_bytes(result['tick_health'],list(TICK_COLUMNS)),
      'health.csv':_csv_bytes(result['health'],list(WINDOW_COLUMNS)),
      'report.html':render_report(plan,result).encode('utf-8'),
    }
    for name,data in files.items():
        p=output/name
        if p.exists() and p.read_bytes()!=data:
            raise FileExistsError(f'FINAL_CONFLICT {p}')
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
    if resume and not output.exists():raise ValueError('RESUME_DIRECTORY_MISSING')
    if output.exists() and not resume:
        raise FileExistsError('RESULT_DIRECTORY_ALREADY_EXISTS_USE_RESUME')
    output.mkdir(parents=True,exist_ok=True)
    p=output/'plan.json'
    if p.exists():
        if p.read_bytes()!=_canonical(plan):raise ValueError('OUTPUT_PLAN_MISMATCH')
    elif resume and any(output.iterdir()):
        raise ValueError('OUTPUT_PLAN_MISSING')
    else:
        _atomic_bytes(p,_canonical(plan))


def _verify_final(plan:dict,output:Path)->dict|None:
    p=output/'complete.json'
    if not p.exists():return None
    seal=json.loads(p.read_bytes())
    if seal.get('plan_sha256')!=plan['sha256'] or type(seal.get('file_sha256')) is not dict:
        raise ValueError('FINAL_PLAN_MISMATCH')
    expected={'summary.json','runs.csv','paired.csv','statistics.csv','policy_comparisons.csv',
              'tick_health.csv','health.csv','report.html'}
    if set(seal['file_sha256'])!=expected:
        raise ValueError('FINAL_FILE_SET_MISMATCH')
    for name,digest in seal['file_sha256'].items():
        file=output/name
        if not file.is_file() or sha256(file.read_bytes()).hexdigest()!=digest:
            raise ValueError(f'FINAL_HASH_MISMATCH {name}')
    result=json.loads((output/'summary.json').read_bytes())
    if _digest(result)!=seal.get('result_sha256'):
        raise ValueError('FINAL_RESULT_HASH_MISMATCH')
    return result


def execute(plan:dict,output:Path,*,resume:bool=False,max_new_arms:int|None=None,
            plan_path:Path|None=None)->dict|None:
    if max_new_arms is not None and (type(max_new_arms) is not int or max_new_arms<1):
        raise ValueError('MAX_NEW_ARMS_INVALID')
    _prepare(plan,output,resume)
    complete=_verify_final(plan,output)
    if complete is not None:
        print('RESEARCH_ALREADY_COMPLETE=YES VERIFIED_FILES=YES',flush=True)
        return complete
    items=list(_items(plan));records=[];new=0
    for horizon,cell,seed,arm in items:
        path=output/'checkpoints'/_key(seed,horizon,cell['id'],arm)
        if path.exists():
            record=_load_checkpoint(path,plan,seed,horizon,cell['id'],arm)
            verb='REUSED'
        else:
            record=run_arm(plan,cell,seed,horizon,arm)
            if plan_path is not None and plan_path.read_bytes()!=_canonical(plan):
                raise ValueError('PLAN_CHANGED_DURING_RUN')
            _atomic_bytes(path,_canonical(_seal({'plan_sha256':plan['sha256'],
                'seed':seed,'horizon':horizon,'cell':cell['id'],'arm':arm,'data':record})))
            verb='SAVED';new+=1
        records.append(record)
        print(f'ARM_{verb}={len(records)}/{len(items)} SEED={seed} TICKS={horizon} CELL={cell["id"]} ARM={arm}',flush=True)
        if max_new_arms is not None and new>=max_new_arms and len(records)<len(items):
            print(f'RESEARCH_PAUSED=YES COMPLETED={len(records)}/{len(items)} RESUME=--resume',flush=True)
            return None
    result=_aggregate(plan,records)
    if plan_path is not None and plan_path.read_bytes()!=_canonical(plan):
        raise ValueError('PLAN_CHANGED_DURING_RUN')
    files=_finalize(plan,result,output)
    print(f'RESEARCH_PASS=YES INTEGRITY_PASS=YES PAIRS={len(result["pairs"])} ARMS={len(result["runs"])}',flush=True)
    for file in files:print(f'FILE={file}',flush=True)
    return result


def progress_status(plan:dict,output:Path)->dict:
    verify_plan(plan)
    if not (output/'plan.json').is_file() or (output/'plan.json').read_bytes()!=_canonical(plan):
        raise ValueError('OUTPUT_PLAN_MISMATCH')
    missing=[];verified=0
    for horizon,cell,seed,arm in _items(plan):
        p=output/'checkpoints'/_key(seed,horizon,cell['id'],arm)
        if p.is_file():
            _load_checkpoint(p,plan,seed,horizon,cell['id'],arm);verified+=1
        else:
            missing.append((seed,horizon,cell['id'],arm))
    complete=_verify_final(plan,output)
    print(f'PROGRESS_VERIFIED={verified}/{verified+len(missing)} NEXT={missing[0] if missing else "complete"} FINALIZED={complete is not None}',flush=True)
    return {'verified':verified,'total':verified+len(missing),'finalized':complete is not None}


def main(argv:list[str]|None=None)->None:
    p=argparse.ArgumentParser(description='Market Lab v1.0 — OFFLINE tick-level liquidity research')
    sub=p.add_subparsers(dest='action',required=True)
    plan=sub.add_parser('plan');plan.add_argument('--seeds',default='301:310')
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
            obj=make_plan(parse_seed_range(args.seeds),parse_horizons(args.horizons),args.profile)
            _atomic_bytes(path,_canonical(obj))
            print(f'PLAN_SHA256={obj["sha256"]} SEEDS={len(obj["seeds"])} HORIZONS={obj["horizons"]} CELLS={len(obj["cells"])}',flush=True)
        else:
            path=Path(args.plan);raw=path.read_bytes();obj=json.loads(raw)
            verify_plan(obj)
            if raw!=_canonical(obj):raise ValueError('PLAN_FILE_NONCANONICAL')
            print(f'PLAN_VERIFIED={obj["sha256"]} SEEDS={len(obj["seeds"])} HORIZONS={obj["horizons"]} CELLS={len(obj["cells"])}',flush=True)
            if args.action=='status':progress_status(obj,Path(args.output))
            else:execute(obj,Path(args.output),resume=args.resume,max_new_arms=args.max_new_arms,plan_path=path)
    except (ValueError,FileExistsError,KeyError,TypeError,OSError) as exc:
        p.error(str(exc))

if __name__=='__main__':
    main()
