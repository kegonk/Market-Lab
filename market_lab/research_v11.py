"""Market Lab v1.1: preregistered, closed-world liquidity-controller research.

No exchange API, external price feed, transfers, debt, money creation or trading.
All comparisons are synthetic; divergent endogenous prices preclude claims of
isolated causal trading alpha. New seeds >= 401 are holdout from v0.7-v1.0.
"""
from __future__ import annotations

import argparse
from hashlib import sha256
from html import escape
import json
from pathlib import Path
from statistics import mean

from .interactive import SessionConfig, SyntheticMarket
from .research import _fingerprint
from .research_v07 import interval,parse_seed_range
from .research_v08 import (_atomic_bytes,_canonical,_csv_bytes,_digest,parse_horizons)

SCHEMA='MARKET_LAB_V11_LIQUIDITY_CONTROL_PLAN_1'
RESULT_SCHEMA='MARKET_LAB_V11_LIQUIDITY_CONTROL_RESULT_1'
SEEDS=tuple(range(401,411))
HORIZONS=(320,1280)
WINDOW=20
THRESHOLDS={'min_active_survival_percent':60,'min_two_sided_tick_percent':70,
            'min_nonzero_trade_tick_percent':35,'max_no_quote_streak_ticks':25,
            'min_depth_per_side':5}

# Precommitted groups. Symmetric and side-aware policies use exactly the same
# existing-wallet funding; size and risk caps are separate ablations.
ALL_CELLS=(
 {'id':'legacy_off','regime':'LEGACY','mode':'OFF','size':3,'cap':20},
 {'id':'legacy_symmetric','regime':'LEGACY','mode':'SYMMETRIC','size':3,'cap':20},
 {'id':'legacy_sideaware','regime':'LEGACY','mode':'SIDE_AWARE','size':3,'cap':20},
 {'id':'legacy_sideaware_small','regime':'LEGACY','mode':'SIDE_AWARE','size':1,'cap':20},
 {'id':'legacy_sideaware_large','regime':'LEGACY','mode':'SIDE_AWARE','size':7,'cap':20},
 {'id':'legacy_sideaware_tight','regime':'LEGACY','mode':'SIDE_AWARE','size':3,'cap':10},
 {'id':'bull_off','regime':'BULL','mode':'OFF','size':3,'cap':20},
 {'id':'bull_symmetric','regime':'BULL','mode':'SYMMETRIC','size':3,'cap':20},
 {'id':'bull_sideaware','regime':'BULL','mode':'SIDE_AWARE','size':3,'cap':20},
 {'id':'bear_off','regime':'BEAR','mode':'OFF','size':3,'cap':20},
 {'id':'bear_symmetric','regime':'BEAR','mode':'SYMMETRIC','size':3,'cap':20},
 {'id':'bear_sideaware','regime':'BEAR','mode':'SIDE_AWARE','size':3,'cap':20},
 {'id':'sideways_off','regime':'SIDEWAYS','mode':'OFF','size':3,'cap':20},
 {'id':'sideways_sideaware','regime':'SIDEWAYS','mode':'SIDE_AWARE','size':3,'cap':20},
 {'id':'thin_off','regime':'THIN','mode':'OFF','size':3,'cap':20},
 {'id':'thin_symmetric','regime':'THIN','mode':'SYMMETRIC','size':3,'cap':20},
 {'id':'thin_sideaware','regime':'THIN','mode':'SIDE_AWARE','size':3,'cap':20},
)
COMPARISONS=(
 ('legacy_symmetric','legacy_off'),
 ('legacy_sideaware','legacy_off'),
 ('legacy_sideaware','legacy_symmetric'),
 ('legacy_sideaware_small','legacy_sideaware'),
 ('legacy_sideaware_large','legacy_sideaware'),
 ('legacy_sideaware_tight','legacy_sideaware'),
 ('bull_symmetric','bull_off'),('bull_sideaware','bull_off'),('bull_sideaware','bull_symmetric'),
 ('bear_symmetric','bear_off'),('bear_sideaware','bear_off'),('bear_sideaware','bear_symmetric'),
 ('sideways_sideaware','sideways_off'),
 ('thin_symmetric','thin_off'),('thin_sideaware','thin_off'),('thin_sideaware','thin_symmetric'),
)
# Only numeric metrics: valid for direct paired differences and CIs.
METRICS=(
 'active_agents','active_survival_percent','relative_vs_hold_vcu',
 'trades','volume','fees_vcu','nonzero_trade_tick_percent',
 'two_sided_tick_percent','bid_below_depth_tick_percent','ask_below_depth_tick_percent',
 'max_no_bid_streak','max_no_ask_streak','avg_bid_depth','avg_ask_depth',
 'mean_spread_ticks','spread_coverage_percent','viable',
 'policy_exits','risk_exits','reentries',
 'bid_placed','ask_placed','bid_placed_qty','ask_placed_qty',
 'bid_filled_qty','ask_filled_qty','bid_cancelled_qty','ask_cancelled_qty',
 'bid_remaining_qty','ask_remaining_qty',
 'bid_mean_closed_lifetime_ticks','ask_mean_closed_lifetime_ticks',
 'bid_attempts','ask_attempts','bid_no_inventory','ask_no_inventory',
 'bid_risk_capped','ask_risk_capped','bid_no_makers','ask_no_makers',
 'maker_fee_vcu','distinct_participating_makers','makers_relative_to_hold_vcu',
 'makers_fees_paid_vcu',
)
WINDOW_COLUMNS=('seed','horizon','cell','arm','tick','span_ticks','active',
 'bid_depth_mean','ask_depth_mean','bid_thin_ticks','ask_thin_ticks','both_ticks',
 'trade_ticks','trades','volume','fees_vcu','controller_bid_orders',
 'controller_ask_orders','controller_bid_fills','controller_ask_fills')


