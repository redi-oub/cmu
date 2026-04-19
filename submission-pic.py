"""
CMIMC PIC – Image Recovery Strategy
====================================
Cooperative strategy with diffusion-based inpainting.

Request budget  : ~20  (1 RegionAvg + 1 center-pixel per missing block)
Response budget : up to 20 answers
Reconstruction  : block-avg fill → Gauss-Seidel diffusion → clamp [0,1]
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Import game types – try the server module first, fall back to local stubs.
# Adapt the first import line to match the actual server package name.
# ---------------------------------------------------------------------------
try:
    from strategy import Strategy
    from strategy import (
        RegionRequest, RegionAverageRequest, SplitRequest, Message,
    )
except ImportError:
    try:
        from game import (
            Strategy, RegionRequest, RegionAverageRequest,
            SplitRequest, Message,
        )
    except ImportError:
        from game_types import (
            Strategy, RegionRequest, RegionAverageRequest,
            SplitRequest, Message,
        )


class SubmissionStrategy(Strategy):
    # ------------------------------------------------------------------ init
    def __init__(self, corrupted: list[list[Optional[float]]]):
        super().__init__(corrupted)

        self.n = 50
        self.bs = 10          # block side length
        self.nb = 5           # blocks per dimension
        self.corrupted = corrupted
        self.image = [row[:] for row in corrupted]

        # ---------- classify blocks as visible / missing ----------
        self.visible: set[tuple[int, int]] = set()
        self.missing: set[tuple[int, int]] = set()
        for br in range(self.nb):
            for bc in range(self.nb):
                if corrupted[br * self.bs][bc * self.bs] is not None:
                    self.visible.add((br, bc))
                else:
                    self.missing.add((br, bc))

        # ---------- compute means for visible blocks ----------
        self.vis_means: dict[tuple[int, int], float] = {}
        all_vals: list[float] = []
        for br, bc in self.visible:
            vals = self._block_vals(br, bc)
            if vals:
                self.vis_means[(br, bc)] = sum(vals) / len(vals)
                all_vals.extend(vals)

        self.global_mean = sum(all_vals) / len(all_vals) if all_vals else 0.5

        # ---------- detect if image is likely binary (circles / blobs) ----------
        self.is_binary = self._detect_binary(all_vals)

        # will be populated during the protocol
        self.req_meta: list[tuple] = []
        self.recv_avgs: dict[tuple[int, int], float] = {}
        self.extra_fixed: set[tuple[int, int]] = set()

    # --------------------------------------------------------- make_requests
    def make_requests(self) -> list:
        reqs: list = []
        meta: list[tuple] = []

        sorted_missing = sorted(self.missing)

        # 1) RegionAverageRequest for every missing block
        for br, bc in sorted_missing:
            r1, c1 = br * self.bs, bc * self.bs
            reqs.append(RegionAverageRequest(r1, c1, r1 + 9, c1 + 9))
            meta.append(("avg", br, bc))

        # 2) RegionRequest for the centre pixel of every missing block
        for br, bc in sorted_missing:
            tr = br * self.bs + self.bs // 2
            tc = bc * self.bs + self.bs // 2
            reqs.append(RegionRequest(tr, tc, tr, tc))
            meta.append(("pix", br, bc, tr, tc))

        self.req_meta = meta
        logger.info(
            "Sending %d requests (%d missing blocks)", len(reqs), len(self.missing)
        )
        return reqs

    # ------------------------------------------------------ receive_requests
    def receive_requests(self, requests: list) -> list:
        responses: list = []
        answered = 0
        max_answers = 20  # (20/50)^2 / 4 = 0.04 cost

        for req in requests:
            if answered >= max_answers:
                responses.append(None)
                continue
            resp = self._respond(req)
            responses.append(resp)
            if resp is not None:
                answered += 1

        logger.info("Answered %d / %d requests", answered, len(requests))
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

            if m[0] == "avg":
                _, br, bc = m
                self.recv_avgs[(br, bc)] = max(0.0, min(1.0, val))

            elif m[0] == "pix":
                _, br, bc, tr, tc = m
                clamped = max(0.0, min(1.0, val))
                self.image[tr][tc] = clamped
                self.extra_fixed.add((tr, tc))

    # ---------------------------------------------------------------- recover
    def recover(self) -> list[list[Optional[float]]]:
        n = self.n

        # Phase 1 – seed missing pixels with block averages
        for br, bc in self.missing:
            avg = self.recv_avgs.get((br, bc))
            if avg is None:
                avg = self._neighbor_avg(br, bc)
            for r in range(br * self.bs, br * self.bs + self.bs):
                for c in range(bc * self.bs, bc * self.bs + self.bs):
                    if self.image[r][c] is None:
                        self.image[r][c] = avg

        # Phase 2 – Gauss-Seidel diffusion inpainting
        fixed = [[False] * n for _ in range(n)]
        non_fixed: list[tuple[int, int]] = []
        for r in range(n):
            for c in range(n):
                if self.corrupted[r][c] is not None or (r, c) in self.extra_fixed:
                    fixed[r][c] = True
                else:
                    non_fixed.append((r, c))

        img = self.image  # alias for speed
        for _ in range(50):
            for r, c in non_fixed:
                total = 0.0
                cnt = 0
                if r > 0:
                    v = img[r - 1][c]
                    if v is not None:
                        total += v; cnt += 1
                if r < n - 1:
                    v = img[r + 1][c]
                    if v is not None:
                        total += v; cnt += 1
                if c > 0:
                    v = img[r][c - 1]
                    if v is not None:
                        total += v; cnt += 1
                if c < n - 1:
                    v = img[r][c + 1]
                    if v is not None:
                        total += v; cnt += 1
                if cnt > 0:
                    img[r][c] = total / cnt

        # Phase 3 – binary thresholding (if detected) then clamp
        if self.is_binary:
            # Threshold visible pixels at 0.5
            for r in range(n):
                for c in range(n):
                    v = img[r][c]
                    if v is None:
                        v = self.global_mean
                    if fixed[r][c]:
                        img[r][c] = 1.0 if v >= 0.5 else 0.0

            # For missing blocks: use proportion-matching threshold.
            # The block avg tells us what fraction should be 1.
            for br, bc in self.missing:
                avg = self.recv_avgs.get((br, bc))
                if avg is None:
                    avg = self._neighbor_avg(br, bc)
                # Collect (diffused_value, r, c) for this block
                pixels = []
                for r in range(br * self.bs, br * self.bs + self.bs):
                    for c in range(bc * self.bs, bc * self.bs + self.bs):
                        pixels.append((img[r][c], r, c))
                # Number of pixels that should be 1
                count_1 = max(0, min(len(pixels), round(avg * len(pixels))))
                # Sort by diffused value descending: highest become 1
                pixels.sort(key=lambda x: -x[0])
                for idx, (_, r, c) in enumerate(pixels):
                    img[r][c] = 1.0 if idx < count_1 else 0.0
        else:
            for r in range(n):
                for c in range(n):
                    v = img[r][c]
                    if v is None:
                        v = self.global_mean
                    img[r][c] = max(0.0, min(1.0, v))

        return img

    # ============================================================== helpers

    @staticmethod
    def _detect_binary(all_vals: list[float]) -> bool:
        """Check if visible pixel values cluster near 0 and 1 (binary image)."""
        if len(all_vals) < 100:
            return False
        near_0 = sum(1 for v in all_vals if v < 0.3)
        near_1 = sum(1 for v in all_vals if v > 0.7)
        mid = sum(1 for v in all_vals if 0.35 <= v <= 0.65)
        total = len(all_vals)
        # binary if >65% are near extremes AND <20% are in the middle
        return (near_0 + near_1) / total > 0.65 and mid / total < 0.20

    def _find_threshold(self) -> float:
        """For binary images, 0.5 is the natural separator."""
        return 0.5

    def _block_vals(self, br: int, bc: int) -> list[float]:
        vals: list[float] = []
        for r in range(br * self.bs, br * self.bs + self.bs):
            for c in range(bc * self.bs, bc * self.bs + self.bs):
                v = self.corrupted[r][c]
                if v is not None:
                    vals.append(v)
        return vals

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
        if isinstance(req, RegionAverageRequest):
            return self._resp_avg(req)
        if isinstance(req, RegionRequest):
            return self._resp_region(req)
        if isinstance(req, SplitRequest):
            return self._resp_split(req)
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
        cr = (req.r1 + req.r2) / 2.0
        cc = (req.c1 + req.c2) / 2.0
        best: Optional[tuple[int, int, float]] = None
        best_d = float("inf")
        for r in range(max(0, req.r1), min(self.n, req.r2 + 1)):
            for c in range(max(0, req.c1), min(self.n, req.c2 + 1)):
                v = self.corrupted[r][c]
                if v is not None:
                    d = (r - cr) ** 2 + (c - cc) ** 2
                    if d < best_d:
                        best = (r, c, v)
                        best_d = d
        if best is None:
            return None
        return Message(row=best[0], col=best[1], value=best[2])

    def _resp_split(self, req) -> Optional[Message]:
        v1 = self._px(req.r1, req.c1)
        v2 = self._px(req.r2, req.c2)
        if v1 is None or v2 is None:
            return None
        return Message(value=1.0 if abs(v1 - v2) > 0.15 else 0.0)

    def _px(self, r: int, c: int) -> Optional[float]:
        if 0 <= r < self.n and 0 <= c < self.n:
            return self.corrupted[r][c]
        return None

    @staticmethod
    def _extract_value(msg) -> Optional[float]:
        if msg is None:
            return None
        if isinstance(msg, (int, float)):
            return float(msg)
        for attr in ("value", "mean", "average", "val"):
            if hasattr(msg, attr):
                v = getattr(msg, attr)
                if v is not None:
                    return float(v)
        return None
