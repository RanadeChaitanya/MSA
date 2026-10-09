"""STAGE 3-7 glue: build QuestionDrafts per parse batch, then materialise final QuestionOut.

build_drafts : segment -> Extract+Ground -> option layout -> visual routing   (no files written)
materialize  : assign qids, crop assets from the canonical images, build JSON models
Splitting the two lets the retry ladder re-draft a page without leaving orphan assets.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from ..config import Settings
from ..models import OptionOut, QuestionOut
from ..utils.geometry import Box
from .crop import save_crop
from .options import RegionLayout, layout_from_spans, layout_region
from .parse_stage import BatchResult
from .segment import Region, segment_page
from .semantics import run_semantics
from .structure import mark_watermarks
from .visuals import ImageCache, VisualRef, option_visual, question_visuals

log = logging.getLogger("pipeline.assemble")
LABELS = ["A", "B", "C", "D"]


@dataclass
class OptionDraft:
    label: str
    text: str = ""
    ref: Optional[VisualRef] = None
    image_only: bool = False
    box: Optional[Box] = None


@dataclass
class QuestionDraft:
    region: Region
    layout: RegionLayout
    page: int
    column: int
    number: int
    number_text: str
    stem_text: str = ""
    correct_option: str = ""
    answer_text: str = ""
    options: dict[str, OptionDraft] = field(default_factory=dict)
    q_visuals: list[VisualRef] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    ocr_conf: Optional[float] = None
    profile: str = "default"
    order_idx: int = 0            # position in Extract's reading order (reliable) - drives output order


_BR = re.compile(r"<br\s*/?>", re.I)


def _clean(t) -> str:
    """Extract returns source line-wraps as <br>; turn them into spaces."""
    return re.sub(r"[ \t]+", " ", _BR.sub(" ", str(t or ""))).strip()


def _opt_text(item: Optional[dict], label: str) -> str:
    if not item:
        return ""
    return _clean(item.get(f"option_{label.lower()}"))


def _digits(x) -> str:
    return "".join(ch for ch in str(x or "") if ch.isdigit())


def _synth_regions(sem, br: BatchResult, sizes, cfg: Settings, start_idx: int) -> dict[int, Region]:
    """Span-derived question regions for extracted items that no anchor-based region claimed.
    Items are grouped per page, split into left/right/full-width columns, and stacked vertically:
    region i spans from the top of item i to the top of the next item in the same column."""
    from .structure import LEAF
    out: dict[int, Region] = {}
    by_page: dict[int, list[tuple[int, Box]]] = {}
    for i in sem.unmatched:
        geo = sem.geos[i]
        if geo.page is None:
            continue
        boxes = [pc.box for pc in geo.all_pieces() if pc.page == geo.page]
        ub = Box.union_all(boxes)
        if ub:
            by_page.setdefault(geo.page, []).append((i, ub))
    idx = start_idx
    for page, lst in by_page.items():
        W, H = sizes[page]
        wide = [(i, b) for i, b in lst if b.w > 0.6 * W]
        narrow = [(i, b) for i, b in lst if b.w <= 0.6 * W]
        left = [(i, b) for i, b in narrow if b.cx < W / 2]
        right = [(i, b) for i, b in narrow if b.cx >= W / 2]
        split = W / 2
        if left and right:
            split = (max(b.x1 for _, b in left) + min(b.x0 for _, b in right)) / 2
        if left and right:
            groups = [(0, 0.0, split, left), (1, split, float(W), right)]
            if wide:
                groups.append((0, 0.0, float(W), wide))
        else:
            groups = [(0, 0.0, float(W), wide + narrow)]
        pb = [b for b in br.blocks if b.page == page and b.type in LEAF and not b.watermark]
        for col, x0, x1, items in groups:
            items = sorted(items, key=lambda t: t[1].y0)
            for k, (i, ub) in enumerate(items):
                top = max(0.0, ub.y0 - 0.012 * H)
                if k + 1 < len(items):
                    bottom = items[k + 1][1].y0 - 0.006 * H
                else:
                    bottom = min(H, ub.y1 + cfg.last_region_pad_frac * H)
                box = Box(x0, top, x1, max(bottom, top + 5))
                num = _digits(sem.items[i].get("number"))
                r = Region(idx=idx, page=page, column=col, number=int(num) if num else 0, number_text=num,
                           box=box, anchor=None, batch_id=br.batch_id)       # type: ignore[arg-type]
                r.blocks = sorted([b for b in pb if box.contains_point(b.box.cx, b.box.cy)],
                                  key=lambda b: (round(b.box.y0 / (0.01 * H)), b.box.x0))
                out[i] = r
                idx += 1
    return out


def _make_draft(region: Region, layout: RegionLayout, item: Optional[dict], geo, ctx, profile: str) -> QuestionDraft:
    cfg: Settings = ctx.cfg
    d = QuestionDraft(region=region, layout=layout, page=region.page, column=region.column, number=region.number,
                      number_text=region.number_text, profile=profile)
    d.flags.extend(layout.flags)
    if geo is not None:
        if geo.method == "text":
            d.flags.append("grounded_by_text_match")
        if geo.page_inferred:
            d.flags.append("page_inferred")
    if item is None:
        d.flags.append("extract_missing")
    else:
        n = _digits(item.get("number"))
        if n and region.number and int(n) != region.number:
            d.flags.append("number_mismatch")
        if n and not region.number:
            d.number, d.number_text = int(n), n
    stem = _clean((item or {}).get("stem"))
    d.stem_text = stem or layout.stem_fallback_text
    d.answer_text = " ".join(b.md for b in layout.answer_blocks if b.md).strip()
    ca = str((item or {}).get("correct_option") or "").strip().upper()[:1]
    d.correct_option = ca if ca in LABELS else ""
    labels = set(layout.options) | {L for L in LABELS if _opt_text(item, L)}
    for L in sorted(labels):
        reg = layout.options.get(L)
        text = _opt_text(item, L) or (reg.fallback_text if reg else "")
        od = OptionDraft(label=L, text=text, box=reg.box if reg else None)
        if reg is not None:
            od.ref, od.image_only = option_visual(ctx.client, ctx.images, region.page, reg, text, cfg, d.flags)
        elif "no_geometry" not in layout.flags:
            d.flags.append(f"option_{L}_without_region")
        if not od.text and od.ref is None:
            d.flags.append(f"option_{L}_empty")
        d.options[L] = od
    if "no_geometry" not in layout.flags:
        d.q_visuals = question_visuals(ctx.client, ctx.images, region.page, layout, cfg, d.flags)
    toks = d.stem_text.split()
    if toks and ((len(toks[-1]) == 1 and toks[-1].isalpha() and toks[-1].lower() not in ("a", "i"))
                 or toks[-1].endswith(("-", "…"))):
        d.flags.append("stem_possibly_truncated")
    return d


def build_drafts(br: BatchResult, ctx, profile: str = "default") -> tuple[list[QuestionDraft], dict[int, list[str]]]:
    """SEMANTIC-FIRST: Extract decides which questions exist; geometry (anchor regions, else span-derived
    regions) is attached afterwards. An extracted question is never dropped for lack of geometry."""
    cfg: Settings = ctx.cfg
    sizes = {p: (ctx.records[p].width, ctx.records[p].height) for p in br.page_nums}
    page_issues: dict[int, list[str]] = {}
    for p in br.failed_pages:
        page_issues.setdefault(p, []).append("parse_failed")
    regions: list[Region] = []
    hint = None
    for p in br.page_nums:
        W, H = sizes[p]
        pb = [b for b in br.blocks if b.page == p]
        mark_watermarks(pb, cfg, W, H)
        rs = segment_page(pb, W, H, cfg, p, br.batch_id, hint=(hint + 1) if hint else None)
        regions.extend(rs)
        if rs:
            hint = max(r.number for r in rs)
    for i, r in enumerate(regions):
        r.idx = i
    sem = run_semantics(ctx.client, br, regions, sizes)
    first = br.page_nums[0]
    for n in dict.fromkeys(sem.notes):
        page_issues.setdefault(first, []).append(n[:120])

    drafts: list[QuestionDraft] = []
    # (a) anchor-based regions
    for r in regions:
        W, H = sizes[r.page]
        layout = layout_region(r, cfg, W, H)
        item_idx = sem.matched.get(r.idx)
        item = sem.items[item_idx] if item_idx is not None else None
        geo = sem.geos[item_idx] if item_idx is not None else None
        if "no_option_anchors" in layout.flags and geo is not None:
            alt = layout_from_spans(r, geo, cfg, W, H)
            if alt.options:
                alt.flags = [f for f in layout.flags if f != "no_option_anchors"] + alt.flags
                layout = alt
        dr = _make_draft(r, layout, item, geo, ctx, profile)
        dr.order_idx = item_idx if item_idx is not None else 10 ** 6 + r.idx
        drafts.append(dr)
    # (b) extracted questions without an anchor region -> span-derived regions
    synth = _synth_regions(sem, br, sizes, cfg, start_idx=len(regions))
    for i in sem.unmatched:
        item, geo = sem.items[i], sem.geos[i]
        r = synth.get(i)
        if r is None:                                   # no grounding at all: keep the TEXT, flag it
            page = geo.page or first
            r = Region(idx=-1, page=page, column=0, number=int(_digits(item.get("number")) or 0),
                       number_text=_digits(item.get("number")), box=Box(0, 0, 1, 1), anchor=None,  # type: ignore[arg-type]
                       batch_id=br.batch_id)
            layout = RegionLayout(r.box, {}, [], [], [], ["no_geometry"])
        else:
            W, H = sizes[r.page]
            layout = layout_region(r, cfg, W, H)
            if "no_option_anchors" in layout.flags:
                alt = layout_from_spans(r, geo, cfg, W, H)
                alt.flags = [f for f in layout.flags if f != "no_option_anchors"] + alt.flags
                layout = alt
            layout.flags.append("region_from_spans")
        dr = _make_draft(r, layout, item, geo, ctx, profile)
        dr.order_idx = i
        drafts.append(dr)
    pages_with = {d.page for d in drafts}
    for p in br.page_nums:
        if p not in pages_with:
            page_issues.setdefault(p, []).append("no_questions")        # informational (theory/blank page)
    return drafts, page_issues


def materialize(drafts: list[QuestionDraft], ctx, assets_dir: Path) -> list[QuestionOut]:
    cfg: Settings = ctx.cfg
    drafts = sorted(drafts, key=lambda d: (d.page, d.order_idx, d.column, d.region.box.y0))
    out: list[QuestionOut] = []
    for i, d in enumerate(drafts, 1):
        qid = f"q_{i:04d}"
        img = ctx.images.rgb(d.page)
        flags = list(dict.fromkeys(d.flags))
        qimgs = []
        for k, ref in enumerate(d.q_visuals, 1):
            a = save_crop(img, ref, d.page, assets_dir / qid / f"question_{k}.png", f"{qid}_img_{k}", cfg.asset_pad_px)
            qimgs.append(a)
        opts: dict[str, OptionOut] = {}
        for L, od in d.options.items():
            asset = None
            if od.ref is not None:
                asset = save_crop(img, od.ref, d.page, assets_dir / qid / f"option_{L}.png", f"{qid}_opt_{L}",
                                  cfg.asset_pad_px)
            opts[L] = OptionOut(text=od.text, image=asset, image_only=od.image_only)
        out.append(QuestionOut(qid=qid, number=d.number, number_text=d.number_text, page=d.page, column=d.column,
                               bbox_px=None if "no_geometry" in d.flags else d.region.box.to_list(), text=d.stem_text,
                               correct_option=d.correct_option or None, answer_text=d.answer_text,
                               question_images=qimgs, options=opts, flags=flags,
                               ocr_confidence=d.ocr_conf, profile=d.profile))
    return out
