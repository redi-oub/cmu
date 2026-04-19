"""
CMIMC PIC – Image Recovery Strategy (Optimized v4)
"""
from __future__ import annotations
import sys
from typing import Optional


def _get(name):
    """Safely get a name from __main__ or known modules."""
    # 1) Check __main__ first (parse.py runs as __main__)
    main = sys.modules.get('__main__')
    if main is not None:
        try:
            v = getattr(main, name, None)
            if v is not None:
                return v
        except Exception:
            pass
    # 2) Check specific safe module names
    for mod_name in ['strategy', 'game', 'game_types']:
        mod = sys.modules.get(mod_name)
        if mod is not None:
            try:
                v = getattr(mod, name, None)
                if v is not None:
                    return v
            except Exception:
                pass
        else:
            try:
                mod = __import__(mod_name)
                v = getattr(mod, name, None)
                if v is not None:
                    return v
            except Exception:
                pass
    return None


# Strategy is needed at import time for class definition
Strategy = _get('Strategy')
if Strategy is None:
    class Strategy:
        def __init__(self, corrupted):
            self.corrupted_image = corrupted

# These will be resolved lazily since parse.py may define them AFTER importing us
RegionRequest = None
RegionAverageRequest = None
SplitRequest = None
Message = None
_types_resolved = False

def _ensure_types():
    """Lazily resolve request/message types (called on first use)."""
    global RegionRequest, RegionAverageRequest, SplitRequest, Message, _types_resolved
    if _types_resolved:
        return
    _types_resolved = True
    RegionRequest = _get('RegionRequest')
    RegionAverageRequest = _get('RegionAverageRequest')
    SplitRequest = _get('SplitRequest')
    Message = _get('Message')
    if Message is None:
        class _Msg:
            def __init__(self, **kw):
                for k, v in kw.items():
                    setattr(self, k, v)
        Message = _Msg


