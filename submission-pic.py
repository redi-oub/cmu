"""CMIMC PIC - Full strategy built on proven stub base."""
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
SplitRequest = None
Message = None
_types_resolved = False


def _ensure_types():
    global RegionRequest, RegionAverageRequest, SplitRequest, Message, _types_resolved
    if _types_resolved:
        return
    _types_resolved = True
    _main = sys.modules.get('__main__')
    if _main:
        RegionRequest = getattr(_main, 'RegionRequest', None)
        RegionAverageRequest = getattr(_main, 'RegionAverageRequest', None)
        SplitRequest = getattr(_main, 'SplitRequest', None)
        Message = getattr(_main, 'Message', None)


def _get_rect(req):
    """Extract (r1,c1,r2,c2) from a request object."""
    try:
        dcf = getattr(type(req), '__dataclass_fields__', None)
        if dcf:
            keys = list(dcf.keys())
            if len(keys) >= 4:
                return tuple(int(getattr(req, keys[i])) for i in range(4))
    except Exception:
        pass
    try:
        nf = getattr(type(req), '_fields', None)
        if nf and len(nf) >= 4:
            return tuple(int(getattr(req, nf[i])) for i in range(4))
    except Exception:
        pass
    try:
        code = type(req).__init__.__code__
        names = code.co_varnames[1:code.co_argcount]
        if len(names) >= 4:
            return tuple(int(getattr(req, names[i])) for i in range(4))
    except Exception:
        pass
    try:
        d = req.__dict__
        vals = [v for k, v in d.items() if not k.startswith('_') and isinstance(v, (int, float))]
        if len(vals) >= 4:
            return tuple(int(v) for v in vals[:4])
    except Exception:
        pass
    try:
        if len(req) >= 4:
            return tuple(int(req[i]) for i in range(4))
    except Exception:
        pass
    return None


def _make_msg(**kw):
    """Construct a Message."""
    if Message is not None:
        try:
            return Message(**kw)
        except Exception:
            pass
    class _M:
        pass
    m = _M()
    m.__dict__.update(kw)
    return m


def _extract_value(msg):
    """Get the numeric value from a Message."""
    if msg is None:
        return None
    if isinstance(msg, (int, float)):
        return float(msg)
    for attr in ("value", "mean", "average", "val"):
        v = getattr(msg, attr, None)
        if v is not None:
            return float(v)
    try:
        dcf = getattr(type(msg), '__dataclass_fields__', None)
        if dcf:
            for fn in dcf:
                v = getattr(msg, fn, None)
                if isinstance(v, (int, float)):
                    return float(v)
    except Exception:
        pass
    try:
        code = type(msg).__init__.__code__
        for fn in code.co_varnames[1:code.co_argcount]:
            v = getattr(msg, fn, None)
            if isinstance(v, (int, float)):
                return float(v)
    except Exception:
        pass
    return None


