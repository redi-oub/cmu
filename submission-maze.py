"""
Maze Optimization – Bot & Ghost Submission
Strategy: Deterministic DFS bot + BFS-exploring ghost with greedy farming.
The ghost simulates the bot's exact DFS to predict its path, then farms
high-value slots timed for bot collection.
"""
import logging
from collections import deque
from typing import Any, List

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Bot memory encoding  (95 bytes = 760 bits, sys.getsizeof -> 128)
#
# Layout (bit offsets):
#   [0..99]       visited  (100 bits)
#   [100..199]    has_slot (100 bits)
#   [200..203]    sweep_count (4 bits, 0-15)
#   [204]         staying  (1 bit)
#   [205..211]    stack_size (7 bits, 0-78)
#   [212..759]    stack entries (78 slots * 7 bits each)
#
# Total: 212 + 546 = 758 bits, fits in 760.
# ---------------------------------------------------------------------------

_VISITED_OFF = 0
_SLOT_OFF = 100
_SWEEP_OFF = 200
_STAY_OFF = 204
_SSIZE_OFF = 205
_STACK_OFF = 212
_MAX_STACK = 78
_TOTAL_BITS = 760
_TOTAL_BYTES = 95


def _pack_bot_state(visited, has_slot, sweep_count, staying, stack):
    """Pack bot state into a 95-byte bytes object."""
    buf = bytearray(_TOTAL_BYTES)

    # Helper: set bit at position
    def setbit(pos):
        buf[pos >> 3] |= 1 << (pos & 7)

    for i in range(100):
        if visited[i]:
            setbit(_VISITED_OFF + i)
        if has_slot[i]:
            setbit(_SLOT_OFF + i)

    # sweep_count: 4 bits at offset 200
    sc = sweep_count & 0xF
    for b in range(4):
        if sc & (1 << b):
            setbit(_SWEEP_OFF + b)

    # staying: 1 bit
    if staying:
        setbit(_STAY_OFF)

    # stack_size: 7 bits
    ss = min(len(stack), _MAX_STACK)
    for b in range(7):
        if ss & (1 << b):
            setbit(_SSIZE_OFF + b)

    # stack entries (top of stack = end of list stored first for easy truncation)
    # Store the top _MAX_STACK entries if stack is longer
    start = max(0, len(stack) - _MAX_STACK)
    for idx in range(ss):
        val = stack[start + idx] & 0x7F
        base = _STACK_OFF + idx * 7
        for b in range(7):
            if val & (1 << b):
                setbit(base + b)

    return bytes(buf)


def _unpack_bot_state(data):
    """Unpack bot state from bytes."""
    buf = data

    def getbit(pos):
        return (buf[pos >> 3] >> (pos & 7)) & 1

    def getbits(off, count):
        val = 0
        for b in range(count):
            val |= getbit(off + b) << b
        return val

    visited = [False] * 100
    has_slot = [False] * 100
    for i in range(100):
        visited[i] = bool(getbit(_VISITED_OFF + i))
        has_slot[i] = bool(getbit(_SLOT_OFF + i))

    sweep_count = getbits(_SWEEP_OFF, 4)
    staying = bool(getbit(_STAY_OFF))
    stack_size = getbits(_SSIZE_OFF, 7)

    stack = []
    for idx in range(stack_size):
        val = getbits(_STACK_OFF + idx * 7, 7)
        stack.append(val)

    return visited, has_slot, sweep_count, staying, stack


# ---------------------------------------------------------------------------
# Deterministic DFS logic (shared between bot execution and ghost simulation)
# ---------------------------------------------------------------------------

