# -*- coding: utf-8 -*-
"""Lemon Tycoon AI submission."""

import logging
import math
from typing import Dict, List, Tuple

try:
    from player import Player
except ModuleNotFoundError:
    class Player:
        def __init__(self, *args, **kwargs):
            pass

logger = logging.getLogger(__name__)


def production_rate(factory_id):
    if factory_id <= 0:
        return 0
    return 2 * int(math.log2(factory_id))


class SubmissionPlayer(Player):

    def __init__(
        self,
        player_id,
        num_players,
        factory_bit_width,
        sell_price,
        buy_price,
        sabotage_cost,
        initial_lemons,
        goal_lemons,
        max_rounds,
    ):
        try:
            super().__init__(
                player_id, num_players, factory_bit_width,
                sell_price, buy_price, sabotage_cost,
                initial_lemons, goal_lemons, max_rounds,
            )
        except Exception:
            pass

        self.player_id = player_id
        self.num_players = num_players
        self.factory_bit_width = factory_bit_width
        self.num_ids = 1 << factory_bit_width
        self.sell_price = sell_price
        self.buy_price = buy_price
        self.sabotage_cost = sabotage_cost
        self.initial_lemons = initial_lemons
        self.goal_lemons = goal_lemons
        self.max_rounds = max_rounds

        self.prod = [production_rate(k) for k in range(self.num_ids + 1)]

        self.tier_top = [16] if self.num_ids >= 16 else []
        self.tier_high = list(range(8, min(16, self.num_ids + 1)))
        self.tier_mid = list(range(4, min(8, self.num_ids + 1)))

        self.last_sabotaged_round = {}
        self.total_sabotage_events = 0
        self.mass_sabotage_count = 0

        self.prev_lemons = [initial_lemons] * num_players

        self.est_factories = [
            [0.0] * (self.num_ids + 1) for _ in range(num_players)
        ]
        self.est_production = [0.0] * num_players

        self.cooldown = 3

    # -- helpers --

    def _id_on_cooldown(self, fid, round_number):
        last = self.last_sabotaged_round.get(fid, -100)
        return (round_number - last) <= self.cooldown

    def _pick_buy_pool(self, round_number):
        sabotage_env = self.total_sabotage_events > 0

        if not sabotage_env:
            return list(self.tier_top) if self.tier_top else list(self.tier_high)

        pool = []
        for k in self.tier_top:
            if not self._id_on_cooldown(k, round_number):
                pool.append(k)
        for k in self.tier_high:
            if not self._id_on_cooldown(k, round_number):
                pool.append(k)
        for k in self.tier_mid:
            if not self._id_on_cooldown(k, round_number):
                pool.append(k)

        if not pool:
            pool = list(self.tier_mid) + list(self.tier_high)

        return pool

    # -- opponent estimation --

    def _update_opponent_estimates(self, round_number, all_lemons,
                                   your_factories, destroyed_factory_counts,
                                   sabotages_by_player):
        for pid in range(self.num_players):
            if pid == self.player_id:
                continue
            delta = all_lemons[pid] - self.prev_lemons[pid]
            sab_spend = len(sabotages_by_player[pid]) * self.sabotage_cost
            est_prod_lb = max(0.0, delta + sab_spend)
            if round_number <= 2:
                self.est_production[pid] = est_prod_lb
            else:
                self.est_production[pid] = (
                    0.4 * est_prod_lb + 0.6 * self.est_production[pid]
                )

        for fid, total_destroyed in destroyed_factory_counts.items():
            if fid < 1 or fid > self.num_ids:
                continue
            for pid in range(self.num_players):
                if pid != self.player_id:
                    self.est_factories[pid][fid] = 0.0

        for pid in range(self.num_players):
            if pid == self.player_id:
                continue
            delta = all_lemons[pid] - self.prev_lemons[pid]
            sab_spend = len(sabotages_by_player[pid]) * self.sabotage_cost
            prod_est = self.est_production[pid]
            buy_spend = max(0.0, prod_est - delta - sab_spend)
            num_bought = int(buy_spend / self.buy_price) if self.buy_price > 0 else 0

            for _ in range(num_bought):
                best_id = self.num_ids
                for k in range(self.num_ids, 0, -1):
                    if self.prod[k] > 0:
                        last_sab = self.last_sabotaged_round.get(k, -100)
                        if round_number - last_sab > 2:
                            best_id = k
                            break
                if best_id > 0:
                    self.est_factories[pid][best_id] += 1.0

            for fid in destroyed_factory_counts:
                if 1 <= fid <= self.num_ids:
                    self.est_factories[pid][fid] = 0.0

    def _sabotage_net_value(self, fid, your_factories):
        our_count = your_factories[fid] if fid < len(your_factories) else 0
        enemy_count = sum(
            self.est_factories[p][fid]
            for p in range(self.num_players)
            if p != self.player_id
        )
        num_opponents = self.num_players - 1
        prod = self.prod[fid]
        value_per_factory = prod + self.sell_price
        enemy_loss = enemy_count * value_per_factory
        our_loss = our_count * value_per_factory * num_opponents
        return enemy_loss - our_loss - self.sabotage_cost

    # -- main play --

    def play(self, round_number, your_lemons, your_factories,
             all_lemons, destroyed_factory_counts, sabotages_by_player):

        buys = []
        sells = []
        sabotages = []

        # Update sabotage tracking
        round_sabs = set()
        for pid in range(self.num_players):
            for sid in sabotages_by_player[pid]:
                round_sabs.add(sid)
        for sid in round_sabs:
            self.last_sabotaged_round[sid] = round_number - 1
            self.total_sabotage_events += 1
        if len(round_sabs) >= 5:
            self.mass_sabotage_count += 1

        # Compute own production
        my_production = sum(
            your_factories[k] * self.prod[k]
            for k in range(min(len(your_factories), len(self.prod)))
        )

        rounds_left = self.max_rounds - round_number + 1
        budget = your_lemons

        # SELL: zero-production factories
        for fid in [0, 1]:
            if fid < len(your_factories) and your_factories[fid] > 0:
                for _ in range(your_factories[fid]):
                    sells.append(fid)
                    budget += self.sell_price
                your_factories[fid] = 0

        # Check if selling can push us over the goal
        total_factory_count = sum(your_factories)
        sell_all_value = budget + total_factory_count * self.sell_price
        lemons_after_production = budget + my_production

        opponent_threat = False
        for pid in range(self.num_players):
            if pid != self.player_id:
                opp_delta = all_lemons[pid] - self.prev_lemons[pid] if round_number > 1 else 0
                est_opp_prod = max(0, opp_delta)
                if all_lemons[pid] + est_opp_prod >= self.goal_lemons * 0.95:
                    opponent_threat = True
                    break

        can_win_by_selling = sell_all_value >= self.goal_lemons
        will_win_by_production = lemons_after_production >= self.goal_lemons

        if can_win_by_selling and (not will_win_by_production or opponent_threat):
            needed = self.goal_lemons - budget
            sells_for_goal = math.ceil(needed / self.sell_price) if needed > 0 else 0
            if sells_for_goal <= total_factory_count:
                sells = []
                budget = your_lemons
                count_needed = sells_for_goal
                for k in range(len(your_factories)):
                    if count_needed <= 0:
                        break
                    to_sell = min(your_factories[k], count_needed)
                    for _ in range(to_sell):
                        sells.append(k)
                        budget += self.sell_price
                        count_needed -= 1
                self.prev_lemons = list(all_lemons)
                return buys, sells, sabotages

        # Update opponent estimates
        self._update_opponent_estimates(
            round_number, all_lemons, your_factories,
            destroyed_factory_counts, sabotages_by_player,
        )

        # SABOTAGE (estimation-driven)
        leader_idx = -1
        leader_lemons = 0.0
        for pid in range(self.num_players):
            if pid != self.player_id and all_lemons[pid] > leader_lemons:
                leader_lemons = all_lemons[pid]
                leader_idx = pid

        do_sabotage = False
        emergency = False

        if leader_idx >= 0:
            if leader_lemons >= self.goal_lemons * 0.75:
                do_sabotage = True
            if leader_lemons >= self.goal_lemons * 0.85:
                emergency = True
            if leader_lemons > your_lemons * 1.5 and leader_lemons > 300:
                do_sabotage = True

        if do_sabotage:
            scored_ids = []
            for k in range(1, self.num_ids + 1):
                if self.prod[k] <= 0:
                    continue
                net_val = self._sabotage_net_value(k, your_factories)
                if net_val > 0 or emergency:
                    scored_ids.append((-net_val, k))
            scored_ids.sort()

            max_sabs = 3 if emergency else 2
            min_reserve = 0 if emergency else self.buy_price
            sab_budget = budget - min_reserve

            for _, k in scored_ids:
                if sab_budget < self.sabotage_cost:
                    break
                if len(sabotages) >= max_sabs:
                    break
                if not emergency and self._sabotage_net_value(k, your_factories) <= 0:
                    break
                sabotages.append(k)
                sab_budget -= self.sabotage_cost
                budget -= self.sabotage_cost

        # BUY
        min_rounds_to_buy = 2
        if rounds_left <= min_rounds_to_buy:
            self.prev_lemons = list(all_lemons)
            return buys, sells, sabotages

        pool = self._pick_buy_pool(round_number)
        if not pool:
            self.prev_lemons = list(all_lemons)
            return buys, sells, sabotages

        # Insurance: 1-in-4 buys into tier_mid after seeing sabotage
        num_affordable = int(budget // self.buy_price)
        want_insurance = self.total_sabotage_events > 0 and num_affordable >= 3
        insurance_buys = max(0, num_affordable // 4) if want_insurance else 0

        mid_pool = [k for k in self.tier_mid
                    if not self._id_on_cooldown(k, round_number)]
        for _ in range(insurance_buys):
            if budget < self.buy_price or not mid_pool:
                break
            ins_id = min(mid_pool, key=lambda k: (
                your_factories[k] if k < len(your_factories) else 0,
            ))
            buys.append(ins_id)
            budget -= self.buy_price
            if ins_id < len(your_factories):
                your_factories[ins_id] += 1

        # Main buys: prefer IDs where we own fewest
        while budget >= self.buy_price and pool:
            best_id = min(pool, key=lambda k: (
                your_factories[k] if k < len(your_factories) else 0,
                -self.prod[k],
            ))
            buys.append(best_id)
            budget -= self.buy_price
            if best_id < len(your_factories):
                your_factories[best_id] += 1

        self.prev_lemons = list(all_lemons)
        return buys, sells, sabotages


# Alias for local testing
SubmissionStrategy = SubmissionPlayer
