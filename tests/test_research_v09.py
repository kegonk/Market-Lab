"""Regression, asset safety, fixed synthetic regimes and resumable v0.9 research."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from dataclasses import replace

from market_lab.interactive import SessionConfig,SyntheticMarket
from market_lab.research_v08 import _canonical,_digest
from market_lab.population import PopulationEvolution
from market_lab.research_v09 import (
    make_plan,verify_plan,run_arm,execute,progress_status,_config,ALL_CELLS,
    _load_checkpoint,HEALTH_EVERY)


class TestV09ConfigAndReentry(unittest.TestCase):
    def test_new_controls_opt_in_and_strict(self):
        baseline=SessionConfig()
        self.assertFalse(baseline.allow_retired_reentry)
        self.assertEqual(baseline.synthetic_regime,'LEGACY')
        for kw in ({'allow_retired_reentry':1},{'reentry_cooldown_ticks':0},
                   {'reentry_cooldown_ticks':40.5},{'synthetic_regime':'ETHUSDT'},
                   {'synthetic_regime':None}):
            with self.subTest(kw=kw),self.assertRaises(ValueError):
                SessionConfig(**kw)

    def test_policy_retire_and_reentry_preserves_assets_and_fifo(self):
        m=SyntheticMarket(SessionConfig(seed=201,ticks=80,shock_tick=40,
                       evolution_enabled=True,waiting_agents=0,
                       allow_retired_reentry=True,reentry_cooldown_ticks=20))
        p=m.population
        agent=next(iter(p.active))
        before=m.economy.wallets[agent]
        old=(before.cash_ticks,before.coin_qty,list(before.lots),before.fees_paid_ticks,
             before.denial_streak,m.economy.initial_cash_ticks,m.economy.initial_coins)
        p._exit(agent,1,m.book.last_trade_ticks,m.book,'Sustained underperformance vs passive holdings')
        self.assertEqual(before.status,'STOPPED')
        p._readmit_retired(20,m.book.last_trade_ticks)
        self.assertNotIn(agent,p.active)
        p._readmit_retired(21,m.book.last_trade_ticks)
        self.assertIn(agent,p.active)
        self.assertEqual(before.status,'ACTIVE')
        self.assertEqual(p.reentries,1)
        after=(before.cash_ticks,before.coin_qty,list(before.lots),before.fees_paid_ticks,
               before.denial_streak,m.economy.initial_cash_ticks,m.economy.initial_coins)
        self.assertEqual(old,after)
        self.assertTrue(m.economy.check(m.book))
        self.assertTrue(p.check())
        self.assertEqual(len(p.initial_wallets),1000)

    def test_risk_stops_never_reenter(self):
        m=SyntheticMarket(SessionConfig(seed=201,ticks=80,shock_tick=40,
                           evolution_enabled=True,waiting_agents=0,
                           allow_retired_reentry=True,reentry_cooldown_ticks=20))
        agent=next(iter(m.population.active))
        m.economy.wallets[agent].status='STOPPED'
        m.population.review(1,m.book.last_trade_ticks,m.book)
        self.assertIn(agent,m.population.retired)
        m.population._readmit_retired(80,m.book.last_trade_ticks)
        self.assertNotIn(agent,m.population.active)
        self.assertEqual(m.population.reentries,0)
        self.assertTrue(m.population.check())

    def test_policy_reentry_rejects_risk_counter(self):
        m=SyntheticMarket(SessionConfig(seed=201,ticks=80,shock_tick=40,
                           evolution_enabled=True,waiting_agents=0,
                           allow_retired_reentry=True,reentry_cooldown_ticks=20))
        agent=next(iter(m.population.active))
        m.population._exit(agent,1,m.book.last_trade_ticks,m.book,
                           'Sustained underperformance vs passive holdings')
        m.economy.wallets[agent].denial_streak=m.economy.stop_denials
        m.population._readmit_retired(80,m.book.last_trade_ticks)
        self.assertNotIn(agent,m.population.active)
        self.assertEqual(m.population.reentries,0)

    def test_legacy_market_deterministic_and_no_reentry(self):
        config=SessionConfig(seed=222,ticks=80,shock_tick=40,evolution_enabled=True)
        left=SyntheticMarket(config);right=SyntheticMarket(replace(config,
                         allow_retired_reentry=False,synthetic_regime='LEGACY'))
        for _ in range(config.ticks):
            left.step(capture_state=False);right.step(capture_state=False)
        self.assertEqual(left.price_history,right.price_history)
        self.assertEqual(left.population.relative_metrics(left.book.last_trade_ticks),
                         right.population.relative_metrics(right.book.last_trade_ticks))

    def test_new_regimes_conserve_and_repeat(self):
        for name in ('BULL','BEAR','SIDEWAYS','THIN'):
            with self.subTest(regime=name):
                config=SessionConfig(seed=205,ticks=80,shock_tick=40,
                      evolution_enabled=True,synthetic_regime=name)
                a=SyntheticMarket(config);b=SyntheticMarket(config)
                for _ in range(80):
                    a.step(capture_state=False);b.step(capture_state=False)
                self.assertEqual(a.price_history,b.price_history)
                self.assertTrue(a.economy.check(a.book))
                self.assertTrue(a.book.check_conservation())
                self.assertTrue(a.population.check())

    def test_long_reentry_and_conservation(self):
        plan=make_plan((201,202),(1280,),profile='core')
        a=run_arm(plan,plan['cells'][1],201,1280,'adaptive')
        self.assertGreater(a['metrics']['reentries'],0)
        self.assertEqual(a['all_wallets'],1000)
        self.assertTrue(a['conservation'])
        self.assertEqual(a['metrics']['policy_exits']+a['metrics']['risk_exits'],
                          a['metrics']['reentries']+a['metrics']['currently_retired'])


class TestV09Research(unittest.TestCase):
    def setUp(self):
        self.plan=make_plan((201,202),(80,),profile='core')

    def test_predeclare_and_reject_plan_tamper(self):
        verify_plan(self.plan)
        changed=json.loads(json.dumps(self.plan))
        changed['thresholds']['min_depth_per_side']=0
        with self.assertRaisesRegex(ValueError,'PLAN_SHA256_MISMATCH'):
            verify_plan(changed)
        changed['sha256']=_digest({k:v for k,v in changed.items() if k!='sha256'})
        with self.assertRaisesRegex(ValueError,'PLAN_NOT_CANONICAL'):
            verify_plan(changed)

    def test_old_seeds_disallowed_and_profiles(self):
        for seeds in ((101,102),(201,201),(1,203),(201,),('201','202')):
            with self.subTest(seeds=seeds),self.assertRaises(ValueError):
                make_plan(seeds,(80,))
        self.assertEqual(len(make_plan((201,202),(80,),profile='full')['cells']),7)
        self.assertEqual(len(make_plan((201,202),(80,),profile='core')['cells']),3)

    def test_periodic_health_conserves_trades_and_fees(self):
        a=run_arm(self.plan,self.plan['cells'][0],201,80,'adaptive')
        self.assertEqual(len(a['health']),80//HEALTH_EVERY)
        self.assertEqual(sum(x['trades'] for x in a['health']),a['metrics']['trades'])
        self.assertEqual(sum(x['volume'] for x in a['health']),a['metrics']['volume'])
        self.assertAlmostEqual(sum(x['fees_vcu'] for x in a['health']),a['metrics']['fees_vcu'])
        self.assertEqual(a['metrics']['active_survival_percent'],100.0)
        self.assertEqual(a['metrics']['reentries'],0)

    def test_pause_resume_verified_and_final_sealed(self):
        with TemporaryDirectory() as temp:
            out=Path(temp)/'partial';full=Path(temp)/'full'
            self.assertIsNone(execute(self.plan,out,max_new_arms=2))
            state=progress_status(self.plan,out)
            self.assertEqual(state['verified'],2)
            resumed=execute(self.plan,out,resume=True)
            uninterrupted=execute(self.plan,full)
            self.assertEqual(resumed,uninterrupted)
            self.assertEqual(len(resumed['runs']),12)
            self.assertEqual(len(resumed['pairs']),6)
            self.assertEqual(len(resumed['health']),12*4)
            self.assertEqual(len(resumed['shadow']),6*4)
            self.assertTrue(progress_status(self.plan,out)['finalized'])
            self.assertEqual(execute(self.plan,out,resume=True),resumed)
            for n in ['summary.json','runs.csv','health.csv','paired.csv','report.html','complete.json']:
                self.assertEqual((out/n).read_bytes(),(full/n).read_bytes())
            self.assertIn('SIMULATED ONLY',(out/'report.html').read_text(encoding='utf-8'))
            (out/'health.csv').write_text('tampered',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'FINAL_HASH_MISMATCH'):
                execute(self.plan,out,resume=True)

    def test_checkpoint_tamper_detected(self):
        with TemporaryDirectory() as temp:
            out=Path(temp)/'r'
            execute(self.plan,out,max_new_arms=1)
            fp=next((out/'checkpoints').iterdir())
            data=json.loads(fp.read_text(encoding='utf-8'))
            data['data']['metrics']['trades']+=999
            fp.write_text(json.dumps(data),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'CHECKPOINT_SHA256_MISMATCH'):
                progress_status(self.plan,out)

    def test_fixed_policy_mode_disables_policy_exits_but_not_risk(self):
        a=run_arm(self.plan,self.plan['cells'][2],201,80,'adaptive')
        self.assertEqual(a['metrics']['policy_exits'],0)
        self.assertEqual(a['metrics']['reentries'],0)


if __name__=='__main__':
    unittest.main()
