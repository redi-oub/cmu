# -*- coding: utf-8 -*-
"""Lemon Tycoon AI - competitive strategy v6.

Key discovery: server uses 2*log2(k) (continuous, NO floor) for production.
ID 15 produces 7.81 (97.7% of ID 16's 8.0), ID 14 produces 7.62.

Strategy:
1. Default: pure ID 16 for maximum growth.
2. Sell-to-win: sell as soon as we CAN cross goal AND an opponent is close.
3. After our factories get sabotaged: temporarily diversify to IDs 14-15
   (nearly as productive as 16 but different sabotage targets).
   Recover to pure 16 after 3 rounds without new sabotage on our IDs.
4. After mass sabotage (5+ IDs): fall back to IDs 4-7 (safe low-value IDs).
"""

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
    return 2 * math.log2(factory_id)


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
        self.max_id = self.num_ids

        self.last_sabotaged_round = {}
        self.sabotaged_ids_ever = set()
        self.total_sab_events = 0
        self.last_our_sab_round = -1
        self.mass_sab_seen = False
        self.mass_sab_rounds = 0

        self.prev_lemons = [initial_lemons] * num_players
        self.est_production = [0.0] * num_players

        self.confirmed_enemy_ids = set()
        self.destroyed_totals = {}
        self.own_counts = {}

    def _idx_to_id(self, idx, facs_len):
        """Convert array index to factory ID.

        Local engine: length num_ids+1, index=ID (index 0 unused).
        Server may use length num_ids, where index i = ID i+1.
        """
        if facs_len > self.num_ids:
            return idx
        return idx + 1

    def _id_to_idx(self, fid, facs_len):
        """Convert factory ID to array index."""
        if facs_len > self.num_ids:
            return fid
        return fid - 1

    def _fac_at(self, facs, fid):
        idx = self._id_to_idx(fid, len(facs))
        if 0 <= idx < len(facs):
            return facs[idx]
        return 0

    def _my_production(self, facs):
        total = 0
        for idx in range(len(facs)):
            fid = self._idx_to_id(idx, len(facs))
            p = self.prod[fid] if fid < len(self.prod) else 0
            total += facs[idx] * p
        return total

    def _total_fac(self, facs):
        return sum(facs)

    def play(self, round_number, your_lemons, your_factories,
             all_lemons, destroyed_factory_counts, sabotages_by_player):

        buys = []
        sells = []
        sabotages = []
        budget = your_lemons

        round_sab_ids = set()
        for pid in range(self.num_players):
            for sid in sabotages_by_player[pid]:
                round_sab_ids.add(sid)
                self.last_sabotaged_round[sid] = max(round_number - 1, 0)
                self.total_sab_events += 1
                self.sabotaged_ids_ever.add(sid)
        if len(round_sab_ids) >= 5:
            self.mass_sab_rounds += 1
            self.mass_sab_seen = True

        for fid, cnt in destroyed_factory_counts.items():
            self.destroyed_totals[fid] = self.destroyed_totals.get(fid, 0) + cnt
            our_had = self.own_counts.get(fid, 0)
            if our_had > 0 and fid in round_sab_ids:
                self.last_our_sab_round = round_number
            if cnt > our_had:
                self.confirmed_enemy_ids.add(fid)

        for pid in range(self.num_players):
            if pid == self.player_id:
                continue
            delta = all_lemons[pid] - self.prev_lemons[pid]
            sab_cost = len(sabotages_by_player[pid]) * self.sabotage_cost
            est = max(0.0, delta + sab_cost)
            if round_number <= 2:
                self.est_production[pid] = est
            else:
                self.est_production[pid] = 0.35 * est + 0.65 * self.est_production[pid]

        my_prod = self._my_production(your_factories)
        total_fac = self._total_fac(your_factories)
        rounds_left = self.max_rounds - round_number

        leader_idx = -1
        leader_lemons = 0.0
        for pid in range(self.num_players):
            if pid != self.player_id:
                if all_lemons[pid] > leader_lemons:
                    leader_lemons = all_lemons[pid]
                    leader_idx = pid

        my_total_value = budget + total_fac * self.sell_price

        opponent_close = False
        for pid in range(self.num_players):
            if pid != self.player_id:
                opp_est = all_lemons[pid] + self.est_production[pid]
                if opp_est >= self.goal_lemons * 0.80:
                    opponent_close = True
                    break

        can_win = my_total_value >= self.goal_lemons

        max_lemons = max(all_lemons) if all_lemons else 0
        progress = max(max_lemons / self.goal_lemons,
                       round_number / self.max_rounds) if self.goal_lemons > 0 else 0

        someone_at_goal = max_lemons >= self.goal_lemons

        will_production_win = (budget + my_prod >= self.goal_lemons)

        should_sell = can_win and (
            opponent_close
            or someone_at_goal
            or (progress >= 0.48 and not will_production_win)
        )

        if should_sell:
            needed = self.goal_lemons - budget
            if needed <= 0:
                self.prev_lemons = list(all_lemons)
                return buys, sells, sabotages
            num_to_sell = math.ceil(needed / self.sell_price)
            if num_to_sell <= total_fac:
                count_needed = num_to_sell
                facs_len = len(your_factories)
                sell_pairs = []
                for idx in range(facs_len):
                    cnt = your_factories[idx]
                    if cnt > 0:
                        fid = self._idx_to_id(idx, facs_len)
                        p = self.prod[fid] if fid < len(self.prod) else 0
                        sell_pairs.append((fid, cnt, p))
                sell_pairs.sort(key=lambda x: x[2])
                for fid, cnt, _ in sell_pairs:
                    if count_needed <= 0:
                        break
                    to_sell = min(cnt, count_needed)
                    for _ in range(to_sell):
                        sells.append(fid)
                        budget += self.sell_price
                        count_needed -= 1
                self.prev_lemons = list(all_lemons)
                return buys, sells, sabotages

        my_rank = 1
        for pid in range(self.num_players):
            if pid != self.player_id and all_lemons[pid] > budget:
                my_rank += 1

        if (leader_idx >= 0 and my_rank > 1
                and leader_lemons >= self.goal_lemons * 0.85):
            best_fid = -1
            best_net = -999999.0
            for fid in self.confirmed_enemy_ids:
                if fid < 1 or fid >= len(self.prod) or self.prod[fid] <= 0:
                    continue
                our_cnt = self._fac_at(your_factories, fid)
                est_enemy = max(1.0, self.destroyed_totals.get(fid, 0) * 0.2)
                val = self.prod[fid] + self.sell_price
                net = est_enemy * val - our_cnt * val * (self.num_players - 1) - self.sabotage_cost
                if net > best_net:
                    best_net = net
                    best_fid = fid
            if best_fid > 0 and budget >= self.sabotage_cost + self.buy_price:
                if best_net > 0 or leader_lemons >= self.goal_lemons * 0.92:
                    sabotages.append(best_fid)
                    budget -= self.sabotage_cost

        if rounds_left <= 1:
            self.prev_lemons = list(all_lemons)
            return buys, sells, sabotages

        id16_last_sab = self.last_sabotaged_round.get(self.max_id, -100)
        id16_hot = (round_number - id16_last_sab) <= 2

        if self.mass_sab_seen:
            pool = list(range(4, 8))
        elif id16_hot:
            best_safe = None
            for fid in [15, 14, 13, 12]:
                if self.last_sabotaged_round.get(fid, -100) < round_number - 1:
                    best_safe = fid
                    break
            pool = [best_safe if best_safe else 7]
        else:
            pool = [self.max_id]

        while budget >= self.buy_price and pool:
            buys.append(pool[0])
            budget -= self.buy_price
            bidx = self._id_to_idx(pool[0], len(your_factories))
            if 0 <= bidx < len(your_factories):
                your_factories[bidx] += 1
            self.own_counts[pool[0]] = self.own_counts.get(pool[0], 0) + 1

        self.prev_lemons = list(all_lemons)
        return buys, sells, sabotages


SubmissionStrategy = SubmissionPlayer
