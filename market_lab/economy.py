"""Closed, synthetic spot economy: per-unit quote fees, FIFO PnL and lifecycle.

Integer price ticks are 0.1 fictional VCU; coin inventory is integral.
No credit, external money, exchange accounts or trading credentials exist.
An operational 'bankruptcy' is a configured equity cut-off, not insolvency.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from .simulation import OrderBook, Side


def fee_unit(price_ticks: int, fee_bps: int) -> int:
    """Round each per-coin fee up to one integer price tick (no float drift)."""
    if type(price_ticks) is not int or price_ticks <= 0:
        raise ValueError('INVALID_PRICE')
    if type(fee_bps) is not int or not 0 <= fee_bps <= 1000:
        raise ValueError('INVALID_FEE_RATE')
    return (price_ticks * fee_bps + 9999) // 10000


@dataclass
class Wallet:
    cash_ticks: int
    coin_qty: int
    reserved_cash_ticks: int = 0
    reserved_coin_qty: int = 0
    initial_equity_ticks: int = 0
    # (whole-unit acquisition cost in VCU ticks, quantity); FIFO lots.
    lots: deque[tuple[int, int]] = field(default_factory=deque)
    realized_pnl_ticks: int = 0
    fees_paid_ticks: int = 0
    traded_qty: int = 0
    denial_streak: int = 0
    status: str = 'ACTIVE'

    @property
    def free_cash(self) -> int:
        return self.cash_ticks - self.reserved_cash_ticks

    @property
    def free_coin(self) -> int:
        return self.coin_qty - self.reserved_coin_qty

    def consume_fifo(self, qty: int) -> int:
        if qty > self.coin_qty or qty <= 0:
            raise AssertionError('INSUFFICIENT_FIFO_INVENTORY')
        left, basis = qty, 0
        while left:
            px, available = self.lots.popleft()
            taken = min(left, available)
            basis += taken * px
            if taken < available:
                self.lots.appendleft((px, available - taken))
            left -= taken
        return basis

    def basis(self) -> int:
        return sum(px * q for px, q in self.lots)


class SyntheticEconomy:
    """Only component allowed to mutate accounts in the synthetic spot session."""

    def __init__(self, book: OrderBook, agent_count: int = 1000,
                 maker_fee_bps: int = 0, taker_fee_bps: int = 0,
                 bankruptcy_threshold_percent: int = 20,
                 stop_denials: int = 6) -> None:
        for v in (maker_fee_bps, taker_fee_bps):
            fee_unit(book.config.mid_ticks, v)
        if type(bankruptcy_threshold_percent) is not int or not 0 <= bankruptcy_threshold_percent <= 100:
            raise ValueError('INVALID_BANKRUPTCY_THRESHOLD')
        if type(stop_denials) is not int or not 1 <= stop_denials <= 100:
            raise ValueError('INVALID_STOP_DENIALS')
        self.maker_fee_bps = maker_fee_bps
        self.taker_fee_bps = taker_fee_bps
        self.bankruptcy_threshold_percent = bankruptcy_threshold_percent
        self.stop_denials = stop_denials
        mid = book.config.mid_ticks
        self.initial_mid_ticks = mid
        self.wallets: dict[str, Wallet] = {f'A{i:04d}': Wallet(250_000, 100)
                                           for i in range(agent_count)}
        self.wallets['MAKER'] = Wallet(4_000_000, 600)
        self.wallets['STRESS'] = Wallet(1_000_000, 600)
        self.wallets['EXCHANGE'] = Wallet(0, 0)
        for name, w in self.wallets.items():
            w.initial_equity_ticks = w.cash_ticks + w.coin_qty * mid
            if w.coin_qty:
                w.lots.append((mid, w.coin_qty))
        self.initial_cash_ticks = sum(w.cash_ticks for w in self.wallets.values())
        self.initial_coins = sum(w.coin_qty for w in self.wallets.values())
        self.denied_actions = 0
        self.clipped_actions = 0
        self.fills_settled = 0
        self.notional_ticks = 0
        self.maker_fee_ticks = 0
        self.taker_fee_ticks = 0
        self.bankrupt_events = 0
        self.stopped_events = 0
        for order in book.orders.values():
            if not self.reserve(order.agent, order.side, order.price_ticks, order.remaining):
                raise AssertionError('INITIAL_DEPTH_UNFUNDED')
        if not self.check(book):
            raise AssertionError('INITIAL_ACCOUNT_CONSERVATION_FAILED')

    def _denied(self, agent: str) -> None:
        self.denied_actions += 1
        wallet = self.wallets[agent]
        if agent == 'EXCHANGE' or wallet.status != 'ACTIVE':
            return
        wallet.denial_streak += 1
        if wallet.denial_streak >= self.stop_denials:
            wallet.status = 'STOPPED'
            self.stopped_events += 1

    def can_trade(self, agent: str) -> bool:
        return self.wallets[agent].status == 'ACTIVE' and agent != 'EXCHANGE'

    def reserve(self, agent: str, side: Side, price_ticks: int, qty: int) -> bool:
        if type(qty) is not int or qty <= 0 or price_ticks <= 0:
            raise ValueError('INVALID_RESERVATION')
        wallet = self.wallets[agent]
        if not self.can_trade(agent):
            self.denied_actions += 1
            return False
        if side == 'BUY':
            amount = (price_ticks + fee_unit(price_ticks, self.maker_fee_bps)) * qty
            if wallet.free_cash < amount:
                self._denied(agent)
                return False
            wallet.reserved_cash_ticks += amount
        elif side == 'SELL':
            if wallet.free_coin < qty:
                self._denied(agent)
                return False
            wallet.reserved_coin_qty += qty
        else:
            raise ValueError('INVALID_SIDE')
        wallet.denial_streak = 0
        return True

    def release(self, agent: str, side: Side, price_ticks: int, qty: int) -> None:
        wallet = self.wallets[agent]
        if side == 'BUY':
            wallet.reserved_cash_ticks -= (price_ticks + fee_unit(price_ticks, self.maker_fee_bps)) * qty
        elif side == 'SELL':
            wallet.reserved_coin_qty -= qty
        else:
            raise ValueError('INVALID_SIDE')
        if wallet.reserved_cash_ticks < 0 or wallet.reserved_coin_qty < 0:
            raise AssertionError('RESERVATION_UNDERFLOW')

    def market_budget(self, agent: str, side: Side, requested: int) -> tuple[int, int | None]:
        wallet = self.wallets[agent]
        if not self.can_trade(agent):
            self.denied_actions += 1
            return 0, None
        if side == 'SELL':
            accepted = min(requested, wallet.free_coin)
            budget = None
        elif side == 'BUY':
            accepted, budget = requested, wallet.free_cash
            if budget <= 0:
                accepted = 0
        else:
            raise ValueError('INVALID_SIDE')
        if accepted < requested:
            self.clipped_actions += 1
        if accepted == 0:
            self._denied(agent)
        else:
            wallet.denial_streak = 0
        return accepted, budget

    def settle(self, taker: str, side: Side,
               matched: tuple[tuple[str, str, int, int], ...]) -> None:
        """Matched prices are authoritative; fees move only to EXCHANGE."""
        for _order_id, maker, price, qty in matched:
            if maker == taker:
                raise AssertionError('SELF_TRADE_NOT_ALLOWED')
            buyer_id = taker if side == 'BUY' else maker
            seller_id = maker if side == 'BUY' else taker
            buyer, seller = self.wallets[buyer_id], self.wallets[seller_id]
            buy_fee_per_coin = fee_unit(price, self.taker_fee_bps if buyer_id == taker else self.maker_fee_bps)
            sell_fee_per_coin = fee_unit(price, self.taker_fee_bps if seller_id == taker else self.maker_fee_bps)
            buy_fee, sell_fee = buy_fee_per_coin * qty, sell_fee_per_coin * qty
            amount = price * qty
            if buyer.cash_ticks < amount + buy_fee or seller.coin_qty < qty:
                raise AssertionError('UNBACKED_FILL')
            self.release(maker, 'SELL' if side == 'BUY' else 'BUY', price, qty)
            # Fee is debited from buyer cash and from seller gross proceeds.
            buyer.cash_ticks -= amount + buy_fee
            buyer.coin_qty += qty
            buyer.lots.append((price + buy_fee_per_coin, qty))
            seller.cash_ticks += amount - sell_fee
            consumed_basis = seller.consume_fifo(qty)
            seller.coin_qty -= qty
            seller.realized_pnl_ticks += amount - sell_fee - consumed_basis
            self.wallets['EXCHANGE'].cash_ticks += buy_fee + sell_fee
            buyer.fees_paid_ticks += buy_fee
            seller.fees_paid_ticks += sell_fee
            buyer.traded_qty += qty
            seller.traded_qty += qty
            self.maker_fee_ticks += fee_unit(price, self.maker_fee_bps) * qty
            self.taker_fee_ticks += fee_unit(price, self.taker_fee_bps) * qty
            self.notional_ticks += amount
            self.fills_settled += 1

    def cancel_book_top(self, book: OrderBook, side: Side, levels: int, fraction: float) -> int:
        selected = {price for price, _ in book.levels(side, levels)}
        before = {oid: (o.agent, o.side, o.price_ticks, o.remaining)
                  for oid, o in book.orders.items() if o.side == side and o.price_ticks in selected}
        removed = book.cancel_top_fraction(side, levels, fraction)
        returned = 0
        for oid, (agent, direction, px, old_qty) in before.items():
            current_qty = book.orders[oid].remaining if oid in book.orders else 0
            cancelled = old_qty - current_qty
            if cancelled:
                self.release(agent, direction, px, cancelled)
            returned += cancelled
        if removed != returned:
            raise AssertionError('CANCEL_RESERVATION_MISMATCH')
        return removed

    def _cancel_agent_orders(self, book: OrderBook, agent: str) -> int:
        removed = 0
        for oid, order in list(book.orders.items()):
            if order.agent == agent:
                self.release(agent, order.side, order.price_ticks, order.remaining)
                book.total_cancelled_qty += order.remaining
                removed += order.remaining
                del book.orders[oid]
        return removed

    def review_lifecycle(self, book: OrderBook, price_ticks: int) -> None:
        """Operational risk stop, not borrowing, debt write-off, or liquidation."""
        for name, wallet in self.wallets.items():
            if name == 'EXCHANGE':
                continue
            if wallet.status == 'ACTIVE' and wallet.initial_equity_ticks > 0:
                equity = wallet.cash_ticks + wallet.coin_qty * price_ticks
                if 100 * equity <= self.bankruptcy_threshold_percent * wallet.initial_equity_ticks:
                    wallet.status = 'BANKRUPT'
                    self.bankrupt_events += 1
            if wallet.status != 'ACTIVE' and (wallet.reserved_cash_ticks or wallet.reserved_coin_qty):
                self._cancel_agent_orders(book, name)

    def check(self, book: OrderBook) -> bool:
        if (sum(w.cash_ticks for w in self.wallets.values()) != self.initial_cash_ticks
                or sum(w.coin_qty for w in self.wallets.values()) != self.initial_coins):
            return False
        if any(min(w.cash_ticks, w.coin_qty, w.free_cash, w.free_coin,
                   w.reserved_cash_ticks, w.reserved_coin_qty) < 0 for w in self.wallets.values()):
            return False
        if any(sum(qty for _, qty in w.lots) != w.coin_qty for w in self.wallets.values()):
            return False
        if self.wallets['EXCHANGE'].cash_ticks != self.maker_fee_ticks + self.taker_fee_ticks:
            return False
        booked_cash: dict[str, int] = {}
        booked_coins: dict[str, int] = {}
        for order in book.orders.values():
            if order.side == 'BUY':
                booked_cash[order.agent] = (booked_cash.get(order.agent, 0) +
                     (order.price_ticks + fee_unit(order.price_ticks, self.maker_fee_bps)) * order.remaining)
            else:
                booked_coins[order.agent] = booked_coins.get(order.agent, 0) + order.remaining
        return all(w.reserved_cash_ticks == booked_cash.get(agent, 0)
                   and w.reserved_coin_qty == booked_coins.get(agent, 0)
                   for agent, w in self.wallets.items())

    def snapshot(self, price_ticks: int) -> dict:
        participants = []
        activities = 0
        winning = losing = flat = traded = bankrupt = stopped = 0
        for agent, w in self.wallets.items():
            if agent == 'EXCHANGE':
                continue
            change = w.cash_ticks + w.coin_qty * price_ticks - w.initial_equity_ticks
            if change or w.reserved_cash_ticks or w.reserved_coin_qty:
                activities += 1
            if w.traded_qty:
                traded += 1
            if w.status == 'BANKRUPT':
                bankrupt += 1
            elif w.status == 'STOPPED':
                stopped += 1
            if change > 0:
                winning += 1
            elif change < 0:
                losing += 1
            else:
                flat += 1
            if agent not in ('MAKER', 'STRESS'):
                participants.append((change, agent, w))
        participants.sort(key=lambda x: (-x[0], x[1]))
        leaders = participants[:5]
        laggards = sorted(participants, key=lambda x: (x[0], x[1]))[:5]

        def wallet_state(agent: str, w: Wallet, pnl: int) -> dict:
            return {
                'agent': agent, 'status': w.status,
                'cash_vcu': round(w.cash_ticks / 10, 2),
                'coin_qty': w.coin_qty, 'free_coin_qty': w.free_coin,
                'locked_coin_qty': w.reserved_coin_qty,
                'free_cash_vcu': round(w.free_cash / 10, 2),
                'locked_cash_vcu': round(w.reserved_cash_ticks / 10, 2),
                'mark_pnl_vcu': round(pnl / 10, 2),
                'realized_pnl_vcu': round(w.realized_pnl_ticks / 10, 2),
                'unrealized_pnl_vcu': round((w.coin_qty * price_ticks - w.basis()) / 10, 2),
                'fees_paid_vcu': round(w.fees_paid_ticks / 10, 2),
                'traded_qty': w.traded_qty,
            }

        total_cash = self.initial_cash_ticks
        total_mark = sum(w.cash_ticks + w.coin_qty * price_ticks - w.initial_equity_ticks
                         for w in self.wallets.values())
        investor_realized = sum(w.realized_pnl_ticks for name, w in self.wallets.items() if name != 'EXCHANGE')
        investor_unrealized = sum(w.coin_qty * price_ticks - w.basis()
                                  for name, w in self.wallets.items() if name != 'EXCHANGE')
        return {
            'wallet_count': len(self.wallets) - 1,
            'fee_account_count': 1,
            'active_wallets': activities,
            'traded_wallets': traded,
            'winners': winning, 'losers': losing, 'unchanged': flat,
            'bankrupt': bankrupt, 'stopped': stopped, 'trading_active': len(self.wallets)-1-bankrupt-stopped,
            'bankrupt_events': self.bankrupt_events, 'stopped_events': self.stopped_events,
            'total_cash_vcu': total_cash / 10,
            'total_coins': self.initial_coins,
            'denied_actions': self.denied_actions, 'clipped_actions': self.clipped_actions,
            'fills_settled': self.fills_settled,
            'traded_quote_vcu': self.notional_ticks / 10,
            'maker_fee_bps': self.maker_fee_bps, 'taker_fee_bps': self.taker_fee_bps,
            'fees_total_vcu': round(self.wallets['EXCHANGE'].cash_ticks / 10, 2),
            'maker_fees_vcu': round(self.maker_fee_ticks / 10, 2),
            'taker_fees_vcu': round(self.taker_fee_ticks / 10, 2),
            'total_realized_pnl_vcu': round(investor_realized / 10, 2),
            'total_unrealized_pnl_vcu': round(investor_unrealized / 10, 2),
            'total_mark_pnl_vcu': round(total_mark / 10, 2),
            'mark_pnl_identity_pass': total_mark == self.initial_coins * (price_ticks - self.initial_mid_ticks),
            'pnl_identity_pass': investor_realized + investor_unrealized == total_mark - self.wallets['EXCHANGE'].cash_ticks,
            'maker': wallet_state('MAKER', self.wallets['MAKER'],
                                  self.wallets['MAKER'].cash_ticks + self.wallets['MAKER'].coin_qty * price_ticks
                                  - self.wallets['MAKER'].initial_equity_ticks),
            'leaders': [wallet_state(a, w, pnl) for pnl, a, w in leaders],
            'laggards': [wallet_state(a, w, pnl) for pnl, a, w in laggards],
            'accounting': 'FIFO_INTEGER_TICKS_VIRTUAL_ONLY',
            'bankruptcy_rule': f'equity <= {self.bankruptcy_threshold_percent}% initial',
        }
