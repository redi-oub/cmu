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
        self.vis_means = {}
        self.is_binary = False
        self.req_order = []
        self.recv_avgs = {}
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
                if val is None:
                    continue
                val = float(val)
                m = self.req_order[i]
                if m[0] == 'avg':
                    self.recv_avgs[(m[1], m[2])] = val
                elif m[0] == 'qavg':
                    self.recv_avgs[(m[3], m[4], m[5], m[6])] = val
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
        for r in range(N):
            for c in range(N):
                v = self.corrupted[r][c]
                if v is not None:
                    if self.is_binary:
                        img[r][c] = 1.0 if v >= 0.5 else 0.0
                    else:
                        img[r][c] = float(v)

        for br, bc in self.missing:
            r1, c1 = br * BS, bc * BS
            block_avg = self.recv_avgs.get((br, bc), self._neighbor_avg(br, bc))

            refs = []
            vis = self.vis_means
            if br > 0 and (br - 1, bc) in vis:
                for c in range(c1, c1 + BS):
                    refs.append((r1 - 1, c, img[r1 - 1][c]))
            if br < 4 and (br + 1, bc) in vis:
                for c in range(c1, c1 + BS):
                    refs.append((r1 + BS, c, img[r1 + BS][c]))
            if bc > 0 and (br, bc - 1) in vis:
                for r in range(r1, r1 + BS):
                    refs.append((r, c1 - 1, img[r][c1 - 1]))
            if bc < 4 and (br, bc + 1) in vis:
                for r in range(r1, r1 + BS):
                    refs.append((r, c1 + BS, img[r][c1 + BS]))
            for dbr, dbc in [(-1, -1), (-1, 1), (1, -1), (1, 1)]:
                nbr, nbc = br + dbr, bc + dbc
                if (nbr, nbc) in vis:
                    cr2 = r1 + (BS if dbr == 1 else -1)
                    cc2 = c1 + (BS if dbc == 1 else -1)
                    if 0 <= cr2 < N and 0 <= cc2 < N:
                        refs.append((cr2, cc2, img[cr2][cc2]))

            quads = [
                (r1, c1, r1+4, c1+4), (r1, c1+5, r1+4, c1+9),
                (r1+5, c1, r1+9, c1+4), (r1+5, c1+5, r1+9, c1+9)]
            q_info = {}
            for q in quads:
                v = self.recv_avgs.get(q)
                if v is not None:
                    q_info[q] = v

            for r in range(r1, r1 + BS):
                for c in range(c1, c1 + BS):
                    q_val = None
                    for qr1, qc1, qr2, qc2 in quads:
                        if qr1 <= r <= qr2 and qc1 <= c <= qc2 and (qr1, qc1, qr2, qc2) in q_info:
                            q_val = q_info[(qr1, qc1, qr2, qc2)]
                            break
                    if refs:
                        tw, tv = 0.0, 0.0
                        for rr, rc, rv in refs:
                            d2 = (r - rr) ** 2 + (c - rc) ** 2
                            if d2 == 0:
                                tw, tv = 1.0, rv
                                break
                            w = 1.0 / d2
                            tw += w
                            tv += w * rv
                        idw = tv / tw if tw > 0 else block_avg
                        if q_val is not None:
                            img[r][c] = 0.5 * idw + 0.5 * q_val
                        else:
                            img[r][c] = idw
                    elif q_val is not None:
                        img[r][c] = q_val
                    else:
                        img[r][c] = block_avg

            avg = self.recv_avgs.get((br, bc))
            if avg is not None:
                bsum = sum(img[rr][cc] for rr in range(r1, r1+BS) for cc in range(c1, c1+BS))
                shift = avg - bsum / (BS * BS)
                if abs(shift) > 0.001:
                    for r in range(r1, r1 + BS):
                        for c in range(c1, c1 + BS):
                            img[r][c] = max(0.0, min(1.0, img[r][c] + shift))

        # Binary thresholding pass
        if self.is_binary:
            for br, bc in self.missing:
                r1, c1 = br * BS, bc * BS
                avg = self.recv_avgs.get((br, bc), self._neighbor_avg(br, bc))
                count_1 = max(0, min(BS*BS, round(avg * BS * BS)))
                pixels = []
                for r in range(r1, r1 + BS):
                    for c in range(c1, c1 + BS):
                        pixels.append((img[r][c], r, c))
                pixels.sort(key=lambda x: -x[0])
                for i, (_, r, c) in enumerate(pixels):
                    img[r][c] = 1.0 if i < count_1 else 0.0

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
