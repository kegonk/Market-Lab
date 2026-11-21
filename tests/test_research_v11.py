"""Market Lab v1.1 closed-synthetic, deterministic, controller + research tests."""
import json
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from market_lab.interactive import SessionConfig,SyntheticMarket
from market_lab.research_v08 import _digest
from market_lab.research_v11 import (
    ALL_CELLS,COMPARISONS,METRICS,WINDOW,make_plan,verify_plan,
    _config,run_arm,execute,progress_status)


class TestV11Controller(unittest.TestCase):
    def test_defaults_and_opt_out_preserve_v10_behavior(self):
        config=SessionConfig(seed=401,ticks=80,shock_tick=40,evolution_enabled=True)
        a=SyntheticMarket(config)
        b=SyntheticMarket(replace(config,liquidity_v11_mode='OFF'))
        self.assertIsNone(a.liquidity_v11)
        for _ in range(80):
            a.step(capture_state=False);b.step(capture_state=False)
        self.assertEqual(a.fingerprint(),b.fingerprint())

    def test_strict_schema_and_mutual_exclusion(self):
        invalid=({'liquidity_v11_mode':'foo'},
                 {'liquidity_v11_bid_target':0},
                 {'liquidity_v11_ask_target':False},
                 {'liquidity_v11_max_order_qty':21},
                 {'liquidity_v11_max_orders_per_side_tick':0},
                 {'liquidity_v11_wallet_cap_percent':101},
                 {'liquidity_v11_max_orders_per_agent':0},
                 {'liquidity_v11_mode':'SYMMETRIC','evolution_enabled':False},
                 {'liquidity_v11_mode':'SIDE_AWARE','evolution_enabled':True,
                  'quote_refresh_mode':'MAKER_ONLY'})
        for kw in invalid:
            with self.subTest(kw=kw),self.assertRaises(ValueError):
                SessionConfig(**kw)

    def test_symmetric_empty_book_both_sides_funded_no_mint(self):
        m=SyntheticMarket(SessionConfig(seed=401,ticks=80,shock_tick=40,
            evolution_enabled=True,liquidity_v11_mode='SYMMETRIC'))
        initial_cash=m.economy.initial_cash_ticks;initial_coins=m.economy.initial_coins
        for side in ('BUY','SELL'):
            m.economy.cancel_book_top(m.book,side,40,1.)
        m.tick=1;m.liquidity_v11.step()
        summary=m.liquidity_v11.snapshot()
        self.assertEqual(summary['bid_placed_qty'],5)
        self.assertEqual(summary['ask_placed_qty'],5)
        self.assertEqual(summary['bid_remaining_qty'],5)
        self.assertEqual(summary['ask_remaining_qty'],5)
        self.assertEqual(sum(w.cash_ticks for w in m.economy.wallets.values()),initial_cash)
        self.assertEqual(sum(w.coin_qty for w in m.economy.wallets.values()),initial_coins)
        self.assertTrue(m.economy.check(m.book))
        self.assertTrue(m.book.check_conservation())
        self.assertTrue(all(o.agent in m.population.active for oid,o in m.book.orders.items()
            if oid.startswith('v11-')))

    def test_side_aware_targets_use_same_pre_controller_book(self):
        m=SyntheticMarket(SessionConfig(seed=402,ticks=80,shock_tick=40,
            evolution_enabled=True,liquidity_v11_mode='SIDE_AWARE'))
        for side in ('BUY','SELL'):
            m.economy.cancel_book_top(m.book,side,40,1.)
        m.tick=1;m.liquidity_v11.step()
        self.assertEqual(m.liquidity_v11.snapshot()['ask_placed_qty'],5)
        self.assertEqual(m.liquidity_v11.snapshot()['bid_placed_qty'],5)
        n=SyntheticMarket(SessionConfig(seed=402,ticks=80,shock_tick=40,
            evolution_enabled=True,liquidity_v11_mode='SIDE_AWARE'))
        n.economy.cancel_book_top(n.book,'BUY',40,1.)
        n.tick=1;n.liquidity_v11.step()
        self.assertEqual(n.liquidity_v11.snapshot()['bid_placed_qty'],6)
        self.assertEqual(n.liquidity_v11.snapshot()['ask_placed_qty'],0)

    def test_fill_cancel_and_censoring_ledger(self):
        m=SyntheticMarket(SessionConfig(seed=401,ticks=80,shock_tick=40,
            evolution_enabled=True,liquidity_v11_mode='SYMMETRIC'))
        for side in ('BUY','SELL'):
            m.economy.cancel_book_top(m.book,side,40,1.)
        m.tick=1;m.liquidity_v11.step()
        m.tick=2;m._market('SELL',3,'STRESS','STRESS')
        m.liquidity_v11.step()
        self.assertEqual(m.liquidity_v11.snapshot()['bid_filled_qty'],3)
        self.assertEqual(m.liquidity_v11.snapshot()['bid_mean_closed_lifetime_ticks'],1.)
        for side in ('BUY','SELL'):
            m.economy.cancel_book_top(m.book,side,40,1.)
        m.tick=3;m.liquidity_v11.step()
        s=m.liquidity_v11.snapshot()
        self.assertGreater(s['bid_cancelled_qty'],0)
        self.assertGreater(s['ask_cancelled_qty'],0)
        self.assertGreater(s['currently_open_orders'],0)
        self.assertEqual(s['bid_placed_qty'],s['bid_filled_qty']+s['bid_cancelled_qty']+s['bid_remaining_qty'])
        self.assertEqual(s['ask_placed_qty'],s['ask_filled_qty']+s['ask_cancelled_qty']+s['ask_remaining_qty'])
        self.assertTrue(m.economy.check(m.book))

    def test_no_funded_active_makers_is_fail_closed(self):
        m=SyntheticMarket(SessionConfig(seed=401,ticks=80,shock_tick=40,
            evolution_enabled=True,liquidity_v11_mode='SIDE_AWARE'))
        for side in ('BUY','SELL'):
            m.economy.cancel_book_top(m.book,side,40,1.)
        for aid,member in m.population.active.items():
            if member.strategy=='MAKER':
                m.economy.wallets[aid].status='STOPPED'
        m.tick=1;m.liquidity_v11.step()
        s=m.liquidity_v11.snapshot()
        self.assertEqual(s['bid_placed']+s['ask_placed'],0)
        self.assertGreater(s['bid_no_makers']+s['ask_no_makers'],0)
        self.assertTrue(m.economy.check(m.book))

    def test_risk_cap_counts_existing_reservations(self):
        m=SyntheticMarket(SessionConfig(seed=401,ticks=80,shock_tick=40,
            evolution_enabled=True,liquidity_v11_mode='SIDE_AWARE',
            liquidity_v11_wallet_cap_percent=1))
        for side in ('BUY','SELL'):
            m.economy.cancel_book_top(m.book,side,40,1.)
        m.tick=1;m.liquidity_v11.step()
        for oid,o in m.book.orders.items():
            if oid.startswith('v11-'):
                w=m.economy.wallets[o.agent]
                self.assertLessEqual(w.reserved_cash_ticks+w.reserved_coin_qty*m.book.last_trade_ticks,
                    (w.cash_ticks+w.coin_qty*m.book.last_trade_ticks)//100)
        self.assertTrue(m.economy.check(m.book))

    def test_deterministic_stress_regime_and_conservation(self):
        p=make_plan((401,402),(80,),profile='full')
        cell=next(c for c in p['cells'] if c['id']=='thin_sideaware')
        a=run_arm(p,cell,401,80,'adaptive')
        b=run_arm(p,cell,401,80,'adaptive')
        self.assertEqual(a,b)
        self.assertTrue(a['conservation'])
        self.assertEqual(sum(x['trades'] for x in a['windows']),a['metrics']['trades'])
        self.assertEqual(set(a['metrics']),set(METRICS))


class TestV11Research(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan=make_plan((401,402),(40,),profile='core')

    def test_frozen_holdout_and_full_profiles(self):
        self.assertEqual(len(self.plan['cells']),6)
        self.assertEqual(len(make_plan()['cells']),17)
        self.assertEqual(make_plan()['seeds'],list(range(401,411)))
        self.assertEqual(len(set(c['id'] for c in ALL_CELLS)),17)
        self.assertEqual(len(COMPARISONS),16)
        for bad in ((301,402),(401,401),(401,),('401','402')):
            with self.subTest(bad=bad),self.assertRaises(ValueError):
                make_plan(bad,(40,))

    def test_plan_tamper_even_with_forged_hash_rejected(self):
        verify_plan(self.plan)
        p=json.loads(json.dumps(self.plan));p['settings']['liquidity_v11_wallet_cap_percent']=95
        with self.assertRaisesRegex(ValueError,'PLAN_SHA256_MISMATCH'):
            verify_plan(p)
        p['sha256']=_digest({k:v for k,v in p.items() if k!='sha256'})
        with self.assertRaisesRegex(ValueError,'PLAN_NOT_CANONICAL'):
            verify_plan(p)

    def test_config_policy_isolated_and_new_controller_off_for_control(self):
        p=self.plan
        for cell in p['cells']:
            for arm in ('adaptive','frozen'):
                conf=_config(p,cell,401,40,arm)
                self.assertEqual(conf.liquidity_v11_mode,cell['mode'])
                self.assertEqual(conf.liquidity_v11_max_order_qty,cell['size'])
                self.assertEqual(conf.quote_refresh_mode,'OFF')
                self.assertEqual(conf.allow_retired_reentry,arm=='adaptive')

    def test_windows_compact_and_all_1000_wallets_included(self):
        c=self.plan['cells'][2]
        r=run_arm(self.plan,c,401,40,'adaptive')
        self.assertEqual(len(r['windows']),2)
        self.assertEqual(r['all_wallets'],1000)
        self.assertEqual(r['initial_agents'],900)
        self.assertNotIn('ticks',r)
        self.assertNotIn('price_tape_ticks',r)
        self.assertEqual(sum(x['span_ticks'] for x in r['windows']),40)

    def test_resume_reproduces_all_outputs_and_sealed_hashes(self):
        with TemporaryDirectory() as tmp:
            part=Path(tmp)/'part';full=Path(tmp)/'full'
            self.assertIsNone(execute(self.plan,part,max_new_arms=2))
            self.assertEqual(progress_status(self.plan,part)['verified'],2)
            res=execute(self.plan,part,resume=True)
            independent=execute(self.plan,full)
            self.assertEqual(res,independent)
            self.assertEqual(len(res['runs']),24)
            self.assertEqual(len(res['pairs']),12)
            self.assertEqual(res['window_rows'],48)
            self.assertTrue(progress_status(self.plan,part)['finalized'])
            self.assertEqual(execute(self.plan,part,resume=True),res)
            for name in ('summary.json','runs.csv','paired.csv','statistics.csv',
                         'policy_comparisons.csv','windows.csv','report.html','complete.json'):
                self.assertEqual((part/name).read_bytes(),(full/name).read_bytes(),name)
            self.assertLess((part/'summary.json').stat().st_size,2_000_000)
            self.assertIn('SIMULATED ONLY',(part/'report.html').read_text(encoding='utf-8'))
            (part/'windows.csv').write_text('corrupt',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'FINAL_HASH_MISMATCH'):
                execute(self.plan,part,resume=True)

    def test_corrupted_checkpoint_fails_closed(self):
        with TemporaryDirectory() as tmp:
            path=Path(tmp)/'out'
            execute(self.plan,path,max_new_arms=1)
            cp=next((path/'checkpoints').iterdir())
            item=json.loads(cp.read_text(encoding='utf-8'))
            item['data']['metrics']['trades']+=1
            cp.write_text(json.dumps(item),encoding='utf8')
            with self.assertRaisesRegex(ValueError,'CHECKPOINT_SHA256_MISMATCH'):
                progress_status(self.plan,path)


if __name__=='__main__':unittest.main()
