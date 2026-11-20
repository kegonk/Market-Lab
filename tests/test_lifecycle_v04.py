"""v0.4 economic invariants, FIFO fees, operating risk stops and safety."""
import json
from pathlib import Path
import unittest

from market_lab.economy import SyntheticEconomy, fee_unit
from market_lab.interactive import SessionConfig, SyntheticMarket
from market_lab.simulation import Config, OrderBook


class TestFeesAndPnL(unittest.TestCase):
    def setUp(self):
        self.book = OrderBook(Config(initial_levels=5, units_per_level=2))
        self.econ = SyntheticEconomy(self.book, maker_fee_bps=10, taker_fee_bps=20)

    def test_precise_per_coin_rounded_fee(self):
        self.assertEqual(fee_unit(1000, 10), 1)
        self.assertEqual(fee_unit(1001, 10), 2)
        self.assertEqual(fee_unit(1001, 20), 3)
        self.assertEqual(fee_unit(1000, 0), 0)
        for rate in (True, -1, 1001, 0.5):
            with self.subTest(rate=rate), self.assertRaises(ValueError):
                fee_unit(1000, rate)

    def test_initial_buy_reserve_includes_maker_fee(self):
        self.assertEqual(self.econ.wallets['MAKER'].reserved_cash_ticks,
                         sum((1000 - i + fee_unit(1000 - i, 10)) * 2 for i in range(1, 6)))
        self.assertTrue(self.econ.check(self.book))

    def test_market_buy_fees_fifo_and_conservation(self):
        taker, maker = self.econ.wallets['A0001'], self.econ.wallets['MAKER']
        before_cash, before_coin = taker.cash_ticks, taker.coin_qty
        ex = self.book.execute_market('BUY', 2, cash_budget_ticks=taker.free_cash,
                                      buyer_fee_bps=20, exclude_agent='A0001')
        self.assertEqual(ex.filled_qty, 2)
        self.econ.settle('A0001', 'BUY', ex.matched_orders)
        cost = 2 * (1001 + fee_unit(1001, 20))
        self.assertEqual(taker.cash_ticks, before_cash - cost)
        self.assertEqual(taker.coin_qty, before_coin + 2)
        self.assertEqual(self.econ.wallets['EXCHANGE'].cash_ticks,
                         2 * (fee_unit(1001, 10) + fee_unit(1001, 20)))
        self.assertEqual(maker.realized_pnl_ticks, 2 * (1001 - 1000 - fee_unit(1001, 10)))
        self.assertEqual(taker.basis(), 100 * 1000 + cost)
        self.assertTrue(self.econ.snapshot(1001)['pnl_identity_pass'])
        self.assertTrue(self.econ.check(self.book))

    def test_market_buy_budget_includes_fees(self):
        taker = self.econ.wallets['A0001']
        unit_cost = 1001 + fee_unit(1001, 20)
        taker.cash_ticks = unit_cost
        self.econ.initial_cash_ticks = sum(w.cash_ticks for w in self.econ.wallets.values())
        ex = self.book.execute_market('BUY', 5, cash_budget_ticks=taker.free_cash,
                                      buyer_fee_bps=20, exclude_agent='A0001')
        self.assertEqual(ex.filled_qty, 1)
        self.econ.settle('A0001', 'BUY', ex.matched_orders)
        self.assertEqual(taker.cash_ticks, 0)
        self.assertTrue(self.econ.check(self.book))

    def test_sell_fifo_recognizes_realized_loss_and_fee(self):
        taker = self.econ.wallets['A0001']
        ex = self.book.execute_market('SELL', 3, exclude_agent='A0001')
        self.econ.settle('A0001', 'SELL', ex.matched_orders)
        basis = 3 * 1000
        proceeds = sum((price - fee_unit(price, 20)) * qty for _, _, price, qty in ex.matched_orders)
        self.assertEqual(taker.realized_pnl_ticks, proceeds - basis)
        self.assertEqual(taker.coin_qty, 97)
        self.assertEqual(taker.basis(), 97 * 1000)
        self.assertTrue(self.econ.snapshot(self.book.last_trade_ticks)['pnl_identity_pass'])
        self.assertTrue(self.econ.check(self.book))

    def test_full_cancel_returns_bid_cash_with_fee(self):
        reserved = self.econ.wallets['MAKER'].reserved_cash_ticks
        removed = self.econ.cancel_book_top(self.book, 'BUY', 5, 1)
        self.assertEqual(removed, 10)
        self.assertEqual(self.econ.wallets['MAKER'].reserved_cash_ticks, 0)
        self.assertGreater(reserved, 0)
        self.assertTrue(self.econ.check(self.book))

    def test_no_partial_spend_outside_wallet(self):
        self.econ.market_budget('A0001', 'SELL', 9000)
        self.assertTrue(self.econ.check(self.book))
        self.assertFalse(self.econ.reserve('A0001', 'BUY', 1001, 100000))
        self.assertTrue(self.econ.check(self.book))


