# -*- coding: utf-8 -*-
"""Lemon Tycoon AI — competitive strategy v15.

Production: 2*log2(k) (continuous). ID 16→8.0, ID 15→7.81, ID 14→7.61.

KEY INSIGHT: Instead of defending against sabotage of 16, AVOID 16 entirely
and SABOTAGE 16 ourselves to cripple pure-16 opponents.

Strategy:
1. BUY IDs 15+14 (never 16). Immune to ID-16 sabotage (the most common
   target). Avg production 7.71/fac — only 3.6% slower than pure 16.
2. SABOTAGE ID 16 periodically: destroys pure-16 opponents' factories.
   Adaptive: stops if no enemies use 16 (waste detection).
3. REACTIVE SWITCH: If 15 or 14 gets sabotaged, shift to next-best ID.
4. SKIP-BUY WIN: When production alone crosses goal, skip buying.
5. SELL-TO-WIN: Sell factories when optimal.
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

        # Sabotage tracking
        self.last_sabotaged_round = {}
        self.sab_count_by_id = {}
        self.any_sabotage_seen = False

        # Evidence from destroyed_factory_counts
        self.confirmed_enemy_round = {}

        # Opponent estimation
        self.prev_lemons = [initial_lemons] * num_players
        self.est_production = [0.0] * num_players

        # Our factory tracking
        self.prev_our_factories = {}

        # Offensive sabotage state
        self.sab_cooldown = 0
        self.enemies_use_max_id = True  # assume yes until proven otherwise
        self.sabotaged_max_last_round = False  # did anyone (incl us) sabotage max_id last round?
        self.detected_saboteur = False  # is there an opponent who sabotages?

    def _idx_to_id(self, idx, facs_len):
        if facs_len > self.num_ids:
            return idx
        return idx + 1

    def _id_to_idx(self, fid, facs_len):
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
            if 0 < fid < len(self.prod):
                total += facs[idx] * self.prod[fid]
        return total

    def _total_fac(self, facs):
        return sum(facs)

    def _best_unsabotaged_id(self, round_number, cooldown=5):
        """Return the single highest-production ID that's safe to buy.
        Uses long cooldown (5) so we don't cycle back to targeted IDs."""
        for fid in range(self.max_id, 0, -1):
            if self.prod[fid] <= 0:
                continue
            last_sab = self.last_sabotaged_round.get(fid, -100)
            if (round_number - last_sab) <= cooldown:
                continue
            # If sabotaged 2+ times, it's being actively targeted — avoid permanently
            if self.sab_count_by_id.get(fid, 0) >= 2:
                continue
            return fid
        # Fallback: least-targeted high-production ID
        all_ids = [(self.sab_count_by_id.get(fid, 0), -self.prod[fid], fid)
                   for fid in range(self.max_id, 0, -1) if self.prod[fid] > 0]
        all_ids.sort()
        return all_ids[0][2] if all_ids else self.max_id

    def play(self, round_number, your_lemons, your_factories,
             all_lemons, destroyed_factory_counts, sabotages_by_player):

        buys = []
        sells = []
        sabotages = []
        budget = your_lemons
        facs_len = len(your_factories)

        # ── Update sabotage tracking ──
        max_id_sabotaged_this_report = False
        for pid in range(self.num_players):
            for sid in sabotages_by_player[pid]:
                self.last_sabotaged_round[sid] = round_number - 1
                self.sab_count_by_id[sid] = self.sab_count_by_id.get(sid, 0) + 1
                self.any_sabotage_seen = True
                if sid == self.max_id:
                    max_id_sabotaged_this_report = True
                # Detect if a non-us player is a saboteur
                if pid != self.player_id:
                    self.detected_saboteur = True

        # Adaptive: if max_id was sabotaged but nothing was found there,
        # nobody uses it — stop wasting money sabotaging it
        if self.sabotaged_max_last_round:
            if self.max_id not in destroyed_factory_counts:
                self.enemies_use_max_id = False
            else:
                # Check if only OUR factories were destroyed (shouldn't happen
                # since we don't buy max_id, but be safe)
                our_prev_max = self.prev_our_factories.get(self.max_id, 0)
                our_now_max = self._fac_at(your_factories, self.max_id)
                our_lost_max = max(0, our_prev_max - our_now_max)
                enemy_destroyed_max = max(0, destroyed_factory_counts[self.max_id] - our_lost_max)
                if enemy_destroyed_max == 0:
                    self.enemies_use_max_id = False
        self.sabotaged_max_last_round = max_id_sabotaged_this_report

        # ── Update evidence-based enemy tracking ──
        for fid, total_destroyed in destroyed_factory_counts.items():
            self.confirmed_enemy_round[fid] = round_number

        # Save factory state
        self.prev_our_factories = {}
        for idx in range(facs_len):
            fid = self._idx_to_id(idx, facs_len)
            if your_factories[idx] > 0:
                self.prev_our_factories[fid] = your_factories[idx]

        # ── Estimate opponent production ──
        for pid in range(self.num_players):
            if pid == self.player_id:
                continue
            delta = all_lemons[pid] - self.prev_lemons[pid]
            sab_spent = len(sabotages_by_player[pid]) * self.sabotage_cost
            est = max(0.0, delta + sab_spent)
            alpha = 0.4 if round_number <= 3 else 0.25
            self.est_production[pid] = alpha * est + (1 - alpha) * self.est_production[pid]

        # ── Game state ──
        my_prod = self._my_production(your_factories)
        total_fac = self._total_fac(your_factories)
        rounds_left = self.max_rounds - round_number
        my_total_value = budget + total_fac * self.sell_price
        max_lemons = max(all_lemons) if all_lemons else 0

        # ── WIN: production alone crosses goal ──
        if budget + my_prod >= self.goal_lemons:
            self.prev_lemons = list(all_lemons)
            return [], [], []

        # ── Opponent threat ──
        opp_imminent = False
        for pid in range(self.num_players):
            if pid == self.player_id:
                continue
            if all_lemons[pid] + self.est_production[pid] >= self.goal_lemons * 0.80:
                opp_imminent = True
                break

        someone_at_goal = max_lemons >= self.goal_lemons

        # ── SELL to win ──
        can_win_by_selling = my_total_value >= self.goal_lemons
        should_sell = can_win_by_selling and (
            someone_at_goal or opp_imminent or rounds_left <= 1
        )

        if should_sell:
            needed = self.goal_lemons - budget
            if needed <= 0:
                self.prev_lemons = list(all_lemons)
                return [], [], []
            num_to_sell = math.ceil(needed / self.sell_price)
            if num_to_sell <= total_fac:
                sell_pairs = []
                for idx in range(facs_len):
                    cnt = your_factories[idx]
                    if cnt > 0:
                        fid = self._idx_to_id(idx, facs_len)
                        p = self.prod[fid] if fid < len(self.prod) else 0
                        sell_pairs.append((fid, cnt, p))
                sell_pairs.sort(key=lambda x: x[2])
                count_needed = num_to_sell
                for fid, cnt, _ in sell_pairs:
                    if count_needed <= 0:
                        break
                    to_sell = min(cnt, count_needed)
                    sells.extend([fid] * to_sell)
                    budget += to_sell * self.sell_price
                    count_needed -= to_sell
                self.prev_lemons = list(all_lemons)
                return buys, sells, sabotages

        # ── OFFENSIVE SABOTAGE ──
        self.sab_cooldown = max(0, self.sab_cooldown - 1)
        if (self.sab_cooldown == 0
                and round_number >= 6
                and budget >= self.sabotage_cost + self.buy_price):
            if self.enemies_use_max_id:
                # Sabotage ID 16 — destroys pure-16 opponents
                sabotages.append(self.max_id)
                budget -= self.sabotage_cost
                self.sab_cooldown = 3
            elif self.detected_saboteur and round_number >= 10:
                # Nobody uses 16, but there's a saboteur. They likely use
                # IDs 13 or below (which we don't own). Sabotage 13.
                counter_id = self.max_id - 3  # ID 13
                if self._fac_at(your_factories, counter_id) == 0:
                    sabotages.append(counter_id)
                    budget -= self.sabotage_cost
                    self.sab_cooldown = 5

        # No buying on last round
        if rounds_left <= 0:
            self.prev_lemons = list(all_lemons)
            return buys, sells, sabotages

        # ── BUY DECISION ──
        # Pick the 2 highest-production IDs below max_id that haven't been
        # sabotaged recently. Never buy max_id (our sabotage target).
        buy_ids = []
        for fid in range(self.max_id - 1, 0, -1):
            if self.sab_count_by_id.get(fid, 0) >= 2:
                continue
            if self.last_sabotaged_round.get(fid, -100) >= round_number - 5:
                continue
            buy_ids.append(fid)
            if len(buy_ids) >= 2:
                break
        if not buy_ids:
            # Fallback: use least-sabotaged IDs
            all_ids = [(self.sab_count_by_id.get(f, 0), -self.prod[f], f)
                       for f in range(self.max_id - 1, 0, -1) if self.prod[f] > 0]
            all_ids.sort()
            buy_ids = [f for _, _, f in all_ids[:2]]

        primary_id = buy_ids[0] if buy_ids else self.max_id - 1
        secondary_id = buy_ids[1] if len(buy_ids) > 1 else primary_id

        num_buy = int(budget // self.buy_price)
        if num_buy > 0:
            # Balanced 50/50 alternation
            my_p = self._fac_at(your_factories, primary_id)
            my_s = self._fac_at(your_factories, secondary_id)
            for _ in range(num_buy):
                if my_p <= my_s:
                    buys.append(primary_id)
                    my_p += 1
                else:
                    buys.append(secondary_id)
                    my_s += 1

        self.prev_lemons = list(all_lemons)
        return buys, sells, sabotages


SubmissionStrategy = SubmissionPlayer