def make_plan(seeds:tuple[int,...]=SEEDS,horizons:tuple[int,...]=HORIZONS,profile:str='full')->dict:
    if (type(seeds) not in (list,tuple) or not 2<=len(seeds)<=100
        or any(type(s) is not int or not 401<=s<=2**31 for s in seeds)
        or len(set(seeds))!=len(seeds)):
        raise ValueError('V11_HOLDOUT_REQUIRES_2_TO_100_UNSEEN_SEEDS_GE_401')
    if (type(horizons) not in (list,tuple) or not 1<=len(horizons)<=5
        or any(type(h) is not int or not 40<=h<=2000 for h in horizons)
        or len(set(horizons))!=len(horizons)):
        raise ValueError('V11_HORIZONS_MUST_BE_40_TO_2000_UNIQUE')
    if type(profile) is not str or profile not in ('core','full'):
        raise ValueError('V11_PROFILE_CORE_OR_FULL')
    cells=list(ALL_CELLS[:6] if profile=='core' else ALL_CELLS)
    body={'schema':SCHEMA,'source':'SIMULATED','role':'PREREGISTERED_LIQUIDITY_CONTROL',
          'seeds':list(seeds),'horizons':list(horizons),'profile':profile,
          'cells':cells,'thresholds':THRESHOLDS.copy(),'interval_ticks':WINDOW,
          'settings':{'events_per_tick':6,'waiting_agents':100,
            'evolution_review_ticks':20,'weak_reviews_to_exit':3,
            'max_joins_per_review':10,'min_strategy_hold_ticks':40,
            'maker_fee_bps':10,'taker_fee_bps':20,
            'bankruptcy_threshold_percent':20,'stop_denials':6,
            'maker_caution_ticks':35,'shock_tick':160,'shock_enabled':True,
            'cancel_percent':85,'sell_qty':26,'reentry_cooldown_ticks':40,
            'liquidity_v11_bid_target':5,'liquidity_v11_ask_target':5,
            'liquidity_v11_max_orders_per_side_tick':2,
            'liquidity_v11_max_orders_per_agent':3},
          'primary_endpoint':'two_sided_tick_percent',
          'methodology':(
            'Entirely synthetic, offline, same-seed pairs. Each world has endogenous prices: '
            'differences are not isolated trader alpha or real-market forecasts. '
            'Existing active funded MAKER wallets place independent BUY/SELL passive orders; '
            'reservations, per-wallet exposure caps and risk stops enforced. '
            'Per-order fill/cancellation/lifetime counters are observational. '
            'Open orders at horizon are right-censored, not falsely marked closed. '
            'Original agent wallets, other maker and stress wallets, fees remain conserved; '
            'no external market connections, debt, subsidy or minting. '
            'Horizon groups reuse seed, and secondary comparisons are unadjusted for multiplicity. '
            'Fixed viability thresholds are synthetic heuristics. Seed 401+ not used in v0.7-v1.0.'),
         }
    for c in cells:
        for arm in ('frozen','adaptive'):
            _config(body,c,seeds[0],horizons[0],arm)
    return {**body,'sha256':_digest(body)}