def _dfs_next_action(pos, neighbors, visited, has_slot, sweep_count, staying, stack, slot_coins, has_slot_here):
    """
    Deterministic DFS step. Returns (action, visited, has_slot, sweep_count, staying, stack).
    action = -1 (stay) or neighbor vertex ID.
    Bot NEVER stays — always moves per DFS for perfect ghost predictability.
    """
    # Record current vertex info
    visited[pos] = True
    if has_slot_here:
        has_slot[pos] = True

    # Sort neighbors: ascending on even sweeps, descending on odd
    reverse = (sweep_count % 2 == 1)
    sorted_neighbors = sorted(neighbors, reverse=reverse)

    # Find first unvisited neighbor
    next_v = None
    for n in sorted_neighbors:
        if not visited[n]:
            next_v = n
            break

    if next_v is not None:
        # Advance: push current position onto path stack, move to next_v
        stack.append(pos)
        if len(stack) > _MAX_STACK:
            stack = stack[-_MAX_STACK:]  # keep top entries
        return next_v, visited, has_slot, sweep_count, staying, stack
    else:
        # Backtrack: pop from stack
        if stack:
            parent = stack.pop()
            return parent, visited, has_slot, sweep_count, staying, stack
        else:
            # Sweep complete – start new sweep
            sweep_count = (sweep_count + 1) & 0xF
            visited = [False] * 100
            visited[pos] = True
            # Immediately try to advance in new sweep
            reverse = (sweep_count % 2 == 1)
            sorted_neighbors = sorted(neighbors, reverse=reverse)
            for n in sorted_neighbors:
                if not visited[n]:
                    stack.append(pos)
                    return n, visited, has_slot, sweep_count, staying, stack
            # No neighbors at all (isolated vertex?) – stay
            return -1, visited, has_slot, sweep_count, staying, stack


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
        # First step: initialize
        visited_arr = [False] * 100
        has_slot_arr = [False] * 100
        sweep_count = 0
        staying = False
        stack = []
    else:
        visited_arr, has_slot_arr, sweep_count, staying, stack = _unpack_bot_state(data)

    action, visited_arr, has_slot_arr, sweep_count, staying, stack = _dfs_next_action(
        pos, neighbors, visited_arr, has_slot_arr, sweep_count, staying, stack,
        slot_coins, has_slot,
    )

    packed = _pack_bot_state(visited_arr, has_slot_arr, sweep_count, staying, stack)
    return action, packed


# ---------------------------------------------------------------------------
# Ghost: BFS explorer + bot simulator + greedy farmer
# ---------------------------------------------------------------------------

class GhostState:
    """Full ghost memory state (up to 1 MB)."""
    __slots__ = (
        'adj', 'slot_set', 'explored', 'dist_from_0',
        'apsp_next', 'apsp_dist',
        'bot_visited', 'bot_has_slot', 'bot_sweep', 'bot_staying', 'bot_stack',
        'bot_pos', 'bot_sim_step',
        'exploration_done', 'explore_queue', 'explore_visited',
        'phase', 'target', 'ghost_spins_at',
        'bot_schedule', 'slot_next_bot_visit',
    )

    def __init__(self):
        self.adj = {}           # vertex -> sorted list of neighbors
        self.slot_set = set()   # vertices with slot machines
        self.explored = set()   # vertices whose neighbors we know
        self.dist_from_0 = []   # BFS distance from vertex 0
        self.apsp_next = None   # next-hop matrix for shortest paths (100x100)
        self.apsp_dist = None   # distance matrix (100x100)

        # Bot simulation state
        self.bot_visited = [False] * 100
        self.bot_has_slot = [False] * 100
        self.bot_sweep = 0
        self.bot_staying = False
        self.bot_stack = []
        self.bot_pos = 0
        self.bot_sim_step = 0   # last step simulated

        # Ghost exploration state
        self.exploration_done = False
        self.explore_queue = deque([0])
        self.explore_visited = {0}

        # Ghost farming state
        self.phase = 'explore'  # 'explore' or 'farm'
        self.target = None      # target vertex to farm
        self.ghost_spins_at = {}  # vertex -> number of ghost spins since last bot visit

        # Precomputed bot schedule: step -> vertex
        self.bot_schedule = None
        # For each slot: next step at which bot visits it
        self.slot_next_bot_visit = {}


