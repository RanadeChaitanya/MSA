"""STAGE 4/5 - Visual routing, residual-ink check and region REPASS to Landing AI.

Decides, per question and per option, whether there is an image and where it is:
  1. Parse `figure` blocks inside the stem/option region  -> method parse_figure
  2. Residual-ink check: dark non-text pixels that Parse did not explain (catches figures
     mislabeled as text on photos). If found -> repass the region crop to Landing AI.
       repass finds a figure -> repass_figure ; otherwise bbox of the ink -> ink_fallback
  3. Option math policy (OPTION_IMAGE_MODE): math/empty options also get a crop (math_text_crop)
Boxes from repass are mapped back: page_px = crop_origin + norm_box * crop_size (scale cancels).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from io import BytesIO
from typing import Callable, Optional

import cv2
import numpy as np
from PIL import Image

from ..config import Settings
from ..utils.geometry import Box
from .structure import iter_nodes
from .options import OptionRegion

log = logging.getLogger("pipeline.visuals")
# Only content that plain text cannot represent faithfully (fractions, roots, reaction arrows, matrices, display math).
# Subscripts/superscripts (CH<sub>3</sub>COOH, sp3, x^2) are NOT complex: the extracted text is already correct.
MATH_RE = re.compile(r"\\(?:frac|dfrac|sqrt|xrightarrow|xleftarrow|longrightarrow|rightleftharpoons|begin|"
                     r"overset|underset|sum|int|vec|overline|underline)\b|\$\$")


@dataclass
class VisualRef:
    box: Box
    source_ids: list[str] = field(default_factory=list)
    method: str = "parse_figure"
    description: str = ""            # Landing AI's figure description (parse or repass), "" if none
    description_source: str = ""     # parse_figure | repass | ""
    pos: float = 1.0                 # fraction of the owner's text that precedes this image (0 = before, 1 = after)
    pos_exact: bool = True           # False when the image sits between text lines and the split is estimated
    certain: bool = True             # False: the box is a fallback (whole cell / ink bbox), not a detected figure


_TAGS = re.compile(r"<[^>]+>")
from .structure import KEY_TABLE as _KEY_TABLE  # noqa: E402
_DESC = re.compile(r"<description>(.*?)</description>", re.S | re.I)


def figure_description(md: str) -> str:
    """Plain-text description of a Landing AI figure block (its <description> or, failing that, its text)."""
    if not md:
        return ""
    m = _DESC.search(md)
    body = m.group(1) if m else _TAGS.sub(" ", md)
    return re.sub(r"\s+", " ", _TAGS.sub(" ", body)).strip()


def _merge_overlapping(blocks: list) -> list[list]:
    """Group figure blocks that overlap (Parse sometimes splits one diagram); separate figures stay separate."""
    groups: list[list] = []
    for b in sorted(blocks, key=lambda b: (b.box.y0, b.box.x0)):
        for g in groups:
            if any(b.box.iou(o.box) > 0.3 or b.box.overlap_ratio(o.box) > 0.7 or o.box.overlap_ratio(b.box) > 0.7
                   for o in g):
                g.append(b)
                break
        else:
            groups.append([b])
    return groups


def refs_from_figs(figs: list, method: str) -> list[VisualRef]:
    out = []
    for g in _merge_overlapping(figs):
        desc = " ".join(d for d in (figure_description(b.md) for b in g) if d)
        out.append(VisualRef(Box.union_all([b.box for b in g]), [b.uid for b in g], method, description=desc,
                             description_source="parse_figure" if desc else ""))
    return out


class ImageCache:
    def __init__(self, records: dict):
        self.records = records
        self._rgb: dict[int, Image.Image] = {}
        self._gray: dict[int, np.ndarray] = {}

    def invalidate(self, page: int) -> None:
        self._rgb.pop(page, None)
        self._gray.pop(page, None)

    def rgb(self, page: int) -> Image.Image:
        if page not in self._rgb:
            self._rgb[page] = Image.open(self.records[page].canonical_path).convert("RGB")
        return self._rgb[page]

    def gray(self, page: int) -> np.ndarray:
        if page not in self._gray:
            self._gray[page] = cv2.cvtColor(np.array(self.rgb(page)), cv2.COLOR_RGB2GRAY)
        return self._gray[page]


def residual_ink(gray: np.ndarray, area: Box, mask_boxes: list[Box], cfg: Settings) -> tuple[bool, Optional[Box]]:
    """Dark, non-text, non-border ink inside `area`. Returns (significant?, bbox in page px)."""
    H, W = gray.shape
    a = area.clamp(W, H)
    x0, y0, x1, y1 = a.int_tuple()
    if x1 - x0 < 10 or y1 - y0 < 10:
        return False, None
    crop = gray[y0:y1, x0:x1]
    binary = (crop < cfg.ink_threshold).astype(np.uint8)
    for mb in mask_boxes:
        m = mb.pad(4)
        bx0, by0 = max(0, int(m.x0 - x0)), max(0, int(m.y0 - y0))
        bx1, by1 = min(binary.shape[1], int(m.x1 - x0) + 1), min(binary.shape[0], int(m.y1 - y0) + 1)
        if bx1 > bx0 and by1 > by0:
            binary[by0:by1, bx0:bx1] = 0
    n, _, stats, _ = cv2.connectedComponentsWithStats(binary, connectivity=8)
    ch, cw = binary.shape
    thin = max(6, int(0.0025 * W))
    boxes, ink_px = [], 0
    for i in range(1, n):
        x, y, w, h, ar = stats[i]
        if ar < 12:
            continue
        if min(w, h) <= thin and max(w, h) >= 3 * thin:           # ruling lines
            continue
        if (x <= 3 and x + w >= cw - 3) or (y <= 3 and y + h >= ch - 3):   # frames spanning the area
            continue
        if w > 40 and h > 40 and ar / float(w * h) < 0.05:          # sparse grids/frames
            continue
        if h < 0.03 * H and w > 6 * h:                              # a line of (unmasked) text, not a figure
            continue
        if (w >= 0.6 * cw or h >= 0.6 * ch) and ar / float(w * h) < 0.15:   # table frame / long rule clipped by the crop
            continue
        boxes.append(Box(x0 + x, y0 + y, x0 + x + w, y0 + y + h))
        ink_px += int(ar)
    ub = Box.union_all(boxes)
    if ub is None:
        return False, None
    sig = ub.area >= cfg.ink_min_bbox_frac * W * H and ink_px >= 200
    return sig, (ub if sig else None)


@dataclass
class _Pending:
    page: int
    box: Box
    found: Callable[[list[tuple[Box, str, str]]], None]
    missing: Callable[[], None]


class RepassQueue:
    """Collects repass candidates while drafts are built, then sends ONE mosaic image per page to Landing AI.
    Only photo/scan pages ever queue anything (digital pages have exact figures from the PDF layer), only
    image-only options and figure-sized stem ink qualify, and at most REPASS_MAX_PER_PAGE regions per page go."""

    def __init__(self) -> None:
        self.items: list[_Pending] = []

    def add(self, page: int, box: Box, found, missing) -> None:
        self.items.append(_Pending(page, box, found, missing))

    def flush(self, client, images, cfg: Settings) -> None:
        by_page: dict[int, list[_Pending]] = {}
        for it in self.items:
            by_page.setdefault(it.page, []).append(it)
        self.items = []
        for page, items in by_page.items():
            items.sort(key=lambda it: it.box.area, reverse=True)
            n = max(0, cfg.repass_max_per_page)
            keep, rest = items[:n], items[n:]
            if rest:
                log.info("page %d: %d repass candidate(s) over budget, using ink fallback", page, len(rest))
            for it in rest:
                it.missing()
            if not keep:
                continue
            results = mosaic_repass(client, images.rgb(page), [it.box for it in keep], cfg)
            for it, found in zip(keep, results):
                if found:
                    it.found(found)
                else:
                    it.missing()


def _element_md(el: dict, out: dict) -> str:
    """An element's markdown: inline when present, else its grounding range in the response markdown
    (repass/describe calls use inline_markdown=false)."""
    if el.get("markdown"):
        return el["markdown"]
    r = (el.get("grounding") or {}).get("range")
    md = out.get("markdown") or ""
    if isinstance(r, dict) and "start" in r and "end" in r:
        return md[r["start"]:r["end"]]
    if isinstance(r, (list, tuple)) and len(r) == 2:
        return md[r[0]:r[1]]
    return ""


def mosaic_repass(client, img: Image.Image, boxes: list[Box], cfg: Settings) -> list[list[tuple[Box, str, str]]]:
    """Stack the (upscaled) crops vertically on white with gaps, parse ONCE, map each figure back to the crop
    whose tile holds its centre: page_px = crop_origin + (mosaic_px - tile_origin) / tile_scale."""
    W, H = img.size
    tiles: list = []
    for box in boxes:
        x0, y0, x1, y1 = box.clamp(W, H).int_tuple()
        if x1 - x0 < 20 or y1 - y0 < 20:
            tiles.append(None)
            continue
        crop = img.crop((x0, y0, x1, y1))
        s = max(1.0, cfg.repass_min_side / max(1, min(crop.size)))
        s = min(s, 4.0, 4000.0 / max(crop.size))
        tiles.append((crop, x0, y0, s))
    live = [t for t in tiles if t is not None]
    if not live:
        return [[] for _ in boxes]
    total = sum(t[0].height * t[3] for t in live)
    shrink = min(1.0, 9000.0 / total)                   # keep the mosaic within a comfortable upload size
    gap = 60 if len(live) > 1 else 0
    placed, y = [], 0
    for t in tiles:
        if t is None:
            placed.append(None)
            continue
        crop, ox, oy, s = t
        s *= shrink
        tw, th = max(1, int(crop.width * s)), max(1, int(crop.height * s))
        im = crop.resize((tw, th), Image.LANCZOS) if (tw, th) != crop.size else crop
        placed.append((im, ox, oy, tw / crop.width, th / crop.height, y))
        y += th + gap
    MW, MH = max(p[0].width for p in placed if p), y - gap
    mosaic = Image.new("RGB", (MW, MH), "white")
    for p in placed:
        if p:
            mosaic.paste(p[0], (0, p[5]))
    buf = BytesIO()
    mosaic.save(buf, format="PNG")
    try:
        out = client.parse(buf.getvalue(), "region.png" if len(live) == 1 else "mosaic.png",
                           options={"inline_markdown": False, "atomic_grounding": False})
    except Exception as e:                       # a failed repass degrades to ink_fallback, never kills the run
        log.warning("mosaic repass failed: %s", e)
        return [[] for _ in boxes]
    res: list[list[tuple[Box, str, str]]] = [[] for _ in boxes]
    for el in iter_nodes(out.get("structure") or {}):
        if el.get("type") != "figure":
            continue
        g = el["grounding"]["box"]
        fb = Box(g["xmin"] * MW, g["ymin"] * MH, g["xmax"] * MW, g["ymax"] * MH)
        for k, p in enumerate(placed):
            if not p:
                continue
            im, ox, oy, sx, sy, ty = p
            tile = Box(0, ty, im.width, ty + im.height)
            if not tile.contains_point(fb.cx, fb.cy):
                continue
            i = fb.intersect(tile)
            if i is not None:
                res[k].append((Box(ox + i.x0 / sx, oy + (i.y0 - ty) / sy, ox + i.x1 / sx, oy + (i.y1 - ty) / sy),
                               f"repass:{el['id']}", figure_description(_element_md(el, out))))
            break
    return res


def _line_height(boxes: list[Box], H: int) -> float:
    hs = sorted(b.h for b in boxes if 0 < b.h < 0.05 * H)
    return hs[len(hs) // 2] if hs else 0.012 * H


def question_visuals(queue: RepassQueue, images, page: int, layout, cfg: Settings, flags: list[str]) -> list[VisualRef]:
    stem_box, stem_texts, stem_figs = layout.stem_box, layout.stem_texts, layout.stem_figs
    refs = refs_from_figs(stem_figs, "pdf_figure" if layout.exact else "parse_figure")
    # tables in the stem are kept as images (their text also stays in the stem as an HTML table component);
    # not answer-key grids, and not tables that ARE the question layout (MSA / OneNote boxes around everything)
    for t in stem_texts:
        if t.type != "table" or not stem_box.area or _KEY_TABLE.search(t.plain or t.md or ""):
            continue
        if t.box.overlap_ratio(stem_box) > 0.7 and t.box.area < 0.75 * stem_box.area:
            refs.append(VisualRef(t.box, [t.uid], "table_crop"))
    if not layout.exact and stem_box.area > 0:
        text_boxes = [t.box for t in stem_texts] + list(layout.stem_mask_boxes)
        for r in [x for x in refs if x.method != "table_crop"]:
            if r.box.overlap_ratio(stem_box) < 0.5 and stem_box.overlap_ratio(r.box) < 0.5:
                continue
            if any(tb.overlap_ratio(r.box) < 0.5 for tb in text_boxes):
                continue                       # the stem's text is outside the figure: a real diagram
            # a "figure" covering most of the stem is the whole question (text + diagram) swallowed by Parse:
            # keep it only as an uncertain fallback and let the page's single repass isolate the real diagram
            r.certain = False
            flags.append("figure_contains_text")

            def found(fs, r=r, refs=refs, flags=flags):
                if r in refs:
                    refs.remove(r)
                refs.extend(VisualRef(b, [sid], "repass_figure", description=d or r.description,
                                      description_source="repass" if d else r.description_source)
                            for b, sid, d in fs)
                flags.append("figure_found_by_repass")
            queue.add(page, r.box, found, lambda: None)
    if layout.exact or refs:
        # exact (PDF-layer) figures or Parse figures already answer the question: text-only stems cost nothing
        return refs
    if not layout.has_stem_coverage:
        # Without knowing where the text is, ink analysis would mistake text for figures (this was a real bug).
        flags.append("stem_geometry_unknown")
        return refs
    gray = images.gray(page)
    atoms = [a["box"] for t in stem_texts for a in t.atomic]
    lh = _line_height(atoms + list(layout.stem_mask_boxes), gray.shape[0])
    # an "atomic line" several line-heights tall is a diagram whose labels Parse read as text: not a mask
    mask = [b for b in atoms if b.h <= 2.5 * lh] + \
        [t.box for t in stem_texts if not t.atomic] + list(layout.stem_mask_boxes)
    sig, ink_box = residual_ink(gray, stem_box, mask, cfg)
    if sig and ink_box.h >= 2.0 * lh and ink_box.w >= 2.0 * lh:     # figure-sized, not a stray formula/arrow
        def found(fs, refs=refs, flags=flags):
            refs.extend(VisualRef(b, [sid], "repass_figure", description=d, description_source="repass" if d else "")
                        for b, sid, d in fs)
            flags.append("figure_found_by_repass")

        def missing(flags=flags):
            flags.append("ink_unconfirmed_question")         # reported, but NO junk crop is produced
        queue.add(page, ink_box.pad(0.01 * gray.shape[1]), found, missing)
    return refs


def option_visual(queue: RepassQueue, images, page: int, opt: OptionRegion, text: str, cfg: Settings,
                  flags: list[str], exact: bool = False, target=None) -> tuple[list[VisualRef], bool]:
    """Returns (image refs - zero, one or several, image_only). A queued repass later replaces `target.refs`."""
    has_text = bool(text.strip())
    ref: Optional[VisualRef] = None
    if opt.figs:
        refs = refs_from_figs(opt.figs, "pdf_figure" if exact else "parse_figure")
        return refs, not has_text
    elif not has_text and not exact:
        # image-only option that Parse did not mark as a figure (photos): the only option case that may repass
        gray = images.gray(page)
        W = gray.shape[1]
        label_box = [opt.anchor.box] if opt.anchor is not None else []
        mask = label_box + [t.box for t in opt.texts] + list(opt.mask_boxes)
        area = Box(opt.box.x0 + 3, opt.box.y0 + 3, opt.box.x1 - 3, opt.box.y1 - 3)
        sig, ink_box = residual_ink(gray, area, mask, cfg)
        if sig:
            ref = VisualRef(ink_box, [], "ink_fallback", certain=False)
            flags.append(f"opt_{opt.label}_ink_fallback")
            if target is not None:
                L = opt.label

                def found(fs, target=target, flags=flags, L=L):
                    target.refs = [VisualRef(b, [sid], "repass_figure", description=d,
                                             description_source="repass" if d else "") for b, sid, d in fs]
                    target.image_only = True
                    if f"opt_{L}_ink_fallback" in flags:
                        flags.remove(f"opt_{L}_ink_fallback")
                    flags.append(f"opt_{L}_figure_by_repass")
                queue.add(page, ink_box.pad(0.008 * W), found, lambda: None)
    mode = cfg.option_image_mode
    # An option with no text but a known cell IS an image option, whatever the default policy says
    # (Extract returns null for it, e.g. "option_d was not found in the document").
    if ref is None and (mode != "visual_only" or not has_text):
        want = (mode == "always") or (not has_text) or bool(MATH_RE.search(text))
        if want:
            W = images.gray(page).shape[1]
            inset = Box(opt.box.x0 + 3, opt.box.y0 + 3, opt.box.x1 - 3, opt.box.y1 - 3)
            cbx = opt.content_box
            if has_text and cbx is not None:
                cb = cbx.pad(0.008 * W)                      # tight crop around the text/equation
            elif opt.use_cell_crop or cbx is None:
                cb = inset                                   # image-only option: the whole grid cell
            else:
                cb = cbx
            ref = VisualRef(cb, [t.uid for t in opt.texts], "math_text_crop" if has_text else "option_region",
                            certain=has_text)
    return ([ref] if ref is not None else []), (ref is not None and not has_text)
