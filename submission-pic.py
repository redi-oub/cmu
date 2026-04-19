"""CMIMC PIC - Image Recovery Strategy"""
import sys
import time

# --- Import Strategy at module level (needed for class definition) ---
Strategy = None
for _mn in ['strategy', 'game', 'game_types']:
    try:
        _m = __import__(_mn)
        if hasattr(_m, 'Strategy'):
            Strategy = _m.Strategy
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

# --- Lazy-resolved types (defined by server AFTER importing us) ---
RegionRequest = None
RegionAverageRequest = None
SplitRequest = None
Message = None
_types_resolved = False


def _lookup(name):
    """Find a name in __main__ or known modules."""
    _main = sys.modules.get('__main__')
    if _main:
        val = getattr(_main, name, None)
        if val is not None:
            return val
    for _mn in ['strategy', 'game', 'game_types']:
        _m = sys.modules.get(_mn)
        if _m:
            val = getattr(_m, name, None)
            if val is not None:
                return val
        else:
            try:
                _m = __import__(_mn)
                val = getattr(_m, name, None)
                if val is not None:
                    return val
            except Exception:
                pass
    return None


def _ensure_types():
    global RegionRequest, RegionAverageRequest, SplitRequest, Message, _types_resolved
    if _types_resolved:
        return
    _types_resolved = True
    RegionRequest = _lookup('RegionRequest')
    RegionAverageRequest = _lookup('RegionAverageRequest')
    SplitRequest = _lookup('SplitRequest')
    Message = _lookup('Message')
    if Message is None:
        class _Msg:
            def __init__(self, **kw):
                self.__dict__.update(kw)
        Message = _Msg


def _make_msg(**kw):
    """Safely construct a Message object."""
    try:
        return Message(**kw)
    except Exception:
        pass
    # Fallback: plain object
    class _M:
        pass
    m = _M()
    m.__dict__.update(kw)
    return m


# --- Class field introspection (discovers attribute names from actual classes) ---
_field_cache = {}  # cls -> tuple of field names


def _get_fields(cls):
    """Discover field names of a class using dataclasses.fields or inspect."""
    if cls in _field_cache:
        return _field_cache[cls]
    names = None
    try:
        import dataclasses
        names = tuple(f.name for f in dataclasses.fields(cls))
    except Exception:
        pass
    if names is None:
        try:
            import inspect
            sig = inspect.signature(cls)
            names = tuple(p for p in sig.parameters if p != 'self')
        except Exception:
            pass
    if names is None:
        # Try constructing a dummy and reading __dict__ key order
        try:
            obj = cls(0, 0, 0, 0)
            names = tuple(k for k in obj.__dict__ if not k.startswith('_'))
        except Exception:
            pass
    _field_cache[cls] = names
    return names


def _make_req(cls, a, b, c, d):
    """Construct a request using introspected field names, positional, or kwargs."""
    fields = _get_fields(cls)
    if fields and len(fields) >= 4:
        try:
            return cls(**{fields[0]: a, fields[1]: b, fields[2]: c, fields[3]: d})
        except Exception:
            pass
    try:
        return cls(a, b, c, d)
    except Exception:
        pass
    return None


def _get_rect(req):
    """Extract (r1,c1,r2,c2) from a request using introspected field names."""
    cls = type(req)
    fields = _get_fields(cls)
    if fields and len(fields) >= 4:
        try:
            return tuple(int(getattr(req, fields[i])) for i in range(4))
        except Exception:
            pass
    # Fallback: try __dict__ ordered values
    if hasattr(req, '__dict__'):
        vals = [v for k, v in req.__dict__.items()
                if not k.startswith('_') and isinstance(v, (int, float))]
        if len(vals) >= 4:
            return tuple(int(v) for v in vals[:4])
    return None


