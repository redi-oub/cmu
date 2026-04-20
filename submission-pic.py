"""vis_means + quadrant avgs + IDW recovery (safe)."""
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
        self.missing = set()
        self.vis_means = {}
        self.is_binary = False
        self.req_order = []
        self.recv_avgs = {}
        self.recv_rows = {}
        self.recv_cols = {}
        all_vals = []
        for br in range(5):
            for bc in range(5):
                if corrupted[br * 10][bc * 10] is None:
                    self.missing.add((br, bc))
                else:
                    s = 0.0
                    cnt = 0
                    for r in range(br * 10, br * 10 + 10):
                        for c in range(bc * 10, bc * 10 + 10):
                            v = corrupted[r][c]
                            if v is not None:
                                s += v
                                cnt += 1
                                all_vals.append(v)
                    if cnt > 0:
                        self.vis_means[(br, bc)] = s / cnt
        if all_vals:
            near_0 = sum(1 for v in all_vals if v < 0.15)
            near_1 = sum(1 for v in all_vals if v > 0.85)
            nn = len(all_vals)
            self.is_binary = (near_0 + near_1) / nn > 0.85

    def make_requests(self):
        try:
            _ensure_types()
            if RegionAverageRequest is None:
                return []
            reqs = []
            order = []
            sm = sorted(self.missing)
            # Block averages
            for br, bc in sm:
                r1, c1 = br * 10, bc * 10
                reqs.append(RegionAverageRequest(r1, c1, r1 + 9, c1 + 9))
                order.append(('avg', br, bc))
            # Quadrant averages
            for br, bc in sm:
                r1, c1 = br * 10, bc * 10
                for qr1, qc1, qr2, qc2 in [
                    (r1, c1, r1+4, c1+4), (r1, c1+5, r1+4, c1+9),
                    (r1+5, c1, r1+9, c1+4), (r1+5, c1+5, r1+9, c1+9)]:
                    reqs.append(RegionAverageRequest(qr1, qc1, qr2, qc2))
                    order.append(('qavg', br, bc, qr1, qc1, qr2, qc2))
            # Row strip averages (1 row x 10 cols each)
            for br, bc in sm:
                r1, c1 = br * 10, bc * 10
                for dr in range(10):
                    reqs.append(RegionAverageRequest(r1+dr, c1, r1+dr, c1+9))
                    order.append(('ravg', br, bc, dr))
            # Column strip averages (10 rows x 1 col each)
            for br, bc in sm:
                r1, c1 = br * 10, bc * 10
                for dc in range(10):
                    reqs.append(RegionAverageRequest(r1, c1+dc, r1+9, c1+dc))
                    order.append(('cavg', br, bc, dc))
            self.req_order = order
            return reqs
        except Exception:
            return []

    def receive_requests(self, requests):
        try:
            _ensure_types()
            if Message is None:
                return [None] * len(requests)
            responses = [None] * len(requests)
            n_ans = 0
            for i, req in enumerate(requests):
                if n_ans >= 40:
                    break
                r1 = getattr(req, 'r1', None)
                c1 = getattr(req, 'c1', None)
                r2 = getattr(req, 'r2', None)
                c2 = getattr(req, 'c2', None)
                if r1 is None or c1 is None or r2 is None or c2 is None:
                    continue
                if r1 < 0 or c1 < 0 or r2 >= 50 or c2 >= 50:
                    continue
                s, cnt, ok = 0.0, 0, True
                for r in range(r1, r2 + 1):
                    for c in range(c1, c2 + 1):
                        v = self.corrupted[r][c]
                        if v is None:
                            ok = False
                            break
                        s += v
                        cnt += 1
                    if not ok:
                        break
                if ok and cnt > 0:
                    responses[i] = Message(value=s / cnt)
                    n_ans += 1
            return responses
        except Exception:
            return [None] * len(requests)

    def receive_messages(self, messages):
        try:
            for i, msg in enumerate(messages):
                if msg is None or i >= len(self.req_order):
                    continue
                val = getattr(msg, 'value', None)
                if val is None:
                    continue
                val = float(val)
                m = self.req_order[i]
                if m[0] == 'avg':
                    self.recv_avgs[(m[1], m[2])] = val
                elif m[0] == 'qavg':
                    self.recv_avgs[(m[3], m[4], m[5], m[6])] = val
                elif m[0] == 'ravg':
                    self.recv_rows[(m[1], m[2], m[3])] = val
                elif m[0] == 'cavg':
                    self.recv_cols[(m[1], m[2], m[3])] = val

        except Exception:
            pass

    def recover(self):
        try:
            return self._do_recover()
        except Exception:
            pass
        # Fallback: flat
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
        img = [[0.0] * N for _ in range(N)]

        # Fill visible pixels (denoise binary images)
        for r in range(N):
            for c in range(N):
                v = self.corrupted[r][c]
                if v is not None:
                    if self.is_binary:
                        img[r][c] = 1.0 if v >= 0.5 else 0.0
                    else:
                        img[r][c] = float(v)
                else:
                    # Initialize missing pixels with block average
                    br, bc = r // BS, c // BS
                    if (br, bc) in self.missing:
                        img[r][c] = self.recv_avgs.get((br, bc),
                                                        self._neighbor_avg(br, bc))
                    else:
                        img[r][c] = self.vis_means.get((br, bc), 0.5)

        # Collect ALL missing pixel coordinates
        missing_px = []
        for r in range(N):
            for c in range(N):
                if self.corrupted[r][c] is None:
                    missing_px.append((r, c))

        # Laplacian relaxation (harmonic inpainting)
        for _ in range(80):
            for r, c in missing_px:
                s, cnt = 0.0, 0
                if r > 0:    s += img[r-1][c]; cnt += 1
                if r < 49:   s += img[r+1][c]; cnt += 1
                if c > 0:    s += img[r][c-1]; cnt += 1
                if c < 49:   s += img[r][c+1]; cnt += 1
                if cnt > 0:
                    img[r][c] = s / cnt

        # Block average shift correction
        for br, bc in self.missing:
            r1, c1 = br * BS, bc * BS
            avg = self.recv_avgs.get((br, bc))
            if avg is not None:
                bsum = sum(img[rr][cc]
                           for rr in range(r1, r1+BS)
                           for cc in range(c1, c1+BS))
                shift = avg - bsum / (BS * BS)
                for r in range(r1, r1 + BS):
                    for c in range(c1, c1 + BS):
                        img[r][c] = max(0.0, min(1.0, img[r][c] + shift))

        # Row/col average refinement when available
        for br, bc in self.missing:
            r1, c1 = br * BS, bc * BS
            for dr in range(BS):
                ravg = self.recv_rows.get((br, bc, dr))
                if ravg is not None:
                    cur = sum(img[r1+dr][c1+dc] for dc in range(BS)) / BS
                    shift = ravg - cur
                    for dc in range(BS):
                        img[r1+dr][c1+dc] = max(0.0, min(1.0,
                            img[r1+dr][c1+dc] + shift))
            for dc in range(BS):
                cavg = self.recv_cols.get((br, bc, dc))
                if cavg is not None:
                    cur = sum(img[r1+dr][c1+dc] for dr in range(BS)) / BS
                    shift = cavg - cur
                    for dr in range(BS):
                        img[r1+dr][c1+dc] = max(0.0, min(1.0,
                            img[r1+dr][c1+dc] + shift))

        # Quick smoothing pass to fix discontinuities from shifting
        for _ in range(10):
            for r, c in missing_px:
                s, cnt = 0.0, 0
                if r > 0:    s += img[r-1][c]; cnt += 1
                if r < 49:   s += img[r+1][c]; cnt += 1
                if c > 0:    s += img[r][c-1]; cnt += 1
                if c < 49:   s += img[r][c+1]; cnt += 1
                if cnt > 0:
                    img[r][c] = s / cnt

        # Re-apply block average after smoothing
        for br, bc in self.missing:
            r1, c1 = br * BS, bc * BS
            avg = self.recv_avgs.get((br, bc))
            if avg is not None:
                bsum = sum(img[rr][cc]
                           for rr in range(r1, r1+BS)
                           for cc in range(c1, c1+BS))
                shift = avg - bsum / (BS * BS)
                for r in range(r1, r1 + BS):
                    for c in range(c1, c1 + BS):
                        img[r][c] = max(0.0, min(1.0, img[r][c] + shift))

        # Binary: per-quadrant thresholding
        if self.is_binary:
            for br, bc in self.missing:
                r1, c1 = br * BS, bc * BS
                block_avg = self.recv_avgs.get((br, bc),
                                                self._neighbor_avg(br, bc))
                quads = [
                    (r1, c1, r1+4, c1+4), (r1, c1+5, r1+4, c1+9),
                    (r1+5, c1, r1+9, c1+4), (r1+5, c1+5, r1+9, c1+9)]
                for qr1, qc1, qr2, qc2 in quads:
                    qavg = self.recv_avgs.get((qr1, qc1, qr2, qc2),
                                               block_avg)
                    qsize = (qr2 - qr1 + 1) * (qc2 - qc1 + 1)
                    count_1 = max(0, min(qsize, round(qavg * qsize)))
                    pixels = []
                    for r in range(qr1, qr2 + 1):
                        for c in range(qc1, qc2 + 1):
                            pixels.append((img[r][c], r, c))
                    pixels.sort(key=lambda x: -x[0])
                    for idx, (_, r, c) in enumerate(pixels):
                        img[r][c] = 1.0 if idx < count_1 else 0.0

        result = []
        for r in range(N):
            row = []
            for c in range(N):
                row.append(max(0.0, min(1.0, img[r][c])))
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
