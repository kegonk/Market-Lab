"""v1.0: fully funded liquidity restoration and tick-resolved, resumable research."""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from market_lab.interactive import SessionConfig, SyntheticMarket
from market_lab.research_v08 import _digest
from market_lab.research_v10 import (
    ALL_CELLS, HEALTH_EVERY, METRICS, TICK_COLUMNS, _config,
    _load_checkpoint, _tick_diagnostic, execute, make_plan,
    progress_status, run_arm, verify_plan,
)


class TestV10LiquidityProvision(unittest.TestCase):
    def test_default_disabled_and_strict_validation(self):
        self.assertEqual(SessionConfig().quote_refresh_mode,'OFF')
        self.assertEqual(SessionConfig().quote_refresh_min_depth,5)
        for opts in ({'quote_refresh_mode':None},{'quote_refresh_mode':'USD'},
                     {'quote_refresh_mode':'maker_only'},
                     {'quote_refresh_min_depth':0},
                     {'quote_refresh_min_depth':51},
                     {'quote_refresh_min_depth':False},
                     {'quote_refresh_min_depth':1.2},
                     {'quote_refresh_mode':'MAKER_ONLY','evolution_enabled':False}):
            with self.subTest(opts=opts), self.assertRaises(ValueError):
                SessionConfig(**opts)

    def test_legacy_opt_out_is_exactly_unchanged(self):
        cfg=SessionConfig(seed=305,ticks=80,shock_tick=40,evolution_enabled=True)
        a=SyntheticMarket(cfg)
        b=SyntheticMarket(replace(cfg,quote_refresh_mode='OFF'))
        for _ in range(80):
            a.step(capture_state=False);b.step(capture_state=False)
        self.assertEqual(a.fingerprint(),b.fingerprint())
        self.assertEqual(a.refresh_posted,{'BUY':0,'SELL':0})

    def test_funded_maker_refresh_orders_and_reservations(self):
        m=SyntheticMarket(SessionConfig(seed=301,ticks=80,shock_tick=40,
                         evolution_enabled=True,quote_refresh_mode='MAKER_ONLY'))
        cash=m.economy.initial_cash_ticks;coins=m.economy.initial_coins
        for side in ('BUY','SELL'):
            m.economy.cancel_book_top(m.book,side,40,1.0)
        self.assertEqual(m.book.depth('BUY',8),0)
        self.assertEqual(m.book.depth('SELL',8),0)
        m.tick=1;m._refresh_quotes()
        self.assertEqual(sum(m.refresh_posted.values()),2)
        self.assertEqual(m.refresh_quantity['BUY'],3)
        self.assertEqual(m.refresh_quantity['SELL'],3)
        self.assertEqual(sum(w.cash_ticks for w in m.economy.wallets.values()),cash)
        self.assertEqual(sum(w.coin_qty for w in m.economy.wallets.values()),coins)
        self.assertTrue(m.economy.check(m.book))
        self.assertTrue(m.book.check_conservation())
        self.assertTrue(m.population.check())
        self.assertTrue(all(o.agent in m.population.active for oid, o in m.book.orders.items()
                            if oid.startswith('q-')))

    def test_no_maker_opt_in_does_not_create_unbacked_quotes(self):
        m=SyntheticMarket(SessionConfig(seed=301,ticks=80,shock_tick=40,
                         evolution_enabled=True,quote_refresh_mode='MAKER_ONLY'))
        for member in m.population.active.values():
            if member.strategy=='MAKER':
                member.strategy='NOISE'
        for side in ('BUY','SELL'):
            m.economy.cancel_book_top(m.book,side,40,1.0)
        m.tick=1;m._refresh_quotes()
        self.assertEqual(sum(m.refresh_posted.values()),0)
        self.assertEqual(m.refresh_no_eligible,{'BUY':1,'SELL':1})
        self.assertTrue(m.economy.check(m.book))

    def test_all_funded_can_quote_without_maker_members(self):
        m=SyntheticMarket(SessionConfig(seed=301,ticks=80,shock_tick=40,
                         evolution_enabled=True,quote_refresh_mode='ALL_FUNDED'))
        for member in m.population.active.values():
            if member.strategy=='MAKER':member.strategy='NOISE'
        for side in ('BUY','SELL'):
            m.economy.cancel_book_top(m.book,side,40,1.0)
        m.tick=1;m._refresh_quotes()
        self.assertEqual(sum(m.refresh_posted.values()),2)
        self.assertTrue(m.economy.check(m.book))

    def test_refresher_does_not_bypass_risk_status(self):
        m=SyntheticMarket(SessionConfig(seed=301,ticks=80,shock_tick=40,
                         evolution_enabled=True,quote_refresh_mode='MAKER_ONLY'))
        for member in m.population.active.values():
            if member.strategy=='MAKER':
                m.economy.wallets[member.agent_id].status='STOPPED'
        for side in ('BUY','SELL'):
            m.economy.cancel_book_top(m.book,side,40,1.0)
        m.tick=1;m._refresh_quotes()
        self.assertEqual(sum(m.refresh_posted.values()),0)
        self.assertEqual(m.refresh_no_eligible,{'BUY':1,'SELL':1})
        # Simulated risk flags are deliberately not reset by a quote attempt.
        self.assertTrue(all(m.economy.wallets[a].status=='STOPPED' for a,x in
                            m.population.active.items() if x.strategy=='MAKER'))

    def test_tick_diagnostic_noncausal_cooccurring_flags(self):
        m=SyntheticMarket(SessionConfig(seed=301,ticks=80,shock_tick=40,
                         evolution_enabled=True,quote_refresh_mode='OFF'))
        for side in ('BUY','SELL'):
            m.economy.cancel_book_top(m.book,side,40,1.0)
        old={'trades':0,'volume':0,'fees_vcu':0,'quote_withheld':0,
             'regime_cancelled_qty':0,'shock_cancelled_qty':0,
             'refresh_attempts':0,'refresh_posted':0,'refresh_quantity':0,
             'refresh_no_eligible':0}
        row,_=_tick_diagnostic(m,old,5)
        self.assertEqual((row['bid_present'],row['ask_present']), (0,0))
        self.assertEqual(row['bid_below_depth'],1)
        self.assertEqual(row['ask_below_depth'],1)
        self.assertEqual(row['two_sided_depth'],0)


