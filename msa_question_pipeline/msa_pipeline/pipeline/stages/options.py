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

    @property
    def content_box(self) -> Optional[Box]:
        return Box.union_all([b.box for b in self.texts + self.figs])

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

    @property
    def stem_fallback_text(self) -> str:
        return " ".join(b.md for b in self.stem_texts if b.md).strip()


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
