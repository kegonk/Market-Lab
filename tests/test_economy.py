"""Tests of conservation, risk-free synthetic accounting and maker settlement."""
import unittest

from market_lab.economy import SyntheticEconomy
from market_lab.interactive import SessionConfig, SyntheticMarket
from market_lab.simulation import Config, OrderBook


class TestWalletAndMatching(unittest.TestCase):
    def setUp(self):
        self.book = OrderBook(Config(initial_levels=5, units_per_level=2))
        self.econ = SyntheticEconomy(self.book)

    def test_initial_reservations_back_depth(self):
        maker = self.econ.wallets['MAKER']
        self.assertEqual(maker.reserved_coin_qty, 10)
        self.assertEqual(maker.reserved_cash_ticks,
                         sum((1000 - i) * 2 for i in range(1, 6)))
        self.assertTrue(self.econ.check(self.book))

    def test_sell_moves_cash_coins_and_releases_reserved_quote(self):
        taker = self.econ.wallets['A0001']
        maker = self.econ.wallets['MAKER']
        old_cash, old_coin = taker.cash_ticks, taker.coin_qty
        old_reserved = maker.reserved_cash_ticks
        e = self.book.execute_market('SELL', 3, exclude_agent='A0001')
        self.econ.settle('A0001', 'SELL', e.matched_orders)
        amount = sum(px * q for _, _, px, q in e.matched_orders)
        self.assertEqual(e.filled_qty, 3)
        self.assertEqual(taker.cash_ticks, old_cash + amount)
        self.assertEqual(taker.coin_qty, old_coin - 3)
        self.assertEqual(maker.reserved_cash_ticks, old_reserved - amount)
        self.assertTrue(self.econ.check(self.book))

    def test_buy_moves_cash_coins_and_releases_reserved_inventory(self):
        taker = self.econ.wallets['A0001']
        maker = self.econ.wallets['MAKER']
        old_cash, old_coin = taker.cash_ticks, taker.coin_qty
        old_reserved = maker.reserved_coin_qty
        e = self.book.execute_market('BUY', 3, cash_budget_ticks=taker.free_cash,
                                     exclude_agent='A0001')
        self.econ.settle('A0001', 'BUY', e.matched_orders)
        amount = sum(px * q for _, _, px, q in e.matched_orders)
        self.assertEqual(taker.cash_ticks, old_cash - amount)
        self.assertEqual(taker.coin_qty, old_coin + 3)
        self.assertEqual(maker.reserved_coin_qty, old_reserved - 3)
        self.assertTrue(self.econ.check(self.book))

    def test_unfunded_passive_order_is_denied(self):
        agent = self.econ.wallets['A0001']
        self.assertFalse(self.econ.reserve('A0001', 'SELL', 1020, agent.free_coin + 1))
        self.assertFalse(self.econ.reserve('A0001', 'BUY', 900, agent.free_cash // 900 + 1))
        self.assertEqual(self.econ.denied_actions, 2)
        self.assertTrue(self.econ.check(self.book))

    def test_buy_budget_partial_fill_without_negative_cash(self):
        agent = self.econ.wallets['A0001']
        budget = self.book.best('SELL')
        e = self.book.execute_market('BUY', 10, cash_budget_ticks=budget,
                                     exclude_agent='A0001')
        self.assertEqual(e.filled_qty, 1)
        self.econ.settle('A0001', 'BUY', e.matched_orders)
        self.assertGreaterEqual(agent.free_cash, 0)
        self.assertTrue(self.econ.check(self.book))

    def test_cancellation_refunds_exact_bid_reserves(self):
        before = self.econ.wallets['MAKER'].reserved_cash_ticks
        deleted = self.econ.cancel_book_top(self.book, 'BUY', 5, 1.0)
        self.assertEqual(deleted, 10)
        self.assertEqual(self.econ.wallets['MAKER'].reserved_cash_ticks, 0)
        self.assertGreater(before, 0)
        self.assertTrue(self.econ.check(self.book))
        self.assertTrue(self.book.check_conservation())

    def test_self_match_prevented(self):
        self.assertTrue(self.econ.reserve('A0001', 'SELL', 1001, 1))
        self.book.add_limit('mine', 'A0001', 'SELL', 1001, 1)
        e = self.book.execute_market('BUY', 1, exclude_agent='A0001')
        self.assertTrue(all(agent != 'A0001' for _, agent, _, _ in e.matched_orders))
        self.econ.settle('A0001', 'BUY', e.matched_orders)
        self.assertTrue(self.econ.check(self.book))


class TestIntegratedAccounting(unittest.TestCase):
    def test_10_seeds_full_session_invariants(self):
        for seed in range(10):
            with self.subTest(seed=seed):
                s = SyntheticMarket(SessionConfig(seed=seed, ticks=130, shock_tick=60,
                                                  events_per_tick=12))
                s.advance(100)
                s.advance(100)
                state = s.state()
                self.assertTrue(state['economy_conservation_pass'])
                self.assertTrue(state['conservation_pass'])
                self.assertTrue(state['economy']['mark_pnl_identity_pass'])
                self.assertEqual(state['economy']['total_mark_pnl_vcu'],
                                 state['economy']['total_coins']
                                 * (s.book.last_trade_ticks - s.economy.initial_mid_ticks) / 10)
                self.assertEqual(state['economy']['wallet_count'], 1002)
                self.assertGreater(state['economy']['fills_settled'], 0)

    def test_full_withdrawal_no_missing_inventory(self):
        s = SyntheticMarket(SessionConfig(seed=900, ticks=80, shock_tick=20,
                                          cancel_percent=100, sell_qty=500))
        s.advance(80)
        self.assertTrue(s.economy.check(s.book))
        self.assertTrue(s.book.check_conservation())
        self.assertEqual(s.economy.wallets['STRESS'].coin_qty, 600 - s.shock['sell_filled'])
        self.assertEqual(s.shock['sell_requested'], 500)

    def test_repeatability_including_wallets(self):
        c = SessionConfig(seed=12, ticks=70, shock_tick=40)
        a, b = SyntheticMarket(c), SyntheticMarket(c)
        a.advance(70)
        b.advance(70)
        self.assertEqual(a.fingerprint(), b.fingerprint())
        self.assertEqual(a.state()['economy'], b.state()['economy'])


if __name__ == '__main__':
    unittest.main()