def verify_plan(plan:dict)->None:
    if type(plan) is not dict or type(plan.get('sha256')) is not str:
        raise ValueError('PLAN_SCHEMA_INVALID')
    body={k:v for k,v in plan.items() if k!='sha256'}
    if plan['sha256']!=_digest(body):
        raise ValueError('PLAN_SHA256_MISMATCH')
    if plan!=make_plan(tuple(plan['seeds']),tuple(plan['horizons']),plan['profile']):
        raise ValueError('PLAN_NOT_CANONICAL')


def _config(plan:dict,cell:dict,seed:int,horizon:int,arm:str)->SessionConfig:
    if arm not in ('frozen','adaptive'):raise ValueError('ARM_INVALID')
    options=dict(plan['settings'])
    options['shock_tick']=min(options['shock_tick'],horizon)
    options.update(synthetic_regime=cell['regime'],
                   allow_retired_reentry=arm=='adaptive',
                   quote_refresh_mode='OFF',
                   liquidity_v11_mode=cell['mode'],
                   liquidity_v11_max_order_qty=cell['size'],
                   liquidity_v11_wallet_cap_percent=cell['cap'])
    return SessionConfig(seed=seed,ticks=horizon,evolution_enabled=True,
                         adaptation_enabled=arm=='adaptive',**options)


def _streak_step(current:int,longest:int,absent:bool)->tuple[int,int]:
    current=current+1 if absent else 0
    return current,max(current,longest)