class SubmissionStrategy(Strategy):

    def __init__(self, corrupted):
        self._t0 = time.time()
        self._init_ok = False
        self.n = 50
        self.bs = 10
        self.corrupted = corrupted
        self.req_meta = []
        self.recv_avgs = {}
        self.recv_pixels = {}
        self.recv_quad_avgs = {}
        self.global_mean = 0.5
        self.is_binary = False
        self.image = [[0.5] * 50 for _ in range(50)]
        self.mask = [[False] * 50 for _ in range(50)]
        self.visible = set()
        self.missing = set()
        self.vis_means = {}
        try:
            super().__init__(corrupted)
            self._do_init(corrupted)
        except BaseException:
            pass

    def _do_init(self, corrupted):
        N = 50
        BS = 10

        img = [[0.0] * N for _ in range(N)]
        mask = [[False] * N for _ in range(N)]
        for r in range(N):
            row = corrupted[r]
            for c in range(N):
                v = row[c]
                if v is not None:
                    img[r][c] = v
                    mask[r][c] = True
        self.image = img
        self.mask = mask

        visible = set()
        missing = set()
        for br in range(5):
            for bc in range(5):
                if mask[br * BS][bc * BS]:
                    visible.add((br, bc))
                else:
                    missing.add((br, bc))
        self.visible = visible
        self.missing = missing

        vis_means = {}
        all_vals = []
        for br, bc in visible:
            s = 0.0
            for r in range(br * BS, br * BS + BS):
                for c in range(bc * BS, bc * BS + BS):
                    s += img[r][c]
            vis_means[(br, bc)] = s / (BS * BS)
            for r in range(br * BS, br * BS + BS):
                for c in range(bc * BS, bc * BS + BS):
                    all_vals.append(img[r][c])
        self.vis_means = vis_means

        self.global_mean = sum(all_vals) / len(all_vals) if all_vals else 0.5
        self.is_binary = self._detect_binary(all_vals)
        self._init_ok = True

    def make_requests(self):
        try:
            return self._do_make_requests()
        except BaseException:
            return []

    def _do_make_requests(self):
        if not self._init_ok:
            return []
        _ensure_types()
        if RegionAverageRequest is None or RegionRequest is None:
            return []
        reqs = []
        meta = []
        BS = self.bs
        sm = sorted(self.missing)

        def _avg(a, b, c, d):
            obj = _make_req(RegionAverageRequest, a, b, c, d)
            if obj is not None:
                reqs.append(obj)
                return True
            return False

        def _reg(a, b, c, d):
            obj = _make_req(RegionRequest, a, b, c, d)
            if obj is not None:
                reqs.append(obj)
                return True
            return False

        for br, bc in sm:
            r1, c1 = br * BS, bc * BS
            if _avg(r1, c1, r1 + 9, c1 + 9):
                meta.append(("avg", br, bc))

        for br, bc in sm:
            r1, c1 = br * BS, bc * BS
            if _reg(r1 + 5, c1 + 5, r1 + 5, c1 + 5):
                meta.append(("pix", br, bc, r1 + 5, c1 + 5))

        for br, bc in sm:
            r1, c1 = br * BS, bc * BS
            for dr, dc in [(2, 2), (2, 7), (7, 2), (7, 7)]:
                if _reg(r1+dr, c1+dc, r1+dr, c1+dc):
                    meta.append(("pix", br, bc, r1+dr, c1+dc))

        for br, bc in sm:
            r1, c1 = br * BS, bc * BS
            for pr, pc in [(r1, c1+5), (r1+9, c1+5), (r1+5, c1), (r1+5, c1+9)]:
                if _reg(pr, pc, pr, pc):
                    meta.append(("pix", br, bc, pr, pc))

        for br, bc in sm:
            r1, c1 = br * BS, bc * BS
            for qr1, qc1, qr2, qc2 in [
                (r1, c1, r1+4, c1+4), (r1, c1+5, r1+4, c1+9),
                (r1+5, c1, r1+9, c1+4), (r1+5, c1+5, r1+9, c1+9),
            ]:
                if _avg(qr1, qc1, qr2, qc2):
                    meta.append(("qavg", br, bc, qr1, qc1, qr2, qc2))

        for br, bc in sm:
            r1, c1 = br * BS, bc * BS
            for dr, dc in [(1,5),(5,1),(5,8),(8,5),(3,3),(3,6),(6,3),(6,6)]:
                if _reg(r1+dr, c1+dc, r1+dr, c1+dc):
                    meta.append(("pix", br, bc, r1+dr, c1+dc))

        self.req_meta = meta
        return reqs

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
        except BaseException:
            return [None] * len(requests)

    def receive_messages(self, messages):
        try:
            self._do_receive_messages(messages)
        except BaseException:
            pass

    def _do_receive_messages(self, messages):
        msg_fields = None
        for i, msg in enumerate(messages):
            if msg is None or i >= len(self.req_meta):
                continue
            m = self.req_meta[i]
            val = self._extract_value(msg)
            if val is None:
                continue
            val = max(0.0, min(1.0, val))

            if m[0] == "avg":
                self.recv_avgs[(m[1], m[2])] = val
            elif m[0] == "qavg":
                self.recv_quad_avgs[(m[3], m[4], m[5], m[6])] = val
            elif m[0] == "pix":
                row = None
                col = None
                if msg_fields is None:
                    msg_fields = _get_fields(type(msg))
                if msg_fields:
                    for fn in msg_fields:
                        v = getattr(msg, fn, None)
                        if v is None:
                            continue
                        fl = fn.lower()
                        if row is None and ('row' in fl or fl == 'r' or fl == 'y'):
                            row = v
                        elif col is None and ('col' in fl or fl == 'c' or fl == 'x'):
                            col = v
                if row is None:
                    row = getattr(msg, 'row', None)
                if col is None:
                    col = getattr(msg, 'col', None)
                if row is not None and col is not None:
                    self.recv_pixels[(row, col)] = val
                else:
                    self.recv_pixels[(m[3], m[4])] = val

    def recover(self):
        try:
            return self._do_recover()
        except BaseException:
            pass
        # Fallback: return corrupted image with 0.5 for missing
        try:
            N = self.n
            result = []
            for r in range(N):
                row = []
                for c in range(N):
                    v = self.corrupted[r][c]
                    row.append(v if v is not None else 0.5)
                result.append(row)
            return result
        except BaseException:
            return [[0.5] * 50 for _ in range(50)]

    def _do_recover(self):
        N = self.n
        BS = self.bs
        img = self.image
        mask = self.mask

        if self.is_binary:
            for r in range(N):
                for c in range(N):
                    if mask[r][c]:
                        img[r][c] = 1.0 if img[r][c] >= 0.5 else 0.0

        for (r, c), v in self.recv_pixels.items():
            if 0 <= r < N and 0 <= c < N:
                if self.is_binary:
                    v = 1.0 if v >= 0.5 else 0.0
                img[r][c] = v
                mask[r][c] = True

        if self.is_binary:
            self._recover_binary(img, mask)
        else:
            self._recover_continuous(img, mask)

        for r in range(N):
            for c in range(N):
                v = img[r][c]
                if v is None:
                    img[r][c] = self.global_mean
                elif v < 0.0:
                    img[r][c] = 0.0
                elif v > 1.0:
                    img[r][c] = 1.0

        return img

    def _recover_binary(self, img, mask):
        N = self.n
        BS = self.bs

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
                    refs.append((pr, pc, img[pr][pc]))

            for r in range(r1, r1 + BS):
                for c in range(c1, c1 + BS):
                    if mask[r][c]:
                        continue
                    if refs:
                        total_w, total_v = 0.0, 0.0
                        for rr, rc, rv in refs:
                            d2 = (r - rr) * (r - rr) + (c - rc) * (c - rc)
                            if d2 == 0:
                                total_w = 1.0; total_v = rv; break
                            w = 1.0 / d2
                            total_w += w; total_v += w * rv
                        img[r][c] = total_v / total_w if total_w > 0 else block_avg
                    else:
                        img[r][c] = block_avg

        non_fixed = []
        for r in range(N):
            for c in range(N):
                if not mask[r][c]:
                    non_fixed.append((r, c))

        omega = 1.75
        for _it in range(120):
            if time.time() - self._t0 > 0.7:
                break
            max_change = 0.0
            for r, c in non_fixed:
                total = 0.0; cnt = 0
                if r > 0:     total += img[r-1][c]; cnt += 1
                if r < N-1:   total += img[r+1][c]; cnt += 1
                if c > 0:     total += img[r][c-1]; cnt += 1
                if c < N-1:   total += img[r][c+1]; cnt += 1
                if cnt > 0:
                    old = img[r][c]
                    img[r][c] = old + omega * (total / cnt - old)
                    d = abs(img[r][c] - old)
                    if d > max_change: max_change = d
            if max_change < 1e-5:
                break

        for br, bc in self.missing:
            avg = self.recv_avgs.get((br, bc), self._neighbor_avg(br, bc))
            r1, c1 = br * BS, bc * BS
            pixels = []
            for r in range(r1, r1 + BS):
                for c in range(c1, c1 + BS):
                    pixels.append((img[r][c], r, c))
            count_1 = max(0, min(100, round(avg * 100)))
            pixels.sort(key=lambda x: -x[0])
            for i, (_, r, c) in enumerate(pixels):
                img[r][c] = 1.0 if i < count_1 else 0.0

    def _recover_continuous(self, img, mask):
        N = self.n
        BS = self.bs
        quad_avgs = self.recv_quad_avgs

        uniform_blocks = set()
        for br, bc in self.missing:
            avg = self.recv_avgs.get((br, bc))
            if avg is None:
                continue
            r1, c1 = br * BS, bc * BS
            block_pix = []
            for (pr, pc), pv in self.recv_pixels.items():
                if r1 <= pr < r1 + BS and c1 <= pc < c1 + BS:
                    block_pix.append(pv)
            if len(block_pix) >= 2:
                if max(abs(p - avg) for p in block_pix) < 0.08:
                    uniform_blocks.add((br, bc))
                    for r in range(r1, r1 + BS):
                        for c in range(c1, c1 + BS):
                            if not mask[r][c]:
                                img[r][c] = avg
                                mask[r][c] = True

        for br, bc in self.missing:
            if (br, bc) in uniform_blocks:
                continue
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
                    refs.append((pr, pc, img[pr][pc]))

            quads = [
                (r1, c1, r1+4, c1+4), (r1, c1+5, r1+4, c1+9),
                (r1+5, c1, r1+9, c1+4), (r1+5, c1+5, r1+9, c1+9),
            ]
            q_info = {q: quad_avgs[q] for q in quads if q in quad_avgs}

            for r in range(r1, r1 + BS):
                for c in range(c1, c1 + BS):
                    if mask[r][c]:
                        continue
                    q_val = None
                    for qr1, qc1, qr2, qc2 in quads:
                        if qr1 <= r <= qr2 and qc1 <= c <= qc2:
                            if (qr1, qc1, qr2, qc2) in q_info:
                                q_val = q_info[(qr1, qc1, qr2, qc2)]
                            break

                    if refs:
                        total_w, total_v = 0.0, 0.0
                        for rr, rc, rv in refs:
                            d2 = (r - rr) * (r - rr) + (c - rc) * (c - rc)
                            if d2 == 0:
                                total_w = 1.0; total_v = rv; break
                            w = 1.0 / d2
                            total_w += w; total_v += w * rv
                        idw = total_v / total_w if total_w > 0 else block_avg
                        img[r][c] = 0.5 * idw + 0.5 * q_val if q_val is not None else idw
                    elif q_val is not None:
                        img[r][c] = q_val
                    else:
                        img[r][c] = block_avg

        fixed = [[False] * N for _ in range(N)]
        non_fixed = []
        for r in range(N):
            for c in range(N):
                if mask[r][c]:
                    fixed[r][c] = True
                else:
                    non_fixed.append((r, c))

        omega = 1.75
        for _it in range(120):
            if time.time() - self._t0 > 0.7:
                break
            max_change = 0.0
            for r, c in non_fixed:
                total = 0.0; cnt = 0
                if r > 0:     total += img[r-1][c]; cnt += 1
                if r < N-1:   total += img[r+1][c]; cnt += 1
                if c > 0:     total += img[r][c-1]; cnt += 1
                if c < N-1:   total += img[r][c+1]; cnt += 1
                if cnt > 0:
                    old = img[r][c]
                    img[r][c] = old + omega * (total / cnt - old)
                    d = abs(img[r][c] - old)
                    if d > max_change: max_change = d
            if max_change < 1e-5:
                break

        for br, bc in self.missing:
            if (br, bc) in uniform_blocks:
                continue
            avg = self.recv_avgs.get((br, bc))
            if avg is None:
                continue
            r1, c1 = br * BS, bc * BS

            for qr1, qc1, qr2, qc2 in [
                (r1, c1, r1+4, c1+4), (r1, c1+5, r1+4, c1+9),
                (r1+5, c1, r1+9, c1+4), (r1+5, c1+5, r1+9, c1+9),
            ]:
                key = (qr1, qc1, qr2, qc2)
                if key in quad_avgs:
                    q_sum, q_cnt = 0.0, 0
                    for r in range(qr1, qr2 + 1):
                        for c in range(qc1, qc2 + 1):
                            q_sum += img[r][c]; q_cnt += 1
                    if q_cnt > 0:
                        shift = quad_avgs[key] - q_sum / q_cnt
                        for r in range(qr1, qr2 + 1):
                            for c in range(qc1, qc2 + 1):
                                if not fixed[r][c]:
                                    img[r][c] = max(0.0, min(1.0, img[r][c] + shift))

            b_sum = sum(img[r][c] for r in range(r1, r1+BS) for c in range(c1, c1+BS))
            shift = avg - b_sum / (BS * BS)
            if abs(shift) > 0.001:
                for r in range(r1, r1 + BS):
                    for c in range(c1, c1 + BS):
                        if not fixed[r][c]:
                            img[r][c] = max(0.0, min(1.0, img[r][c] + shift))

    @staticmethod
    def _detect_binary(all_vals):
        if len(all_vals) < 100:
            return False
        near_0 = sum(1 for v in all_vals if v < 0.3)
        near_1 = sum(1 for v in all_vals if v > 0.7)
        mid = sum(1 for v in all_vals if 0.35 <= v <= 0.65)
        total = len(all_vals)
        return (near_0 + near_1) / total > 0.60 and mid / total < 0.25

    def _neighbor_avg(self, br, bc):
        total, cnt = 0.0, 0
        for dbr in (-1, 0, 1):
            for dbc in (-1, 0, 1):
                if dbr == 0 and dbc == 0:
                    continue
                nb = (br + dbr, bc + dbc)
                if nb in self.vis_means:
                    total += self.vis_means[nb]; cnt += 1
                elif nb in self.recv_avgs:
                    total += self.recv_avgs[nb]; cnt += 1
        return total / cnt if cnt > 0 else self.global_mean

    @staticmethod
    def _rect(req):
        return _get_rect(req)

    def _respond(self, req):
        try:
            if RegionAverageRequest is not None and isinstance(req, RegionAverageRequest):
                return self._resp_avg(req)
            if RegionRequest is not None and isinstance(req, RegionRequest):
                return self._resp_region(req)
            if SplitRequest is not None and isinstance(req, SplitRequest):
                return self._resp_split(req)
            cls_name = type(req).__name__
            if 'Average' in cls_name:
                return self._resp_avg(req)
            if 'Split' in cls_name:
                return self._resp_split(req)
            if 'Region' in cls_name:
                return self._resp_region(req)
            rect = _get_rect(req)
            if rect is not None:
                return self._resp_region(req)
        except BaseException:
            pass
        return None

    def _resp_avg(self, req):
        rect = _get_rect(req)
        if rect is None:
            return None
        rr1, cc1, rr2, cc2 = rect
        total, cnt = 0.0, 0
        for r in range(max(0, rr1), min(self.n, rr2 + 1)):
            for c in range(max(0, cc1), min(self.n, cc2 + 1)):
                v = self.corrupted[r][c]
                if v is not None:
                    total += v; cnt += 1
        if cnt == 0:
            return None
        return _make_msg(value=total / cnt)

    def _resp_region(self, req):
        rect = _get_rect(req)
        if rect is None:
            return None
        rr1, cc1, rr2, cc2 = rect
        cr = (rr1 + rr2) * 0.5
        cc = (cc1 + cc2) * 0.5
        best = None
        best_d = 1e18
        for r in range(max(0, rr1), min(self.n, rr2 + 1)):
            for c in range(max(0, cc1), min(self.n, cc2 + 1)):
                v = self.corrupted[r][c]
                if v is not None:
                    d = (r - cr) * (r - cr) + (c - cc) * (c - cc)
                    if d < best_d:
                        best = (r, c, v)
                        best_d = d
        if best is None:
            return None
        return _make_msg(row=best[0], col=best[1], value=best[2])

    def _resp_split(self, req):
        rect = _get_rect(req)
        if rect is None:
            return None
        rr1, cc1, rr2, cc2 = rect
        v1 = self.corrupted[rr1][cc1] if 0 <= rr1 < self.n and 0 <= cc1 < self.n else None
        v2 = self.corrupted[rr2][cc2] if 0 <= rr2 < self.n and 0 <= cc2 < self.n else None
        if v1 is None or v2 is None:
            return None
        return _make_msg(value=1.0 if abs(v1 - v2) > 0.15 else 0.0)

    @staticmethod
    def _extract_value(msg):
        if msg is None:
            return None
        if isinstance(msg, (int, float)):
            return float(msg)
        # Try known names first
        for attr in ("value", "mean", "average", "val"):
            v = getattr(msg, attr, None)
            if v is not None:
                return float(v)
        # Try introspected fields - first float field is likely the value
        fields = _get_fields(type(msg))
        if fields:
            for fn in fields:
                v = getattr(msg, fn, None)
                if isinstance(v, (int, float)):
                    return float(v)
        return None
