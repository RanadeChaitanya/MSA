"""STAGE 3a - Geometric question segmentation (the 'QUESTION BOX RETRIEVAL' of the diagram).

Landing AI is not question-aware, so question boxes are derived from its structure:
the question NUMBERS in the left gutter of each column are anchors; a question box runs from its
number down to the next number in the same column. Guards that keep stacked-fraction digits and
option digits from being mistaken for numbers:
  * number must be a small block, pure digits (optionally followed by . or ) )
  * x-aligned with other numbers (cluster), consecutive (n, n+1, ...), and vertically >= MIN_ANCHOR_GAP_FRAC apart
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from ..config import Settings
from ..utils.geometry import Box
from .structure import LEAF, TEXTLIKE, Block, key_table_ids

PURE = re.compile(r"^\(?(\d{1,3})[.)]?$")                # "10", "10.", "10)", "(10)"
START = re.compile(r"^\(?(\d{1,3})[.)]\s*[^\d\s.,)]")   # "12. text", "568.Picric", "(27) text"; not "2.5"


@dataclass
class Region:
    idx: int = -1
    page: int = 0
    column: int = 0
    number: int = 0
    number_text: str = ""
    box: Box = None            # type: ignore[assignment]
    anchor: Block = None       # type: ignore[assignment]
    batch_id: int = 0
    blocks: list[Block] = field(default_factory=list)   # leaf blocks whose centre is inside box (anchor excluded)
    inline_number: bool = False


def _style(b: Block) -> str:
    """How a question number is printed: (12) | 12. | 12) | 12. Fraction digits / superscripts are 'bare'."""
    t = re.sub(r"\s+", "", b.plain or "")
    if t.startswith("("):
        return "paren"
    m = re.match(r"^\d{1,3}([.)])", t)
    return {".": "dot", ")": "bracket"}.get(m.group(1), "bare") if m else "bare"


def _chains(cands: list[tuple[int, Block, bool]], min_gap: float, min_size: int):
    """Longest chains of consecutive numbers ordered top-to-bottom; repeated to allow restarts."""
    remaining = sorted(cands, key=lambda c: c[1].box.y0)
    out = []
    while remaining:
        n = len(remaining)
        best, prev = [1] * n, [-1] * n
        for i in range(n):
            for j in range(i):
                if (remaining[i][0] == remaining[j][0] + 1
                        and remaining[i][1].box.y0 - remaining[j][1].box.y0 >= min_gap
                        and best[j] + 1 > best[i]):
                    best[i], prev[i] = best[j] + 1, j
        k = max(range(n), key=lambda i: (best[i], -i))
        if best[k] < min_size:
            break
        chain, i = [], k
        while i != -1:
            chain.append(remaining[i])
            i = prev[i]
        chain.reverse()
        out.extend(chain)
        taken = {id(c) for c in chain}
        remaining = [c for c in remaining if id(c) not in taken]
    return out


def segment_page(blocks: list[Block], W: int, H: int, cfg: Settings, page: int, batch_id: int,
                 hint: int | None = None, min_gap_frac: float | None = None,
                 min_cluster: int | None = None) -> list[Region]:
    leaf = [b for b in blocks if b.type in LEAF and not b.watermark]
    keys = key_table_ids(blocks)
    cands: list[tuple[int, Block, bool]] = []
    for b in leaf:
        if b.type not in TEXTLIKE or not b.plain or (b.parent and b.parent in keys):
            continue
        t = re.sub(r"\s+", "", b.plain)
        m = PURE.match(t)
        if m and b.box.w <= 0.10 * W and b.box.h <= 0.06 * H:
            cands.append((int(m.group(1)), b, False))
            continue
        m2 = START.match(b.plain.strip())
        if m2:
            cands.append((int(m2.group(1)), b, True))
    if not cands:
        return []

    # cluster by left edge
    tol = cfg.gutter_cluster_tol * W
    groups: list[list] = []
    for c in sorted(cands, key=lambda c: c[1].box.x0):
        if groups and c[1].box.x0 - groups[-1][0][1].box.x0 <= tol:
            groups[-1].append(c)
        else:
            groups.append([c])
    gap = cfg.min_anchor_gap_frac if min_gap_frac is None else min_gap_frac

    def build(min_size: int):
        cs, lo = [], []
        for g in groups:
            chain = _chains(g, gap * H, min_size)
            if chain:
                cs.append(sorted(chain, key=lambda c: c[1].box.y0))
            else:
                lo.append(g)
        return cs, lo

    cols, leftovers = build(cfg.min_cluster_size)
    if not cols and min_cluster is not None and min_cluster < cfg.min_cluster_size:
        cols, leftovers = build(min_cluster)        # a page holding one single (long) question
    # one page prints its question numbers in ONE style: on a page numbered "(1) (2) ..." a bare "22" is a
    # superscript and "12"/"13" are the digits of a stacked fraction, not questions
    styles = [_style(c[1]) for ch in cols for c in ch]
    marked = [s for s in styles if s != "bare"]
    if marked:
        dominant = max(set(marked), key=marked.count)
        cols = [ch for ch in cols if sum(_style(c[1]) == dominant for c in ch) * 2 > len(ch)]
        leftovers = [[c for c in g if _style(c[1]) == dominant] for g in leftovers]
    # continuity rule: a lone number is a real question number if it continues the numbering of another
    # column on this page (or the previous page's last number, `hint`).
    accept = {hint} if hint else set()
    for ch in cols:
        accept.add(ch[-1][0] + 1)
        accept.add(ch[0][0] - 1)
    for g in leftovers:
        for c in sorted(g, key=lambda c: c[1].box.y0):
            if c[0] in accept:
                cols.append([c])
                break
    cols.sort(key=lambda ch: sum(c[1].box.x0 for c in ch) / len(ch))

    regions: list[Region] = []
    for ci, chain in enumerate(cols):
        x_left = min(c[1].box.x0 for c in chain) - 0.006 * W
        if ci + 1 < len(cols):
            nxt = cols[ci + 1]
            x_right = min(c[1].box.x0 for c in nxt) - 0.004 * W
        else:
            x_right = float(W)
        col_blocks = [b for b in leaf if x_left <= b.box.cx <= x_right]
        for k, (num, anchor, inline) in enumerate(chain):
            top = anchor.box.y0 - 0.003 * H
            if k + 1 < len(chain):
                bottom = chain[k + 1][1].box.y0 - 0.003 * H
            else:
                below = [b.box.y1 for b in col_blocks if b.box.cy >= anchor.box.y0]
                bottom = min(H, max(below + [anchor.box.y1]) + cfg.last_region_pad_frac * H)
            box = Box(x_left, top, x_right, bottom)
            r = Region(page=page, column=ci, number=num, number_text=str(num), box=box, anchor=anchor,
                       batch_id=batch_id, inline_number=inline)
            r.blocks = sorted([b for b in col_blocks if b is not anchor and box.contains_point(b.box.cx, b.box.cy)],
                              key=lambda b: (round(b.box.y0 / (0.01 * H)), b.box.x0))
            regions.append(r)
    regions.sort(key=lambda r: (r.column, r.box.y0))
    return regions
