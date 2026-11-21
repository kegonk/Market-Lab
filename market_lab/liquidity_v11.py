"""Opt-in, offline, inventory-backed synthetic quote controller (v1.1).

This is NOT an exchange market maker. No minting or funding injection exists.
Every synthetic limit is reserved by the same economy and order book as v1.0.
All tracking is observational; no claims of causal trader alpha.
"""
from __future__ import annotations

from collections import Counter

from .economy import fee_unit


class AdaptiveLiquidityController:
    """Independent BID/ASK target and wallet-risk-capped quoting; deterministic."""

    SIDES = ('BUY', 'SELL')

    def __init__(self, market):
        self.m = market
        self.placed = Counter()
        self.quantity = Counter()
        self.attempts = Counter()
        self.no_inventory = Counter()
        self.risk_capped = Counter()
        self.no_makers = Counter()
        self.filled_qty = Counter()
        self.cancelled_qty = Counter()
        self.filled_orders = Counter()
        self.cancelled_orders = Counter()
        self.closed_orders = Counter()
        self.age_qty_ticks = Counter()
        self.active: dict[str, dict] = {}
        self.participants: set[str] = set()
        self.total_fee_ticks = 0
        self.max_open_orders = 0
        self.depth_before_sum = Counter()
        self.depth_after_sum = Counter()
        self.controller_calls = 0
        self.closed_details: list[dict] = []

    def observe_fills(self, matched, tick: int) -> None:
        """Settlement already performed by the economy, only observe fills."""
        for order_id, _owner, px, qty in matched:
            rec = self.active.get(order_id)
            if rec is None:
                continue
            if rec['remaining'] < qty or rec['price_ticks'] != px:
                raise AssertionError('CONTROL_FILL_TRACKING_ERROR')
            rec['remaining'] -= qty
            rec['filled_qty'] += qty
            self.filled_qty[rec['side']] += qty
            self.total_fee_ticks += fee_unit(px, self.m.economy.maker_fee_bps) * qty

    def _settle_lifetimes(self) -> None:
        """Detect cancellations after lifecycle review and synthetic shocks.

        Fills are recorded at execution time; any other observed reduction is
        cancelled (risk stop, policy exit, shock, or regime withdrawal).
        """
        for oid, rec in list(self.active.items()):
            resting = self.m.book.orders.get(oid)
            current = resting.remaining if resting else 0
            if current > rec['remaining']:
                raise AssertionError('CONTROL_ORDER_QUANTITY_INCREASED')
            if current < rec['remaining']:
                qty = rec['remaining'] - current
                rec['cancelled_qty'] += qty
                self.cancelled_qty[rec['side']] += qty
            rec['remaining'] = current
            if current == 0:
                rec['closed_tick'] = self.m.tick
                age = self.m.tick - rec['placed_tick']
                self.age_qty_ticks[rec['side']] += age * rec['qty']
                self.closed_orders[rec['side']] += 1
                if rec['filled_qty']:
                    self.filled_orders[rec['side']] += 1
                if rec['cancelled_qty']:
                    self.cancelled_orders[rec['side']] += 1
                self.closed_details.append({k: rec[k] for k in (
                    'side','owner','placed_tick','closed_tick','price_ticks',
                    'qty','filled_qty','cancelled_qty')})
                del self.active[oid]

    def _target(self, side: str, own_depth: int, other_depth: int) -> int:
        cfg = self.m.config
        target = cfg.liquidity_v11_bid_target if side == 'BUY' else cfg.liquidity_v11_ask_target
        if cfg.liquidity_v11_mode == 'SIDE_AWARE':
            # Intentionally bounded: increase ONLY the under-supplied side.
            if own_depth < target and other_depth >= 2 * max(1, own_depth):
                target = min(2 * target, 50)
        return target

    def _fundable_qty(self, agent: str, side: str, px: int, need: int) -> tuple[int, str]:
        m = self.m
        w = m.economy.wallets[agent]
        unit = px + fee_unit(px, m.economy.maker_fee_bps) if side == 'BUY' else px
        inventory = w.free_cash // unit if side == 'BUY' else w.free_coin
        if inventory < 1:
            return 0, 'NO_INVENTORY'
        mark = m.book.last_trade_ticks
        equity = w.cash_ticks + w.coin_qty * mark
        # Risk cap counts both existing ordinary and controller order reserves.
        outstanding = w.reserved_cash_ticks + w.reserved_coin_qty * mark
        cap = equity * m.config.liquidity_v11_wallet_cap_percent // 100
        available = max(0, cap - outstanding)
        cap_qty = available // unit
        if cap_qty < 1:
            return 0, 'RISK_CAP'
        return min(need,inventory,cap_qty,m.config.liquidity_v11_max_order_qty), 'OK'

    def _quote(self, side: str, need: int, seq: int) -> bool:
        m = self.m
        cfg = m.config
        px = m._quote_price(side)
        opposite = m.book.best('SELL' if side == 'BUY' else 'BUY')
        if opposite is not None and ((side == 'BUY' and px >= opposite) or
                                     (side == 'SELL' and px <= opposite)):
            self.no_makers[side] += 1
            return False
        agents = sorted(a for a,member in m.population.active.items()
                        if member.strategy == 'MAKER' and m.economy.can_trade(a))
        if not agents:
            self.no_makers[side] += 1
            return False
        start = (m.tick * 7 + (97 if side == 'SELL' else 0) + seq) % len(agents)
        agents = agents[start:] + agents[:start]
        saw_inventory_failure = False
        saw_risk_cap = False
        for agent in agents:
            if sum(v['owner'] == agent for v in self.active.values()) >= cfg.liquidity_v11_max_orders_per_agent:
                saw_risk_cap = True
                continue
            qty,reason = self._fundable_qty(agent, side, px, need)
            if qty == 0:
                saw_inventory_failure |= reason == 'NO_INVENTORY'
                saw_risk_cap |= reason == 'RISK_CAP'
                continue
            # Existing reserve() is authoritative; no fabricated liquidity.
            if not m.economy.reserve(agent, side, px, qty):
                raise AssertionError('CONTROL_FUNDED_RESERVATION_FAILED')
            oid = f'v11-{m.tick}-{side}-{seq}-{agent}'
            try:
                m.book.add_limit(oid, agent, side, px, qty)
            except Exception:
                m.economy.release(agent, side, px, qty)
                raise
            self.active[oid] = {'side':side,'owner':agent,'placed_tick':m.tick,
                                'price_ticks':px,'qty':qty,'filled_qty':0,
                                'cancelled_qty':0,'remaining':qty,'closed_tick':None}
            self.participants.add(agent)
            self.placed[side] += 1
            self.quantity[side] += qty
            self.max_open_orders = max(self.max_open_orders,len(self.active))
            return True
        if saw_inventory_failure:
            self.no_inventory[side] += 1
        if saw_risk_cap:
            self.risk_capped[side] += 1
        return False

    def step(self) -> None:
        m = self.m
        self._settle_lifetimes()
        self.controller_calls += 1
        # Both targets use the SAME pre-control book state. Otherwise placing
        # BUY first would create a spurious imbalance that changes the SELL
        # target (an arbitrary iteration-order artefact).
        starting_depth = {side:m.book.depth(side,8) for side in self.SIDES}
        targets = {side:self._target(side,starting_depth[side],
                   starting_depth['SELL' if side == 'BUY' else 'BUY'])
                   for side in self.SIDES}
        for side in self.SIDES:
            depth = starting_depth[side]
            self.depth_before_sum[side] += depth
            target = targets[side]
            for seq in range(m.config.liquidity_v11_max_orders_per_side_tick):
                need = target - m.book.depth(side,8)
                if need <= 0:
                    break
                self.attempts[side] += 1
                if not self._quote(side,need,seq):
                    break
            self.depth_after_sum[side] += m.book.depth(side,8)

    def snapshot(self) -> dict:
        result = {
            'calls':self.controller_calls,
            'max_open_orders':self.max_open_orders,
            'currently_open_orders':len(self.active),
            'maker_fee_vcu':round(self.total_fee_ticks/10,5),
            'distinct_participating_makers':len(self.participants),
        }
        for side in self.SIDES:
            key = 'bid' if side == 'BUY' else 'ask'
            for name,counter in (('placed',self.placed),('placed_qty',self.quantity),
                                 ('attempts',self.attempts),('no_inventory',self.no_inventory),
                                 ('risk_capped',self.risk_capped),('no_makers',self.no_makers),
                                 ('filled_qty',self.filled_qty),('cancelled_qty',self.cancelled_qty),
                                 ('fully_or_partly_filled_orders',self.filled_orders),
                                 ('cancelled_orders',self.cancelled_orders),
                                 ('closed_orders',self.closed_orders)):
                result[f'{key}_{name}'] = counter[side]
            count=self.closed_orders[side]
            result[f'{key}_mean_closed_lifetime_ticks'] = round(
                sum((x['closed_tick']-x['placed_tick']) for x in self.closed_details if x['side']==side)/count,5
            ) if count else 0.0
            result[f'{key}_mean_pre_depth'] = round(self.depth_before_sum[side]/self.controller_calls,5) if self.controller_calls else 0.
            result[f'{key}_mean_post_depth'] = round(self.depth_after_sum[side]/self.controller_calls,5) if self.controller_calls else 0.
            result[f'{key}_remaining_qty'] = sum(v['remaining'] for v in self.active.values() if v['side']==side)
            if result[f'{key}_placed_qty'] != (result[f'{key}_filled_qty'] + result[f'{key}_cancelled_qty'] + result[f'{key}_remaining_qty']):
                raise AssertionError('CONTROL_ORDER_LEDGER_QUANTITY_MISMATCH')
        return result
