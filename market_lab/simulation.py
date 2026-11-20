"""Deterministic synthetic limit-order-book liquidity shock experiment.

No exchange access, trading credentials, external inputs or network calls.
Prices are integer ticks of 0.1 VCU and quantities are whole virtual units.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import random
from typing import Literal

Side = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class Config:
    seed: int = 20261120
    ticks: int = 120
    shock_tick: int = 60
    cancel_fraction: float = 0.85
    cancel_levels: int = 8
    shock_sell_qty: int = 26
    agent_count: int = 1000
    initial_levels: int = 40
    units_per_level: int = 8
    mid_ticks: int = 1000

    def __post_init__(self) -> None:
        if self.ticks < 2 or not (1 <= self.shock_tick < self.ticks):
            raise ValueError("shock_tick must be in [1, ticks-1]")
        if not (0.0 <= self.cancel_fraction <= 1.0):
            raise ValueError("cancel_fraction must be between 0 and 1")
        if min(self.cancel_levels, self.shock_sell_qty, self.agent_count,
               self.initial_levels, self.units_per_level, self.mid_ticks) <= 0:
            raise ValueError("levels, quantities, agent_count and mid_ticks must be > 0")


@dataclass
class Resting:
    order_id: str
    agent: str
    side: Side
    price_ticks: int
    remaining: int
    sequence: int


@dataclass(frozen=True)
class Execution:
    aggressive_side: Side
    requested_qty: int
    filled_qty: int
    unfilled_qty: int
    best_before_ticks: int | None
    vwap_ticks: float | None
    last_trade_ticks: int | None
    slippage_bps: float | None
    fills: tuple[tuple[int, int], ...]


class OrderBook:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.orders: dict[str, Resting] = {}
        self.sequence = 0
        self.last_trade_ticks = config.mid_ticks
        self.total_submitted_limit_qty = 0
        self.total_cancelled_qty = 0
        self.total_resting_filled_qty = 0
        self.traded_qty = 0
        for i in range(1, config.initial_levels + 1):
            self.add_limit(f"initial-bid-{i}", "MAKER", "BUY", config.mid_ticks - i,
                           config.units_per_level)
            self.add_limit(f"initial-ask-{i}", "MAKER", "SELL", config.mid_ticks + i,
                           config.units_per_level)

    def add_limit(self, order_id: str, agent: str, side: Side,
                  price_ticks: int, qty: int) -> None:
        if order_id in self.orders or price_ticks <= 0 or qty <= 0:
            raise ValueError("invalid or repeated passive limit order")
        opposite = self.best("SELL" if side == "BUY" else "BUY")
        if opposite is not None and ((side == "BUY" and price_ticks >= opposite) or
                                     (side == "SELL" and price_ticks <= opposite)):
            raise ValueError("crossing limit orders are not supported by this passive-agent MVP")
        self.sequence += 1
        self.orders[order_id] = Resting(order_id, agent, side, price_ticks, qty, self.sequence)
        self.total_submitted_limit_qty += qty

    def best(self, side: Side) -> int | None:
        prices = [o.price_ticks for o in self.orders.values()
                  if o.side == side and o.remaining > 0]
        if not prices:
            return None
        return max(prices) if side == "BUY" else min(prices)

    def levels(self, side: Side, n: int | None = None) -> list[tuple[int, int]]:
        aggregated: dict[int, int] = {}
        for o in self.orders.values():
            if o.side == side and o.remaining > 0:
                aggregated[o.price_ticks] = aggregated.get(o.price_ticks, 0) + o.remaining
        ordered = sorted(aggregated.items(), reverse=(side == "BUY"))
        return ordered if n is None else ordered[:n]

    def depth(self, side: Side, n: int) -> int:
        return sum(qty for _, qty in self.levels(side, n))

    def cancel_top_fraction(self, side: Side, n: int, fraction: float) -> int:
        """Proportionally remove passive resting qty at the top N price levels."""
        import math
        prices = {price for price, _ in self.levels(side, n)}
        removed = 0
        for order_id, order in list(self.orders.items()):
            if order.side == side and order.price_ticks in prices:
                cancelled = min(order.remaining, math.floor(order.remaining * fraction + 0.5))
                order.remaining -= cancelled
                removed += cancelled
                if order.remaining == 0:
                    del self.orders[order_id]
        self.total_cancelled_qty += removed
        return removed

    def execute_market(self, side: Side, qty: int) -> Execution:
        if qty <= 0:
            raise ValueError("market order qty must be positive")
        opposite: Side = "SELL" if side == "BUY" else "BUY"
        best = self.best(opposite)
        candidates = sorted((o for o in self.orders.values() if o.side == opposite),
                            key=lambda o: ((o.price_ticks if side == "BUY" else -o.price_ticks), o.sequence))
        left = qty
        fills: list[tuple[int, int]] = []
        for resting in candidates:
            if left <= 0:
                break
            take = min(left, resting.remaining)
            if take <= 0:
                continue
            resting.remaining -= take
            left -= take
            self.last_trade_ticks = resting.price_ticks
            fills.append((resting.price_ticks, take))
            if resting.remaining == 0:
                del self.orders[resting.order_id]
        filled = qty - left
        self.traded_qty += filled
        self.total_resting_filled_qty += filled
        vwap = sum(p * q for p, q in fills) / filled if filled else None
        slippage = None
        if vwap is not None and best is not None:
            slippage = (vwap - best if side == "BUY" else best - vwap) / best * 10_000
        return Execution(side, qty, filled, left, best, vwap,
                         self.last_trade_ticks if filled else None, slippage, tuple(fills))

    def snapshot(self, tick: int) -> dict:
        bid, ask = self.best("BUY"), self.best("SELL")
        mid = (bid + ask) / 2 if bid is not None and ask is not None else None
        spread = ((ask - bid) / mid * 10_000) if mid else None
        return {"tick": tick, "last_price": round(self.last_trade_ticks / 10, 5),
                "best_bid": bid / 10 if bid is not None else None,
                "best_ask": ask / 10 if ask is not None else None,
                "spread_bps": round(spread, 4) if spread is not None else None,
                "bid_depth_8": self.depth("BUY", 8),
                "ask_depth_8": self.depth("SELL", 8),
                "traded_qty_cumulative": self.traded_qty}

    def check_conservation(self) -> bool:
        resting = sum(o.remaining for o in self.orders.values())
        return (resting + self.total_cancelled_qty + self.total_resting_filled_qty
                == self.total_submitted_limit_qty)


def create_tape(c: Config) -> list[dict]:
    """Exogenous agent actions. Same tape is replayed in both cases."""
    rng = random.Random(c.seed)
    tape: list[dict] = []
    for tick in range(c.ticks):
        if tick == c.shock_tick:
            tape.append({"tick": tick, "kind": "STRESS_SELL", "qty": c.shock_sell_qty,
                         "agent": "STRESS"})
            continue
        side: Side = "BUY" if rng.getrandbits(1) else "SELL"
        agent = f"A{rng.randrange(c.agent_count):04d}"
        qty = rng.randint(1, 4)
        if rng.random() < 0.65:
            tape.append({"tick": tick, "kind": "MARKET", "side": side, "qty": qty,
                         "agent": agent})
        else:
            distance = rng.randint(1, 20)
            price_ticks = c.mid_ticks + (distance if side == "SELL" else -distance)
            tape.append({"tick": tick, "kind": "LIMIT", "side": side, "qty": qty,
                         "price_ticks": price_ticks, "agent": agent})
    return tape


def _shock_summary(book: OrderBook, execution: Execution, before: dict,
                   cancelled: int, original_top_prices: set[int],
                   bid_depth_after_cancel: int) -> dict:
    reference_bid = before["best_bid"]
    # Compare both executions against the SAME pre-shock reference price,
    # including when a 100% withdrawal removes the former best bid.
    slippage = ((reference_bid - execution.vwap_ticks / 10) / reference_bid * 10_000
                if reference_bid is not None and execution.vwap_ticks is not None else None)
    return {"bid_depth_before": before["bid_depth_8"],
            "bid_depth_after_cancellation": bid_depth_after_cancel,
            "bid_depth_after_sell": sum(qty for price, qty in book.levels("BUY")
                                        if price in original_top_prices),
            "cancelled_bid_qty": cancelled,
            "reference_best_bid": before["best_bid"],
            "filled_qty": execution.filled_qty,
            "unfilled_qty": execution.unfilled_qty,
            "vwap": round(execution.vwap_ticks / 10, 6) if execution.vwap_ticks is not None else None,
            "slippage_bps": round(slippage, 4) if slippage is not None else None,
            "last_execution_price": execution.last_trade_ticks / 10 if execution.last_trade_ticks is not None else None,
            "levels_consumed": len({price for price, _ in execution.fills})}


def simulate_case(config: Config, tape: list[dict], shock: bool) -> dict:
    book = OrderBook(config)
    timeline: list[dict] = []
    summary: dict | None = None
    for action in tape:
        tick = action["tick"]
        if action["kind"] == "LIMIT":
            book.add_limit(f"agent-limit-{tick}", action["agent"], action["side"],
                           action["price_ticks"], action["qty"])
        elif action["kind"] == "MARKET":
            book.execute_market(action["side"], action["qty"])
        else:
            before = book.snapshot(tick)
            original_top_prices = {price for price, _ in book.levels("BUY", config.cancel_levels)}
            removed = book.cancel_top_fraction("BUY", config.cancel_levels,
                                               config.cancel_fraction) if shock else 0
            after_cancel = sum(qty for price, qty in book.levels("BUY")
                               if price in original_top_prices)
            execution = book.execute_market("SELL", action["qty"])
            summary = _shock_summary(book, execution, before, removed,
                                     original_top_prices, after_cancel)
        timeline.append(book.snapshot(tick))
    if not book.check_conservation():
        raise AssertionError("resting liquidity conservation failed")
    return {"case": "LIQUIDITY_SHOCK" if shock else "BASELINE",
            "timeline": timeline, "shock_trade": summary,
            "conservation_pass": True,
            "final_resting_qty": sum(o.remaining for o in book.orders.values()),
            "total_cancelled_qty": book.total_cancelled_qty,
            "total_traded_qty": book.traded_qty}


def run_experiment(config: Config) -> dict:
    tape = create_tape(config)
    baseline = simulate_case(config, tape, False)
    shock = simulate_case(config, tape, True)
    b, s = baseline["shock_trade"], shock["shock_trade"]
    assert b is not None and s is not None
    delta = None if b["slippage_bps"] is None or s["slippage_bps"] is None else round(
        s["slippage_bps"] - b["slippage_bps"], 4)
    payload = json.dumps({"settings": asdict(config), "tape": tape}, sort_keys=True,
                         separators=(",", ":"), ensure_ascii=True).encode()
    return {"project": "Kairos Market Lab", "experiment": "LQ-001",
            "market": "SYNTHETIC", "symbol": "VCOIN/VCU", "price_tick_vcu": "0.1",
            "source": "SIMULATED", "network_access": False, "exchange_orders": False,
            "tape_sha256": sha256(payload).hexdigest(), "settings": asdict(config),
            "baseline": baseline, "shock": shock,
            "comparison": {"slippage_increase_bps": delta,
                           "shock_vwap_minus_baseline_vcu": (
                               round(s["vwap"] - b["vwap"], 6)
                               if s["vwap"] is not None and b["vwap"] is not None else None),
                           "shock_cancelled_qty": s["cancelled_bid_qty"],
                           "paired_identical_order_flow": True}}