def run_arm(plan:dict,cell:dict,seed:int,horizon:int,arm:str)->dict:
    m=SyntheticMarket(_config(plan,cell,seed,horizon,arm))
    initial=_fingerprint(m);before_review=None
    prev_trades=prev_volume=0
    prev_fee=0.
    prev_placed={'BUY':0,'SELL':0};prev_filled={'BUY':0,'SELL':0}
    totals={'both':0,'trade':0,'bid_thin':0,'ask_thin':0,
            'bid_depth':0,'ask_depth':0,'spread_total':0,'spread_samples':0}
    cur_bid=cur_ask=long_bid=long_ask=0
    current=[];windows=[];threshold=plan['thresholds']['min_depth_per_side']
    for tick in range(1,horizon+1):
        m.step(capture_state=False)
        if tick==min(horizon,m.config.evolution_review_ticks-1):
            before_review=_fingerprint(m)
        bid=m.book.best('BUY');ask=m.book.best('SELL')
        b=m.book.depth('BUY',8);a=m.book.depth('SELL',8)
        now_trades=m.trades;now_vol=m.total_traded_qty
        now_fees=m.economy.wallets['EXCHANGE'].cash_ticks/10
        spread=ask-bid if bid is not None and ask is not None else None
        both=int(b>=threshold and a>=threshold and spread is not None)
        trade=int(now_trades>prev_trades)
        cur_bid,long_bid=_streak_step(cur_bid,long_bid,bid is None)
        cur_ask,long_ask=_streak_step(cur_ask,long_ask,ask is None)
        totals['both']+=both;totals['trade']+=trade
        totals['bid_thin']+=b<threshold;totals['ask_thin']+=a<threshold
        totals['bid_depth']+=b;totals['ask_depth']+=a
        if spread is not None:
            totals['spread_total']+=spread;totals['spread_samples']+=1
        ctrl=m.liquidity_v11
        placed={'BUY':ctrl.placed['BUY'] if ctrl else 0,'SELL':ctrl.placed['SELL'] if ctrl else 0}
        filled={'BUY':ctrl.filled_qty['BUY'] if ctrl else 0,'SELL':ctrl.filled_qty['SELL'] if ctrl else 0}
        current.append({'tick':tick,'active':len(m.population.active),
                        'bid_depth':b,'ask_depth':a,'bid_thin':int(b<threshold),
                        'ask_thin':int(a<threshold),'both':both,'trade':trade,
                        'trades':now_trades-prev_trades,'volume':now_vol-prev_volume,
                        'fees_vcu':round(now_fees-prev_fee,5),
                        'bid_orders':placed['BUY']-prev_placed['BUY'],
                        'ask_orders':placed['SELL']-prev_placed['SELL'],
                        'bid_fills':filled['BUY']-prev_filled['BUY'],
                        'ask_fills':filled['SELL']-prev_filled['SELL']})
        prev_trades=now_trades;prev_volume=now_vol;prev_fee=now_fees
        prev_placed=placed;prev_filled=filled
        if tick%WINDOW==0 or tick==horizon:
            windows.append({'tick':tick,'span_ticks':len(current),
             'active':current[-1]['active'],
             'bid_depth_mean':round(mean(x['bid_depth'] for x in current),5),
             'ask_depth_mean':round(mean(x['ask_depth'] for x in current),5),
             'bid_thin_ticks':sum(x['bid_thin'] for x in current),
             'ask_thin_ticks':sum(x['ask_thin'] for x in current),
             'both_ticks':sum(x['both'] for x in current),
             'trade_ticks':sum(x['trade'] for x in current),
             'trades':sum(x['trades'] for x in current),
             'volume':sum(x['volume'] for x in current),
             'fees_vcu':round(sum(x['fees_vcu'] for x in current),5),
             'controller_bid_orders':sum(x['bid_orders'] for x in current),
             'controller_ask_orders':sum(x['ask_orders'] for x in current),
             'controller_bid_fills':sum(x['bid_fills'] for x in current),
             'controller_ask_fills':sum(x['ask_fills'] for x in current)})
            current=[]
    snap=m.state();p=m.population; t=plan['thresholds']
    if not (m.economy.check(m.book) and p.check() and m.book.check_conservation() and
            snap['economy_conservation_pass'] and snap['conservation_pass'] and
            snap['economy']['pnl_identity_pass'] and snap['population']['conservation_pass']):
        raise AssertionError('V11_ECONOMY_INTEGRITY_FAILED')
    if p.relative_metrics(m.book.last_trade_ticks)['all_agents']!=1000:
        raise AssertionError('V11_WALLET_LOSS')
    if sum(x['trades'] for x in windows)!=m.trades or sum(x['volume'] for x in windows)!=m.total_traded_qty:
        raise AssertionError('V11_WINDOW_TRADE_RECONCILIATION')
    if abs(sum(x['fees_vcu'] for x in windows)-snap['economy']['fees_total_vcu'])>0.00001:
        raise AssertionError('V11_WINDOW_FEE_RECONCILIATION')
    ctrl=m.liquidity_v11
    controller=ctrl.snapshot() if ctrl else {}
    if sum(x['controller_bid_orders'] for x in windows)!=controller.get('bid_placed',0):
        raise AssertionError('V11_WINDOW_BID_RECONCILIATION')
    if sum(x['controller_ask_orders'] for x in windows)!=controller.get('ask_placed',0):
        raise AssertionError('V11_WINDOW_ASK_RECONCILIATION')
    if sum(x['controller_bid_fills'] for x in windows)!=controller.get('bid_filled_qty',0):
        raise AssertionError('V11_WINDOW_BID_FILL_RECONCILIATION')
    if sum(x['controller_ask_fills'] for x in windows)!=controller.get('ask_filled_qty',0):
        raise AssertionError('V11_WINDOW_ASK_FILL_RECONCILIATION')
    participants=ctrl.participants if ctrl else set()
    maker_vs_hold=0.;maker_paid=0.
    last=m.book.last_trade_ticks
    for aid in participants:
        w=m.economy.wallets[aid]
        start_cash,start_coins=p.initial_wallets[aid]
        maker_vs_hold+=(w.cash_ticks+w.coin_qty*last-start_cash-start_coins*last)/10
        maker_paid+=w.fees_paid_ticks/10
    pct=lambda v:round(100*v/horizon,5)
    metrics={'active_agents':len(p.active),
     'active_survival_percent':round(100*len(p.active)/len(p.initial_active_ids),5),
     'relative_vs_hold_vcu':snap['population']['relative_to_hold']['total_relative_vcu'],
     'trades':m.trades,'volume':m.total_traded_qty,'fees_vcu':snap['economy']['fees_total_vcu'],
     'nonzero_trade_tick_percent':pct(totals['trade']),
     'two_sided_tick_percent':pct(totals['both']),
     'bid_below_depth_tick_percent':pct(totals['bid_thin']),
     'ask_below_depth_tick_percent':pct(totals['ask_thin']),
     'max_no_bid_streak':long_bid,'max_no_ask_streak':long_ask,
     'avg_bid_depth':round(totals['bid_depth']/horizon,5),
     'avg_ask_depth':round(totals['ask_depth']/horizon,5),
     'mean_spread_ticks':round(totals['spread_total']/totals['spread_samples'],5) if totals['spread_samples'] else -1.,
     'spread_coverage_percent':pct(totals['spread_samples']),
     'policy_exits':p.exit_by_reason['policy'],'risk_exits':p.exit_by_reason['risk'],
     'reentries':p.reentries,
     'maker_fee_vcu':controller.get('maker_fee_vcu',0),
     'distinct_participating_makers':controller.get('distinct_participating_makers',0),
     'makers_relative_to_hold_vcu':round(maker_vs_hold,5),
     'makers_fees_paid_vcu':round(maker_paid,5)}
    for side in ('bid','ask'):
        for suffix in ('placed','placed_qty','filled_qty','cancelled_qty','remaining_qty',
                       'mean_closed_lifetime_ticks','attempts','no_inventory',
                       'risk_capped','no_makers'):
            metrics[f'{side}_{suffix}']=controller.get(f'{side}_{suffix}',0)
    metrics['viable']=int(metrics['active_survival_percent']>=t['min_active_survival_percent'] and
                          metrics['two_sided_tick_percent']>=t['min_two_sided_tick_percent'] and
                          metrics['nonzero_trade_tick_percent']>=t['min_nonzero_trade_tick_percent'] and
                          max(long_bid,long_ask)<=t['max_no_quote_streak_ticks'])
    if set(metrics)!=set(METRICS):
        raise AssertionError('V11_METRIC_SCHEMA_MISMATCH')
    return {'seed':seed,'horizon':horizon,'cell':cell['id'],'arm':arm,
            'initial_fingerprint':initial,'pre_review_fingerprint':before_review,
            'metrics':metrics,'windows':windows,'conservation':True,
            'all_wallets':1000,'initial_agents':len(p.initial_active_ids),
            'initial_cash_ticks':m.economy.initial_cash_ticks,
            'initial_coins':m.economy.initial_coins,
            'open_orders_censored':controller.get('currently_open_orders',0)}


