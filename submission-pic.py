"""test3b: test2 + receive_messages extracting value + use in recovery. NO init computation."""
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
RegionRequest = None
RegionAverageRequest = None
_types_resolved = False

def _ensure_types():
    global RegionRequest, RegionAverageRequest, _types_resolved
    if _types_resolved:
        return
    _types_resolved = True
    _main = sys.modules.get('__main__')
    if _main:
        RegionRequest = getattr(_main, 'RegionRequest', None)
        RegionAverageRequest = getattr(_main, 'RegionAverageRequest', None)


class SubmissionStrategy(Strategy):
    def __init__(self, corrupted):
        super().__init__(corrupted)
        self.corrupted = corrupted
        self.missing = set()
        self.req_order = []
        self.recv_avgs = {}
        for br in range(5):
            for bc in range(5):
                if corrupted[br * 10][bc * 10] is None:
                    self.missing.add((br, bc))

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
                val = getattr(msg, 'value', None)
                if val is not None:
                    self.recv_avgs[self.req_order[i]] = float(val)
        except Exception:
            pass

    def recover(self):
        N = 50
        result = []
        for r in range(N):
            row = []
            for c in range(N):
                v = self.corrupted[r][c]
                if v is not None:
                    row.append(v)
                else:
                    avg = self.recv_avgs.get((r // 10, c // 10))
                    row.append(avg if avg is not None else 0.5)
            result.append(row)
        return result
