"""v0.7: ablation, pre-registration, seed-level uncertainty, cross-sectional distributions."""
from __future__ import annotations

import copy
import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from market_lab.economy import SyntheticEconomy
from market_lab.interactive import SessionConfig, SyntheticMarket
from market_lab.population import PopulationEvolution
from market_lab.research_v07 import (METRICS, _sign_p, execute_plan, interval,
    make_plan, parse_seed_range, render_report, save_results, verify_plan)
from market_lab.simulation import Config, OrderBook


class TestIndependentAblations(unittest.TestCase):
    def test_flags_must_be_boolean(self):
        for key in ('allow_strategy_switch','allow_population_exit','allow_population_join'):
            with self.subTest(key=key), self.assertRaises(ValueError):
                SessionConfig(**{key:1})
        econ=SyntheticEconomy(OrderBook(Config(initial_levels=4,units_per_level=2)))
        with self.assertRaises(ValueError):
            PopulationEvolution(econ,allow_switch=0)

    def test_switch_blocked_without_blocking_retirement(self):
        book=OrderBook(Config(initial_levels=5, units_per_level=2))
        econ=SyntheticEconomy(book,maker_fee_bps=10,taker_fee_bps=20)
        pop=PopulationEvolution(econ,reserve_agents=2,review_every=5,
                                weak_reviews_to_exit=2,allow_switch=False,
                                allow_exit=True,allow_join=False)
        original=pop.rule_for('A0001')
        for tick in (5,10):
            w=econ.wallets['A0001']
            e=book.execute_market('BUY',1,cash_budget_ticks=w.free_cash,
                                   buyer_fee_bps=econ.taker_fee_bps,exclude_agent='A0001')
            econ.settle('A0001','BUY',e.matched_orders)
            pop.review(tick,book.last_trade_ticks,book)
        self.assertEqual(pop.switches,0)
        self.assertEqual(pop.exits,1)
        self.assertEqual(pop.joins,0)
        self.assertEqual(pop.retired['A0001']['strategy'],original)
        self.assertTrue(pop.check())
        self.assertTrue(econ.check(book))

    def test_exit_disabled_allows_only_switches(self):
        book=OrderBook(Config(initial_levels=5,units_per_level=2))
        econ=SyntheticEconomy(book)
        pop=PopulationEvolution(econ,reserve_agents=2,review_every=5,
                                weak_reviews_to_exit=2,allow_exit=False,
                                allow_join=True,allow_switch=True)
        for tick in (5,10,15):
            w=econ.wallets['A0001']
            e=book.execute_market('BUY',1,cash_budget_ticks=w.free_cash,
                                   buyer_fee_bps=econ.taker_fee_bps,exclude_agent='A0001')
            econ.settle('A0001','BUY',e.matched_orders)
            pop.review(tick,book.last_trade_ticks,book)
        self.assertIn('A0001',pop.active)
        self.assertEqual(pop.exits,0)
        self.assertEqual(pop.joins,0)
        self.assertTrue(pop.check())
        self.assertTrue(econ.check(book))

    def test_join_disabled_preserves_waitlist(self):
        book=OrderBook(Config(initial_levels=5,units_per_level=2))
        econ=SyntheticEconomy(book)
        pop=PopulationEvolution(econ,reserve_agents=2,review_every=5,
                                allow_join=False)
        pop._exit('A0001',1,1000,book,'unit')
        pop.review(5,1000,book)
        self.assertEqual(pop.exits,1)
        self.assertEqual(pop.joins,0)
        self.assertEqual(len(pop.waiting),2)
        self.assertTrue(pop.check())
        self.assertTrue(econ.check(book))

    def test_distribution_all_1000_including_inactive(self):
        book=OrderBook(Config(initial_levels=5,units_per_level=2))
        econ=SyntheticEconomy(book)
        pop=PopulationEvolution(econ,reserve_agents=100)
        before=pop.relative_metrics(1000)
        self.assertEqual([before[k] for k in ('median_relative_vcu','p10_relative_vcu','p90_relative_vcu','ahead_percent')],[0,0,0,0])
        # Cash transfer between two wallets: all-money conservation still holds.
        econ.wallets['A0001'].cash_ticks += 100
        econ.wallets['A0999'].cash_ticks -= 100
        after=pop.relative_metrics(1000)
        self.assertEqual(after['all_agents'],1000)
        self.assertEqual(after['total_relative_vcu'],0.0)
        self.assertEqual(after['ahead'],1)
        self.assertEqual(after['behind'],1)
        self.assertEqual(after['ahead_percent'],0.1)
        self.assertEqual(after['median_relative_vcu'],0)
        self.assertEqual(after['p10_relative_vcu'],0)
        self.assertEqual(after['p90_relative_vcu'],0)
        self.assertTrue(econ.check(book))


