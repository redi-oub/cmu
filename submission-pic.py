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
        mask = [[False] * N for _ in range(N)]
        for r in range(N):
            for c in range(N):
                v = self.corrupted[r][c]
                if v is not None:
                    img[r][c] = float(v)
                    mask[r][c] = True

        # Sort missing blocks: most visible neighbors first (cascade fill)
        available = set()
        for br in range(5):
            for bc in range(5):
                if (br, bc) not in self.missing:
                    available.add((br, bc))

        remaining = list(self.missing)

        def _count_avail(br, bc):
            n = 0
            for dbr in (-1, 0, 1):
                for dbc in (-1, 0, 1):
                    if dbr == 0 and dbc == 0:
                        continue
                    if (br + dbr, bc + dbc) in available:
                        n += 1
            return n

        # Two full passes for refinement
        for pass_num in range(2):
            if pass_num == 0:
                order = sorted(remaining, key=lambda b: -_count_avail(b[0], b[1]))
            else:
                order = sorted(remaining, key=lambda b: _count_avail(b[0], b[1]))

            for br, bc in order:
                r1, c1 = br * BS, bc * BS
                block_avg = self.recv_avgs.get((br, bc), self._neighbor_avg(br, bc))

                # Gather border refs from all adjacent available blocks
                refs = []
                for dbr in (-1, 0, 1):
                    for dbc in (-1, 0, 1):
                        if dbr == 0 and dbc == 0:
                            continue
                        nbr, nbc = br + dbr, bc + dbc
                        if not (0 <= nbr < 5 and 0 <= nbc < 5):
                            continue
                        if (nbr, nbc) not in available:
                            continue
                        # Cardinal neighbors: use full edge (2 rows/cols deep)
                        if dbr == 0 or dbc == 0:
                            if dbr == -1:  # block above
                                for d in range(2):
                                    rr = r1 - 1 - d
                                    if 0 <= rr < N:
                                        for cc in range(c1, c1 + BS):
                                            refs.append((rr, cc, img[rr][cc]))
                            elif dbr == 1:  # block below
                                for d in range(2):
                                    rr = r1 + BS + d
                                    if 0 <= rr < N:
                                        for cc in range(c1, c1 + BS):
                                            refs.append((rr, cc, img[rr][cc]))
                            elif dbc == -1:  # block left
                                for d in range(2):
                                    cc = c1 - 1 - d
                                    if 0 <= cc < N:
                                        for rr in range(r1, r1 + BS):
                                            refs.append((rr, cc, img[rr][cc]))
                            elif dbc == 1:  # block right
                                for d in range(2):
                                    cc = c1 + BS + d
                                    if 0 <= cc < N:
                                        for rr in range(r1, r1 + BS):
                                            refs.append((rr, cc, img[rr][cc]))
                        else:
                            # Diagonal: use corner 2x2
                            for dr2 in range(2):
                                for dc2 in range(2):
                                    rr = (r1 - 1 - dr2) if dbr == -1 else (r1 + BS + dr2)
                                    cc = (c1 - 1 - dc2) if dbc == -1 else (c1 + BS + dc2)
                                    if 0 <= rr < N and 0 <= cc < N:
                                        refs.append((rr, cc, img[rr][cc]))

                # Quadrant averages
                quads = [
                    (r1, c1, r1+4, c1+4), (r1, c1+5, r1+4, c1+9),
                    (r1+5, c1, r1+9, c1+4), (r1+5, c1+5, r1+9, c1+9)]
                q_info = {}
                for q in quads:
                    v = self.recv_avgs.get(q)
                    if v is not None:
                        q_info[q] = v

                # Row and column strip averages
                row_avgs = {}
                col_avgs = {}
                for dr in range(BS):
                    v = self.recv_rows.get((br, bc, dr))
                    if v is not None:
                        row_avgs[dr] = v
                for dc in range(BS):
                    v = self.recv_cols.get((br, bc, dc))
                    if v is not None:
                        col_avgs[dc] = v

                for r in range(r1, r1 + BS):
                    for c in range(c1, c1 + BS):
                        dr = r - r1
                        dc = c - c1

                        # Row+col additive model
                        rc_val = None
                        if dr in row_avgs and dc in col_avgs:
                            rc_val = row_avgs[dr] + col_avgs[dc] - block_avg
                        elif dr in row_avgs:
                            rc_val = row_avgs[dr]
                        elif dc in col_avgs:
                            rc_val = col_avgs[dc]

                        q_val = None
                        for qr1, qc1, qr2, qc2 in quads:
                            if qr1 <= r <= qr2 and qc1 <= c <= qc2 and (qr1, qc1, qr2, qc2) in q_info:
                                q_val = q_info[(qr1, qc1, qr2, qc2)]
                                break

                        if refs:
                            tw, tv = 0.0, 0.0
                            for rr, rc2, rv in refs:
                                d2 = (r - rr) ** 2 + (c - rc2) ** 2
                                if d2 == 0:
                                    tw, tv = 1.0, rv
                                    break
                                w = 1.0 / (d2 * d2 ** 0.5)  # 1/d^3
                                tw += w
                                tv += w * rv
                            idw = tv / tw if tw > 0 else block_avg
                        else:
                            idw = block_avg

                        # Combine available signals
                        if rc_val is not None:
                            base = max(0.0, min(1.0, rc_val))
                            img[r][c] = 0.6 * base + 0.4 * idw
                        elif q_val is not None:
                            img[r][c] = 0.4 * idw + 0.6 * q_val
                        else:
                            img[r][c] = idw

                # Shift to match known block average
                avg = self.recv_avgs.get((br, bc))
                if avg is not None:
                    bsum = sum(img[rr][cc] for rr in range(r1, r1+BS) for cc in range(c1, c1+BS))
                    shift = avg - bsum / (BS * BS)
                    if abs(shift) > 0.001:
                        for r in range(r1, r1 + BS):
                            for c in range(c1, c1 + BS):
                                img[r][c] = max(0.0, min(1.0, img[r][c] + shift))

                # Mark this block as available for subsequent blocks
                if pass_num == 0:
                    available.add((br, bc))

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