def _ghost_record_vertex(state, pos, neighbors, has_slot_here):
    """Record graph info discovered at current position."""
    state.adj[pos] = sorted(neighbors)
    state.explored.add(pos)
    if has_slot_here:
        state.slot_set.add(pos)
    for n in neighbors:
        if n not in state.adj:
            state.adj[n] = []  # placeholder
        if n not in state.explore_visited:
            state.explore_visited.add(n)
            state.explore_queue.append(n)


def _compute_bfs_dist(adj, source, n=100):
    """BFS from source, return distance dict."""
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
    """All-pairs shortest paths using BFS from each vertex. Returns (dist, next_hop)."""
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
                    # next hop from src to u = first step on path
                    nx[u] = nx[v] if v != src else u
                    q.append(u)
    return dist, nxt


def _ghost_simulate_bot_full(state, total_steps):
    """Simulate the bot's entire DFS trajectory using the ghost's full graph knowledge.
    Produces bot_schedule: dict mapping step -> vertex for all 2000 steps.
    Also computes slot_next_bot_visit for scheduling.
    """
    # Reset simulation state
    bv = [False] * 100
    bhs = [False] * 100
    bsw = 0
    bst = False
    bsk = []
    bpos = 0
    
    schedule = {}
    # For each slot, collect list of visit steps
    slot_visits = {v: [] for v in state.slot_set}
    
    for s in range(1, total_steps + 1):
        neighbors = state.adj.get(bpos, [])
        has_slot_here = bpos in state.slot_set
        # Bot doesn't stay in simulation (slot_coins=0)
        action, bv, bhs, bsw, bst, bsk = _dfs_next_action(
            bpos, neighbors, bv, bhs, bsw, bst, bsk, 0, has_slot_here,
        )
        if action != -1:
            bpos = action
        schedule[s] = bpos
        if bpos in slot_visits:
            slot_visits[bpos].append(s)
    
    state.bot_schedule = schedule
    
    # For each slot, build a sorted list of bot visit steps
    for v in state.slot_set:
        state.slot_next_bot_visit[v] = slot_visits.get(v, [])


def _ghost_pick_target(state, ghost_pos, current_step, total_steps):
    """Pick the best slot for the ghost to farm next."""
    if not state.slot_set or state.apsp_dist is None:
        return None

    best_score = -1.0
    best_v = None
    apsp_dist = state.apsp_dist

    for v in state.slot_set:
        if apsp_dist[ghost_pos][v] >= 9999:
            continue

        ghost_travel = apsp_dist[ghost_pos][v]
        
        # Estimate slot value: proportional to distance from vertex 0
        d0 = state.dist_from_0[v] if v < len(state.dist_from_0) and state.dist_from_0[v] >= 0 else 1
        est_alpha = max(0.5, 1.5 * d0)  # midpoint of uniform(0, 3*d0), floor at 0.5
        est_value_per_spin = est_alpha * 2.3  # alpha * ln(10)

        # Find next bot visit to this slot after ghost could arrive
        ghost_arrival = current_step + ghost_travel
        next_bot_visit = total_steps + 1  # default: bot never comes
        visits = state.slot_next_bot_visit.get(v, [])
        # Binary search for first visit >= ghost_arrival
        lo, hi = 0, len(visits)
        while lo < hi:
            mid = (lo + hi) // 2
            if visits[mid] < ghost_arrival:
                lo = mid + 1
            else:
                hi = mid
        if lo < len(visits):
            next_bot_visit = visits[lo]
        
        # How many spins ghost can do at this slot before bot arrives
        spins_available = max(0, next_bot_visit - ghost_arrival)
        
        # Accumulated coins from those spins (capped at 50)
        est_coins = min(50, spins_available * est_value_per_spin)
        
        # How many coins already accumulated by ghost (not yet collected by bot)?
        already = state.ghost_spins_at.get(v, 0)
        remaining_cap = max(0, 50 - already * est_value_per_spin)
        est_coins = min(est_coins, remaining_cap)

        # Score: coins per step invested (travel + spinning)
        total_time = max(1, ghost_travel + min(spins_available, 8))
        score = est_coins / total_time
        
        if score > best_score:
            best_score = score
            best_v = v

    return best_v


