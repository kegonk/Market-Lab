"""Reproducible, adaptive, *synthetic-only* market session for the local UI.

No real prices, accounts, exchange clients, network calls or trading credentials.
Matching uses the tested integer-tick OrderBook from LQ-001. A common seed
aligns the two scenarios until the shock; agents react endogenously thereafter.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
import json
import random

from .simulation import Config, Execution, OrderBook, Side


@dataclass(frozen=True)
class SessionConfig:
    seed: int = 42
    ticks: int = 320
    shock_tick: int = 160
    shock_enabled: bool = True
    cancel_percent: int = 85
    sell_qty: int = 26
    events_per_tick: int = 6
    candle_ticks: int = 5

    def __post_init__(self) -> None:
        if not (20 <= self.ticks <= 2000 and 1 <= self.shock_tick <= self.ticks):
            raise ValueError("ticks must be 20..2000 and shock_tick within ticks")
        if not (0 <= self.cancel_percent <= 100):
            raise ValueError("cancel_percent must be 0..100")
        if not (1 <= self.sell_qty <= 500 and 1 <= self.events_per_tick <= 25):
            raise ValueError("sell_qty or events_per_tick out of range")
        if not (1 <= self.candle_ticks <= 60):
            raise ValueError("candle_ticks must be 1..60")
        if not isinstance(self.shock_enabled, bool):
            raise ValueError("shock_enabled must be boolean")
        if not isinstance(self.seed, int) or isinstance(self.seed, bool) or abs(self.seed) > 2**31:
            raise ValueError("seed must be an integer <= 2^31")


class SyntheticMarket:
    """Single-threaded discrete-event simulation, owned by localhost server."""

    def __init__(self, config: SessionConfig | None = None) -> None:
        self.config = config or SessionConfig()
        self.rng = random.Random(self.config.seed)
        self.book = OrderBook(Config(ticks=max(self.config.ticks, 2),
                                     shock_tick=1, initial_levels=40,
                                     units_per_level=8, agent_count=1000))
        self.tick = 0
        self.price_history = [self.book.last_trade_ticks]
        self.candles: list[dict] = []
        self.event_log: list[dict] = []
        self.recent_trades: list[dict] = []
        self.trades = 0
        self.total_traded_qty = 0
        self.agent_actions = {"NOISE": 0, "MAKER": 0, "TREND": 0, "REVERSION": 0}
        self.shock: dict | None = None
        self._candle: dict | None = None

    @staticmethod
    def _units(price_ticks: int | None) -> float | None:
        return price_ticks / 10 if price_ticks is not None else None

    def _log(self, kind: str, message: str) -> None:
        self.event_log.append({"tick": self.tick, "kind": kind, "message": message})
        self.event_log = self.event_log[-48:]

    def _record_execution(self, e: Execution, agent: str, agent_type: str) -> None:
        if not e.filled_qty:
            return
        for price, qty in e.fills:
            self.trades += 1
            self.total_traded_qty += qty
            px = price / 10
            if self._candle is None:
                self._candle = {"start": self.tick, "open": px, "high": px,
                                "low": px, "close": px, "volume": 0, "trades": 0}
            self._candle["high"] = max(self._candle["high"], px)
            self._candle["low"] = min(self._candle["low"], px)
            self._candle["close"] = px
            self._candle["volume"] += qty
            self._candle["trades"] += 1
            self.recent_trades.append({"tick": self.tick, "side": e.aggressive_side,
                                       "price": px, "qty": qty, "agent": agent,
                                       "type": agent_type})
        self.recent_trades = self.recent_trades[-60:]

    def _market(self, side: Side, qty: int, agent: str, agent_type: str) -> Execution:
        e = self.book.execute_market(side, qty)
        self._record_execution(e, agent, agent_type)
        return e

    def _limit(self, side: Side, qty: int, agent: str, seq: int,
               anchor: int | None = None) -> None:
        # Place orders *passively*, observing the current opposite best quote.
        bid = self.book.best("BUY")
        ask = self.book.best("SELL")
        center = anchor if anchor is not None else self.book.last_trade_ticks
        center = max(5, center)
        dist = self.rng.randint(1, 7)
        price = center - dist if side == "BUY" else center + dist
        if side == "BUY" and ask is not None:
            price = min(price, ask - 1)
        if side == "SELL" and bid is not None:
            price = max(price, bid + 1)
        price = max(1, price)
        self.book.add_limit(f"a-{self.tick}-{seq}-{side}", agent, side, price, qty)

    def _act(self, seq: int) -> None:
        agent_id = self.rng.randrange(1000)
        agent = f"A{agent_id:04d}"
        kind = ("NOISE" if agent_id % 10 < 5 else
                "MAKER" if agent_id % 10 < 7 else
                "TREND" if agent_id % 10 < 9 else "REVERSION")
        self.agent_actions[kind] += 1
        last = self.book.last_trade_ticks
        reference = self.price_history[max(0, len(self.price_history) - 8)]
        trend = last - reference
        qty = self.rng.randint(1, 5)

        if kind == "MAKER":
            # Passive replenishment reacts to the current price; maker cannot
            # create fills without an aggressor. Single placement per action.
            side: Side = "BUY" if self.rng.getrandbits(1) else "SELL"
            self._limit(side, qty + 2, agent, seq)
        elif kind == "NOISE":
            side = "BUY" if self.rng.getrandbits(1) else "SELL"
            if self.rng.random() < 0.72:
                self._market(side, qty, agent, kind)
            else:
                self._limit(side, qty, agent, seq)
        elif kind == "TREND":
            side = "BUY" if trend > 0 else "SELL" if trend < 0 else ("BUY" if self.rng.getrandbits(1) else "SELL")
            if self.rng.random() < 0.75:
                self._market(side, qty, agent, kind)
            else:
                self._limit(side, qty, agent, seq)
        else:
            side = "SELL" if trend > 0 else "BUY" if trend < 0 else ("BUY" if self.rng.getrandbits(1) else "SELL")
            if self.rng.random() < 0.70:
                self._market(side, qty, agent, kind)
            else:
                self._limit(side, qty, agent, seq)

    def _ensure_candle(self) -> None:
        if self._candle is None:
            # Explicit zero-trade interval, flat at prior last trade; not
            # a fabricated trade or inferred real OHLCV.
            px = self.book.last_trade_ticks / 10
            self._candle = {"start": self.tick, "open": px, "high": px,
                            "low": px, "close": px, "volume": 0, "trades": 0}

    def step(self) -> dict:
        if self.tick >= self.config.ticks:
            return self.state()
        self.tick += 1
        if (self.tick - 1) % self.config.candle_ticks == 0:
            self._candle = None
        if self.tick == self.config.shock_tick:
            pre_bid = self.book.best("BUY")
            pre_depth = self.book.depth("BUY", 8)
            removed = (self.book.cancel_top_fraction("BUY", 8, self.config.cancel_percent / 100)
                       if self.config.shock_enabled else 0)
            after_depth = self.book.depth("BUY", 8)
            e = self._market("SELL", self.config.sell_qty, "STRESS", "STRESS")
            # Report slippage against the pre-cancellation best bid; the
            # normal Execution metric uses the possibly modified best.
            slippage = None
            if pre_bid is not None and e.vwap_ticks is not None:
                slippage = round(10000 * (pre_bid - e.vwap_ticks) / pre_bid, 4)
            self.shock = {"tick": self.tick, "enabled": self.config.shock_enabled,
                          "cancelled_qty": removed, "bid_depth_before": pre_depth,
                          "bid_depth_after": after_depth, "sell_requested": self.config.sell_qty,
                          "sell_filled": e.filled_qty, "sell_unfilled": e.unfilled_qty,
                          "slippage_bps": slippage,
                          "vwap": round(e.vwap_ticks / 10, 5) if e.vwap_ticks is not None else None}
            self._log("SHOCK" if self.config.shock_enabled else "CONTROL",
                      f"{'Отмена BUY-ликвидности' if self.config.shock_enabled else 'Контроль без отмены'}: -{removed} ед., SELL {e.filled_qty}/{e.requested_qty}")
        else:
            for index in range(self.config.events_per_tick):
                self._act(index)

        self._ensure_candle()
        self.price_history.append(self.book.last_trade_ticks)
        if self.tick % self.config.candle_ticks == 0 or self.tick == self.config.ticks:
            self.candles.append(dict(self._candle))
        if not self.book.check_conservation():
            raise AssertionError("LIMIT_QUANTITY_CONSERVATION_FAILED")
        return self.state()

    def advance(self, count: int = 1) -> dict:
        if type(count) is not int or not (1 <= count <= 100):
            raise ValueError("step count must be 1..100")
        for _ in range(min(count, self.config.ticks - self.tick)):
            self.step()
        return self.state()

    def state(self) -> dict:
        b = self.book.snapshot(self.tick)
        active = dict(self._candle) if self._candle is not None else None
        bars = self.candles[-120:]
        if active is not None and self.tick % self.config.candle_ticks != 0:
            bars = bars + [active]
        return {"project": "Kairos Market Lab", "source": "SIMULATED",
                "mode": "SYNTHETIC_OFFLINE", "symbol": "VCOIN/VCU",
                "config": asdict(self.config), "tick": self.tick,
                "done": self.tick >= self.config.ticks,
                "market": b, "best_bids": [[p / 10, q] for p, q in self.book.levels("BUY", 12)],
                "best_asks": [[p / 10, q] for p, q in self.book.levels("SELL", 12)],
                "candles": bars, "total_trades": self.trades,
                "volume": self.total_traded_qty, "agent_actions": dict(self.agent_actions),
                "shock": self.shock, "events": list(self.event_log),
                "recent_trades": self.recent_trades[-20:],
                "conservation_pass": self.book.check_conservation()}

    def fingerprint(self) -> str:
        payload = json.dumps(self.state(), sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True).encode("utf-8")
        return sha256(payload).hexdigest()