def _key(seed:int,horizon:int,cell:str,arm:str)->str:
    return f'seed-{seed}-ticks-{horizon}-{cell}-{arm}.json'


def _seal(item:dict)->dict:
    return {**item,'content_sha256':_digest(item)}


def _load_checkpoint(path:Path,plan:dict,seed:int,horizon:int,cell:str,arm:str)->dict:
    obj=json.loads(path.read_bytes())
    if type(obj) is not dict or set(obj)!={'plan_sha256','seed','horizon','cell','arm','data','content_sha256'}:
        raise ValueError('CHECKPOINT_SCHEMA_INVALID')
    payload={k:v for k,v in obj.items() if k!='content_sha256'}
    if obj['content_sha256']!=_digest(payload) or tuple(obj[k] for k in ('plan_sha256','seed','horizon','cell','arm'))!=(plan['sha256'],seed,horizon,cell,arm):
        raise ValueError('CHECKPOINT_SHA256_MISMATCH')
    data=obj['data'];ws=data.get('windows',[])
    if (data.get('conservation') is not True or data.get('all_wallets')!=1000
        or data.get('initial_agents')!=900 or data.get('metrics',{}).keys()!=set(METRICS)
        or (data.get('seed'),data.get('horizon'),data.get('cell'),data.get('arm'))!=(seed,horizon,cell,arm)
        or len(ws)!=(horizon+WINDOW-1)//WINDOW
        or sum(x['span_ticks'] for x in ws)!=horizon
        or sum(x['trades'] for x in ws)!=data['metrics']['trades']):
        raise ValueError('CHECKPOINT_DATA_INTEGRITY_FAILED')
    return data


def _items(plan:dict):
    for h in plan['horizons']:
        for cell in plan['cells']:
            for seed in plan['seeds']:
                for arm in ('frozen','adaptive'):
                    yield h,cell,seed,arm


