"""STAGE 3b - Option layout inside one question region ('OPTION BOX RETRIEVAL').

Option letters (A/B/C/D) printed as tiny glyphs are the anchors. Option boxes are the grid cells
implied by the anchors (works for 2x2, 1x4, 4x1). Blocks and figures are then bucketed by
centre-containment into: stem | option A..D | answer line.
Fallback when <2 anchors: the 4 cells of a table (row-major).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

from ..config import Settings
from ..utils.geometry import Box
from .segment import Region
from .structure import Block

LABEL = re.compile(r"^\(?\s*([A-Da-d])\s*\)?\s*[.):]?$")


@dataclass
class OptionRegion:
    label: str
    box: Box
    anchor: Optional[Block] = None
    texts: list[Block] = field(default_factory=list)
    figs: list[Block] = field(default_factory=list)
    mask_boxes: list[Box] = field(default_factory=list)   # FULL text-line boxes: only for ink masking
    geom_boxes: list[Box] = field(default_factory=list)   # this option's own (possibly sliced) text boxes: for crops
    use_cell_crop: bool = False                           # crop the whole grid cell (span-derived layouts)

    @property
    def content_box(self) -> Optional[Box]:
        return Box.union_all([b.box for b in self.texts + self.figs] + self.geom_boxes)

    @property
    def fallback_text(self) -> str:
        return " ".join(b.md for b in self.texts if b.md).strip()


@dataclass
class RegionLayout:
    stem_box: Box
    options: dict[str, OptionRegion]
    stem_texts: list[Block]
    stem_figs: list[Block]
    answer_blocks: list[Block]
    flags: list[str] = field(default_factory=list)
    stem_mask_boxes: list[Box] = field(default_factory=list)
    exact: bool = False          # geometry from the PDF text/vector layer: figures are exact, no ink analysis

    @property
    def stem_fallback_text(self) -> str:
        return " ".join(b.md for b in self.stem_texts if b.md).strip()

    @property
    def has_stem_coverage(self) -> bool:
        return bool(self.stem_texts or self.stem_mask_boxes)


def find_answer_blocks(blocks: list[Block], cfg: Settings) -> list[Block]:
    pats = [re.compile(p, re.I) for p in cfg.answer_markers]
    marker = None
    for b in blocks:
        if b.type in ("text", "marginalia") and any(p.search(b.plain) for p in pats):
            if marker is None or b.box.y0 < marker.box.y0:
                marker = b
    if marker is None:
        return []
    return [b for b in blocks if b is marker or b.box.cy > marker.box.y0 + 0.5 * marker.box.h]


def _nearest(opts: dict[str, OptionRegion], b: Block) -> Optional[str]:
    if not opts:
        return None
    return min(opts, key=lambda L: opts[L].box.dist_to(b.box.cx, b.box.cy))


def layout_region(region: Region, cfg: Settings, W: int, H: int) -> RegionLayout:
    flags: list[str] = []
    answer = find_answer_blocks(region.blocks, cfg)
    ans_ids = {b.uid for b in answer}
    body = [b for b in region.blocks if b.uid not in ans_ids]
    figs = [b for b in body if b.type == "figure"]
    texts = [b for b in body if b.type != "figure"]
    ans_top = min((b.box.y0 for b in answer), default=None)
    bottom = region.box.y1 if ans_top is None else min(region.box.y1, ans_top)

    def inside_fig(b: Block) -> bool:
        return any(b.box.overlap_ratio(f.box) > 0.6 for f in figs)

    cand: dict[str, list[Block]] = {}
    for b in texts:
        m = LABEL.match(b.plain.strip())
        if not m or b.box.w > 0.2 * W or inside_fig(b):
            continue
        cand.setdefault(m.group(1).upper(), []).append(b)
    anchors = {L: min(bs, key=lambda x: (x.box.y0, x.box.x0)) for L, bs in cand.items()}

    options: dict[str, OptionRegion] = {}
    if len(anchors) >= 2:
        ordered = sorted(anchors.items(), key=lambda kv: (kv[1].box.y0, kv[1].box.x0))
        row_tol = 0.012 * H
        rows: dict[str, int] = {}
        r, first_y = -1, None
        for L, a in ordered:
            if first_y is None or a.box.y0 - first_y > row_tol:
                r += 1
                first_y = a.box.y0
            rows[L] = r
        seq = [L for L, _ in sorted(anchors.items(), key=lambda kv: (rows[kv[0]], kv[1].box.x0))]
        if seq != sorted(seq):
            flags.append("option_order_mismatch")
        px, py = 0.004 * W, 0.003 * H
        for L, a in anchors.items():
            right = [o.box.x0 for L2, o in anchors.items() if rows[L2] == rows[L] and o.box.x0 > a.box.x0 + 2]
            below = [o.box.y0 for L2, o in anchors.items() if rows[L2] > rows[L]]
            x1 = (min(right) - px) if right else region.box.x1
            y1 = (min(below) - py) if below else bottom
            options[L] = OptionRegion(L, Box(a.box.x0 - px, a.box.y0 - py, x1, y1), anchor=a)
        stem_bottom = min(o.box.y0 for o in options.values())
    else:
        cells = [b for b in body if b.type == "table_cell" and b.parent]
        by_parent: dict[str, list[Block]] = {}
        for c in cells:
            by_parent.setdefault(c.parent, []).append(c)
        grid = next((cs for cs in by_parent.values() if len(cs) == 4), None)
        if grid:
            grid = sorted(grid, key=lambda c: (c.row if c.row is not None else 0, c.col if c.col is not None else 0))
            for L, c in zip("ABCD", grid):
                options[L] = OptionRegion(L, c.box)
            flags.append("options_from_table_cells")
            stem_bottom = min(o.box.y0 for o in options.values())
        else:
            flags.append("no_option_anchors")
            stem_bottom = region.box.y1

    stem_box = Box(region.box.x0, region.box.y0, region.box.x1, max(region.box.y0 + 1, stem_bottom))
    anchor_ids = {o.anchor.uid for o in options.values() if o.anchor is not None}
    stem_texts: list[Block] = []
    stem_figs: list[Block] = []
    for b in texts:
        if b.uid in anchor_ids:
            continue
        placed = False
        for L, o in options.items():
            if o.box.contains_point(b.box.cx, b.box.cy):
                o.texts.append(b)
                placed = True
                break
        if not placed:
            if stem_box.contains_point(b.box.cx, b.box.cy) or not options:
                stem_texts.append(b)
            else:
                L = _nearest(options, b)
                if L:
                    options[L].texts.append(b)
                    flags.append("text_between_options")
    for f in figs:
        placed = False
        if f.box.overlap_ratio(region.box) > 0.6 and f.box.area > 0.5 * region.box.area:
            stem_figs.append(f)                 # whole question swallowed into one figure -> stem level
            continue
        for L, o in options.items():
            if o.box.contains_point(f.box.cx, f.box.cy):
                o.figs.append(f)
                placed = True
                break
        if not placed:
            if f.box.cy <= stem_bottom or not options:
                stem_figs.append(f)
            else:
                L = _nearest(options, f)
                options[L].figs.append(f)
                flags.append("figure_between_options")
    return RegionLayout(stem_box, options, stem_texts, stem_figs, answer, flags)


# ----------------------------------------------------------------------------------------------
# Span-derived layout: used when option label glyphs are not separate blocks (opaque tables etc.)
# ----------------------------------------------------------------------------------------------
LABELS = ["A", "B", "C", "D"]


def _rel(a: Box, b: Box, tol_y: float, tol_x: float) -> str:
    if abs(a.cy - b.cy) <= tol_y:
        return "row"
    if abs(a.cx - b.cx) <= tol_x:
        return "col"
    return "diag"


def infer_cells(known: dict[str, Box], region: Box, H: float, notes: Optional[list[str]] = None) -> dict[str, Box]:
    """Grid cells (A..D) from the content boxes of the options we could ground. Supports 2x2, 1x4, 4x1.
    Missing options (image-only) get the cell their slot implies."""
    if len(known) < 2:
        return {}
    hs = sorted(b.h for b in known.values())
    tol_y = max(0.6 * hs[len(hs) // 2], 0.008 * H)
    tol_x = 0.12 * region.w

    def rel(l1: str, l2: str):
        return _rel(known[l1], known[l2], tol_y, tol_x) if l1 in known and l2 in known else None

    layout = "2x2"
    for pair, third in ((("A", "B"), ("B", "C")), (("C", "D"), ("B", "C"))):
        r = rel(*pair)
        if r == "col":
            layout = "4x1"
            break
        if r == "row":
            layout = "1x4" if (rel(*third) == "row" or rel("A", "C") == "row" or rel("B", "D") == "row") else "2x2"
            break
    else:
        r = rel("B", "C")
        if r == "row":
            layout = "1x4"
        elif r == "col":
            layout = "4x1"

    def slot(i: int) -> tuple[int, int]:
        return {"2x2": (i // 2, i % 2), "1x4": (0, i), "4x1": (i, 0)}[layout]

    nrows = {"2x2": 2, "1x4": 1, "4x1": 4}[layout]
    ncols = {"2x2": 2, "1x4": 4, "4x1": 1}[layout]
    colr: dict[int, tuple[float, float]] = {}
    rowr: dict[int, tuple[float, float]] = {}
    for L, b in known.items():
        r_, c_ = slot(LABELS.index(L))
        x0, x1 = colr.get(c_, (b.x0, b.x1))
        colr[c_] = (min(x0, b.x0), max(x1, b.x1))
        y0, y1 = rowr.get(r_, (b.y0, b.y1))
        rowr[r_] = (min(y0, b.y0), max(y1, b.y1))
    # fill unknown rows/cols by shifting a known one by the pitch (fallback: equal split of the region)
    def fill(rng: dict[int, tuple[float, float]], n: int, lo: float, hi: float):
        if len(rng) == n:
            return
        ks = sorted(rng)
        if len(ks) >= 2:
            pitch = (rng[ks[-1]][0] - rng[ks[0]][0]) / (ks[-1] - ks[0])
            size = rng[ks[0]][1] - rng[ks[0]][0]
            for k in range(n):
                if k not in rng:
                    st = rng[ks[0]][0] + pitch * (k - ks[0])
                    rng[k] = (st, st + size)
        else:
            k0 = ks[0]
            size = rng[k0][1] - rng[k0][0]
            pitch = (hi - lo) / n
            for k in range(n):
                if k not in rng:
                    st = rng[k0][0] + pitch * (k - k0)
                    rng[k] = (st, st + size)
    fill(colr, ncols, region.x0, region.x1)
    fill(rowr, nrows, region.y0, region.y1)

    def bounds(rng: dict[int, tuple[float, float]], n: int, lo: float, hi: float) -> list[float]:
        hi = max(hi, lo + 1.0)
        b = [lo]
        for k in range(n - 1):
            b.append((rng[k][1] + rng[k + 1][0]) / 2)
        b.append(hi)
        # Out-of-order grounding (e.g. option C's slice landing right of D) or a pitch extrapolated past the
        # region gives non-increasing bounds -> inverted cells that break cropping. Fall back to an equal split.
        min_gap = 8.0                                  # px; anything thinner is not a real cell
        if any(b1 - b0 < min_gap for b0, b1 in zip(b, b[1:])):
            if notes is not None and "option_cells_equal_split" not in notes:
                notes.append("option_cells_equal_split")
            b = [lo + (hi - lo) * k / n for k in range(n + 1)]
        return b
    top = min(b.y0 for b in known.values()) - 0.012 * H
    xb = bounds(colr, ncols, region.x0, region.x1)
    yb = bounds(rowr, nrows, max(region.y0, top), region.y1)
    out: dict[str, Box] = {}
    for i, L in enumerate(LABELS):
        r_, c_ = slot(i)
        if r_ < nrows and c_ < ncols:
            out[L] = Box(xb[c_], yb[r_], xb[c_ + 1], yb[r_ + 1])
    return out


def layout_from_spans(region: Region, geo, cfg: Settings, W: int, H: int) -> RegionLayout:
    flags = ["layout_from_spans"]
    if geo.coarse:
        flags.append("coarse_geometry")
    page = region.page
    def pcs(name):
        return [p for p in geo.fields.get(name, []) if p.page == page]
    opt_lines = {L: [p.box for p in pcs(f"option_{L.lower()}")] for L in LABELS}
    opt_masks = {L: [p.line for p in pcs(f"option_{L.lower()}") if p.line is not None] for L in LABELS}
    known = {L: Box.union_all(bs) for L, bs in opt_lines.items() if bs}
    stem_lines = [p.line for p in pcs("stem") if p.line is not None] + \
                 [p.line for p in pcs("number") if p.line is not None]
    cells = infer_cells(known, region.box, H, flags) if len(known) >= 2 else {}
    if not cells:
        flags.append("no_option_cells")
    options = {L: OptionRegion(L, c, mask_boxes=opt_masks.get(L, []), geom_boxes=opt_lines.get(L, []),
                                 use_cell_crop=True) for L, c in cells.items()}
    top = min((o.box.y0 for o in options.values()), default=region.box.y1)
    stem_box = Box(region.box.x0, region.box.y0, region.box.x1, max(region.box.y0 + 1, top))
    figs_stem: list[Block] = []
    for b in region.blocks:
        if b.type != "figure":
            continue
        placed = False
        if b.box.overlap_ratio(region.box) > 0.6 and b.box.area > 0.5 * region.box.area:
            figs_stem.append(b)                 # whole question swallowed into one figure -> stem level
            continue
        spanned = sum(1 for o in options.values()
                      if (i := b.box.intersect(o.box)) is not None and i.area > 0.2 * b.box.area)
        if spanned >= 2:
            figs_stem.append(b)                 # one diagram beside/over several options belongs to the stem
            continue
        for o in options.values():
            if o.box.contains_point(b.box.cx, b.box.cy):
                o.figs.append(b)
                placed = True
                break
        if not placed:
            figs_stem.append(b)
    return RegionLayout(stem_box, options, [], figs_stem, [], flags, stem_mask_boxes=stem_lines)
