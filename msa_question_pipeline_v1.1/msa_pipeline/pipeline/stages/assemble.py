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
from .pdf_layer import is_digital, page_blocks
from .options import RegionLayout, layout_from_spans, layout_region
from .parse_stage import BatchResult
from .segment import Region, segment_page
from .semantics import run_semantics
from .structure import mark_watermarks
from .visuals import (ImageCache, RepassQueue, VisualRef, figure_description, mosaic_repass, option_visual,
                      question_visuals)

log = logging.getLogger("pipeline.assemble")
LABELS = ["A", "B", "C", "D"]


@dataclass
class OptionDraft:
    label: str
    text: str = ""
    refs: list[VisualRef] = field(default_factory=list)      # zero, one or several images
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


def _synth_regions(sem, br: BatchResult, sizes, cfg: Settings, start_idx: int,
                   page_blocks_override: Optional[dict[int, list]] = None) -> dict[int, Region]:
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
        src = (page_blocks_override or {}).get(page)
        pb = [b for b in (src if src is not None else br.blocks)
              if b.page == page and b.type in LEAF and not b.watermark]
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


def _make_draft(region: Region, layout: RegionLayout, item: Optional[dict], geo, ctx, profile: str,
                queue: RepassQueue) -> QuestionDraft:
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
    fallback = layout.stem_fallback_text
    if region.inline_number and region.anchor is not None:        # "568.Picric acid is": the anchor line IS stem text
        fallback = (re.sub(r"^\s*\d{1,3}\s*[.)]\s*", "", region.anchor.plain) + " " + fallback).strip()
    d.stem_text = stem or fallback
    d.answer_text = " ".join(b.md for b in layout.answer_blocks if b.md).strip()
    ca = str((item or {}).get("correct_option") or "").strip().upper()[:1]
    d.correct_option = ca if ca in LABELS else ""
    labels = set(layout.options) | {L for L in LABELS if _opt_text(item, L)}
    for L in sorted(labels):
        reg = layout.options.get(L)
        text = _opt_text(item, L) or (reg.fallback_text if reg else "")
        od = OptionDraft(label=L, text=text, box=reg.box if reg else None)
        if reg is not None:
            od.refs, od.image_only = option_visual(queue, ctx.images, region.page, reg, text, cfg, d.flags,
                                                   exact=layout.exact, target=od)
        elif "no_geometry" not in layout.flags:
            d.flags.append(f"option_{L}_without_region")
        if not od.text and not od.refs:
            d.flags.append(f"option_{L}_empty")
        d.options[L] = od
    if "no_geometry" not in layout.flags:
        d.q_visuals = question_visuals(queue, ctx.images, region.page, layout, cfg, d.flags)
    toks = d.stem_text.split()
    if toks and ((len(toks[-1]) == 1 and toks[-1].isalpha() and toks[-1].lower() not in ("a", "i"))
                 or toks[-1].endswith(("-", "…"))):
        d.flags.append("stem_possibly_truncated")
    return d


def _line_weights(blocks, boxes) -> list[tuple[Box, float]]:
    """Text lines (box, weight=chars) that make up a stem/option; weight 1 when the text length is unknown."""
    out = [(b.box, float(max(1, len(b.plain or "")))) for b in blocks if b.type != "figure"]
    out += [(bx, 1.0) for bx in boxes]
    return out


def _position(ref: VisualRef, lines: list[tuple[Box, float]]) -> tuple[float, bool]:
    """Where the image sits in its owner's reading order: fraction of text weight before it, and whether that is
    exact (all text strictly before or strictly after) or an estimate (text both above and below the image)."""
    before = after = 0.0
    for bx, w in lines:
        if bx.overlap_ratio(ref.box) > 0.5:          # labels drawn inside the figure are part of the image
            continue
        if bx.y1 <= ref.box.y0 + 0.25 * bx.h:
            before += w
        elif bx.y0 >= ref.box.y1 - 0.25 * bx.h:
            after += w
        elif bx.cx < ref.box.x0:                     # same height: left of the image reads first
            before += w
        else:
            after += w
    if before + after == 0:
        return 1.0, True
    return before / (before + after), (before == 0 or after == 0)