def _ghost_get_next_step(state, ghost_pos, target):
    """Get the next vertex to move toward target using precomputed paths."""
    if ghost_pos == target:
        return -1  # stay and spin
    if state.apsp_next is None:
        return -1
    nxt = state.apsp_next[ghost_pos][target]
    if nxt == -1 or nxt == ghost_pos:
        return -1
    return nxt


def _ghost_explore_action(state, ghost_pos, neighbors):
    """During exploration, move toward nearest unexplored vertex via BFS."""
    # If there are unexplored neighbors, go to the closest one
    for n in sorted(neighbors):
        if n not in state.explored:
            return n

    # Use BFS to find nearest unexplored vertex through known edges
    visited_bfs = {ghost_pos}
    q = deque([(ghost_pos, None)])  # (vertex, first_step)
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

    # Everything explored
    return None


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

    # Record graph info at current position
    _ghost_record_vertex(state, pos, neighbors, has_slot)

    # Check if exploration is complete
    if not state.exploration_done:
        all_explored = all(v in state.explored for v in state.adj)
        if all_explored and len(state.explored) >= 2:
            if not state.explore_queue or all(v in state.explored for v in state.explore_visited):
                state.exploration_done = True
                state.dist_from_0 = _compute_bfs_dist(state.adj, 0)
                state.apsp_dist, state.apsp_next = _compute_apsp(state.adj)
                # Precompute full bot schedule
                _ghost_simulate_bot_full(state, total_steps)
                state.phase = 'farm'

    if state.phase == 'explore':
        action = _ghost_explore_action(state, pos, neighbors)
        if action is None:
            state.exploration_done = True
            state.dist_from_0 = _compute_bfs_dist(state.adj, 0)
            state.apsp_dist, state.apsp_next = _compute_apsp(state.adj)
            _ghost_simulate_bot_full(state, total_steps)
            state.phase = 'farm'
            action = -1
        return (action if action is not None else -1), state

    # === Farming phase ===
    
    # Track: if bot visited our current slot position, reset our spin count there 
    # (bot collected the coins)
    if state.bot_schedule:
        bot_pos_now = state.bot_schedule.get(step, -1)
        # Reset spin counts for slots the bot is visiting this step
        if bot_pos_now in state.ghost_spins_at:
            state.ghost_spins_at[bot_pos_now] = 0

    # If we're at a slot, track our spins
    if has_slot and (state.target == pos or state.target is None):
        state.ghost_spins_at[pos] = state.ghost_spins_at.get(pos, 0) + 1
        spins_here = state.ghost_spins_at[pos]
        
        # Don't spin at same slot as bot (wasted)
        bot_here = state.bot_schedule and state.bot_schedule.get(step) == pos
        
        if not bot_here and spins_here <= 12:
            # Keep spinning — return stay
            # But re-evaluate target every few spins
            if spins_here % 4 == 0:
                new_target = _ghost_pick_target(state, pos, step, total_steps)
                if new_target is not None and new_target != pos:
                    state.target = new_target
                    nxt = _ghost_get_next_step(state, pos, state.target)
                    if nxt != -1 and nxt in neighbors:
                        return nxt, state
            return -1, state

    # Pick new target if needed
    if state.target is None or state.target == pos:
        state.target = _ghost_pick_target(state, pos, step, total_steps)

    if state.target is not None and state.target != pos:
        action = _ghost_get_next_step(state, pos, state.target)
        if action != -1 and action in neighbors:
            return action, state

    # Fallback: if at a slot, spin; otherwise move toward nearest slot
    if has_slot:
        state.ghost_spins_at[pos] = state.ghost_spins_at.get(pos, 0) + 1
        return -1, state

    best_d = 9999
    best_n = -1
    if state.apsp_dist:
        for s in state.slot_set:
            if state.apsp_dist[pos][s] < best_d:
                best_d = state.apsp_dist[pos][s]
                best_n = s
    if best_n >= 0:
        action = _ghost_get_next_step(state, pos, best_n)
        if action != -1 and action in neighbors:
            return action, state

    return -1, state
