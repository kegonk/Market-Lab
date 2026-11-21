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
from .economy import SyntheticEconomy, fee_unit
from .population import PopulationEvolution


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
    maker_fee_bps: int = 10
    taker_fee_bps: int = 20
    bankruptcy_threshold_percent: int = 20
    stop_denials: int = 6
    maker_caution_ticks: int = 35
    evolution_enabled: bool = False
    waiting_agents: int = 100
    evolution_review_ticks: int = 20
    weak_reviews_to_exit: int = 3
    max_joins_per_review: int = 10
    adaptation_enabled: bool = True
    min_strategy_hold_ticks: int = 40
    allow_strategy_switch: bool = True
    allow_population_exit: bool = True
    allow_population_join: bool = True
    allow_retired_reentry: bool = False
    reentry_cooldown_ticks: int = 40
    synthetic_regime: str = 'LEGACY'
    # v1.0 opt-in liquidity provision by existing, funded active wallets.
    quote_refresh_mode: str = 'OFF'  # OFF, MAKER_ONLY, ALL_FUNDED
    quote_refresh_min_depth: int = 5
    # v1.1: separate, opt-in synthetic inventory-backed liquidity controller.
    liquidity_v11_mode: str = 'OFF'  # OFF, SYMMETRIC, SIDE_AWARE
    liquidity_v11_bid_target: int = 5
    liquidity_v11_ask_target: int = 5
    liquidity_v11_max_order_qty: int = 3
    liquidity_v11_max_orders_per_side_tick: int = 2
    liquidity_v11_wallet_cap_percent: int = 20
    liquidity_v11_max_orders_per_agent: int = 3

    def __post_init__(self) -> None:
        for field in ('seed', 'ticks', 'shock_tick', 'cancel_percent', 'sell_qty',
                      'events_per_tick', 'candle_ticks', 'maker_fee_bps', 'taker_fee_bps',
                      'bankruptcy_threshold_percent', 'stop_denials', 'maker_caution_ticks',
                      'waiting_agents', 'evolution_review_ticks', 'weak_reviews_to_exit',
                      'max_joins_per_review', 'min_strategy_hold_ticks', 'reentry_cooldown_ticks',
                      'quote_refresh_min_depth',
                      'liquidity_v11_bid_target', 'liquidity_v11_ask_target',
                      'liquidity_v11_max_order_qty', 'liquidity_v11_max_orders_per_side_tick',
                      'liquidity_v11_wallet_cap_percent','liquidity_v11_max_orders_per_agent'):
            if type(getattr(self, field)) is not int:
                raise ValueError(f'{field} must be an integer')
        if not (20 <= self.ticks <= 2000 and 1 <= self.shock_tick <= self.ticks):
            raise ValueError("ticks must be 20..2000 and shock_tick within ticks")
        if not (0 <= self.cancel_percent <= 100):
            raise ValueError("cancel_percent must be 0..100")
        if not (1 <= self.sell_qty <= 500 and 1 <= self.events_per_tick <= 25):
            raise ValueError("sell_qty or events_per_tick out of range")
        if not (1 <= self.candle_ticks <= 60):
            raise ValueError("candle_ticks must be 1..60")
        if not (0 <= self.maker_fee_bps <= 1000 and 0 <= self.taker_fee_bps <= 1000):
            raise ValueError('fee rates must be 0..1000 basis points')
        if not (0 <= self.bankruptcy_threshold_percent <= 100 and 1 <= self.stop_denials <= 100):
            raise ValueError('invalid lifecycle thresholds')
        if not (0 <= self.maker_caution_ticks <= 2000):
            raise ValueError('maker_caution_ticks must be 0..2000')
        if not (0 <= self.waiting_agents <= 500 and 5 <= self.evolution_review_ticks <= 500
                and 2 <= self.weak_reviews_to_exit <= 20
                and 1 <= self.max_joins_per_review <= 100):
            raise ValueError('invalid evolution parameters')
        if not 20 <= self.reentry_cooldown_ticks <= 2000:
            raise ValueError('invalid reentry_cooldown_ticks')
        if self.synthetic_regime not in ('LEGACY', 'BULL', 'BEAR', 'SIDEWAYS', 'THIN') or type(self.synthetic_regime) is not str:
            raise ValueError('invalid synthetic_regime')
        if type(self.quote_refresh_mode) is not str or self.quote_refresh_mode not in ('OFF', 'MAKER_ONLY', 'ALL_FUNDED'):
            raise ValueError('invalid quote_refresh_mode')
        if not 1 <= self.quote_refresh_min_depth <= 50:
            raise ValueError('invalid quote_refresh_min_depth')
        if type(self.liquidity_v11_mode) is not str or self.liquidity_v11_mode not in ('OFF','SYMMETRIC','SIDE_AWARE'):
            raise ValueError('INVALID_V11_CONTROL_MODE')
        if not (1 <= self.liquidity_v11_bid_target <= 50 and
                1 <= self.liquidity_v11_ask_target <= 50 and
                1 <= self.liquidity_v11_max_order_qty <= 20 and
                1 <= self.liquidity_v11_max_orders_per_side_tick <= 5 and
                1 <= self.liquidity_v11_wallet_cap_percent <= 100 and
                1 <= self.liquidity_v11_max_orders_per_agent <= 10):
            raise ValueError('INVALID_V11_RISK_AND_SIDE_LIMITS')
        if self.liquidity_v11_mode != 'OFF' and not self.evolution_enabled:
            raise ValueError('V11_CONTROL_REQUIRES_EVOLUTION')
        if self.liquidity_v11_mode != 'OFF' and self.quote_refresh_mode != 'OFF':
            raise ValueError('V11_CONTROL_CANNOT_COMBINE_WITH_V10_REFRESH')
        if not 0 <= self.min_strategy_hold_ticks <= 2000:
            raise ValueError('min_strategy_hold_ticks must be 0..2000')
        if any(type(v) is not bool for v in (self.allow_strategy_switch,
                self.allow_population_exit, self.allow_population_join,
                self.allow_retired_reentry)):
            raise ValueError('ablation flags must be booleans')
        if type(self.adaptation_enabled) is not bool:
            raise ValueError('adaptation_enabled must be boolean')
        if type(self.evolution_enabled) is not bool:
            raise ValueError('evolution_enabled must be boolean')
        if not isinstance(self.shock_enabled, bool):
            raise ValueError("shock_enabled must be boolean")
        if self.quote_refresh_mode != 'OFF' and not self.evolution_enabled:
            raise ValueError('quote_refresh_mode requires evolution_enabled')
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
        self.economy = SyntheticEconomy(self.book,
                                        maker_fee_bps=self.config.maker_fee_bps,
                                        taker_fee_bps=self.config.taker_fee_bps,
                                        bankruptcy_threshold_percent=self.config.bankruptcy_threshold_percent,
                                        stop_denials=self.config.stop_denials)
        self.population = (PopulationEvolution(
            self.economy, reserve_agents=self.config.waiting_agents,
            review_every=self.config.evolution_review_ticks,
            weak_reviews_to_exit=self.config.weak_reviews_to_exit,
            max_joins_per_review=self.config.max_joins_per_review,
            min_strategy_hold_ticks=self.config.min_strategy_hold_ticks,
            adaptation_enabled=self.config.adaptation_enabled,
            allow_switch=self.config.allow_strategy_switch,
            allow_exit=self.config.allow_population_exit,
            allow_join=self.config.allow_population_join,
            allow_retired_reentry=self.config.allow_retired_reentry,
            reentry_cooldown_ticks=self.config.reentry_cooldown_ticks)
            if self.config.evolution_enabled else None)
        self.maker_withheld = 0
        self.maker_widened = 0
        self.tick = 0
        self.price_history = [self.book.last_trade_ticks]
        self.candles: list[dict] = []
        self.event_log: list[dict] = []
        self.recent_trades: list[dict] = []
        self.trades = 0
        self.total_traded_qty = 0
        self.regime_cancelled_qty = 0
        self.liquidity_v11 = None
        if self.config.liquidity_v11_mode != 'OFF':
            from .liquidity_v11 import AdaptiveLiquidityController
            self.liquidity_v11 = AdaptiveLiquidityController(self)
        self.refresh_attempts = {'BUY': 0, 'SELL': 0}
        self.refresh_posted = {'BUY': 0, 'SELL': 0}
        self.refresh_quantity = {'BUY': 0, 'SELL': 0}
        self.refresh_no_eligible = {'BUY': 0, 'SELL': 0}
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
        if self.liquidity_v11 is not None:
            self.liquidity_v11.observe_fills(e.matched_orders, self.tick)
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
        accepted, budget = self.economy.market_budget(agent, side, qty)
        if accepted == 0:
            return Execution(side, qty, 0, qty, None, None, None, None, ())
        e = self.book.execute_market(side, accepted, cash_budget_ticks=budget,
                                     exclude_agent=agent,
                                     buyer_fee_bps=self.economy.taker_fee_bps if side == 'BUY' else 0)
        self.economy.settle(agent, side, e.matched_orders)
        self._record_execution(e, agent, agent_type)
        return e

    def _limit(self, side: Side, qty: int, agent: str, seq: int,
               anchor: int | None = None, wide: bool = False) -> None:
        # Place orders *passively*, observing the current opposite best quote.
        bid = self.book.best("BUY")
        ask = self.book.best("SELL")
        center = anchor if anchor is not None else self.book.last_trade_ticks
        center = max(5, center)
        dist = self.rng.randint(8, 20) if wide else self.rng.randint(1, 7)
        price = center - dist if side == "BUY" else center + dist
        if side == "BUY" and ask is not None:
            price = min(price, ask - 1)
        if side == "SELL" and bid is not None:
            price = max(price, bid + 1)
        price = max(1, price)
        if not self.economy.reserve(agent, side, price, qty):
            return
        try:
            self.book.add_limit(f"a-{self.tick}-{seq}-{side}", agent, side, price, qty)
        except Exception:
            self.economy.release(agent, side, price, qty)
            raise

    def _act(self, seq: int) -> None:
        # Draw from the fixed 1,000-ID universe, preserving the original RNG
        # event tape. Waitlisted / exited identities cannot place orders.
        agent_id = self.rng.randrange(1000)
        agent = f"A{agent_id:04d}"
        if self.population:
            if agent not in self.population.active:
                return
            kind = self.population.rule_for(agent)
        else:
            kind = ("NOISE" if agent_id % 10 < 5 else
                    "MAKER" if agent_id % 10 < 7 else
                    "TREND" if agent_id % 10 < 9 else "REVERSION")
        self.agent_actions[kind] += 1
        if not self.economy.can_trade(agent):
            return
        last = self.book.last_trade_ticks
        reference = self.price_history[max(0, len(self.price_history) - 8)]
        trend = last - reference
        qty = self.rng.randint(1, 5)

        if kind == "MAKER":
            if self.config.synthetic_regime == 'THIN' and self.rng.random() < 0.75:
                self.maker_withheld += 1
                return
            # Risk-averse quoting is a model assumption, not a forecast.
            caution = self.maker_cautious()
            if caution and self.rng.random() < 0.60:
                self.maker_withheld += 1
                return
            side: Side = "BUY" if self.rng.getrandbits(1) else "SELL"
            side = self._regime_side(side)
            if caution:
                self.maker_widened += 1
                self._limit(side, min(qty, 2), agent, seq, wide=True)
            else:
                self._limit(side, qty + 2, agent, seq)
        elif kind == "NOISE":
            side = "BUY" if self.rng.getrandbits(1) else "SELL"
            side = self._regime_side(side)
            if self.rng.random() < 0.72:
                self._market(side, qty, agent, kind)
            else:
                self._limit(side, qty, agent, seq)
        elif kind == "TREND":
            side = "BUY" if trend > 0 else "SELL" if trend < 0 else ("BUY" if self.rng.getrandbits(1) else "SELL")
            side = self._regime_side(side)
            if self.rng.random() < 0.75:
                self._market(side, qty, agent, kind)
            else:
                self._limit(side, qty, agent, seq)
        else:
            side = "SELL" if trend > 0 else "BUY" if trend < 0 else ("BUY" if self.rng.getrandbits(1) else "SELL")
            side = self._regime_side(side)
            if self.rng.random() < 0.70:
                self._market(side, qty, agent, kind)
            else:
                self._limit(side, qty, agent, seq)

    def _regime_side(self, side: Side) -> Side:
        """Synthetic demand imbalance, never used by legacy sessions."""
        regime = self.config.synthetic_regime
        if regime == 'LEGACY' or regime == 'THIN':
            return side
        if regime == 'BULL' and self.rng.random() < 0.75:
            return 'BUY'
        if regime == 'BEAR' and self.rng.random() < 0.75:
            return 'SELL'
        if regime == 'SIDEWAYS' and self.rng.random() < 0.75:
            return 'BUY' if self.book.last_trade_ticks < self.book.config.mid_ticks else 'SELL'
        return side

    def _quote_price(self, side: Side) -> int:
        """Passive price near last synthetic trade, never crosses the opposite side."""
        center = max(5, self.book.last_trade_ticks)
        price = max(1, center - 2) if side == 'BUY' else center + 2
        opposite = self.book.best('SELL' if side == 'BUY' else 'BUY')
        if opposite is not None:
            price = min(price, opposite - 1) if side == 'BUY' else max(price, opposite + 1)
        return max(1, price)

    def _refresh_quotes(self) -> None:
        """At most one real, fully reserved passive order on each thin side per tick.

        This is an explicitly opt-in behavioural policy of already active wallets.
        No privileged maker liquidity, gifts, debt, synthetic counterparties,
        transfer of assets, or replayed stopped accounts. No RNG draws: paired
        trajectories retain their original random-event stream until divergence.
        """
        mode = self.config.quote_refresh_mode
        if mode == 'OFF':
            return
        if self.population is None:
            raise AssertionError('QUOTE_REFRESH_REQUIRES_POPULATION')
        for side in ('BUY', 'SELL'):
            depth = self.book.depth(side, 8)
            need = self.config.quote_refresh_min_depth - depth
            if need <= 0:
                continue
            self.refresh_attempts[side] += 1
            price = self._quote_price(side)
            opposite = self.book.best('SELL' if side == 'BUY' else 'BUY')
            if opposite is not None and ((side == 'BUY' and price >= opposite) or
                                         (side == 'SELL' and price <= opposite)):
                self.refresh_no_eligible[side] += 1
                continue
            if mode == 'MAKER_ONLY':
                candidates = [a for a, m in self.population.active.items()
                              if m.strategy == 'MAKER']
            else:
                candidates = list(self.population.active)
            candidates.sort()
            if candidates:
                offset = (self.tick + (0 if side == 'BUY' else 97)) % len(candidates)
                candidates = candidates[offset:] + candidates[:offset]
            for agent in candidates:
                wallet = self.economy.wallets[agent]
                if not self.economy.can_trade(agent):
                    continue
                if side == 'BUY':
                    unit_cost = price + fee_unit(price, self.economy.maker_fee_bps)
                    maximum = wallet.free_cash // unit_cost
                else:
                    maximum = wallet.free_coin
                qty = min(3, need, maximum)
                if qty < 1:
                    continue
                # Never trigger reserve denials on insufficient inventory. They
                # are NOT free risk-stops and should not alter legacy policy.
                if not self.economy.reserve(agent, side, price, qty):
                    raise AssertionError('REFRESH_FUNDED_RESERVATION_FAILED')
                try:
                    self.book.add_limit(f'q-{self.tick}-{side}-{agent}',agent,side,price,qty)
                except Exception:
                    self.economy.release(agent, side, price, qty)
                    raise
                self.refresh_posted[side] += 1
                self.refresh_quantity[side] += qty
                break
            else:
                self.refresh_no_eligible[side] += 1

    def maker_cautious(self) -> bool:
        within_shock = (self.shock is not None and self.shock['enabled'] and
                        0 <= self.tick - self.config.shock_tick <= self.config.maker_caution_ticks)
        thin_book = self.book.depth('BUY', 8) < 12 or self.book.depth('SELL', 8) < 12
        return within_shock or thin_book

    def _ensure_candle(self) -> None:
        if self._candle is None:
            # Explicit zero-trade interval, flat at prior last trade; not
            # a fabricated trade or inferred real OHLCV.
            px = self.book.last_trade_ticks / 10
            self._candle = {"start": self.tick, "open": px, "high": px,
                            "low": px, "close": px, "volume": 0, "trades": 0}

    def step(self, *, capture_state: bool = True) -> dict | None:
        if self.tick >= self.config.ticks:
            return self.state() if capture_state else None
        self.tick += 1
        if (self.tick - 1) % self.config.candle_ticks == 0:
            self._candle = None
        if self.tick == self.config.shock_tick:
            pre_bid = self.book.best("BUY")
            pre_depth = self.book.depth("BUY", 8)
            removed = (self.economy.cancel_book_top(self.book, "BUY", 8,
                                                     self.config.cancel_percent / 100)
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

        if self.config.synthetic_regime == 'THIN' and self.tick % 80 == 0:
            # Remove synthetic resting depth; the economic module releases
            # corresponding owner reservations. No asset is destroyed.
            self.regime_cancelled_qty += self.economy.cancel_book_top(self.book, 'BUY', 8, 0.55)
            self.regime_cancelled_qty += self.economy.cancel_book_top(self.book, 'SELL', 8, 0.55)
        self._ensure_candle()
        self.price_history.append(self.book.last_trade_ticks)
        if self.tick % self.config.candle_ticks == 0 or self.tick == self.config.ticks:
            self.candles.append(dict(self._candle))
        self.economy.review_lifecycle(self.book, self.book.last_trade_ticks)
        if self.population:
            self.population.review(self.tick, self.book.last_trade_ticks, self.book)
            self._refresh_quotes()
            if self.liquidity_v11 is not None:
                self.liquidity_v11.step()
            if not self.population.check():
                raise AssertionError('POPULATION_INVARIANT_FAILED')
        if not self.economy.check(self.book):
            raise AssertionError('ECONOMY_INVARIANT_FAILED')
        if not self.book.check_conservation():
            raise AssertionError("LIMIT_QUANTITY_CONSERVATION_FAILED")
        if not self.economy.check(self.book):
            raise AssertionError("ECONOMY_CONSERVATION_FAILED")
        return self.state() if capture_state else None

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
                "economy": self.economy.snapshot(self.book.last_trade_ticks),
                "population": (self.population.snapshot(self.book.last_trade_ticks) if self.population
                               else {"enabled": False, "source": "SIMULATED"}),
                "regime_cancelled_qty": self.regime_cancelled_qty,
                "maker_risk": {"cautious": self.maker_cautious(),
                               "withheld_quotes": self.maker_withheld,
                               "widened_quotes": self.maker_widened},
                "conservation_pass": self.book.check_conservation(),
                "economy_conservation_pass": self.economy.check(self.book)}

    def fingerprint(self) -> str:
        payload = json.dumps(self.state(), sort_keys=True, separators=(",", ":"),
                             ensure_ascii=True).encode("utf-8")
        return sha256(payload).hexdigest()