class SubmissionStrategy(Strategy):

    def __init__(self, corrupted: list[list[Optional[float]]]):
        super().__init__(corrupted)
        N = 50
        BS = 10
        self.n = N
        self.bs = BS
        self.corrupted = corrupted

        # Build image / mask arrays
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

        # Classify blocks
        self.visible: set[tuple[int, int]] = set()
        self.missing: set[tuple[int, int]] = set()
        for br in range(5):
            for bc in range(5):
                if mask[br * BS][bc * BS]:
                    self.visible.add((br, bc))
                else:
                    self.missing.add((br, bc))

        # Block means for visible blocks
        self.vis_means: dict[tuple[int, int], float] = {}
        all_vals: list[float] = []
        for br, bc in self.visible:
            s = 0.0
            for r in range(br * BS, br * BS + BS):
                for c in range(bc * BS, bc * BS + BS):
                    s += img[r][c]
            self.vis_means[(br, bc)] = s / (BS * BS)
            for r in range(br * BS, br * BS + BS):
                for c in range(bc * BS, bc * BS + BS):
                    all_vals.append(img[r][c])

        self.global_mean = sum(all_vals) / len(all_vals) if all_vals else 0.5
        self.is_binary = self._detect_binary(all_vals)

        self.req_meta: list[tuple] = []
        self.recv_avgs: dict[tuple[int, int], float] = {}
        self.recv_pixels: dict[tuple[int, int], float] = {}
        self.recv_quad_avgs: dict[tuple, float] = {}

    # --------------------------------------------------------- make_requests
    def make_requests(self) -> list:
        _ensure_types()
        reqs: list = []
        meta: list[tuple] = []
        BS = self.bs
        sm = sorted(self.missing)

        # Pass 1: block averages (highest priority)
        for br, bc in sm:
            r1, c1 = br * BS, bc * BS
            reqs.append(RegionAverageRequest(r1, c1, r1 + 9, c1 + 9))
            meta.append(("avg", br, bc))

        # Pass 2: center pixel per block
        for br, bc in sm:
            r1, c1 = br * BS, bc * BS
            reqs.append(RegionRequest(r1 + 5, c1 + 5, r1 + 5, c1 + 5))
            meta.append(("pix", br, bc, r1 + 5, c1 + 5))

        # Pass 3: 4-corner pixels per block
        for br, bc in sm:
            r1, c1 = br * BS, bc * BS
            for dr, dc in [(2, 2), (2, 7), (7, 2), (7, 7)]:
                reqs.append(RegionRequest(r1+dr, c1+dc, r1+dr, c1+dc))
                meta.append(("pix", br, bc, r1+dr, c1+dc))

        # Pass 4: edge centres
        for br, bc in sm:
            r1, c1 = br * BS, bc * BS
            for pr, pc in [(r1, c1+5), (r1+9, c1+5), (r1+5, c1), (r1+5, c1+9)]:
                reqs.append(RegionRequest(pr, pc, pr, pc))
                meta.append(("pix", br, bc, pr, pc))

        # Pass 5: quadrant averages
        for br, bc in sm:
            r1, c1 = br * BS, bc * BS
            for qr1, qc1, qr2, qc2 in [
                (r1, c1, r1+4, c1+4), (r1, c1+5, r1+4, c1+9),
                (r1+5, c1, r1+9, c1+4), (r1+5, c1+5, r1+9, c1+9),
            ]:
                reqs.append(RegionAverageRequest(qr1, qc1, qr2, qc2))
                meta.append(("qavg", br, bc, qr1, qc1, qr2, qc2))

        # Pass 6: denser pixel grid
        for br, bc in sm:
            r1, c1 = br * BS, bc * BS
            for dr, dc in [(1,5),(5,1),(5,8),(8,5),(3,3),(3,6),(6,3),(6,6)]:
                reqs.append(RegionRequest(r1+dr, c1+dc, r1+dr, c1+dc))
                meta.append(("pix", br, bc, r1+dr, c1+dc))

        self.req_meta = meta
        return reqs

    # ------------------------------------------------------ receive_requests
    def receive_requests(self, requests: list) -> list:
        responses: list = []
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

    # ------------------------------------------------------ receive_messages
    def receive_messages(self, messages: list) -> None:
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
                row = getattr(msg, 'row', None)
                col = getattr(msg, 'col', None)
                if row is not None and col is not None:
                    self.recv_pixels[(row, col)] = val
                else:
                    self.recv_pixels[(m[3], m[4])] = val

    # ---------------------------------------------------------------- recover
    def recover(self) -> list[list[Optional[float]]]:
        N = self.n
        BS = self.bs
        img = self.image
        mask = self.mask

        # === Phase 0: Denoise visible blocks ===
        if self.is_binary:
            for r in range(N):
                for c in range(N):
                    if mask[r][c]:
                        img[r][c] = 1.0 if img[r][c] >= 0.5 else 0.0

        # === Phase 1: Place received pixel values ===
        for (r, c), v in self.recv_pixels.items():
            if 0 <= r < N and 0 <= c < N:
                if self.is_binary:
                    v = 1.0 if v >= 0.5 else 0.0
                img[r][c] = v
                mask[r][c] = True

        # === Phase 2: Reconstruct missing blocks ===
        if self.is_binary:
            self._recover_binary(img, mask)
        else:
            self._recover_continuous(img, mask)

        # Final clamp
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
        """Reconstruct missing blocks for binary images using diffusion + proportion threshold."""
        N = self.n
        BS = self.bs

        # IDW initialization from boundary + received pixels
        for br, bc in self.missing:
            r1, c1 = br * BS, bc * BS
            block_avg = self.recv_avgs.get((br, bc), self._neighbor_avg(br, bc))

            refs: list[tuple[int, int, float]] = []
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

            # Diagonal corners
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

        # SOR diffusion
        non_fixed: list[tuple[int, int]] = []
        for r in range(N):
            for c in range(N):
                if not mask[r][c]:
                    non_fixed.append((r, c))

        omega = 1.75
        for _ in range(200):
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

        # Proportion-matched thresholding per missing block
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
        """Reconstruct missing blocks for continuous images."""
        N = self.n
        BS = self.bs
        quad_avgs = self.recv_quad_avgs

        # Detect uniform missing blocks
        uniform_blocks: set[tuple[int, int]] = set()
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

        # IDW initialization for non-uniform missing blocks
        for br, bc in self.missing:
            if (br, bc) in uniform_blocks:
                continue
            r1, c1 = br * BS, bc * BS
            block_avg = self.recv_avgs.get((br, bc), self._neighbor_avg(br, bc))

            refs: list[tuple[int, int, float]] = []
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

        # SOR diffusion for missing pixels
        fixed = [[False] * N for _ in range(N)]
        non_fixed: list[tuple[int, int]] = []
        for r in range(N):
            for c in range(N):
                if mask[r][c]:
                    fixed[r][c] = True
                else:
                    non_fixed.append((r, c))

        omega = 1.75
        for _ in range(200):
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

        # Enforce average constraint
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

    # ============================================================== helpers

    @staticmethod
    def _detect_binary(all_vals: list[float]) -> bool:
        if len(all_vals) < 100:
            return False
        near_0 = sum(1 for v in all_vals if v < 0.3)
        near_1 = sum(1 for v in all_vals if v > 0.7)
        mid = sum(1 for v in all_vals if 0.35 <= v <= 0.65)
        total = len(all_vals)
        return (near_0 + near_1) / total > 0.60 and mid / total < 0.25

    def _neighbor_avg(self, br: int, bc: int) -> float:
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

    def _respond(self, req) -> Optional[Message]:
        if RegionAverageRequest is not None and isinstance(req, RegionAverageRequest):
            return self._resp_avg(req)
        if RegionRequest is not None and isinstance(req, RegionRequest):
            return self._resp_region(req)
        if SplitRequest is not None and isinstance(req, SplitRequest):
            return self._resp_split(req)
        # Fallback: detect request type by attribute names
        cls_name = type(req).__name__
        if 'Average' in cls_name or 'average' in cls_name:
            return self._resp_avg(req)
        if 'Split' in cls_name or 'split' in cls_name:
            return self._resp_split(req)
        if 'Region' in cls_name or 'region' in cls_name:
            return self._resp_region(req)
        # Last resort: if it has r1/c1/r2/c2, treat as region request
        if hasattr(req, 'r1') and hasattr(req, 'c1'):
            return self._resp_region(req)
        return None

    def _resp_avg(self, req) -> Optional[Message]:
        total, cnt = 0.0, 0
        for r in range(max(0, req.r1), min(self.n, req.r2 + 1)):
            for c in range(max(0, req.c1), min(self.n, req.c2 + 1)):
                v = self.corrupted[r][c]
                if v is not None:
                    total += v; cnt += 1
        if cnt == 0:
            return None
        return Message(value=total / cnt)

    def _resp_region(self, req) -> Optional[Message]:
        cr = (req.r1 + req.r2) * 0.5
        cc = (req.c1 + req.c2) * 0.5
        best = None
        best_d = 1e18
        for r in range(max(0, req.r1), min(self.n, req.r2 + 1)):
            for c in range(max(0, req.c1), min(self.n, req.c2 + 1)):
                v = self.corrupted[r][c]
                if v is not None:
                    d = (r - cr) * (r - cr) + (c - cc) * (c - cc)
                    if d < best_d:
                        best = (r, c, v)
                        best_d = d
        if best is None:
            return None
        return Message(row=best[0], col=best[1], value=best[2])

    def _resp_split(self, req) -> Optional[Message]:
        v1 = self.corrupted[req.r1][req.c1] if 0 <= req.r1 < self.n and 0 <= req.c1 < self.n else None
        v2 = self.corrupted[req.r2][req.c2] if 0 <= req.r2 < self.n and 0 <= req.c2 < self.n else None
        if v1 is None or v2 is None:
            return None
        return Message(value=1.0 if abs(v1 - v2) > 0.15 else 0.0)

    @staticmethod
    def _extract_value(msg) -> Optional[float]:
        if msg is None:
            return None
        if isinstance(msg, (int, float)):
            return float(msg)
        for attr in ("value", "mean", "average", "val"):
            v = getattr(msg, attr, None)
            if v is not None:
                return float(v)
        return None