class TestV10Research(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan=make_plan((301,302),(40,),profile='core')

    def test_profiles_and_distinct_seed_range(self):
        self.assertEqual(len(self.plan['cells']),6)
        self.assertEqual(len(make_plan((301,302),(40,),'full')['cells']),10)
        self.assertEqual(make_plan()['seeds'],list(range(301,311)))
        for seeds in ((201,302),(301,301),(301,),('301','302')):
            with self.subTest(seeds=seeds), self.assertRaises(ValueError):
                make_plan(seeds,(40,))

    def test_signed_plan_canonical_no_post_hoc_thresholds(self):
        verify_plan(self.plan)
        altered=json.loads(json.dumps(self.plan))
        altered['thresholds']['min_depth_per_side']=1
        with self.assertRaisesRegex(ValueError,'PLAN_SHA256_MISMATCH'):
            verify_plan(altered)
        altered['sha256']=_digest({k:v for k,v in altered.items() if k!='sha256'})
        with self.assertRaisesRegex(ValueError,'PLAN_NOT_CANONICAL'):
            verify_plan(altered)

    def test_isolated_population_policies(self):
        cells={c['id']:c for c in ALL_CELLS}
        p=self.plan
        for cell in ('legacy_baseline','legacy_reentry','legacy_maker_refresh',
                     'legacy_all_refresh','legacy_no_inflow','legacy_no_exit'):
            conf=_config(p,cells[cell],301,40,'adaptive')
            self.assertEqual(conf.allow_population_join,cells[cell]['join'])
            self.assertEqual(conf.allow_population_exit,cells[cell]['exit'])
            self.assertEqual(conf.quote_refresh_mode,cells[cell]['refresh'])
            self.assertEqual(conf.allow_retired_reentry,cells[cell]['reentry'])
        self.assertFalse(_config(p,cells['legacy_reentry'],301,40,'frozen').allow_retired_reentry)

    def test_every_tick_and_windows_reconcile(self):
        out=run_arm(self.plan,self.plan['cells'][2],301,40,'adaptive')
        self.assertEqual(len(out['ticks']),40)
        self.assertEqual(len(out['health']),40//HEALTH_EVERY)
        self.assertEqual(sum(x['trades'] for x in out['ticks']),out['metrics']['trades'])
        self.assertEqual(sum(x['volume'] for x in out['ticks']),out['metrics']['volume'])
        self.assertAlmostEqual(sum(x['fees_vcu'] for x in out['ticks']),out['metrics']['fees_vcu'])
        self.assertTrue(out['conservation'])
        self.assertEqual(out['all_wallets'],1000)
        self.assertEqual(set(out['metrics']),set(METRICS))

    def test_thin_refresh_deterministic_and_safe(self):
        # Keep the integration check short enough for CI; stress-specific behaviour
        # is verified with the actual THIN cell and a high-liquidity short horizon.
        cell=next(x for x in ALL_CELLS if x['id']=='thin_maker_refresh')
        a=run_arm(self.plan,cell,301,80,'adaptive')
        b=run_arm(self.plan,cell,301,80,'adaptive')
        self.assertEqual(a,b)
        self.assertGreaterEqual(a['metrics']['quote_refresh_posted'],0)
        self.assertTrue(a['conservation'])

    def test_resume_reuses_clean_checkpoint_and_final_hashes(self):
        with TemporaryDirectory() as tmp:
            part=Path(tmp)/'partial';full=Path(tmp)/'full'
            self.assertIsNone(execute(self.plan,part,max_new_arms=3))
            self.assertEqual(progress_status(self.plan,part)['verified'],3)
            resumed=execute(self.plan,part,resume=True)
            continuous=execute(self.plan,full)
            self.assertEqual(resumed,continuous)
            self.assertEqual(len(resumed['runs']),24)
            self.assertEqual(len(resumed['pairs']),12)
            self.assertEqual(len(resumed['tick_health']),24*40)
            self.assertEqual(len(resumed['health']),24*2)
            self.assertTrue(progress_status(self.plan,part)['finalized'])
            self.assertEqual(execute(self.plan,part,resume=True),resumed)
            for name in ('summary.json','runs.csv','paired.csv','statistics.csv',
                         'tick_health.csv','health.csv','report.html','complete.json'):
                self.assertEqual((part/name).read_bytes(),(full/name).read_bytes(),name)
            self.assertIn('SIMULATED ONLY',(part/'report.html').read_text(encoding='utf-8'))
            (part/'tick_health.csv').write_text('tampered',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'FINAL_HASH_MISMATCH'):
                execute(self.plan,part,resume=True)

    def test_tampered_checkpoint_rejected(self):
        with TemporaryDirectory() as tmp:
            out=Path(tmp)/'result'
            execute(self.plan,out,max_new_arms=1)
            path=next((out/'checkpoints').iterdir())
            obj=json.loads(path.read_text(encoding='utf-8'))
            obj['data']['metrics']['trades']+=100
            path.write_text(json.dumps(obj),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'CHECKPOINT_SHA256_MISMATCH'):
                progress_status(self.plan,out)


if __name__=='__main__':unittest.main()
