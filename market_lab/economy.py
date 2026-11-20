"""Closed-system synthetic accounting for the interactive toy exchange.

Integer quote ticks (0.1 VCU) and whole VCOIN units; no real balances,
credentials, borrowing, fees or external services. Not a trading ledger.
"""
from __future__ import annotations

from dataclasses import dataclass

from .simulation import OrderBook, Resting, Side


@dataclass
class Wallet:
    """Owned assets including amounts locked for passive orders."""

    cash_ticks: int
    coin_qty: int
    reserved_cash_ticks: int = 0
    reserved_coin_qty: int = 0
    initial_equity_ticks: int = 0

    @property
    def free_cash(self) -> int:
        return self.cash_ticks - self.reserved_cash_ticks

    @property
    def free_coin(self) -> int:
        return self.coin_qty - self.reserved_coin_qty


class SyntheticEconomy:
    """Sole owner of synthetic wallet mutations and quote/asset invariants."""

    def __init__(self, book: OrderBook, agent_count: int = 1000) -> None:
        mid = book.config.mid_ticks
        self.initial_mid_ticks = mid
        self.wallets: dict[str, Wallet] = {
            f'A{i:04d}': Wallet(250_000, 100) for i in range(agent_count)
        }
        self.wallets['MAKER'] = Wallet(4_000_000, 600)
        self.wallets['STRESS'] = Wallet(1_000_000, 600)
        for w in self.wallets.values():
            w.initial_equity_ticks = w.cash_ticks + w.coin_qty * mid
        self.initial_cash_ticks = sum(w.cash_ticks for w in self.wallets.values())
        self.initial_coins = sum(w.coin_qty for w in self.wallets.values())
        self.denied_actions = 0
        self.clipped_actions = 0
        self.fills_settled = 0
        self.notional_ticks = 0
        # Existing initial BUY and SELL depth is backed by MAKER's wallet.
        for order in book.orders.values():
            if not self.reserve(order.agent, order.side, order.price_ticks, order.remaining):
                raise AssertionError('INITIAL_DEPTH_UNFUNDED')
        if not self.check(book):
            raise AssertionError('INITIAL_ACCOUNT_CONSERVATION_FAILED')

    def reserve(self, agent: str, side: Side, price_ticks: int, qty: int) -> bool:
        if type(qty) is not int or qty <= 0 or price_ticks <= 0:
            raise ValueError('INVALID_RESERVATION')
        wallet = self.wallets[agent]
        if side == 'BUY':
            amount = price_ticks * qty
            if wallet.free_cash < amount:
                self.denied_actions += 1
                return False
            wallet.reserved_cash_ticks += amount
        elif side == 'SELL':
            if wallet.free_coin < qty:
                self.denied_actions += 1
                return False
            wallet.reserved_coin_qty += qty
        else:
            raise ValueError('INVALID_SIDE')
        return True

    def release(self, agent: str, side: Side, price_ticks: int, qty: int) -> None:
        wallet = self.wallets[agent]
        if side == 'BUY':
            wallet.reserved_cash_ticks -= price_ticks * qty
        elif side == 'SELL':
            wallet.reserved_coin_qty -= qty
        else:
            raise ValueError('INVALID_SIDE')
        if wallet.reserved_cash_ticks < 0 or wallet.reserved_coin_qty < 0:
            raise AssertionError('RESERVATION_UNDERFLOW')

    def market_budget(self, agent: str, side: Side, requested: int) -> tuple[int, int | None]:
        """Return order qty and optional quote budget, without creating credit."""
        wallet = self.wallets[agent]
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
            self.denied_actions += 1
        return accepted, budget

    def settle(self, taker: str, side: Side, matched: tuple[tuple[str, str, int, int], ...]) -> None:
        """Transfer actual assets to both counterparties per executed resting order."""
        for _order_id, maker, price_ticks, qty in matched:
            if maker == taker:
                raise AssertionError('SELF_TRADE_NOT_ALLOWED')
            buyer = self.wallets[taker if side == 'BUY' else maker]
            seller = self.wallets[maker if side == 'BUY' else taker]
            amount = price_ticks * qty
            if buyer.cash_ticks < amount or seller.coin_qty < qty:
                raise AssertionError('UNBACKED_FILL')
            # Release the resting maker's locked funds/asset, never the taker's.
            self.release(maker, 'SELL' if side == 'BUY' else 'BUY', price_ticks, qty)
            buyer.cash_ticks -= amount
            buyer.coin_qty += qty
            seller.cash_ticks += amount
            seller.coin_qty -= qty
            self.notional_ticks += amount
            self.fills_settled += 1

    def cancel_book_top(self, book: OrderBook, side: Side, levels: int, fraction: float) -> int:
        """Return reserves for the exact order quantities cancelled by the book."""
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

    def check(self, book: OrderBook) -> bool:
        if (sum(w.cash_ticks for w in self.wallets.values()) != self.initial_cash_ticks
                or sum(w.coin_qty for w in self.wallets.values()) != self.initial_coins):
            return False
        if any(min(w.cash_ticks, w.coin_qty, w.free_cash, w.free_coin,
                   w.reserved_cash_ticks, w.reserved_coin_qty) < 0 for w in self.wallets.values()):
            return False
        booked_cash: dict[str, int] = {}
        booked_coins: dict[str, int] = {}
        for order in book.orders.values():
            if order.side == 'BUY':
                booked_cash[order.agent] = booked_cash.get(order.agent, 0) + order.price_ticks * order.remaining
            else:
                booked_coins[order.agent] = booked_coins.get(order.agent, 0) + order.remaining
        return all(w.reserved_cash_ticks == booked_cash.get(agent, 0)
                   and w.reserved_coin_qty == booked_coins.get(agent, 0)
                   for agent, w in self.wallets.items())

    def snapshot(self, price_ticks: int) -> dict:
        participants = []
        active = 0
        for agent, w in self.wallets.items():
            change = w.cash_ticks + w.coin_qty * price_ticks - w.initial_equity_ticks
            if change or w.reserved_cash_ticks or w.reserved_coin_qty:
                active += 1
            if agent not in ('MAKER', 'STRESS'):
                participants.append((change, agent, w))
        participants.sort(key=lambda t: (-t[0], t[1]))
        leaders = participants[:5]
        laggards = sorted(participants, key=lambda t: (t[0], t[1]))[:5]
        def wallet_state(agent: str, w: Wallet, pnl: int) -> dict:
            return {'agent': agent, 'cash_vcu': round(w.cash_ticks / 10, 2),
                    'coin_qty': w.coin_qty, 'free_coin_qty': w.free_coin,
                    'locked_coin_qty': w.reserved_coin_qty,
                    'free_cash_vcu': round(w.free_cash / 10, 2),
                    'locked_cash_vcu': round(w.reserved_cash_ticks / 10, 2),
                    'mark_pnl_vcu': round(pnl / 10, 2)}
        return {
            'wallet_count': len(self.wallets), 'active_wallets': active,
            'total_cash_vcu': self.initial_cash_ticks / 10,
            'total_coins': self.initial_coins,
            'denied_actions': self.denied_actions, 'clipped_actions': self.clipped_actions,
            'fills_settled': self.fills_settled,
            'traded_quote_vcu': self.notional_ticks / 10,
            'total_mark_pnl_vcu': round(sum(w.cash_ticks + w.coin_qty * price_ticks - w.initial_equity_ticks
                                           for w in self.wallets.values()) / 10, 2),
            'mark_pnl_identity_pass': (sum(w.cash_ticks + w.coin_qty * price_ticks - w.initial_equity_ticks
                                             for w in self.wallets.values())
                                        == self.initial_coins * (price_ticks - self.initial_mid_ticks)),
            'maker': wallet_state('MAKER', self.wallets['MAKER'],
                                  self.wallets['MAKER'].cash_ticks + self.wallets['MAKER'].coin_qty * price_ticks
                                  - self.wallets['MAKER'].initial_equity_ticks),
            'leaders': [wallet_state(a, w, pnl) for pnl, a, w in leaders],
            'laggards': [wallet_state(a, w, pnl) for pnl, a, w in laggards],
            'accounting': 'INTEGER_TICKS_VIRTUAL_ONLY',
        }