def _aggregate(plan:dict,records:list[dict])->tuple[dict,list[dict]]:
    by={(r['seed'],r['horizon'],r['cell'],r['arm']):r for r in records}
    runs=[];pairs=[];windows=[];stats=[];policy=[]
    for h in plan['horizons']:
        for cell in plan['cells']:
            for seed in plan['seeds']:
                fr=by[seed,h,cell['id'],'frozen'];ad=by[seed,h,cell['id'],'adaptive']
                if (fr['initial_fingerprint']!=ad['initial_fingerprint'] or
                    fr['pre_review_fingerprint']!=ad['pre_review_fingerprint'] or
                    fr['initial_cash_ticks']!=ad['initial_cash_ticks'] or
                    fr['initial_coins']!=ad['initial_coins']):
                    raise AssertionError('PAIR_INITIAL_OR_PRE_REVIEW_MISMATCH')
                for r in (fr,ad):
                    runs.append({k:r[k] for k in ('seed','horizon','cell','arm')} | r['metrics'])
                    windows.extend([{**{k:r[k] for k in ('seed','horizon','cell','arm')},**w} for w in r['windows']])
                pairs.append({'seed':seed,'horizon':h,'cell':cell['id'],
                             **{f'delta_{k}':round(ad['metrics'][k]-fr['metrics'][k],6) for k in METRICS}})
            for metric in METRICS:
                for group in ('frozen','adaptive','delta'):
                    data=([x[f'delta_{metric}'] for x in pairs if x['horizon']==h and x['cell']==cell['id']]
                          if group=='delta' else
                          [x[metric] for x in runs if x['horizon']==h and x['cell']==cell['id'] and x['arm']==group])
                    stats.append({'horizon':h,'cell':cell['id'],'metric':metric,'group':group,**interval(data)})
    allowed={c['id'] for c in plan['cells']}
    for h in plan['horizons']:
        for target,control in COMPARISONS:
            if target not in allowed or control not in allowed:continue
            for metric in METRICS:
                delta=[round(by[seed,h,target,'adaptive']['metrics'][metric]-
                             by[seed,h,control,'adaptive']['metrics'][metric],6) for seed in plan['seeds']]
                policy.append({'horizon':h,'comparison':f'{target} - {control}',
                               'metric':metric,**interval(delta)})
    summary={'schema':RESULT_SCHEMA,'source':'SIMULATED','plan_sha256':plan['sha256'],
             'runs':runs,'pairs':pairs,'statistics':stats,'policy_comparisons':policy,
             'window_rows':len(windows),
             'integrity':{'asset_conservation':True,'all_1000_wallets':True,
                          'paired_initial_and_pre_review':True,
                          'window_trade_fee_and_order_reconciliation':True,
                          'fully_backed_control_quotes':True},
             'limitations':plan['methodology']}
    return summary,windows


def render_report(plan:dict,result:dict)->str:
    base='''<!doctype html><html lang="ru"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Market Lab v1.1 — Liquidity Control</title><style>body{font:15px system-ui,sans-serif;background:#0b1620;color:#e8f2fc;max-width:1200px;margin:2rem auto;padding:1rem}h1{color:#72e9c8}section{border:1px solid #345;border-radius:10px;margin:1rem 0;padding:1rem;overflow:auto}table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid #345;padding:.55rem;text-align:right;white-space:nowrap}td:first-child,th:first-child{text-align:left}</style></head><body>'''
    out=[base,'<p>SIMULATED ONLY / OFFLINE / NO EXCHANGE CONNECTIONS</p>',
         '<h1>Market Lab v1.1 — управление двусторонней ликвидностью</h1>',
         '<p>SHA256 '+escape(plan['sha256'])+' · seeds '+escape(str(plan['seeds']))+
         ' · horizons '+escape(str(plan['horizons']))+'</p>']
    for metric,title in (
        ('two_sided_tick_percent','Доля тиков с глубиной BID и ASK ≥ 5'),
        ('nonzero_trade_tick_percent','Тики со сделками'),
        ('relative_vs_hold_vcu','Результат относительно Hold (VCU)'),
        ('bid_placed_qty','Новые лимиты BUY (шт. виртуальных монет)'),
        ('ask_placed_qty','Новые лимиты SELL (шт. виртуальных монет)'),
        ('bid_filled_qty','Исполненные лимиты BUY'),
        ('ask_filled_qty','Исполненные лимиты SELL'),
        ('viable','Доля прогонов, прошедших все пороги')):
        out.append('<section><h2>'+escape(title)+'</h2><table><tr><th>Тики</th><th>Сценарий</th><th>Средняя Δ (adaptive − frozen)</th><th>95% нижняя</th><th>95% верхняя</th></tr>')
        for h in plan['horizons']:
            for cell in plan['cells']:
                s=next(r for r in result['statistics'] if (r['horizon'],r['cell'],r['metric'],r['group'])==(h,cell['id'],metric,'delta'))
                out.append('<tr>'+''.join(f'<td>{escape(str(v))}</td>' for v in (h,cell['id'],s['mean'],s['ci95_low'],s['ci95_high']))+'</tr>')
        out.append('</table></section>')
    out.append('<section><h2>Прямые контрасты (adaptive против adaptive)</h2><table><tr><th>Тики</th><th>Контраст</th><th>Метрика</th><th>Δ</th><th>95% нижняя</th><th>95% верхняя</th></tr>')
    focus={'two_sided_tick_percent','nonzero_trade_tick_percent','relative_vs_hold_vcu',
           'bid_placed_qty','ask_placed_qty','maker_fee_vcu','viable'}
    for r in result['policy_comparisons']:
        if r['metric'] in focus:
            out.append('<tr>'+''.join(f'<td>{escape(str(v))}</td>' for v in (
                r['horizon'],r['comparison'],r['metric'],r['mean'],r['ci95_low'],r['ci95_high']))+'</tr>')
    out.append('</table></section><section><h2>Учёт и ограничения</h2><p>'+
      escape(plan['methodology'])+'</p><p>Срок жизни завершённых лимитов измеряется от выставления до закрытия. '
      'Открытые лимиты в конце горизонта отмечены цензурированными, а не закрытыми. '
      'Подробная динамика по 20-тиковым окнам — в windows.csv. '
      'Сумма новых лимитов = исполненное + отменённое + оставшееся количество. '
      'Данные групп маркет-мейкеров описательные, без причинного PnL attribution.</p></section></body></html>')
    return ''.join(out)