def _annotate_visuals(d: QuestionDraft, br: BatchResult) -> None:
    """Fill reading-order position and Landing AI description for every image of a draft."""
    if "no_geometry" in d.flags:
        return
    lay = d.layout
    stem_lines = _line_weights(lay.stem_texts, lay.stem_mask_boxes)
    if d.region.inline_number and d.region.anchor is not None:
        stem_lines.append((d.region.anchor.box, float(max(1, len(d.region.anchor.plain or "")))))
    figs = [b for b in br.blocks if b.type == "figure" and b.page == d.page]
    for r in d.q_visuals:
        r.pos, r.pos_exact = _position(r, stem_lines)
    for L, od in d.options.items():
        reg = lay.options.get(L)
        lines = _line_weights(reg.texts, reg.geom_boxes) if reg is not None else []
        for r in od.refs:
            r.pos, r.pos_exact = _position(r, lines)
    for r in list(d.q_visuals) + [r for od in d.options.values() for r in od.refs]:
        if r.description or r.method in ("math_text_crop", "option_region", "ink_fallback"):
            continue
        best, score = None, 0.0                       # PDF-layer figure -> description of the Parse figure over it
        for f in figs:
            i = r.box.intersect(f.box)
            if i is None:
                continue
            sc = i.area / max(1.0, min(r.box.area, f.box.area))
            if sc > score:
                best, score = f, sc
        if best is not None and score > 0.5:
            r.description = figure_description(best.md)
            r.description_source = "parse_figure" if r.description else ""


def _describe_missing(drafts: list[QuestionDraft], ctx) -> None:
    """Images found from the PDF layer (or tables) have no Landing AI description: send all of a page's
    undescribed crops as ONE mosaic and take the description Landing AI gives each tile. One call per such page,
    cached like every other call; pages whose images are already described cost nothing."""
    by_page: dict[int, list[VisualRef]] = {}
    for d in drafts:
        for r in list(d.q_visuals) + [r for od in d.options.values() for r in od.refs]:
            if not r.description and r.method in ("pdf_figure", "parse_figure", "repass_figure", "table_crop"):
                by_page.setdefault(d.page, []).append(r)
    pad = 0.006 * max(1, max((ctx.records[p].width for p in by_page), default=1))
    for page, refs in by_page.items():
        results = mosaic_repass(ctx.client, ctx.images.rgb(page), [r.box.pad(pad) for r in refs], ctx.cfg)
        for r, found in zip(refs, results):
            desc = " ".join(dict.fromkeys(d for _, _, d in found if d))
            if desc:
                r.description, r.description_source = desc, "describe"


def _pdf_geometry(br: BatchResult, ctx, sizes) -> dict[int, list]:
    """Exact Blocks from the PDF for pages that were rendered from a born-digital PDF (not photos/scans)."""
    pdf_path = getattr(ctx, "pdf_path", None)
    kinds = getattr(ctx, "page_kinds", {}) or {}
    # only pages whose questions are REAL text: on composite pages (pasted screenshots) every screenshot would
    # look like a figure, and scans have no usable text layer
    pages = [p for p in br.page_nums if ctx.records[p].source == "render" and not ctx.records[p].rectified
             and kinds.get(p, "question_digital") == "question_digital"]
    if pdf_path is None or not pages:
        return {}
    import pymupdf
    out: dict[int, list] = {}
    with pymupdf.open(str(pdf_path)) as doc:
        for p in pages:
            page = doc[p - 1]
            if is_digital(page):
                W, H = sizes[p]
                out[p] = page_blocks(page, W, H, p, br.batch_id)
    return out


def _number_anchors(br: BatchResult, ctx, sizes, pdf_geo) -> dict[int, list]:
    """Exact question-number positions from the PDF text layer for screenshot / scanned pages."""
    pdf_path = getattr(ctx, "pdf_path", None)
    kinds = getattr(ctx, "page_kinds", {}) or {}
    pages = [p for p in br.page_nums if p not in pdf_geo and kinds.get(p) in ("question_composite", "question_scanned")
             and ctx.records[p].source == "render" and not ctx.records[p].rectified]
    if pdf_path is None or not pages:
        return {}
    import pymupdf
    from .pdf_layer import number_anchors
    out: dict[int, list] = {}
    with pymupdf.open(str(pdf_path)) as doc:
        for p in pages:
            W, H = sizes[p]
            a = number_anchors(doc[p - 1], W, H, p)
            if a:
                out[p] = a
    return out


