"""v0.5: deterministic population changes, conservation and UI contracts."""
from __future__ import annotations

from pathlib import Path
import json
import unittest
from urllib.request import Request, urlopen
from http.server import ThreadingHTTPServer
from threading import Thread

from market_lab.economy import SyntheticEconomy
from market_lab.interactive import SessionConfig, SyntheticMarket
from market_lab.population import PopulationEvolution, STRATEGIES, original_strategy
from market_lab.simulation import Config, OrderBook
from market_lab.web import create_handler


class TestPopulationUnit(unittest.TestCase):
    def setUp(self):
        self.book = OrderBook(Config(initial_levels=5, units_per_level=2))
        self.econ = SyntheticEconomy(self.book, maker_fee_bps=10, taker_fee_bps=20)

    def test_prefunded_waitlist_no_account_or_money_creation(self):
        money, coins = self.econ.initial_cash_ticks, self.econ.initial_coins
        baseline_count = len(self.econ.wallets)
        p = PopulationEvolution(self.econ, reserve_agents=100, review_every=5)
        self.assertEqual(len(p.active), 900)
        self.assertEqual(len(p.waiting), 100)
        self.assertNotIn('A0999', p.active)
        self.assertFalse(any(a.startswith('A') and a not in self.econ.wallets
                             for a in p.eligible_ids()))
        self.assertEqual(len(self.econ.wallets), baseline_count)
        self.assertEqual(self.econ.initial_cash_ticks, money)
        self.assertEqual(self.econ.initial_coins, coins)
        self.assertTrue(self.econ.check(self.book))
        self.assertTrue(p.check())

    def test_relative_return_is_price_neutral_before_trading(self):
        p = PopulationEvolution(self.econ, reserve_agents=1)
        m = p.active['A0001']
        for price in (1, 400, 1000, 2000):
            self.assertEqual(p.relative_pnl(m, price), 0)
        self.assertEqual(m.strategy, original_strategy('A0001'))

    def test_two_bad_filled_periods_switch_then_retire_and_admit(self):
        p = PopulationEvolution(self.econ, reserve_agents=1, review_every=5,
                                weak_reviews_to_exit=2)
        who='A0001'
        original=p.rule_for(who)
        for review_tick in (5,10):
            wallet = self.econ.wallets[who]
            e = self.book.execute_market('BUY',1,cash_budget_ticks=wallet.free_cash,
                                         buyer_fee_bps=self.econ.taker_fee_bps,exclude_agent=who)
            self.assertEqual(e.filled_qty,1)
            self.econ.settle(who,'BUY',e.matched_orders)
            p.review(review_tick, self.book.last_trade_ticks, self.book)
            self.assertTrue(p.check())
            self.assertTrue(self.econ.check(self.book))
        self.assertNotIn(who,p.active)
        self.assertEqual(self.econ.wallets[who].status,'STOPPED')
        self.assertIn('A0999',p.active)
        self.assertEqual(p.exits,1)
        self.assertEqual(p.joins,1)
        self.assertGreaterEqual(p.switches,1)
        self.assertNotEqual(original, p.retired[who]['strategy'])
        self.assertEqual(p.snapshot(self.book.last_trade_ticks)['funding'],
                         'PRE_FUNDED_WAITLIST_NO_MINT')
        self.assertEqual(p.snapshot(self.book.last_trade_ticks)['source'],'SIMULATED')

    def test_exit_cancels_orders_and_preserves_reserved_assets(self):
        p = PopulationEvolution(self.econ,reserve_agents=1,review_every=5)
        a='A0001'
        self.assertTrue(self.econ.reserve(a,'BUY',970,2))
        self.book.add_limit('population_test',a,'BUY',970,2)
        prior=self.econ.wallets[a].cash_ticks
        self.econ.wallets[a].status='STOPPED'
        self.econ.stopped_events+=1
        p.review(1,self.book.last_trade_ticks,self.book)
        self.assertNotIn('population_test',self.book.orders)
        self.assertEqual(self.econ.wallets[a].reserved_cash_ticks,0)
        self.assertEqual(self.econ.wallets[a].cash_ticks,prior)
        self.assertTrue(p.check())
        self.assertTrue(self.econ.check(self.book))
        self.assertTrue(self.book.check_conservation())

    def test_depleted_waitlist_does_not_mint_participants(self):
        p = PopulationEvolution(self.econ,reserve_agents=0,review_every=5)
        p._exit('A0001',1,self.book.last_trade_ticks,self.book,'Test manual exit')
        p.review(5,self.book.last_trade_ticks,self.book)
        self.assertEqual(p.joins,0)
        self.assertEqual(p.exits,1)
        self.assertEqual(len(self.econ.wallets),1003)
        self.assertTrue(p.check())
        self.assertTrue(self.econ.check(self.book))

    def test_ineligible_waiting_accounts_are_not_revived(self):
        p=PopulationEvolution(self.econ,reserve_agents=1,review_every=5)
        self.econ.wallets['A0999'].status='BANKRUPT'
        p._exit('A0001',1,self.book.last_trade_ticks,self.book,'Test exit')
        p.review(5,self.book.last_trade_ticks,self.book)
        self.assertEqual(p.joins,0)
        self.assertEqual(p.snapshot(self.book.last_trade_ticks)['ineligible_reserves'],1)
        self.assertNotIn('A0999',p.active)
        self.assertEqual(self.econ.wallets['A0999'].status,'BANKRUPT')
        self.assertTrue(p.check())
        self.assertTrue(self.econ.check(self.book))

    def test_strict_constructor_validation(self):
        for args in ({'reserve_agents':501},{'reserve_agents':-1},{'review_every':0},
                     {'weak_reviews_to_exit':1},{'max_joins_per_review':0}):
            with self.subTest(config=args),self.assertRaises(ValueError):
                PopulationEvolution(self.econ,**args)


