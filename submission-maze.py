"""
Maze Optimization – Bot & Ghost Submission
Strategy: Bot does DFS until it finds a ghost-signaled high-value slot, then
parks and spins forever. Ghost explores the graph, picks the optimal beacon
slot (highest remaining_steps × expected_α), fills it with coins as a signal,
then farms secondary slots.
"""
import logging
from collections import deque
from typing import Any, List

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bot memory encoding  (95 bytes = 760 bits, sys.getsizeof -> 128)
#
# Layout (bit offsets):
#   [0]           spinning  (1 bit: 0=DFS, 1=parked & spinning)
#   [1..100]      visited   (100 bits)
#   [101..104]    sweep_count (4 bits, 0-15)
#   [105..111]    stack_size (7 bits, 0-92)
#   [112..755]    stack entries (92 slots × 7 bits each = 644 bits)
#
# Total used: 112 + 644 = 756 bits, fits in 760.
# When spinning=1, bot just returns -1 every step (rest of bits are ignored).
# ---------------------------------------------------------------------------

_SPIN_OFF = 0
_VISITED_OFF = 1
_SWEEP_OFF = 101
_SSIZE_OFF = 105
_STACK_OFF = 112
_MAX_STACK = 92
_TOTAL_BYTES = 95


def _pack_bot_state(spinning, visited, sweep_count, stack):
    buf = bytearray(_TOTAL_BYTES)

    def setbit(pos):
        buf[pos >> 3] |= 1 << (pos & 7)

    if spinning:
        setbit(_SPIN_OFF)
        return bytes(buf)

    for i in range(100):
        if visited[i]:
            setbit(_VISITED_OFF + i)

    sc = sweep_count & 0xF
    for b in range(4):
        if sc & (1 << b):
            setbit(_SWEEP_OFF + b)

    ss = min(len(stack), _MAX_STACK)
    for b in range(7):
        if ss & (1 << b):
            setbit(_SSIZE_OFF + b)

    start = max(0, len(stack) - _MAX_STACK)
    for idx in range(ss):
        val = stack[start + idx] & 0x7F
        base = _STACK_OFF + idx * 7
        for b in range(7):
            if val & (1 << b):
                setbit(base + b)

    return bytes(buf)


def _unpack_bot_state(data):
    buf = data

    def getbit(pos):
        return (buf[pos >> 3] >> (pos & 7)) & 1

    def getbits(off, count):
        val = 0
        for b in range(count):
            val |= getbit(off + b) << b
        return val

    spinning = bool(getbit(_SPIN_OFF))
    if spinning:
        return True, None, 0, []

    visited = [False] * 100
    for i in range(100):
        visited[i] = bool(getbit(_VISITED_OFF + i))

    sweep_count = getbits(_SWEEP_OFF, 4)
    stack_size = getbits(_SSIZE_OFF, 7)

    stack = []
    for idx in range(stack_size):
        val = getbits(_STACK_OFF + idx * 7, 7)
        stack.append(val)

    return False, visited, sweep_count, stack


# ---------------------------------------------------------------------------
# Deterministic DFS logic (shared between bot execution and ghost simulation)
# ---------------------------------------------------------------------------

def _dfs_next_action(pos, neighbors, visited, sweep_count, stack):
    """
    Deterministic DFS step.
    Returns (action, visited, sweep_count, stack).
    action = -1 (stay) or neighbor vertex ID.
    """
    visited[pos] = True

    reverse = (sweep_count % 2 == 1)
    sorted_neighbors = sorted(neighbors, reverse=reverse)

    next_v = None
    for n in sorted_neighbors:
        if not visited[n]:
            next_v = n
            break

    if next_v is not None:
        stack.append(pos)
        if len(stack) > _MAX_STACK:
            stack = stack[-_MAX_STACK:]
        return next_v, visited, sweep_count, stack

    if stack:
        parent = stack.pop()
        return parent, visited, sweep_count, stack

    # Sweep complete – start new sweep
    sweep_count = (sweep_count + 1) & 0xF
    visited = [False] * 100
    visited[pos] = True
    reverse = (sweep_count % 2 == 1)
    sorted_neighbors = sorted(neighbors, reverse=reverse)
    for n in sorted_neighbors:
        if not visited[n]:
            stack.append(pos)
            return n, visited, sweep_count, stack
    return -1, visited, sweep_count, stack


