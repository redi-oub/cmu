"""Lemon Tycoon AI — SubmissionStrategy.

Strategy overview
─────────────────
1. **Default: buy ID 16** (8 lemons/round) for maximum growth. In passive
   lobbies this matches the best possible pure-growth strategy.
2. **On sabotage: diversify into IDs 8-15** (6 lemons/round, 8 distinct IDs).
   Once any sabotage is observed, shift buying away from sabotaged IDs and
   spread across the tier-high pool for resilience.
3. **Sabotage cooldown**: Avoid buying any ID that was sabotaged in the last
   3 rounds.
4. **ID 4-7 fallback**: If most of IDs 8-16 are on cooldown, shift into
   IDs 4-7 (4 lemons/round, 4 extra IDs).
5. **Sabotage opponents**: Only sabotage when an opponent is dangerously close
   to goal_lemons, preferring IDs where we own nothing.
6. **End-game**: Stop buying when remaining rounds can't recoup. Sell to cross
   the goal threshold if possible.
"""

import logging
import math
from typing import Dict, List, Tuple

try:
    from player import Player
except ModuleNotFoundError:
    class Player:
        pass

logger = logging.getLogger(__name__)


def production_rate(factory_id: int) -> int:
    """Lemons produced per round by a factory with the given ID.

    Formula: 2 * floor(log2(k)) for k >= 1.  ID 0 is unused.
    Examples: ID 1 -> 0, ID 2 -> 2, ID 4 -> 4, ID 8 -> 6, ID 16 -> 8.
    """
    if factory_id <= 0:
        return 0
    return 2 * int(math.log2(factory_id))


