"""Safety, determinism, pairing and accounting tests for the synthetic lab."""

import dataclasses
import unittest

from market_lab.simulation import Config, OrderBook, create_tape, run_experiment


class TestMarketLab(unittest.TestCase):
    def test_reproducible_with_seed(self):
        cfg = Config()
        self.assertEqual(run_experiment(cfg), run_experiment(cfg))

    def test_reference_paired_order_flow(self):
        cfg = Config()
        result = run_experiment(cfg)
        self.assertTrue(result["comparison"]["paired_identical_order_flow"])
        self.assertEqual(len(result["baseline"]["timeline"]), cfg.ticks)
        self.assertEqual(len(result["shock"]["timeline"]), cfg.ticks)
        # Up to shock event, the scenarios consume exactly the same actions.
        self.assertEqual(result["baseline"]["timeline"][:cfg.shock_tick],
                         result["shock"]["timeline"][:cfg.shock_tick])

    def test_shock_effect_is_observable(self):
        result = run_experiment(Config())
        b = result["baseline"]["shock_trade"]
        s = result["shock"]["shock_trade"]
        self.assertEqual(b["filled_qty"], s["filled_qty"])
        self.assertGreater(s["cancelled_bid_qty"], 0)
        self.assertLess(s["bid_depth_after_cancellation"], s["bid_depth_before"])
        self.assertGreater(s["slippage_bps"], b["slippage_bps"])
        self.assertLess(s["vwap"], b["vwap"])

    def test_full_withdrawal_measures_original_price_band(self):
        result = run_experiment(Config(cancel_fraction=1.0))
        b = result["baseline"]["shock_trade"]
        s = result["shock"]["shock_trade"]
        self.assertEqual(s["bid_depth_after_cancellation"], 0)
        self.assertEqual(s["cancelled_bid_qty"], s["bid_depth_before"])
        self.assertGreater(s["slippage_bps"], b["slippage_bps"])

    def test_conservation(self):
        cfg = Config()
        result = run_experiment(cfg)
        self.assertTrue(result["baseline"]["conservation_pass"])
        self.assertTrue(result["shock"]["conservation_pass"])
        self.assertEqual(result["baseline"]["total_cancelled_qty"], 0)

    def test_partial_fill_does_not_create_liquidity(self):
        book = OrderBook(Config(initial_levels=1, units_per_level=2))
        ex = book.execute_market("SELL", 10)
        self.assertEqual(ex.filled_qty, 2)
        self.assertEqual(ex.unfilled_qty, 8)
        self.assertTrue(book.check_conservation())

    def test_price_time_priority_and_no_negative_depth(self):
        book = OrderBook(Config(initial_levels=1, units_per_level=2))
        book.add_limit("second", "S1", "BUY", 999, 2)
        e = book.execute_market("SELL", 3)
        self.assertEqual(e.fills, ((999, 2), (999, 1)))
        self.assertEqual(book.depth("BUY", 1), 1)
        self.assertTrue(book.check_conservation())

    def test_config_rejects_invalid_shock(self):
        with self.assertRaises(ValueError):
            Config(cancel_fraction=1.1)
        with self.assertRaises(ValueError):
            Config(shock_tick=120)

    def test_seed_changes_tape(self):
        self.assertNotEqual(create_tape(Config(seed=1)), create_tape(Config(seed=2)))


if __name__ == "__main__":
    unittest.main()
