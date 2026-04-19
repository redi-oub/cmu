"""CMIMC PIC - Minimal increment over proven test2 base.
Adds: receive_messages value extraction + use averages in recovery.
NO introspection, NO extra request types, NO responding to opponent.
"""
import sys

# Try every possible way to get Strategy
Strategy = None
for _mod_name in ['strategy', 'game', 'game_types']:
    try:
        _mod = __import__(_mod_name)
        if hasattr(_mod, 'Strategy'):
            Strategy = _mod.Strategy
            break
    except Exception:
        pass

if Strategy is None:
    _main = sys.modules.get('__main__')
    if _main and hasattr(_main, 'Strategy'):
        Strategy = _main.Strategy

if Strategy is None:
    class Strategy:
        def __init__(self, corrupted):
            pass

# Lazy types - resolved once when make_requests is first called
RegionAverageRequest = None
_types_resolved = False

def _ensure_types():
    global RegionAverageRequest, _types_resolved
    if _types_resolved:
        return
    _types_resolved = True
    _main = sys.modules.get('__main__')
    if _main:
        RegionAverageRequest = getattr(_main, 'RegionAverageRequest', None)


class SubmissionStrategy(Strategy):
    def __init__(self, corrupted):
        super().__init__(corrupted)
        self.corrupted = corrupted
        self.missing = set()
        self.visible = set()
        self.vis_means = {}
        self.req_order = []
        self.recv_avgs = {}

        for br in range(5):
            for bc in range(5):
                if corrupted[br * 10][bc * 10] is None:
                    self.missing.add((br, bc))
                else:
                    self.visible.add((br, bc))

        for br, bc in self.visible:
            s = 0.0
            for r in range(br * 10, br * 10 + 10):
                for c in range(bc * 10, bc * 10 + 10):
                    s += corrupted[r][c]
            self.vis_means[(br, bc)] = s / 100.0

    def make_requests(self):
        try:
            _ensure_types()
            if RegionAverageRequest is None:
                return []
            reqs = []
            order = []
            for br, bc in sorted(self.missing):
                r1, c1 = br * 10, bc * 10
                reqs.append(RegionAverageRequest(r1, c1, r1 + 9, c1 + 9))
                order.append((br, bc))
            self.req_order = order
            return reqs
        except Exception:
            return []

    def receive_requests(self, requests):
        return [None] * len(requests)

    def receive_messages(self, messages):
        try:
            for i, msg in enumerate(messages):
                if msg is None or i >= len(self.req_order):
                    continue
                br, bc = self.req_order[i]
                # Try simple attribute access - no introspection
                val = None
                if isinstance(msg, (int, float)):
                    val = float(msg)
                else:
                    for attr in ('value', 'mean', 'average', 'val'):
                        v = getattr(msg, attr, None)
                        if v is not None and isinstance(v, (int, float)):
                            val = float(v)
                            break
                if val is not None:
                    self.recv_avgs[(br, bc)] = max(0.0, min(1.0, val))
        except Exception:
            pass

    def recover(self):
        try:
            return self._do_recover()
        except Exception:
            pass
        # Fallback: exact same as test2
        N = 50
        result = []
        for r in range(N):
            row = []
            for c in range(N):
                v = self.corrupted[r][c]
                row.append(float(v) if v is not None else 0.5)
            result.append(row)
        return result

    def _do_recover(self):
        N = 50
        BS = 10
        result = []
        for r in range(N):
            row = []
            for c in range(N):
                v = self.corrupted[r][c]
                if v is not None:
                    row.append(float(v))
                else:
                    br, bc = r // BS, c // BS
                    avg = self.recv_avgs.get((br, bc))
                    if avg is not None:
                        row.append(avg)
                    else:
                        row.append(self._neighbor_avg(br, bc))
            result.append(row)
        return result

    def _neighbor_avg(self, br, bc):
        total, cnt = 0.0, 0
        for dbr in (-1, 0, 1):
            for dbc in (-1, 0, 1):
                if dbr == 0 and dbc == 0:
                    continue
                nb = (br + dbr, bc + dbc)
                if nb in self.vis_means:
                    total += self.vis_means[nb]
                    cnt += 1
                elif nb in self.recv_avgs:
                    total += self.recv_avgs[nb]
                    cnt += 1
        return total / cnt if cnt > 0 else 0.5