class TestLifecycle(unittest.TestCase):
    def test_explicit_bankruptcy_threshold_cancels_orders(self):
        book = OrderBook(Config(initial_levels=5, units_per_level=2))
        econ = SyntheticEconomy(book, bankruptcy_threshold_percent=90)
        self.assertTrue(econ.reserve('A0001', 'BUY', 970, 5))
        book.add_limit('owned', 'A0001', 'BUY', 970, 5)
        econ.review_lifecycle(book, 1)  # deliberately severe synthetic price crash
        self.assertEqual(econ.wallets['A0001'].status, 'BANKRUPT')
        self.assertNotIn('owned', book.orders)
        self.assertEqual(econ.wallets['A0001'].reserved_cash_ticks, 0)
        self.assertFalse(econ.can_trade('A0001'))
        self.assertTrue(econ.check(book))
        self.assertTrue(book.check_conservation())

    def test_multiple_denials_stop_and_release(self):
        book = OrderBook(Config(initial_levels=5, units_per_level=2))
        econ = SyntheticEconomy(book, stop_denials=2)
        self.assertTrue(econ.reserve('A0001', 'BUY', 970, 2))
        book.add_limit('owned', 'A0001', 'BUY', 970, 2)
        self.assertFalse(econ.reserve('A0001', 'BUY', 970, 99999))
        self.assertFalse(econ.reserve('A0001', 'BUY', 970, 99999))
        self.assertEqual(econ.wallets['A0001'].status, 'STOPPED')
        econ.review_lifecycle(book, 1000)
        self.assertNotIn('owned', book.orders)
        self.assertFalse(econ.can_trade('A0001'))
        self.assertTrue(econ.check(book))
        self.assertTrue(book.check_conservation())

    def test_no_hidden_liquidation_or_debt(self):
        s = SyntheticMarket(SessionConfig(seed=5, ticks=60, shock_tick=20,
                                          bankruptcy_threshold_percent=100))
        s.step()
        snap = s.state()['economy']
        self.assertGreater(snap['bankrupt'], 0)
        self.assertEqual(snap['fees_total_vcu'], s.economy.wallets['EXCHANGE'].cash_ticks / 10)
        self.assertTrue(s.state()['economy_conservation_pass'])
        self.assertEqual(snap['total_coins'], s.economy.initial_coins)


class TestIntegratedV04(unittest.TestCase):
    def test_seeded_sessions_preserve_assets_and_fee_identity(self):
        for seed in range(10):
            with self.subTest(seed=seed):
                s = SyntheticMarket(SessionConfig(seed=seed, ticks=85, shock_tick=35,
                                                  events_per_tick=9))
                s.advance(85)
                e = s.state()['economy']
                self.assertTrue(e['pnl_identity_pass'])
                self.assertTrue(e['mark_pnl_identity_pass'])
                self.assertTrue(s.state()['economy_conservation_pass'])
                self.assertEqual(s.economy.wallets['EXCHANGE'].cash_ticks,
                                 s.economy.maker_fee_ticks + s.economy.taker_fee_ticks)
                self.assertEqual(e['winners']+e['losers']+e['unchanged'], e['wallet_count'])
                self.assertEqual(e['trading_active']+e['bankrupt']+e['stopped'], e['wallet_count'])
                self.assertGreater(e['fees_total_vcu'], 0)

    def test_repeated_seed_deterministic(self):
        cfg = SessionConfig(seed=73, ticks=60, shock_tick=20)
        a, b = SyntheticMarket(cfg), SyntheticMarket(cfg)
        a.advance(60)
        b.advance(60)
        self.assertEqual(a.fingerprint(), b.fingerprint())

    def test_maker_caution_for_shock_and_recovery(self):
        s = SyntheticMarket(SessionConfig(seed=17, ticks=130, shock_tick=15,
                                          maker_caution_ticks=40, events_per_tick=12))
        s.advance(65)
        self.assertGreater(s.maker_withheld+s.maker_widened, 0)
        self.assertTrue(s.state()['economy_conservation_pass'])
        self.assertFalse(s.maker_cautious() and s.tick > 55 and
                         s.book.depth('BUY',8)>=12 and s.book.depth('SELL',8)>=12)

    def test_all_public_data_is_synthetic(self):
        s = SyntheticMarket()
        payload = s.state()
        self.assertEqual(payload['source'], 'SIMULATED')
        self.assertNotIn('api_key', json.dumps(payload).lower())
        self.assertEqual(payload['economy']['fee_account_count'], 1)
        self.assertIn('maker_risk', payload)

    def test_ui_has_lifecycle_fields_and_no_remote_dependencies(self):
        static = Path(__file__).resolve().parents[1] / 'market_lab' / 'static'
        html = (static/'index.html').read_text(encoding='utf-8')
        js = (static/'app.js').read_text(encoding='utf-8')
        for key in ('totalFees', 'realizedPnl', 'unrealizedPnl', 'bankruptStopped',
                    'winningLosing', 'makerRisk', 'makerFee', 'takerFee'):
            self.assertIn('id="'+key+'"',html)
            self.assertIn(key,js)
        self.assertNotIn('https://', html+js)


if __name__ == '__main__':
    unittest.main()

class TestLocalRouteQuery(unittest.TestCase):
    def test_url_query_suffix_is_not_a_404(self):
        from http.server import ThreadingHTTPServer
        from threading import Thread
        from urllib.request import urlopen
        from market_lab.web import create_handler
        server = ThreadingHTTPServer(('127.0.0.1', 0), create_handler())
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f'http://127.0.0.1:{server.server_port}'
            with urlopen(base + '/?utm_source=local', timeout=3) as response:
                self.assertEqual(response.status, 200)
                self.assertIn(b'MARKET', response.read())
            with urlopen(base + '/api/state?ref=local', timeout=3) as response:
                self.assertEqual(json.load(response)['source'], 'SIMULATED')
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)
