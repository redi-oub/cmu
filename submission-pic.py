"""CMIMC PIC - Incremental from working stub."""
import sys

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

# Lazy types
RegionRequest = None
RegionAverageRequest = None
Message = None
_types_resolved = False

def _ensure_types():
    global RegionRequest, RegionAverageRequest, Message, _types_resolved
    if _types_resolved:
        return
    _types_resolved = True
    _main = sys.modules.get('__main__')
    if _main:
        RegionRequest = getattr(_main, 'RegionRequest', None)
        RegionAverageRequest = getattr(_main, 'RegionAverageRequest', None)
        Message = getattr(_main, 'Message', None)


class SubmissionStrategy(Strategy):
    def __init__(self, corrupted):
        super().__init__(corrupted)
        self.corrupted = corrupted
        self.n = 50
        self.bs = 10
        self.missing = set()
        self.visible = set()
        self.vis_means = {}
        self.recv_avgs = {}
        self.recv_pixels = {}
        self.req_meta = []
        self.global_mean = 0.5

        for br in range(5):
            for bc in range(5):
                if corrupted[br*10][bc*10] is None:
                    self.missing.add((br, bc))
                else:
                    self.visible.add((br, bc))

        all_vals = []
        for br, bc in self.visible:
            s = 0.0
            for r in range(br*10, br*10+10):
                for c in range(bc*10, bc*10+10):
                    s += corrupted[r][c]
                    all_vals.append(corrupted[r][c])
            self.vis_means[(br,bc)] = s / 100.0
        if all_vals:
            self.global_mean = sum(all_vals) / len(all_vals)

    def make_requests(self):
        _ensure_types()
        if RegionAverageRequest is None or RegionRequest is None:
            return []
        reqs = []
        meta = []
        BS = self.bs
        for br, bc in sorted(self.missing):
            r1, c1 = br*BS, bc*BS
            reqs.append(RegionAverageRequest(r1, c1, r1+9, c1+9))
            meta.append(("avg", br, bc))
        for br, bc in sorted(self.missing):
            r1, c1 = br*BS, bc*BS
            for dr, dc in [(5,5),(2,2),(2,7),(7,2),(7,7),(0,5),(9,5),(5,0),(5,9)]:
                reqs.append(RegionRequest(r1+dr, c1+dc, r1+dr, c1+dc))
                meta.append(("pix", br, bc, r1+dr, c1+dc))
        self.req_meta = meta
        return reqs

    def receive_requests(self, requests):
        return [None] * len(requests)

    def receive_messages(self, messages):
        for i, msg in enumerate(messages):
            if msg is None or i >= len(self.req_meta):
                continue
            m = self.req_meta[i]
            # Extract value
            val = None
            if isinstance(msg, (int, float)):
                val = float(msg)
            else:
                val = getattr(msg, 'value', None)
                if val is None:
                    # Try first float field from __dict__
                    try:
                        for v in msg.__dict__.values():
                            if isinstance(v, (int, float)):
                                val = float(v)
                                break
                    except Exception:
                        pass
            if val is None:
                continue
            val = max(0.0, min(1.0, float(val)))

            if m[0] == "avg":
                self.recv_avgs[(m[1], m[2])] = val
            elif m[0] == "pix":
                # Use metadata position (not msg.row/col which may use unknown names)
                self.recv_pixels[(m[3], m[4])] = val

    def recover(self):
        N = self.n
        BS = self.bs
        result = []
        for r in range(N):
            row = []
            for c in range(N):
                v = self.corrupted[r][c]
                if v is not None:
                    row.append(float(v))
                else:
                    # Find which block
                    br, bc = r // BS, c // BS
                    # Use received pixel if available
                    pv = self.recv_pixels.get((r, c))
                    if pv is not None:
                        row.append(float(pv))
                    else:
                        # Use block average or neighbor average or global mean
                        avg = self.recv_avgs.get((br, bc))
                        if avg is None:
                            avg = self._neighbor_avg(br, bc)
                        row.append(float(avg))
            result.append(row)
        return result

    def _neighbor_avg(self, br, bc):
        total, cnt = 0.0, 0
        for dbr in (-1, 0, 1):
            for dbc in (-1, 0, 1):
                if dbr == 0 and dbc == 0:
                    continue
                nb = (br+dbr, bc+dbc)
                if nb in self.vis_means:
                    total += self.vis_means[nb]; cnt += 1
                elif nb in self.recv_avgs:
                    total += self.recv_avgs[nb]; cnt += 1
        return total/cnt if cnt > 0 else self.global_mean