def _mark_exact(layout: RegionLayout, exact: bool) -> None:
    if exact:
        layout.exact = True
        layout.flags.append("geometry_from_pdf")


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
    pdf_geo = _pdf_geometry(br, ctx, sizes)               # page -> exact blocks (digital pages only)
    num_anchors = _number_anchors(br, ctx, sizes, pdf_geo)   # page -> exact question numbers (picture pages)
    queue = RepassQueue()
    for p in br.page_nums:
        W, H = sizes[p]
        pb = pdf_geo.get(p) or [b for b in br.blocks if b.page == p]
        mark_watermarks(pb, cfg, W, H)
        # PDF text-layer numbers are real glyph runs at line starts (no stacked-fraction digits), so dense
        # one-line questions only need a one-line gap; OCR blocks keep the conservative MIN_ANCHOR_GAP_FRAC.
        anchors = num_anchors.get(p)
        exact = p in pdf_geo or bool(anchors)
        rs = segment_page(pb, W, H, cfg, p, br.batch_id, hint=(hint + 1) if hint else None,
                          min_gap_frac=0.008 if exact else None,
                          min_cluster=1 if exact else None,             # one long question per page is common
                          anchors=anchors or None)
        regions.extend(rs)
        if rs:
            hint = max(r.number for r in rs)
    for i, r in enumerate(regions):
        r.idx = i
    sem = run_semantics(ctx.client, br, regions, sizes, cfg)
    if sem.retry_pages and hasattr(ctx, "retry_parse"):
        ctx.retry_parse.update(sem.retry_pages)
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
        if item is None and (len([o for o in layout.options.values() if o.anchor]) < 3
                             or "option_order_mismatch" in layout.flags):
            continue      # a numbered line Extract did not call a question and without >=3 in-order a)-d) labels
                          # is an answer-key/solution entry or a continuation, not an MCQ
        if "no_option_anchors" in layout.flags and geo is not None:
            alt = layout_from_spans(r, geo, cfg, W, H)
            if alt.options:
                alt.flags = [f for f in layout.flags if f != "no_option_anchors"] + alt.flags
                layout = alt
        _mark_exact(layout, r.page in pdf_geo)
        dr = _make_draft(r, layout, item, geo, ctx, profile, queue)
        dr.order_idx = item_idx if item_idx is not None else 10 ** 6 + r.idx
        drafts.append(dr)
    # (b) extracted questions without an anchor region -> span-derived regions
    synth = _synth_regions(sem, br, sizes, cfg, start_idx=len(regions), page_blocks_override=pdf_geo)
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
            _mark_exact(layout, r.page in pdf_geo)
        dr = _make_draft(r, layout, item, geo, ctx, profile, queue)
        dr.order_idx = i
        drafts.append(dr)
    queue.flush(ctx.client, ctx.images, cfg)            # at most ONE mosaic repass call per photo page
    for d in drafts:
        _annotate_visuals(d, br)
    if cfg.describe_images:                         # not for pages about to be re-read with Parse
        retry = getattr(ctx, "retry_parse", set()) or set()
        _describe_missing([d for d in drafts if d.page not in retry], ctx)
    for p in pdf_geo:                                     # exact geometry: reading order is the page order
        on_page = sorted((d for d in drafts if d.page == p), key=lambda d: (d.column, d.region.box.y0))
        for k, d in enumerate(on_page):
            d.order_idx = k
    pages_with = {d.page for d in drafts}
    for p in br.page_nums:
        if p not in pages_with:
            page_issues.setdefault(p, []).append("no_questions")        # informational (theory/blank page)
    return drafts, page_issues


def question_ids(drafts: list[QuestionDraft]) -> list[str]:
    """Stable, human-readable ids: p<page>-q<printed number> (x<index> when no number); -2, -3 for duplicates."""
    ids, seen = [], {}
    for k, d in enumerate(drafts, 1):
        base = f"p{d.page:04d}-q{d.number:03d}" if d.number else f"p{d.page:04d}-x{k:03d}"
        n = seen.get(base, 0) + 1
        seen[base] = n
        ids.append(base if n == 1 else f"{base}-{n}")
    return ids


def materialize(drafts: list[QuestionDraft], ctx, assets_dir: Path) -> list[QuestionOut]:
    cfg: Settings = ctx.cfg
    drafts = sorted(drafts, key=lambda d: (d.page, d.order_idx, d.column, d.region.box.y0))
    out: list[QuestionOut] = []
    for qid, d in zip(question_ids(drafts), drafts):
        img = ctx.images.rgb(d.page)
        flags = list(dict.fromkeys(d.flags))
        if qid.count("-") > 1:
            flags.append("duplicate_number")
        qimgs = [save_crop(img, ref, d.page, assets_dir / qid / f"stem_{k}.png", f"{qid}/stem/{k}", cfg.asset_pad_px)
                 for k, ref in enumerate(d.q_visuals, 1)]
        opts: dict[str, OptionOut] = {}
        for L, od in d.options.items():
            imgs = [save_crop(img, ref, d.page, assets_dir / qid / f"opt_{L}_{k}.png", f"{qid}/opt_{L}/{k}",
                              cfg.asset_pad_px) for k, ref in enumerate(od.refs, 1)]
            opts[L] = OptionOut(text=od.text, images=imgs, image_only=od.image_only)
        out.append(QuestionOut(qid=qid, number=d.number, number_text=d.number_text, page=d.page, column=d.column,
                               bbox_px=None if "no_geometry" in d.flags else d.region.box.to_list(), text=d.stem_text,
                               correct_option=d.correct_option or None,
                               answer_source="printed_inline" if d.correct_option else None,
                               answer_text=d.answer_text, question_images=qimgs, options=opts, flags=flags,
                               ocr_confidence=d.ocr_conf, profile=d.profile))
    return out