def _finalize(plan:dict,result:dict,windows:list[dict],output:Path)->None:
    files={
      'summary.json':_canonical(result),
      'runs.csv':_csv_bytes(result['runs'],['seed','horizon','cell','arm',*METRICS]),
      'paired.csv':_csv_bytes(result['pairs'],['seed','horizon','cell',*(f'delta_{m}' for m in METRICS)]),
      'statistics.csv':_csv_bytes(result['statistics'],['horizon','cell','metric','group','n','mean','median','ci95_low','ci95_high','positive','negative','zero','sign_p_two_sided']),
      'policy_comparisons.csv':_csv_bytes(result['policy_comparisons'],['horizon','comparison','metric','n','mean','median','ci95_low','ci95_high','positive','negative','zero','sign_p_two_sided']),
      'windows.csv':_csv_bytes(windows,list(WINDOW_COLUMNS)),
      'report.html':render_report(plan,result).encode('utf8'),
    }
    for name,data in files.items():
        path=output/name
        if path.exists() and path.read_bytes()!=data:
            raise FileExistsError(f'FINAL_CONFLICT {name}')
    for name,data in files.items():
        if not (output/name).exists():_atomic_bytes(output/name,data)
    seal={'plan_sha256':plan['sha256'],'result_sha256':_digest(result),
          'file_sha256':{name:sha256(data).hexdigest() for name,data in files.items()}}
    _atomic_bytes(output/'complete.json',_canonical(seal))


def _verify_final(plan:dict,output:Path)->dict|None:
    path=output/'complete.json'
    if not path.is_file():return None
    seal=json.loads(path.read_bytes())
    if seal.get('plan_sha256')!=plan['sha256'] or set(seal.get('file_sha256',{}))!={
       'summary.json','runs.csv','paired.csv','statistics.csv','policy_comparisons.csv',
       'windows.csv','report.html'}:
        raise ValueError('FINAL_PLAN_OR_FILE_SET_MISMATCH')
    for name,digest in seal['file_sha256'].items():
        file=output/name
        if not file.is_file() or sha256(file.read_bytes()).hexdigest()!=digest:
            raise ValueError(f'FINAL_HASH_MISMATCH {name}')
    data=json.loads((output/'summary.json').read_bytes())
    if _digest(data)!=seal.get('result_sha256'):
        raise ValueError('FINAL_RESULT_HASH_MISMATCH')
    return data


def _prepare(plan:dict,output:Path,resume:bool)->None:
    verify_plan(plan)
    if resume and not output.is_dir():raise ValueError('RESUME_DIRECTORY_MISSING')
    if output.exists() and not resume:raise FileExistsError('OUTPUT_EXISTS_USE_RESUME')
    output.mkdir(parents=True,exist_ok=True)
    path=output/'plan.json'
    if path.exists():
        if path.read_bytes()!=_canonical(plan):raise ValueError('OUTPUT_PLAN_MISMATCH')
    elif resume and any(output.iterdir()):raise ValueError('OUTPUT_PLAN_MISSING')
    else:_atomic_bytes(path,_canonical(plan))


