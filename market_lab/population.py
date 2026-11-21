"""Deterministic, synthetic-only agent population and rule adaptation.

All accounts are pre-funded by SyntheticEconomy at session start. A waiting
agent has an existing wallet but has not yet been admitted to trading. Exit
never burns assets, and admission never creates money, coins or new wallets.

This is a deliberately simple evolutionary *heuristic*, not machine learning
or evidence of market predictability.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

from .economy import SyntheticEconomy
from .simulation import OrderBook

STRATEGIES = ('NOISE', 'MAKER', 'TREND', 'REVERSION')


def original_strategy(agent_id: str) -> str:
    idx = int(agent_id[1:]) % 10
    return 'NOISE' if idx < 5 else 'MAKER' if idx < 7 else 'TREND' if idx < 9 else 'REVERSION'


@dataclass
class Member:
    agent_id: str
    strategy: str
    entered_at: int
    admission_cash: int
    admission_coins: int
    traded_qty_at_admission: int
    weak_reviews: int = 0
    strategy_changes: int = 0
    best_relative_pnl_ticks: int = 0
    last_reviewed_traded_qty: int = 0
    initial_strategy: str = ''
    last_strategy_change_tick: int = 0


class PopulationEvolution:
    """Switch weak-performing rules; retire agents; admit pre-funded reserves."""

    def __init__(self, economy: SyntheticEconomy, *, reserve_agents: int = 100,
                 review_every: int = 20, weak_reviews_to_exit: int = 3,
                 max_joins_per_review: int = 10,
                 min_strategy_hold_ticks: int = 0,
                 adaptation_enabled: bool = True) -> None:
        if not 0 <= reserve_agents <= 500:
            raise ValueError('reserve_agents must be 0..500')
        if not 5 <= review_every <= 500:
            raise ValueError('review_every must be 5..500')
        if not 2 <= weak_reviews_to_exit <= 20:
            raise ValueError('weak_reviews_to_exit must be 2..20')
        if not 1 <= max_joins_per_review <= 100:
            raise ValueError('max_joins_per_review must be 1..100')
        if type(min_strategy_hold_ticks) is not int or not 0 <= min_strategy_hold_ticks <= 2000:
            raise ValueError('min_strategy_hold_ticks must be 0..2000')
        if type(adaptation_enabled) is not bool:
            raise ValueError('adaptation_enabled must be boolean')
        self.min_strategy_hold_ticks = min_strategy_hold_ticks
        self.adaptation_enabled = adaptation_enabled
        self.blocked_switches = 0
        self.switches_by_original_strategy = {s: 0 for s in STRATEGIES}
        self.economy = economy
        self.review_every = review_every
        self.weak_reviews_to_exit = weak_reviews_to_exit
        self.max_joins_per_review = max_joins_per_review
        agents = sorted(a for a in economy.wallets if a.startswith('A') and a[1:].isdigit())
        if reserve_agents > len(agents):
            raise ValueError('reserve_agents exceed available synthetic agents')
        active_ids, waiting_ids = (agents[:-reserve_agents], agents[-reserve_agents:]) if reserve_agents else (agents, [])
        self.initial_active_ids = frozenset(active_ids)
        self.initial_cohorts = {s: sum(original_strategy(a) == s for a in active_ids)
                                for s in STRATEGIES}
        self.initial_wallets = {a: (economy.wallets[a].cash_ticks,
                                    economy.wallets[a].coin_qty) for a in agents}
        self.active: dict[str, Member] = {}
        self.waiting = deque(waiting_ids)
        self.retired: dict[str, dict] = {}
        self.ineligible_reserves: set[str] = set()
        self.events: list[dict] = []
        self.switches = 0
        self.joins = 0
        self.exits = 0
        self.positive_reviews = 0
        self.negative_reviews = 0
        self.review_count = 0
        self.active_agent_tick_exposure = 0
        self._all_ids = set(agents)
        for agent in active_ids:
            self.active[agent] = self._member(agent, 0)

    def _member(self, agent_id: str, tick: int) -> Member:
        w = self.economy.wallets[agent_id]
        strategy = original_strategy(agent_id)
        return Member(agent_id, strategy, tick,
                      w.cash_ticks, w.coin_qty, w.traded_qty,
                      last_reviewed_traded_qty=w.traded_qty,
                      initial_strategy=strategy,
                      last_strategy_change_tick=tick)

    def _log(self, tick: int, kind: str, agent: str, note: str) -> None:
        self.events.append({'tick': tick, 'kind': kind, 'agent': agent, 'note': note})
        if len(self.events) > 40:
            self.events.pop(0)

    def eligible_ids(self) -> tuple[str, ...]:
        return tuple(self.active)

    def rule_for(self, agent_id: str) -> str:
        return self.active[agent_id].strategy

    def relative_pnl(self, member: Member, price_ticks: int) -> int:
        """Agent equity minus a buy-and-hold benchmark at admission price.

        For identical initial holdings, changes in market price by themselves
        do not penalize a participant. Fees and post-entry trading do.
        """
        w = self.economy.wallets[member.agent_id]
        passive_equity = member.admission_cash + member.admission_coins * price_ticks
        return w.cash_ticks + w.coin_qty * price_ticks - passive_equity

    def _exit(self, agent: str, tick: int, price_ticks: int, book: OrderBook,
              reason: str) -> None:
        member = self.active.pop(agent)
        wallet = self.economy.wallets[agent]
        score = self.relative_pnl(member, price_ticks)
        # The economic account remains in the closed universe with its holdings.
        if wallet.status == 'ACTIVE':
            wallet.status = 'STOPPED'
            self.economy.stopped_events += 1
        self.economy._cancel_agent_orders(book, agent)
        self.retired[agent] = {'agent': agent, 'tick': tick, 'reason': reason,
                               'strategy': member.strategy,
                               'relative_pnl_vcu': round(score / 10, 2),
                               'cash_vcu': wallet.cash_ticks / 10,
                               'coin_qty': wallet.coin_qty}
        self.exits += 1
        self._log(tick, 'EXIT', agent, reason)

    def _admit_replacements(self, tick: int) -> None:
        # Each newcomer is ALREADY funded at initialization. No stipend, mint,
        # wealth transfer, wallet creation or forced reactivation is performed.
        needed = min(self.max_joins_per_review, max(0, self.exits - self.joins))
        admitted = 0
        while self.waiting and admitted < needed:
            agent = self.waiting.popleft()
            if not self.economy.can_trade(agent):
                self.ineligible_reserves.add(agent)
                self._log(tick, 'SKIP', agent, 'Reserve wallet no longer eligible')
                continue
            self.active[agent] = self._member(agent, tick)
            self.joins += 1
            admitted += 1
            self._log(tick, 'JOIN', agent, 'Pre-funded reserve participant admitted')

    def review(self, tick: int, price_ticks: int, book: OrderBook) -> None:
        # Risk stops and bankruptcies always override population decisions.
        for agent in tuple(self.active):
            if not self.economy.can_trade(agent):
                self._exit(agent, tick, price_ticks, book,
                           f'Risk status {self.economy.wallets[agent].status}')

        # Aggregate pre-review active exposure once per simulation tick.
        # Used to normalize switching frequency as population size declines.
        self.active_agent_tick_exposure += len(self.active)
        if tick % self.review_every:
            return
        self.review_count += 1
        # Frozen control keeps identical initial wallets and eligible agents,
        # but never adaptively switches or replaces agents. Risk stops still apply.
        if not self.adaptation_enabled:
            return
        # Peer performance is independently measured before changing rules.
        samples: dict[str, list[int]] = {s: [] for s in STRATEGIES}
        for member in self.active.values():
            w = self.economy.wallets[member.agent_id]
            if w.traded_qty > member.traded_qty_at_admission:
                samples[member.strategy].append(self.relative_pnl(member, price_ticks))
        means = {rule: sum(v) / len(v) if v else float('-inf') for rule, v in samples.items()}
        ranks = sorted(STRATEGIES, key=lambda s: (-means[s], STRATEGIES.index(s)))
        for agent in tuple(self.active):
            member = self.active[agent]
            w = self.economy.wallets[agent]
            # Never penalize a trader repeatedly for an old, unchanged
            # result: each weak review requires at least one *new* fill.
            if w.traded_qty <= member.last_reviewed_traded_qty:
                continue
            member.last_reviewed_traded_qty = w.traded_qty
            score = self.relative_pnl(member, price_ticks)
            member.best_relative_pnl_ticks = max(member.best_relative_pnl_ticks, score)
            if score < 0:
                self.negative_reviews += 1
                member.weak_reviews += 1
                if member.weak_reviews >= self.weak_reviews_to_exit:
                    self._exit(agent, tick, price_ticks, book, 'Sustained underperformance vs passive holdings')
                    continue
                replacement = next((s for s in ranks if s != member.strategy and means[s] != float('-inf')), None)
                if replacement is None:
                    replacement = STRATEGIES[(STRATEGIES.index(member.strategy) + 1) % len(STRATEGIES)]
                if tick - member.last_strategy_change_tick < self.min_strategy_hold_ticks:
                    self.blocked_switches += 1
                    continue
                old = member.strategy
                member.strategy = replacement
                member.last_strategy_change_tick = tick
                self.switches_by_original_strategy[member.initial_strategy] += 1
                member.strategy_changes += 1
                self.switches += 1
                self._log(tick, 'SWITCH', agent, f'{old} → {replacement}: relative return below zero')
            elif score > 0:
                self.positive_reviews += 1
                member.weak_reviews = 0
        self._admit_replacements(tick)

    def check(self) -> bool:
        active, waiting, retired = set(self.active), set(self.waiting), set(self.retired)
        if len(active) != len(self.active) or len(waiting) != len(self.waiting):
            return False
        groups = (active, waiting, retired, self.ineligible_reserves)
        for i, group in enumerate(groups):
            if any(group & other for other in groups[i+1:]):
                return False
        # Risk-invalid reserves keep their pre-funded wallet even when their
        # entry is skipped; every original identity remains accounted for.
        if set().union(*groups) != self._all_ids:
            return False
        if self.joins > self.exits or self.exits != len(self.retired):
            return False
        return all(self.economy.can_trade(a) for a in self.active)

    def relative_metrics(self, price_ticks: int) -> dict:
        """End-of-run cohort-inclusive, after-fee return vs each wallet's HOLD.

        Includes waiting and retired pre-funded participants (no survivor bias).
        The benchmark holds the original cash and coin quantities at the *same*
        current modeled price for the given arm, so market appreciation alone
        cannot make a strategy appear profitable.
        """
        scores = []
        traded = []
        for agent, (cash0, coins0) in self.initial_wallets.items():
            wallet = self.economy.wallets[agent]
            value = wallet.cash_ticks + wallet.coin_qty * price_ticks
            benchmark = cash0 + coins0 * price_ticks
            score = value - benchmark
            scores.append(score)
            if wallet.traded_qty:
                traded.append(score)
        total = sum(scores)
        return {'all_agents': len(scores),
                'traded_agents': len(traded),
                'total_relative_vcu': round(total / 10, 2),
                'mean_relative_vcu': round(total / (10 * len(scores)), 5),
                'mean_traded_relative_vcu': round(sum(traded) / (10 * len(traded)), 5) if traded else 0.0,
                'ahead': sum(x > 0 for x in scores),
                'behind': sum(x < 0 for x in scores),
                'equal': sum(x == 0 for x in scores)}

    def snapshot(self, price_ticks: int) -> dict:
        counts = {rule: 0 for rule in STRATEGIES}
        positive = negative = even = 0
        for m in self.active.values():
            counts[m.strategy] += 1
            score = self.relative_pnl(m, price_ticks)
            if score > 0:
                positive += 1
            elif score < 0:
                negative += 1
            else:
                even += 1
        cohort_survival = {
            rule: {
                'initial': self.initial_cohorts[rule],
                'surviving': sum(a in self.active for a in self.initial_active_ids
                                 if original_strategy(a) == rule),
                'survival_percent': round(100 * sum(a in self.active for a in self.initial_active_ids
                                                    if original_strategy(a) == rule) /
                                          self.initial_cohorts[rule], 2) if self.initial_cohorts[rule] else None,
                'switches': self.switches_by_original_strategy[rule],
            } for rule in STRATEGIES
        }
        return {'enabled': True, 'active': len(self.active),
                'adaptation_enabled': self.adaptation_enabled,
                'min_strategy_hold_ticks': self.min_strategy_hold_ticks,
                'blocked_switches': self.blocked_switches,
                'active_agent_tick_exposure': self.active_agent_tick_exposure,
                'switches_per_1000_agent_ticks': round(
                    1000 * self.switches / self.active_agent_tick_exposure, 5)
                    if self.active_agent_tick_exposure else 0.0,
                'initial_cohort_survival': cohort_survival,
                'relative_to_hold': self.relative_metrics(price_ticks),
                'waiting': len(self.waiting), 'ineligible_reserves': len(self.ineligible_reserves),
                'retired': self.exits,
                'admitted': self.joins, 'strategy_switches': self.switches,
                'review_cycles': self.review_count,
                'positive_reviews': self.positive_reviews,
                'negative_reviews': self.negative_reviews,
                'rule_counts': counts, 'ahead_of_hold': positive,
                'behind_hold': negative, 'equal_hold': even,
                'recent_events': list(self.events[-14:]),
                'recent_exits': list(self.retired.values())[-8:],
                'source': 'SIMULATED',
                'funding': 'PRE_FUNDED_WAITLIST_NO_MINT',
                'learning_rule': 'DETERMINISTIC_RELATIVE_PNL_HEURISTIC',
                'conservation_pass': self.check()}