def _stop_threshold(step):
    """Coin threshold for bot to stop DFS and start spinning at a slot.
    Starts high (only ghost-signaled slots), decays to accept any decent slot.
    """
    return max(5, 50 - step // 8)


# ---------------------------------------------------------------------------
# SubmissionBot
# ---------------------------------------------------------------------------

def SubmissionBot(
    step: int,
    total_steps: int,
    pos: int,
    last_pos: int,
    neighbors: List[int],
    has_slot: bool,
    slot_coins: int,
    data: Any,
) -> tuple:
    if data is None:
        spinning = False
        visited_arr = [False] * 100
        sweep_count = 0
        stack = []
    else:
        spinning, visited_arr, sweep_count, stack = _unpack_bot_state(data)

    # Phase 1: already parked — spin forever
    if spinning:
        return -1, data

    # Phase 0: DFS exploration — check if we should stop here
    if has_slot and slot_coins >= _stop_threshold(step):
        packed = _pack_bot_state(True, None, 0, [])
        return -1, packed

    # Continue DFS
    action, visited_arr, sweep_count, stack = _dfs_next_action(
        pos, neighbors, visited_arr, sweep_count, stack,
    )

    packed = _pack_bot_state(False, visited_arr, sweep_count, stack)
    return action, packed


# ---------------------------------------------------------------------------
# Ghost: BFS explorer → beacon placer → secondary farmer
# ---------------------------------------------------------------------------

class GhostState:
    __slots__ = (
        'adj', 'slot_set', 'explored', 'dist_from_0',
        'apsp_next', 'apsp_dist',
        'exploration_done', 'explore_queue', 'explore_visited',
        'phase', 'beacon_slot', 'beacon_ready',
        'bot_schedule', 'slot_visit_steps', 'beacon_bot_visit_step',
        'target', 'ghost_spins_at',
        'bot_settled', 'bot_settled_pos',
    )

    def __init__(self):
        self.adj = {}
        self.slot_set = set()
        self.explored = set()
        self.dist_from_0 = []
        self.apsp_next = None
        self.apsp_dist = None

        self.exploration_done = False
        self.explore_queue = deque([0])
        self.explore_visited = {0}

        # Phases: 'explore' → 'beacon' → 'farm'
        self.phase = 'explore'
        self.beacon_slot = None
        self.beacon_ready = False

        self.bot_schedule = None
        self.slot_visit_steps = {}
        self.beacon_bot_visit_step = None

        self.target = None
        self.ghost_spins_at = {}

        self.bot_settled = False
        self.bot_settled_pos = -1


def _ghost_record_vertex(state, pos, neighbors, has_slot_here):
    state.adj[pos] = sorted(neighbors)
    state.explored.add(pos)
    if has_slot_here:
        state.slot_set.add(pos)
    for n in neighbors:
        if n not in state.adj:
            state.adj[n] = []
        if n not in state.explore_visited:
            state.explore_visited.add(n)
            state.explore_queue.append(n)


def _compute_bfs_dist(adj, source, n=100):
    dist = [-1] * n
    dist[source] = 0
    q = deque([source])
    while q:
        v = q.popleft()
        for u in adj.get(v, []):
            if dist[u] == -1:
                dist[u] = dist[v] + 1
                q.append(u)
    return dist


def _compute_apsp(adj, n=100):
    INF = 9999
    dist = [[INF] * n for _ in range(n)]
    nxt = [[-1] * n for _ in range(n)]
    for src in range(n):
        if src not in adj or not adj[src]:
            dist[src][src] = 0
            nxt[src][src] = src
            continue
        d = dist[src]
        nx = nxt[src]
        d[src] = 0
        nx[src] = src
        q = deque([src])
        while q:
            v = q.popleft()
            for u in adj.get(v, []):
                if d[u] == INF:
                    d[u] = d[v] + 1
                    nx[u] = nx[v] if v != src else u
                    q.append(u)
    return dist, nxt


def _ghost_simulate_bot_full(state, total_steps):
    """Simulate the bot's deterministic DFS (without stopping) over all steps.
    Produces bot_schedule and slot_visit_steps (all visit steps per slot).
    """
    bv = [False] * 100
    bsw = 0
    bsk = []
    bpos = 0

    schedule = {}
    slot_visits = {v: [] for v in state.slot_set}

    for s in range(1, total_steps + 1):
        neighbors = state.adj.get(bpos, [])
        action, bv, bsw, bsk = _dfs_next_action(bpos, neighbors, bv, bsw, bsk)
        if action != -1:
            bpos = action
        schedule[s] = bpos
        if bpos in slot_visits:
            slot_visits[bpos].append(s)

    state.bot_schedule = schedule
    state.slot_visit_steps = slot_visits


def _find_next_bot_visit(visits, after_step):
    """Binary search for first bot visit step >= after_step."""
    lo, hi = 0, len(visits)
    while lo < hi:
        mid = (lo + hi) // 2
        if visits[mid] < after_step:
            lo = mid + 1
        else:
            hi = mid
    return visits[lo] if lo < len(visits) else None


def _ghost_pick_beacon(state, ghost_pos, current_step, total_steps):
    """Pick the best slot for the ghost to use as a beacon.
    For each slot, finds the NEXT bot visit after ghost can arrive and fill it.
    Score = (total_steps - bot_visit_step) × expected_α.
    The bot will stop there (coins ≥ threshold) and spin for the remainder.
    """
    if not state.slot_set or state.apsp_dist is None:
        return None

    best_score = -1.0
    best_v = None
    best_bot_visit = None

    for v in state.slot_set:
        ghost_travel = state.apsp_dist[ghost_pos][v]
        if ghost_travel >= 9999:
            continue

        d0 = state.dist_from_0[v]
        if d0 < 0:
            continue
        est_alpha = max(0.5, 1.5 * d0)

        ghost_arrival = current_step + ghost_travel
        # Ghost needs ≥3 spins to accumulate enough coins above threshold
        min_fill_time = max(3, int(5.0 / max(0.1, est_alpha * 2.3)) + 1)
        earliest_bot_ok = ghost_arrival + min_fill_time

        visits = state.slot_visit_steps.get(v, [])
        bot_visit = _find_next_bot_visit(visits, earliest_bot_ok)
        if bot_visit is None:
            continue

        # The bot will stop here and spin for (total_steps - bot_visit) steps
        remaining_spins = total_steps - bot_visit
        if remaining_spins <= 0:
            continue
        score = remaining_spins * est_alpha
        if score > best_score:
            best_score = score
            best_v = v
            best_bot_visit = bot_visit

    if best_v is not None:
        state.beacon_bot_visit_step = best_bot_visit
        return best_v

    # Fallback: pick highest-α slot, ghost fills it, bot's threshold eventually
    # drops low enough to stop there on a future DFS pass
    best_alpha = -1.0
    for v in state.slot_set:
        ghost_travel = state.apsp_dist[ghost_pos][v]
        if ghost_travel >= 9999:
            continue
        d0 = state.dist_from_0[v]
        if d0 < 0:
            continue
        est_alpha = max(0.5, 1.5 * d0)
        if est_alpha > best_alpha:
            best_alpha = est_alpha
            best_v = v

    if best_v is not None:
        # Find earliest bot visit to this slot
        visits = state.slot_visit_steps.get(best_v, [])
        ghost_travel = state.apsp_dist[ghost_pos][best_v]
        ghost_arrival = current_step + ghost_travel
        bot_visit = _find_next_bot_visit(visits, ghost_arrival + 1)
        state.beacon_bot_visit_step = bot_visit if bot_visit else None
    return best_v


def _ghost_pick_farm_target(state, ghost_pos, current_step, total_steps):
    """Pick a secondary slot for the ghost to farm after beacon is placed.
    Prioritize slots the bot will visit on its DFS path (before it stops).
    """
    if not state.slot_set or state.apsp_dist is None:
        return None

    best_score = -1.0
    best_v = None

    for v in state.slot_set:
        if v == state.bot_settled_pos:
            continue  # bot is spinning here, ghost spinning is wasted
        ghost_travel = state.apsp_dist[ghost_pos][v]
        if ghost_travel >= 9999:
            continue

        d0 = state.dist_from_0[v]
        if d0 < 0:
            continue
        est_alpha = max(0.5, 1.5 * d0)
        est_value_per_spin = est_alpha * 2.3

        ghost_arrival = current_step + ghost_travel

        # Find next bot visit to this slot after ghost arrives
        visits = state.slot_visit_steps.get(v, [])
        bot_visit = _find_next_bot_visit(visits, int(ghost_arrival))
        if bot_visit is None or state.bot_settled:
            bot_visit = total_steps + 1

        # Coins ghost can accumulate before bot arrives (capped at 50)
        spins_available = max(0, bot_visit - ghost_arrival)
        est_coins = min(50, spins_available * est_value_per_spin)

        already = state.ghost_spins_at.get(v, 0)
        remaining_cap = max(0.0, 50 - already * est_value_per_spin)
        est_coins = min(est_coins, remaining_cap)

        total_time = max(1, ghost_travel + min(spins_available, int(50 / max(0.1, est_value_per_spin)) + 1))
        score = est_coins / total_time

        if score > best_score:
            best_score = score
            best_v = v

    return best_v


def _ghost_get_next_step(state, ghost_pos, target):
    if ghost_pos == target:
        return -1
    if state.apsp_next is None:
        return -1
    nxt = state.apsp_next[ghost_pos][target]
    if nxt == -1 or nxt == ghost_pos:
        return -1
    return nxt


def _ghost_explore_action(state, ghost_pos, neighbors):
    for n in sorted(neighbors):
        if n not in state.explored:
            return n
    visited_bfs = {ghost_pos}
    q = deque([(ghost_pos, None)])
    while q:
        v, first = q.popleft()
        for u in state.adj.get(v, []):
            if u in visited_bfs:
                continue
            visited_bfs.add(u)
            step_first = first if first is not None else u
            if u not in state.explored:
                return step_first
            q.append((u, step_first))
    return None


def _finish_exploration(state, total_steps):
    state.exploration_done = True
    state.dist_from_0 = _compute_bfs_dist(state.adj, 0)
    state.apsp_dist, state.apsp_next = _compute_apsp(state.adj)
    _ghost_simulate_bot_full(state, total_steps)


def SubmissionGhost(
    step: int,
    total_steps: int,
    pos: int,
    last_pos: int,
    neighbors: List[int],
    has_slot: bool,
    slot_coins: int,
    data: Any,
) -> tuple:
    if data is None:
        state = GhostState()
    else:
        state = data

    _ghost_record_vertex(state, pos, neighbors, has_slot)

    # --- Check if exploration is complete ---
    if not state.exploration_done:
        all_explored = all(v in state.explored for v in state.adj)
        if all_explored and len(state.explored) >= 2:
            if not state.explore_queue or all(v in state.explored for v in state.explore_visited):
                _finish_exploration(state, total_steps)

    # --- Detect if bot has settled (stopped moving) ---
    if not state.bot_settled and state.beacon_slot is not None:
        bot_visit = state.beacon_bot_visit_step
        if bot_visit is not None and step >= bot_visit:
            state.bot_settled = True
            state.bot_settled_pos = state.beacon_slot

    # === EXPLORE PHASE ===
    if state.phase == 'explore':
        if state.exploration_done:
            # Pick the beacon slot and transition
            state.beacon_slot = _ghost_pick_beacon(state, pos, step, total_steps)
            if state.beacon_slot is not None:
                state.phase = 'beacon'
                state.target = state.beacon_slot
            else:
                state.phase = 'farm'

        if state.phase == 'explore':
            action = _ghost_explore_action(state, pos, neighbors)
            if action is None:
                _finish_exploration(state, total_steps)
                state.beacon_slot = _ghost_pick_beacon(state, pos, step, total_steps)
                if state.beacon_slot is not None:
                    state.phase = 'beacon'
                    state.target = state.beacon_slot
                else:
                    state.phase = 'farm'
            else:
                return action, state

    # === BEACON PHASE: travel to beacon slot, spin to fill it ===
    if state.phase == 'beacon':
        target = state.beacon_slot
        if target is None:
            state.phase = 'farm'
        elif pos == target:
            # We're at the beacon slot — spin to accumulate coins
            state.ghost_spins_at[pos] = state.ghost_spins_at.get(pos, 0) + 1

            # Check if we should leave: bot is about to arrive at THIS visit
            bot_visit = state.beacon_bot_visit_step
            if bot_visit is None:
                bot_visit = total_steps + 1

            if step >= bot_visit - 1:
                # Bot arriving — beacon done, transition to farm
                state.beacon_ready = True
                state.phase = 'farm'
                state.target = None
            else:
                # Keep spinning at beacon
                return -1, state
        else:
            # Travel toward beacon
            nxt = _ghost_get_next_step(state, pos, target)
            if nxt != -1 and nxt in neighbors:
                return nxt, state
            # Can't reach — skip to farming
            state.phase = 'farm'
            state.target = None

    # === FARM PHASE: farm secondary slots for bonus collection ===
    if state.phase == 'farm':
        # If at a slot and target matches, spin for a while
        if has_slot and pos == state.target:
            state.ghost_spins_at[pos] = state.ghost_spins_at.get(pos, 0) + 1
            spins = state.ghost_spins_at[pos]

            # Don't spin at same slot as bot (wasted if both spin)
            bot_here_now = state.bot_schedule and state.bot_schedule.get(step) == pos
            if bot_here_now or (state.bot_settled and state.bot_settled_pos == pos):
                state.target = None
            elif spins <= 15:
                return -1, state
            else:
                state.target = None

        # Pick new farm target
        if state.target is None or state.target == pos:
            state.target = _ghost_pick_farm_target(state, pos, step, total_steps)

        if state.target is not None and state.target != pos:
            nxt = _ghost_get_next_step(state, pos, state.target)
            if nxt != -1 and nxt in neighbors:
                return nxt, state
            state.target = None

        # Fallback: at a slot, spin; otherwise move toward nearest slot
        if has_slot:
            state.ghost_spins_at[pos] = state.ghost_spins_at.get(pos, 0) + 1
            return -1, state

        if state.apsp_dist:
            best_d = 9999
            best_s = -1
            for s in state.slot_set:
                if state.bot_settled and s == state.bot_settled_pos:
                    continue
                if state.apsp_dist[pos][s] < best_d:
                    best_d = state.apsp_dist[pos][s]
                    best_s = s
            if best_s >= 0:
                nxt = _ghost_get_next_step(state, pos, best_s)
                if nxt != -1 and nxt in neighbors:
                    return nxt, state

    return -1, state