def execute(plan:dict,output:Path,*,resume:bool=False,max_new_arms:int|None=None,
            plan_path:Path|None=None)->dict|None:
    if max_new_arms is not None and (type(max_new_arms) is not int or max_new_arms<1):
        raise ValueError('INVALID_MAX_NEW_ARMS')
    _prepare(plan,output,resume)
    prior=_verify_final(plan,output)
    if prior is not None:
        print('RESEARCH_ALREADY_COMPLETE=YES VERIFIED_FILES=YES',flush=True)
        return prior
    records=[];new=0;items=list(_items(plan))
    for h,cell,seed,arm in items:
        path=output/'checkpoints'/_key(seed,h,cell['id'],arm)
        if path.exists():
            record=_load_checkpoint(path,plan,seed,h,cell['id'],arm)
            verb='REUSED'
        else:
            record=run_arm(plan,cell,seed,h,arm)
            if plan_path is not None and plan_path.read_bytes()!=_canonical(plan):
                raise ValueError('PLAN_CHANGED_DURING_RUN')
            _atomic_bytes(path,_canonical(_seal({'plan_sha256':plan['sha256'],
                'seed':seed,'horizon':h,'cell':cell['id'],'arm':arm,'data':record})))
            verb='SAVED';new+=1
        records.append(record)
        print(f'ARM_{verb}={len(records)}/{len(items)} SEED={seed} TICKS={h} CELL={cell["id"]} ARM={arm}',flush=True)
        if max_new_arms is not None and new>=max_new_arms and len(records)<len(items):
            print(f'RESEARCH_PAUSED=YES COMPLETED={len(records)}/{len(items)} RESUME=--resume',flush=True)
            return None
    result,windows=_aggregate(plan,records)
    if plan_path is not None and plan_path.read_bytes()!=_canonical(plan):
        raise ValueError('PLAN_CHANGED_DURING_RUN')
    _finalize(plan,result,windows,output)
    print(f'RESEARCH_PASS=YES INTEGRITY_PASS=YES ARMS={len(result["runs"])} PAIRS={len(result["pairs"])} WINDOWS={len(windows)}',flush=True)
    for name in ('summary.json','runs.csv','paired.csv','statistics.csv','policy_comparisons.csv','windows.csv','report.html','complete.json'):
        print(f'FILE={output/name}',flush=True)
    return result


def progress_status(plan:dict,output:Path)->dict:
    verify_plan(plan)
    if not (output/'plan.json').is_file() or (output/'plan.json').read_bytes()!=_canonical(plan):
        raise ValueError('OUTPUT_PLAN_MISMATCH')
    verified=0;missing=[]
    for h,cell,seed,arm in _items(plan):
        path=output/'checkpoints'/_key(seed,h,cell['id'],arm)
        if path.exists():_load_checkpoint(path,plan,seed,h,cell['id'],arm);verified+=1
        else:missing.append((seed,h,cell['id'],arm))
    finalized=_verify_final(plan,output) is not None
    print(f'PROGRESS_VERIFIED={verified}/{verified+len(missing)} NEXT={missing[0] if missing else "complete"} FINALIZED={finalized}',flush=True)
    return {'verified':verified,'total':verified+len(missing),'finalized':finalized}


def main(argv:list[str]|None=None)->None:
    parser=argparse.ArgumentParser(description='Market Lab v1.1: CLOSED SYNTHETIC LIQUIDITY')
    sub=parser.add_subparsers(dest='action',required=True)
    p=sub.add_parser('plan');p.add_argument('--seeds',default='401:410')
    p.add_argument('--horizons',default='320,1280');p.add_argument('--profile',choices=('core','full'),default='full')
    p.add_argument('--output',required=True)
    r=sub.add_parser('run');r.add_argument('--plan',required=True)
    r.add_argument('--output',required=True);r.add_argument('--resume',action='store_true')
    r.add_argument('--max-new-arms',type=int,default=None)
    t=sub.add_parser('status');t.add_argument('--plan',required=True);t.add_argument('--output',required=True)
    args=parser.parse_args(argv)
    try:
        if args.action=='plan':
            file=Path(args.output)
            if file.exists():raise FileExistsError('PLAN_EXISTS')
            plan=make_plan(parse_seed_range(args.seeds),parse_horizons(args.horizons),args.profile)
            _atomic_bytes(file,_canonical(plan))
            print(f'PLAN_SHA256={plan["sha256"]} SEEDS={len(plan["seeds"])} HORIZONS={plan["horizons"]} CELLS={len(plan["cells"])}',flush=True)
        else:
            file=Path(args.plan);raw=file.read_bytes();plan=json.loads(raw)
            verify_plan(plan)
            if raw!=_canonical(plan):raise ValueError('NONCANONICAL_PLAN_FILE')
            print(f'PLAN_VERIFIED={plan["sha256"]} SEEDS={len(plan["seeds"])} HORIZONS={plan["horizons"]} CELLS={len(plan["cells"])}',flush=True)
            if args.action=='status':progress_status(plan,Path(args.output))
            else:execute(plan,Path(args.output),resume=args.resume,max_new_arms=args.max_new_arms,plan_path=file)
    except (ValueError,FileExistsError,KeyError,TypeError,OSError) as exc:
        parser.error(str(exc))

if __name__=='__main__':main()