class TestIntegratedEvolution(unittest.TestCase):
    def test_evolution_opt_in_keeps_legacy_mode_unchanged(self):
        old=SyntheticMarket(SessionConfig(seed=42,ticks=70,shock_tick=40))
        self.assertIsNone(old.population)
        old.advance(70)
        self.assertFalse(old.state()['population']['enabled'])
        self.assertTrue(old.state()['economy_conservation_pass'])
        self.assertEqual(sum(old.agent_actions.values()),69*old.config.events_per_tick)

    def test_session_validation_for_evolution_controls(self):
        invalid=[{'waiting_agents':True},{'waiting_agents':501},
                 {'evolution_enabled':1},{'evolution_review_ticks':4},
                 {'weak_reviews_to_exit':0},{'max_joins_per_review':101}]
        for args in invalid:
            with self.subTest(args=args),self.assertRaises(ValueError):
                SessionConfig(**args)

    def test_10_seeds_full_evolution_and_conservation(self):
        for seed in range(10):
            with self.subTest(seed=seed):
                s=SyntheticMarket(SessionConfig(seed=seed,ticks=240,shock_tick=100,
                                                  events_per_tick=12,evolution_enabled=True))
                s.advance(100);s.advance(100);s.advance(40)
                snap=s.state(); p=snap['population']; e=snap['economy']
                self.assertTrue(p['conservation_pass'])
                self.assertTrue(snap['economy_conservation_pass'])
                self.assertTrue(snap['conservation_pass'])
                self.assertTrue(e['pnl_identity_pass'])
                self.assertEqual(e['wallet_count'],1002)
                self.assertEqual(e['total_coins'],s.economy.initial_coins)
                self.assertEqual(s.economy.wallets['EXCHANGE'].cash_ticks,
                                 s.economy.maker_fee_ticks+s.economy.taker_fee_ticks)
                self.assertEqual(p['admitted']<=p['retired'],True)
                self.assertEqual(p['active']+p['waiting']+p['retired']+
                                 p['ineligible_reserves'],1000)
                self.assertTrue(all(t in STRATEGIES for t in p['rule_counts']))

    def test_same_seed_same_evolution_trace(self):
        cfg=SessionConfig(seed=42,ticks=200,shock_tick=80,
                          evolution_enabled=True,waiting_agents=120)
        a,b=SyntheticMarket(cfg),SyntheticMarket(cfg)
        a.advance(100);a.advance(100)
        b.advance(60);b.advance(60);b.advance(80)
        self.assertEqual(a.fingerprint(),b.fingerprint())
        self.assertGreater(a.state()['population']['strategy_switches'],0)
        self.assertGreater(a.state()['population']['admitted'],0)

    def test_disabled_mode_no_transition_under_extreme_market_shock(self):
        c=SessionConfig(seed=2,ticks=80,shock_tick=20,evolution_enabled=False,
                        cancel_percent=100,sell_qty=500)
        s=SyntheticMarket(c)
        s.advance(80)
        self.assertFalse(s.state()['population']['enabled'])
        self.assertTrue(s.state()['economy_conservation_pass'])

    def test_ui_local_only_no_remote_dependency(self):
        static=Path(__file__).resolve().parents[1]/'market_lab'/'static'
        html=(static/'index.html').read_text(encoding='utf8')
        js=(static/'app.js').read_text(encoding='utf8')
        for item in ('evolutionEnabled','waitingAgents','evolutionReviewTicks',
                     'populationActive','populationWaiting','populationEvents',
                     'populationRules','populationSwitches','populationInvariant'):
            self.assertIn('id="'+item+'"',html)
            self.assertIn(item,js)
        self.assertNotIn('https://',html+js)
        self.assertIn('SIMULATED',html)

    def test_http_reset_evolution_optin(self):
        server=ThreadingHTTPServer(('127.0.0.1',0),create_handler())
        t=Thread(target=server.serve_forever,daemon=True);t.start()
        url=f'http://127.0.0.1:{server.server_port}'
        try:
            payload={'evolution_enabled':True,'waiting_agents':20,
                     'ticks':30,'shock_tick':15,'evolution_review_ticks':5}
            req=Request(url+'/api/reset',data=json.dumps(payload).encode('utf-8'),
                        headers={'Content-Type':'application/json'},method='POST')
            with urlopen(req,timeout=5) as response:
                obj=json.load(response)
                self.assertTrue(obj['population']['enabled'])
                self.assertEqual(obj['population']['waiting'],20)
            req=Request(url+'/api/step',data=b'{"count":20}',
                        headers={'Content-Type':'application/json'},method='POST')
            with urlopen(req,timeout=5) as response:
                obj=json.load(response)
                self.assertEqual(obj['population']['review_cycles'],4)
                self.assertTrue(obj['economy_conservation_pass'])
        finally:
            server.shutdown();server.server_close()


if __name__=='__main__':
    unittest.main()
