"""v0.6: paired synthetic controls, benchmarks, minimum strategy hold, exports."""
from __future__ import annotations

import csv
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from market_lab.economy import SyntheticEconomy
from market_lab.interactive import SessionConfig, SyntheticMarket
from market_lab.population import PopulationEvolution, STRATEGIES
from market_lab.research import (FIELDS, parse_seeds, paired_experiment,
                                 render_report, save_experiment)
from market_lab.simulation import Config, OrderBook


class TestResearchControls(unittest.TestCase):
    def test_new_config_validates_bool_and_integer(self):
        for kwargs in ({'adaptation_enabled': 1}, {'min_strategy_hold_ticks': True},
                       {'min_strategy_hold_ticks': -1}, {'min_strategy_hold_ticks': 2001}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                SessionConfig(**kwargs)

    def test_minimum_hold_blocks_then_allows_strategy_change(self):
        book = OrderBook(Config(initial_levels=5, units_per_level=2))
        economy = SyntheticEconomy(book, maker_fee_bps=10, taker_fee_bps=20)
        p = PopulationEvolution(economy, reserve_agents=1, review_every=5,
                                weak_reviews_to_exit=5, min_strategy_hold_ticks=10)
        original = p.rule_for('A0001')
        for tick in (5, 10):
            w = economy.wallets['A0001']
            e = book.execute_market('BUY', 1, cash_budget_ticks=w.free_cash,
                                    buyer_fee_bps=economy.taker_fee_bps,
                                    exclude_agent='A0001')
            self.assertEqual(e.filled_qty, 1)
            economy.settle('A0001', 'BUY', e.matched_orders)
            p.review(tick, book.last_trade_ticks, book)
            self.assertTrue(economy.check(book))
            if tick == 5:
                self.assertEqual(p.rule_for('A0001'), original)
                self.assertGreaterEqual(p.blocked_switches, 1)
        self.assertNotEqual(p.rule_for('A0001'), original)
        self.assertEqual(p.switches, 1)
        self.assertGreater(p.snapshot(book.last_trade_ticks)['switches_per_1000_agent_ticks'], 0)
        self.assertEqual(p.active['A0001'].last_strategy_change_tick, 10)
        self.assertTrue(p.check())

    def test_frozen_policy_never_switches_or_joins(self):
        cfg = SessionConfig(seed=2, ticks=70, shock_tick=30,
                            evolution_enabled=True, adaptation_enabled=False)
        market = SyntheticMarket(cfg)
        market.advance(70)
        p = market.state()['population']
        self.assertTrue(p['enabled'])
        self.assertFalse(p['adaptation_enabled'])
        self.assertEqual(p['strategy_switches'], 0)
        self.assertEqual(p['admitted'], 0)
        self.assertTrue(p['conservation_pass'])
        self.assertEqual(p['switches_per_1000_agent_ticks'],0.0)
        self.assertGreater(p['active_agent_tick_exposure'],0)
        self.assertTrue(market.state()['economy_conservation_pass'])

    def test_relative_hold_initial_invariant_and_after_trade_fee(self):
        book = OrderBook(Config(initial_levels=5, units_per_level=2))
        econ = SyntheticEconomy(book, maker_fee_bps=10, taker_fee_bps=20)
        p = PopulationEvolution(econ, reserve_agents=10)
        for px in (1, 1000, 2000):
            initial = p.relative_metrics(px)
            self.assertEqual(initial['total_relative_vcu'], 0.0)
            self.assertEqual(initial['ahead'], 0)
            self.assertEqual(initial['behind'], 0)
            self.assertEqual(initial['all_agents'], 1000)
        w = econ.wallets['A0001']
        e = book.execute_market('BUY', 1, cash_budget_ticks=w.free_cash,
                                buyer_fee_bps=econ.taker_fee_bps,
                                exclude_agent='A0001')
        econ.settle('A0001', 'BUY', e.matched_orders)
        score = p.relative_metrics(book.last_trade_ticks)
        self.assertEqual(score['traded_agents'], 1)
        self.assertLess(p.relative_pnl(p.active['A0001'], book.last_trade_ticks), 0)
        self.assertLess(score['total_relative_vcu'], 0)
        self.assertEqual(score['ahead'] + score['behind'] + score['equal'], 1000)
        self.assertTrue(econ.check(book))

    def test_survival_uses_initial_cohort_not_admitted_newcomers(self):
        book = OrderBook(Config(initial_levels=5, units_per_level=2))
        econ = SyntheticEconomy(book)
        p = PopulationEvolution(econ, reserve_agents=100)
        self.assertEqual(sum(v['initial'] for v in p.snapshot(1000)['initial_cohort_survival'].values()), 900)
        p._exit('A0001', 1, 1000, book, 'unit test')
        p.review(20, 1000, book)
        snap = p.snapshot(1000)
        self.assertEqual(snap['admitted'], 1)
        self.assertEqual(sum(v['surviving'] for v in snap['initial_cohort_survival'].values()), 899)
        self.assertTrue(all(0 <= v['survival_percent'] <= 100 for v in snap['initial_cohort_survival'].values()))
        self.assertTrue(p.check())

    def test_legacy_evolution_off_ignores_adaptation_settings(self):
        a = SyntheticMarket(SessionConfig(seed=5, ticks=20, shock_tick=10,
                                          evolution_enabled=False, adaptation_enabled=False,
                                          min_strategy_hold_ticks=0))
        b = SyntheticMarket(SessionConfig(seed=5, ticks=20, shock_tick=10,
                                          evolution_enabled=False, adaptation_enabled=True,
                                          min_strategy_hold_ticks=2000))
        for _ in range(20):
            a.step(capture_state=False)
            b.step(capture_state=False)
        self.assertEqual(a.price_history, b.price_history)
        self.assertEqual(a.trades, b.trades)
        self.assertEqual(a.economy.snapshot(1000)['fees_total_vcu'],
                         b.economy.snapshot(1000)['fees_total_vcu'])

    def test_step_capture_false_preserves_economy(self):
        a = SyntheticMarket(SessionConfig(seed=7, ticks=24, shock_tick=12,
                                         evolution_enabled=True))
        b = SyntheticMarket(a.config)
        for _ in range(24):
            self.assertIsNone(a.step(capture_state=False))
            self.assertIsInstance(b.step(), dict)
        self.assertEqual(a.fingerprint(), b.fingerprint())
        self.assertTrue(a.state()['economy_conservation_pass'])


class TestResearchExport(unittest.TestCase):
    def test_seed_validation(self):
        self.assertEqual(parse_seeds('42,-7,0'), (42,-7,0))
        for bad in ('', '42,42', 'abc', ',42', '0,'*21, str(2**31+1)):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                parse_seeds(bad)

    def test_paired_same_initial_and_pre_review(self):
        r = paired_experiment((1,2), ticks=36, shock_tick=18, review_every=10,
                              min_hold_ticks=20)
        self.assertEqual(r['experiment'], 'PAIRED_FROZEN_VS_ADAPTIVE')
        self.assertEqual(len(r['runs']), 4)
        self.assertEqual(len(r['paired_differences']), 2)
        self.assertTrue(all(r['integrity'].values()))
        self.assertTrue(all(x['initial_state_match'] and x['pre_first_review_match']
                            for x in r['paired_differences']))
        self.assertEqual(set(FIELDS), set(r['aggregate_differences']))
        self.assertEqual({x['arm'] for x in r['runs']}, {'frozen', 'adaptive'})
        self.assertEqual([x['strategy_switches'] for x in r['runs'] if x['arm']=='frozen'], [0,0])

    def test_research_determinism(self):
        a=paired_experiment((9,),ticks=30,shock_tick=10,review_every=5,min_hold_ticks=5)
        b=paired_experiment((9,),ticks=30,shock_tick=10,review_every=5,min_hold_ticks=5)
        self.assertEqual(json.dumps(a,sort_keys=True), json.dumps(b,sort_keys=True))
        self.assertEqual(render_report(a), render_report(b))

    def test_reject_duplicate_or_invalid_seeds(self):
        for seeds in ((), (1,1), (True,), (2**31+1,), tuple(range(21))):
            with self.subTest(seeds=seeds), self.assertRaises(ValueError):
                paired_experiment(seeds, ticks=20)

    def test_exports_are_local_reproducible_and_refuse_overwrite(self):
        result=paired_experiment((42,),ticks=20,shock_tick=10,review_every=5)
        with TemporaryDirectory() as folder:
            target=Path(folder)/'research'
            paths=save_experiment(result,target)
            self.assertEqual({p.name for p in paths},
                             {'summary.json','runs.csv','paired.csv','report.html'})
            self.assertTrue(all(p.exists() for p in paths))
            self.assertEqual(json.loads((target/'summary.json').read_text('utf8')),result)
            with (target/'runs.csv').open(encoding='utf-8-sig',newline='') as fp:
                rows=list(csv.DictReader(fp))
            self.assertEqual(len(rows),2)
            with (target/'paired.csv').open(encoding='utf-8-sig',newline='') as fp:
                pairs=list(csv.DictReader(fp))
            self.assertEqual(len(pairs),1)
            html=(target/'report.html').read_text('utf8')
            self.assertIn('парное сравнение',html)
            self.assertIn('SIMULATED',html)
            self.assertIn('Выживаемость исходных когорт',html)
            self.assertNotIn('https://',html)
            raw=[p.read_bytes() for p in paths]
            with self.assertRaises(FileExistsError):
                save_experiment(result,target)
            save_experiment(result,target,overwrite=True)
            self.assertEqual(raw,[p.read_bytes() for p in paths])

    def test_browser_contract_for_new_controls_and_no_external_scripts(self):
        project=Path(__file__).resolve().parents[1]
        html=(project/'market_lab/static/index.html').read_text('utf8')
        js=(project/'market_lab/static/app.js').read_text('utf8')
        for i in ('minStrategyHoldTicks','adaptationEnabled','populationRelative',
                  'populationAllCompared','populationCohorts','populationBlocked','populationControl','populationSwitchRate'):
            self.assertIn('id="'+i+'"',html)
            self.assertIn(i,js)
        self.assertIn('market_lab.research',html)
        self.assertNotIn('https://',html+js)
        self.assertIn('SIMULATED',html)


if __name__ == '__main__':
    unittest.main()