class SubmissionPlayer(Player):
    # ──────────────────────────── init ────────────────────────────

    def __init__(
        self,
        player_id: int,
        num_players: int,
        factory_bit_width: int,
        sell_price: float,
        buy_price: float,
        sabotage_cost: float,
        initial_lemons: float,
        goal_lemons: float,
        max_rounds: int,
    ) -> None:
        super().__init__(
            player_id,
            num_players,
            factory_bit_width,
            sell_price,
            buy_price,
            sabotage_cost,
            initial_lemons,
            goal_lemons,
            max_rounds,
        )
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

        # Pre-compute production table (index 0..num_ids)
        self.prod = [production_rate(k) for k in range(self.num_ids + 1)]

        # Tier classification
        self.tier_top = [16] if self.num_ids >= 16 else []
        self.tier_high = list(range(8, min(16, self.num_ids + 1)))  # 8..15
        self.tier_mid = list(range(4, min(8, self.num_ids + 1)))    # 4..7

        # Sabotage tracking: factory_id -> last round sabotaged
        self.last_sabotaged_round: Dict[int, int] = {}
        self.total_sabotage_events = 0
        # Count of rounds where 5+ distinct IDs were sabotaged at once
        self.mass_sabotage_count = 0

        # Opponent tracking
        self.prev_lemons: List[float] = [initial_lemons] * num_players

        # Opponent factory estimation: est_factories[pid][fid] = estimated count
        self.est_factories: List[List[float]] = [
            [0.0] * (self.num_ids + 1) for _ in range(num_players)
        ]
        # Estimated total production per opponent (smoothed)
        self.est_production: List[float] = [0.0] * num_players

        # Sabotage cooldown window (rounds)
        self.cooldown = 3

    # ───────────────── helpers ────────────────────

    def _id_on_cooldown(self, fid: int, round_number: int) -> bool:
        last = self.last_sabotaged_round.get(fid, -100)
        return (round_number - last) <= self.cooldown

    def _pick_buy_pool(self, round_number: int) -> List[int]:
        """Return the list of factory IDs we should buy from this round."""
        sabotage_env = self.total_sabotage_events > 0

        if not sabotage_env:
            # No sabotage ever seen: go all-in on ID 16 for max growth
            return list(self.tier_top) if self.tier_top else list(self.tier_high)

        heavy = self.mass_sabotage_count >= 1

        pool: List[int] = []
        for k in self.tier_top:
            if not self._id_on_cooldown(k, round_number):
                pool.append(k)
        for k in self.tier_high:
            if not self._id_on_cooldown(k, round_number):
                pool.append(k)

        # Always include tier_mid in sabotage environments for resilience
        for k in self.tier_mid:
            if not self._id_on_cooldown(k, round_number):
                pool.append(k)

        if not pool:
            pool = list(self.tier_mid) + list(self.tier_high)

        return pool

    # ───────────── opponent estimation ─────────────

    def _update_opponent_estimates(
        self,
        round_number: int,
        all_lemons: List[float],
        your_factories: List[int],
        destroyed_factory_counts: Dict[int, int],
        sabotages_by_player: List[List[int]],
    ) -> None:
        """Update estimated factory holdings for each opponent."""
        for pid in range(self.num_players):
            if pid == self.player_id:
                continue

            # 1) Estimate production from lemon delta
            delta = all_lemons[pid] - self.prev_lemons[pid]
            sab_spend = len(sabotages_by_player[pid]) * self.sabotage_cost
            # delta = production - buy_spend + sell_revenue - sab_spend
            # production >= delta + sab_spend  (buys reduce delta, sells increase)
            est_prod_lb = max(0.0, delta + sab_spend)
            # Smooth with previous estimate (EMA, alpha=0.4)
            if round_number <= 2:
                self.est_production[pid] = est_prod_lb
            else:
                self.est_production[pid] = (
                    0.4 * est_prod_lb + 0.6 * self.est_production[pid]
                )

        # 2) Use destroyed_factory_counts for ground truth at sabotaged IDs
        for fid, total_destroyed in destroyed_factory_counts.items():
            if fid < 1 or fid > self.num_ids:
                continue
            # total_destroyed = sum across ALL players (including us)
            our_destroyed = 0  # our factories at that ID were already gone
            # We can't know exactly per-opponent, but we can estimate
            # the collective enemy count: total - our_count
            # Note: our factories were already destroyed, so we track
            # how many we had from last round (before sabotage)
            others_destroyed = max(0, total_destroyed)
            # Distribute proportionally among opponents based on est_production
            total_opp_prod = sum(
                self.est_production[p]
                for p in range(self.num_players)
                if p != self.player_id
            )
            for pid in range(self.num_players):
                if pid == self.player_id:
                    continue
                if total_opp_prod > 0:
                    share = self.est_production[pid] / total_opp_prod
                else:
                    share = 1.0 / max(1, self.num_players - 1)
                self.est_factories[pid][fid] = 0  # wiped by sabotage

        # 3) Estimate new factory purchases from production capacity.
        #    If opponent has production P and we haven't seen sabotage reveal,
        #    assume they buy the highest-prod IDs (like a rational player).
        for pid in range(self.num_players):
            if pid == self.player_id:
                continue
            # Estimate how many factories they bought last round
            delta = all_lemons[pid] - self.prev_lemons[pid]
            sab_spend = len(sabotages_by_player[pid]) * self.sabotage_cost
            prod_est = self.est_production[pid]
            # buy_spend ≈ production - delta - sab_spend (rough)
            buy_spend = max(0.0, prod_est - delta - sab_spend)
            num_bought = int(buy_spend / self.buy_price) if self.buy_price > 0 else 0

            # Assume they buy highest-production IDs
            for _ in range(num_bought):
                # Find the ID they'd most likely buy (highest prod, not sabotaged)
                best_id = self.num_ids  # default: top ID
                for k in range(self.num_ids, 0, -1):
                    if self.prod[k] > 0:
                        last_sab = self.last_sabotaged_round.get(k, -100)
                        if round_number - last_sab > 2:
                            best_id = k
                            break
                if best_id > 0:
                    self.est_factories[pid][best_id] += 1.0

            # Clear factories at IDs that were sabotaged this round
            for fid in destroyed_factory_counts:
                if 1 <= fid <= self.num_ids:
                    self.est_factories[pid][fid] = 0.0

    def _sabotage_net_value(self, fid: int, your_factories: List[int]) -> float:
        """Compute net value of sabotaging factory ID `fid`.

        In a 4-player game, 1:1 attrition with a single opponent doesn't help
        us — the other 2 players gain a relative advantage. We need the total
        enemy loss to significantly exceed our own loss.

        We require: enemy_loss > (num_opponents) * our_loss + sabotage_cost
        Equivalently: net = enemy_loss - num_opponents * our_loss - sabotage_cost

        This ensures sabotage only when it gives us a net *relative* gain
        against the field, not just one opponent.
        """
        our_count = your_factories[fid] if fid < len(your_factories) else 0
        enemy_count = sum(
            self.est_factories[p][fid]
            for p in range(self.num_players)
            if p != self.player_id
        )
        num_opponents = self.num_players - 1  # typically 3
        prod = self.prod[fid]
        value_per_factory = prod + self.sell_price
        enemy_loss = enemy_count * value_per_factory
        # Our loss is amplified: losing 1 factory while 3 opponents exist
        # means we fall behind relative to each of them
        our_loss = our_count * value_per_factory * num_opponents
        return enemy_loss - our_loss - self.sabotage_cost

    # ──────────────────────────── play ────────────────────────────

    def play(
        self,
        round_number: int,
        your_lemons: float,
        your_factories: List[int],
        all_lemons: List[float],
        destroyed_factory_counts: Dict[int, int],
        sabotages_by_player: List[List[int]],
    ) -> Tuple[List[int], List[int], List[int]]:

        buys: List[int] = []
        sells: List[int] = []
        sabotages: List[int] = []

        # ── Update sabotage tracking ──────────────────────────────
        round_sabs: set = set()
        for pid in range(self.num_players):
            for sid in sabotages_by_player[pid]:
                round_sabs.add(sid)
        for sid in round_sabs:
            self.last_sabotaged_round[sid] = round_number - 1
            self.total_sabotage_events += 1
        if len(round_sabs) >= 5:
            self.mass_sabotage_count += 1

        # ── Compute own production ────────────────────────────────
        my_production = sum(
            your_factories[k] * self.prod[k]
            for k in range(min(len(your_factories), len(self.prod)))
        )

        rounds_left = self.max_rounds - round_number + 1
        budget = your_lemons

        # ── SELL LOGIC ────────────────────────────────────────────
        # Sell zero-production factories (IDs 0, 1)
        for fid in [0, 1]:
            if fid < len(your_factories) and your_factories[fid] > 0:
                for _ in range(your_factories[fid]):
                    sells.append(fid)
                    budget += self.sell_price
                your_factories[fid] = 0

        # Check if selling factories can push us over the goal.
        # Only do this if production alone won't get us there, or if opponents
        # are close to winning and we need to win NOW.
        total_factory_count = sum(your_factories)
        sell_all_value = budget + total_factory_count * self.sell_price
        lemons_after_production = budget + my_production

        # Check if any opponent might cross goal this round
        opponent_threat = False
        for pid in range(self.num_players):
            if pid != self.player_id:
                # Rough estimate: opponent lemons + their production
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
                logger.info(
                    "Round %d: selling %d to cross goal (budget=%.0f)",
                    round_number,
                    sells_for_goal,
                    budget,
                )
                return buys, sells, sabotages

        # ── Update opponent factory estimates ─────────────────────
        self._update_opponent_estimates(
            round_number, all_lemons, your_factories,
            destroyed_factory_counts, sabotages_by_player,
        )

        # ── SABOTAGE LOGIC (estimation-driven) ────────────────────
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
            # Score each ID by net sabotage value (hurts them more than us)
            scored_ids = []
            for k in range(1, self.num_ids + 1):
                if self.prod[k] <= 0:
                    continue
                net_val = self._sabotage_net_value(k, your_factories)
                if net_val > 0 or emergency:
                    scored_ids.append((-net_val, k))  # sort descending
            scored_ids.sort()

            max_sabs = 3 if emergency else 2
            min_reserve = 0 if emergency else self.buy_price
            sab_budget = budget - min_reserve

            for _, k in scored_ids:
                if sab_budget < self.sabotage_cost:
                    break
                if len(sabotages) >= max_sabs:
                    break
                # In non-emergency mode, only sabotage if net value > 0
                if not emergency and self._sabotage_net_value(k, your_factories) <= 0:
                    break
                sabotages.append(k)
                sab_budget -= self.sabotage_cost
                budget -= self.sabotage_cost

        # ── BUY LOGIC ─────────────────────────────────────────────
        # Don't buy if too few rounds left to recoup
        min_rounds_to_buy = 2
        if rounds_left <= min_rounds_to_buy:
            logger.info(
                "Round %d: holding (rounds_left=%d)", round_number, rounds_left
            )
            self.prev_lemons = list(all_lemons)
            return buys, sells, sabotages

        pool = self._pick_buy_pool(round_number)
        if not pool:
            self.prev_lemons = list(all_lemons)
            return buys, sells, sabotages

        # Insurance: dedicate ~1 in 4 buys to tier_mid (IDs 4-7) as sabotage
        # insurance, but only after we've seen actual sabotage activity.
        num_affordable = int(budget // self.buy_price)
        want_insurance = self.total_sabotage_events > 0 and num_affordable >= 3
        insurance_buys = max(0, num_affordable // 4) if want_insurance else 0

        # Buy insurance factories first (lowest-count tier_mid ID)
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

        # Buy remaining from main pool, preferring IDs where we own fewest
        while budget >= self.buy_price and pool:
            best_id = min(pool, key=lambda k: (
                your_factories[k] if k < len(your_factories) else 0,
                -self.prod[k],  # tie-break: higher production first
            ))
            buys.append(best_id)
            budget -= self.buy_price
            if best_id < len(your_factories):
                your_factories[best_id] += 1

        logger.info(
            "Round %d: lemons=%.0f prod=%d buys=%s sells=%s sabs=%s",
            round_number,
            your_lemons,
            my_production,
            buys,
            sells,
            sabotages,
        )

        self.prev_lemons = list(all_lemons)
        return buys, sells, sabotages


# Alias for local testing compatibility
SubmissionStrategy = SubmissionPlayer