class TestPreregisteredPlan(unittest.TestCase):
    def test_seeds_range_validation(self):
        self.assertEqual(parse_seed_range('1:20'),tuple(range(1,21)))
        self.assertEqual(parse_seed_range('1, 2,-3'),(1,2,-3))
        self.assertEqual(len(parse_seed_range('1:100')),100)
        for item in ('1:101','4:3','1,1','','a:b','1,,2','1:2:3'):
            with self.subTest(item=item), self.assertRaises(ValueError):
                parse_seed_range(item)

    def test_grid_complete_and_prespecified(self):
        p=make_plan((1,2),ticks=30,shock_tick=15)
        self.assertTrue(verify_plan(p))
        self.assertEqual(len(p['cells']),13)
        self.assertEqual({x['id'] for x in p['cells'] if x['family']=='ablation'},
                         {f'ablation_{a}{b}{c}' for a in (0,1) for b in (0,1) for c in (0,1)})
        self.assertEqual({c['min_hold'] for c in p['cells'] if c['family']=='holding_period'}, {80,160})
        self.assertEqual({(c['maker_fee_bps'],c['taker_fee_bps']) for c in p['cells'] if c['family']=='fees'},{(0,0),(20,40)})
        self.assertEqual(sum(c['family']=='shock' for c in p['cells']),1)
        self.assertEqual(p['primary_endpoint'],'relative_vs_hold_vcu')
        with self.assertRaises(ValueError):
            make_plan((1,1))
        with self.assertRaises(ValueError):
            make_plan((1,),maker_fee_bps=1000)

    def test_modified_plan_rejected_even_with_forged_hash(self):
        p=make_plan((4,),ticks=25)
        q=copy.deepcopy(p)
        q['cells'][0]['join']=True
        with self.assertRaisesRegex(ValueError,'PLAN_HASH_MISMATCH'):
            verify_plan(q)
        from market_lab.research_v07 import _digest
        q['sha256']=_digest({k:v for k,v in q.items() if k!='sha256'})
        with self.assertRaisesRegex(ValueError,'noncanonical'):
            verify_plan(q)

    def test_ci_and_sign_small_samples(self):
        self.assertEqual(_sign_p([1]*20),0.000001907)
        self.assertEqual(_sign_p([1]*19+[-1]),0.000040054)
        self.assertIsNone(_sign_p([0,0]))
        one=interval([10])
        self.assertIsNone(one['ci95_low'])
        self.assertIsNone(one['ci95_high'])
        for values in ([0,0],[2,2],[1,2,3],[1]*32):
            x=interval(values)
            self.assertLessEqual(x['ci95_low'],x['mean'])
            self.assertGreaterEqual(x['ci95_high'],x['mean'])
        with self.assertRaises(ValueError):
            interval([float('nan')])

    def test_pairwise_runs_export_determinism_and_integrity(self):
        p=make_plan((1,2),ticks=25,shock_tick=15)
        a=execute_plan(p)
        b=execute_plan(p)
        self.assertEqual(a,b)
        self.assertTrue(all(a['integrity'].values()))
        self.assertEqual(len(a['runs']),2*2*13)
        self.assertEqual(len(a['paired_differences']),2*13)
        self.assertEqual(len(a['statistics']),13*len(METRICS)*3)
        self.assertEqual({x['group'] for x in a['statistics']},{'frozen','adaptive','delta'})
        self.assertEqual({x['metric'] for x in a['statistics']},set(METRICS))
        self.assertTrue(all(x['delta_strategy_switches']==0 for x in a['paired_differences'] if x['cell']=='ablation_000'))
        html=render_report(p,a)
        self.assertIn('SHA-256',html)
        self.assertIn('P10',html)
        self.assertNotIn('https://',html)
        with TemporaryDirectory() as d:
            folder=Path(d)/'results'
            targets=save_results(p,a,folder)
            self.assertEqual(len(targets),6)
            self.assertEqual(json.loads((folder/'summary.json').read_text('utf8')),a)
            self.assertEqual(json.loads((folder/'plan.json').read_text('utf8')),p)
            for name,rows in [('runs.csv',52),('paired.csv',26),('statistics.csv',len(a['statistics']))]:
                with (folder/name).open(encoding='utf-8-sig',newline='') as fp:
                    self.assertEqual(len(list(csv.DictReader(fp))),rows)
            original=[x.read_bytes() for x in targets]
            with self.assertRaises(FileExistsError):
                save_results(p,a,folder)
            save_results(p,a,folder,overwrite=True)
            self.assertEqual(original,[x.read_bytes() for x in targets])

    def test_ui_has_ablation_controls_and_distribution(self):
        static=Path(__file__).resolve().parents[1]/'market_lab/static'
        html=(static/'index.html').read_text('utf-8')
        js=(static/'app.js').read_text('utf-8')
        for field in ('allowStrategySwitch','allowPopulationExit','allowPopulationJoin',
                      'populationMedian','populationP10','populationP90','populationAheadPercent'):
            self.assertIn(f'id="{field}"',html)
            self.assertIn(field,js)
        self.assertNotIn('https://',html+js)
        self.assertIn('SIMULATED',html)


if __name__=='__main__':
    unittest.main()