class SubmissionStrategy(Strategy):
    def __init__(self, corrupted):
        super().__init__(corrupted)
        self.corrupted = corrupted
        self.n = 50
        self.bs = 10
        self.missing = set()
        self.visible = set()
        self.vis_means = {}
        self.global_mean = 0.5
        self.is_binary = False
        self.req_meta = []
        self.recv_avgs = {}
        self.recv_pixels = {}
        self.recv_quad_avgs = {}

        for br in range(5):
            for bc in range(5):
                if corrupted[br * 10][bc * 10] is None:
                    self.missing.add((br, bc))
                else:
                    self.visible.add((br, bc))

        all_vals = []
        for br, bc in self.visible:
            s = 0.0
            for r in range(br * 10, br * 10 + 10):
                for c in range(bc * 10, bc * 10 + 10):
                    v = corrupted[r][c]
                    s += v
                    all_vals.append(v)
            self.vis_means[(br, bc)] = s / 100.0
        if all_vals:
            self.global_mean = sum(all_vals) / len(all_vals)
            near_0 = sum(1 for v in all_vals if v < 0.3)
            near_1 = sum(1 for v in all_vals if v > 0.7)
            mid = sum(1 for v in all_vals if 0.35 <= v <= 0.65)
            nn = len(all_vals)
            self.is_binary = (near_0 + near_1) / nn > 0.6 and mid / nn < 0.25

    def make_requests(self):
        try:
            _ensure_types()
            if RegionAverageRequest is None:
                return []
            reqs = []
            meta = []
            BS = self.bs
            sm = sorted(self.missing)

            # Block averages
            for br, bc in sm:
                r1, c1 = br * BS, bc * BS
                reqs.append(RegionAverageRequest(r1, c1, r1 + 9, c1 + 9))
                meta.append(("avg", br, bc))

            # Pixel samples (only if RegionRequest available)
            if RegionRequest is not None:
                for br, bc in sm:
                    r1, c1 = br * BS, bc * BS
                    for dr, dc in [(5,5),(2,2),(2,7),(7,2),(7,7),
                                   (0,5),(9,5),(5,0),(5,9),
                                   (1,5),(5,1),(5,8),(8,5),
                                   (3,3),(3,6),(6,3),(6,6)]:
                        reqs.append(RegionRequest(r1+dr, c1+dc, r1+dr, c1+dc))
                        meta.append(("pix", br, bc, r1+dr, c1+dc))

            # Quadrant averages
            for br, bc in sm:
                r1, c1 = br * BS, bc * BS
                for qr1, qc1, qr2, qc2 in [
                    (r1, c1, r1+4, c1+4), (r1, c1+5, r1+4, c1+9),
                    (r1+5, c1, r1+9, c1+4), (r1+5, c1+5, r1+9, c1+9)]:
                    reqs.append(RegionAverageRequest(qr1, qc1, qr2, qc2))
                    meta.append(("qavg", br, bc, qr1, qc1, qr2, qc2))

            self.req_meta = meta
            return reqs
        except Exception:
            return []

    def receive_requests(self, requests):
        try:
            _ensure_types()
            responses = []
            answered = 0
            for req in requests:
                if answered >= 12:
                    responses.append(None)
                    continue
                resp = self._respond(req)
                responses.append(resp)
                if resp is not None:
                    answered += 1
            return responses
        except Exception:
            return [None] * len(requests)

    def _respond(self, req):
        try:
            rect = _get_rect(req)
            if rect is None:
                return None
            rr1, cc1, rr2, cc2 = rect
            cls_name = type(req).__name__
            if 'Average' in cls_name:
                total, cnt = 0.0, 0
                for r in range(max(0, rr1), min(self.n, rr2 + 1)):
                    for c in range(max(0, cc1), min(self.n, cc2 + 1)):
                        v = self.corrupted[r][c]
                        if v is not None:
                            total += v
                            cnt += 1
                if cnt == 0:
                    return None
                return _make_msg(value=total / cnt)
            elif 'Split' in cls_name:
                v1 = self.corrupted[rr1][cc1] if 0 <= rr1 < self.n and 0 <= cc1 < self.n else None
                v2 = self.corrupted[rr2][cc2] if 0 <= rr2 < self.n and 0 <= cc2 < self.n else None
                if v1 is None or v2 is None:
                    return None
                return _make_msg(value=1.0 if abs(v1 - v2) > 0.15 else 0.0)
            else:
                best = None
                best_d = 1e18
                cr = (rr1 + rr2) * 0.5
                cc_m = (cc1 + cc2) * 0.5
                for r in range(max(0, rr1), min(self.n, rr2 + 1)):
                    for c in range(max(0, cc1), min(self.n, cc2 + 1)):
                        v = self.corrupted[r][c]
                        if v is not None:
                            d = (r - cr) ** 2 + (c - cc_m) ** 2
                            if d < best_d:
                                best = (r, c, v)
                                best_d = d
                if best is None:
                    return None
                return _make_msg(row=best[0], col=best[1], value=best[2])
        except Exception:
            return None

    def receive_messages(self, messages):
        try:
            for i, msg in enumerate(messages):
                if msg is None or i >= len(self.req_meta):
                    continue
                m = self.req_meta[i]
                val = _extract_value(msg)
                if val is None:
                    continue
                val = max(0.0, min(1.0, float(val)))
                if m[0] == "avg":
                    self.recv_avgs[(m[1], m[2])] = val
                elif m[0] == "qavg":
                    self.recv_quad_avgs[(m[3], m[4], m[5], m[6])] = val
                elif m[0] == "pix":
                    self.recv_pixels[(m[3], m[4])] = val
        except Exception:
            pass

    def recover(self):
        try:
            return self._do_recover()
        except Exception:
            pass
        N = self.n
        result = []
        for r in range(N):
            row = []
            for c in range(N):
                v = self.corrupted[r][c]
                row.append(float(v) if v is not None else 0.5)
            result.append(row)
        return result

    def _do_recover(self):
        N = self.n
        BS = self.bs

        img = [[0.0] * N for _ in range(N)]
        mask = [[False] * N for _ in range(N)]
        for r in range(N):
            for c in range(N):
                v = self.corrupted[r][c]
                if v is not None:
                    img[r][c] = float(v)
                    mask[r][c] = True

        if self.is_binary:
            for r in range(N):
                for c in range(N):
                    if mask[r][c]:
                        img[r][c] = 1.0 if img[r][c] >= 0.5 else 0.0

        for (r, c), v in self.recv_pixels.items():
            if 0 <= r < N and 0 <= c < N:
                if self.is_binary:
                    v = 1.0 if v >= 0.5 else 0.0
                img[r][c] = float(v)
                mask[r][c] = True

        for br, bc in self.missing:
            r1, c1 = br * BS, bc * BS
            block_avg = self.recv_avgs.get((br, bc), self._neighbor_avg(br, bc))

            refs = []
            if br > 0 and (br - 1, bc) in self.visible:
                for c in range(c1, c1 + BS):
                    refs.append((r1 - 1, c, img[r1 - 1][c]))
            if br < 4 and (br + 1, bc) in self.visible:
                for c in range(c1, c1 + BS):
                    refs.append((r1 + BS, c, img[r1 + BS][c]))
            if bc > 0 and (br, bc - 1) in self.visible:
                for r in range(r1, r1 + BS):
                    refs.append((r, c1 - 1, img[r][c1 - 1]))
            if bc < 4 and (br, bc + 1) in self.visible:
                for r in range(r1, r1 + BS):
                    refs.append((r, c1 + BS, img[r][c1 + BS]))
            for dbr, dbc in [(-1, -1), (-1, 1), (1, -1), (1, 1)]:
                nbr, nbc = br + dbr, bc + dbc
                if (nbr, nbc) in self.visible:
                    cr = r1 + (BS if dbr == 1 else -1)
                    cc = c1 + (BS if dbc == 1 else -1)
                    if 0 <= cr < N and 0 <= cc < N:
                        refs.append((cr, cc, img[cr][cc]))
            for (pr, pc), pv in self.recv_pixels.items():
                if r1 <= pr < r1 + BS and c1 <= pc < c1 + BS:
                    refs.append((pr, pc, float(pv)))

            quads = [
                (r1, c1, r1+4, c1+4), (r1, c1+5, r1+4, c1+9),
                (r1+5, c1, r1+9, c1+4), (r1+5, c1+5, r1+9, c1+9)]
            q_info = {}
            for q in quads:
                if q in self.recv_quad_avgs:
                    q_info[q] = self.recv_quad_avgs[q]

            for r in range(r1, r1 + BS):
                for c in range(c1, c1 + BS):
                    if mask[r][c]:
                        continue
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
                                tw = 1.0
                                tv = rv
                                break
                            w = 1.0 / d2
                            tw += w
                            tv += w * rv
                        idw = tv / tw if tw > 0 else block_avg
                        img[r][c] = 0.5 * idw + 0.5 * q_val if q_val is not None else idw
                    elif q_val is not None:
                        img[r][c] = q_val
                    else:
                        img[r][c] = block_avg

        if self.is_binary:
            for br, bc in self.missing:
                r1, c1 = br * BS, bc * BS
                avg = self.recv_avgs.get((br, bc), self._neighbor_avg(br, bc))
                pixels = []
                for r in range(r1, r1 + BS):
                    for c in range(c1, c1 + BS):
                        pixels.append((img[r][c], r, c))
                count_1 = max(0, min(100, round(avg * 100)))
                pixels.sort(key=lambda x: -x[0])
                for i, (_, r, c) in enumerate(pixels):
                    img[r][c] = 1.0 if i < count_1 else 0.0
        else:
            for br, bc in self.missing:
                avg = self.recv_avgs.get((br, bc))
                if avg is None:
                    continue
                r1, c1 = br * BS, bc * BS
                for qr1, qc1, qr2, qc2 in [
                    (r1, c1, r1+4, c1+4), (r1, c1+5, r1+4, c1+9),
                    (r1+5, c1, r1+9, c1+4), (r1+5, c1+5, r1+9, c1+9)]:
                    key = (qr1, qc1, qr2, qc2)
                    if key in self.recv_quad_avgs:
                        q_sum, q_cnt = 0.0, 0
                        for r in range(qr1, qr2 + 1):
                            for c in range(qc1, qc2 + 1):
                                q_sum += img[r][c]
                                q_cnt += 1
                        if q_cnt > 0:
                            shift = self.recv_quad_avgs[key] - q_sum / q_cnt
                            for r in range(qr1, qr2 + 1):
                                for c in range(qc1, qc2 + 1):
                                    if not mask[r][c]:
                                        img[r][c] = max(0.0, min(1.0, img[r][c] + shift))
                b_sum = sum(img[r][c] for r in range(r1, r1 + BS) for c in range(c1, c1 + BS))
                shift = avg - b_sum / (BS * BS)
                if abs(shift) > 0.001:
                    for r in range(r1, r1 + BS):
                        for c in range(c1, c1 + BS):
                            if not mask[r][c]:
                                img[r][c] = max(0.0, min(1.0, img[r][c] + shift))

        result = []
        for r in range(N):
            row = []
            for c in range(N):
                v = img[r][c]
                if v != v or v is None:
                    row.append(float(self.global_mean))
                else:
                    row.append(max(0.0, min(1.0, float(v))))
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
        return total / cnt if cnt > 0 else self.global_mean
