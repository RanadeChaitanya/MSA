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
from typing import Optional

import cv2
import numpy as np
from PIL import Image

from ..config import Settings
from ..utils.geometry import Box
from .structure import iter_nodes
from .options import OptionRegion

log = logging.getLogger("pipeline.visuals")
MATH_RE = re.compile(r"\$|\\frac|\\sqrt|\\[A-Za-z]+|<sup>|<sub>|\^|_\{")


@dataclass
class VisualRef:
    box: Box
    source_ids: list[str] = field(default_factory=list)
    method: str = "parse_figure"


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
        boxes.append(Box(x0 + x, y0 + y, x0 + x + w, y0 + y + h))
        ink_px += int(ar)
    ub = Box.union_all(boxes)
    if ub is None:
        return False, None
    sig = ub.area >= cfg.ink_min_bbox_frac * W * H and ink_px >= 200
    return sig, (ub if sig else None)


def repass_region(client, img: Image.Image, box: Box, cfg: Settings) -> list[tuple[Box, str]]:
    """Send a tight crop to Landing AI as its own one-page image; return figure boxes in page px."""
    W, H = img.size
    b = box.clamp(W, H)
    x0, y0, x1, y1 = b.int_tuple()
    if x1 - x0 < 20 or y1 - y0 < 20:
        return []
    crop = img.crop((x0, y0, x1, y1))
    s = max(1.0, cfg.repass_min_side / max(1, min(crop.size)))
    s = min(s, 4.0, 4000.0 / max(crop.size))
    if s > 1.01:
        crop = crop.resize((int(crop.width * s), int(crop.height * s)), Image.LANCZOS)
    buf = BytesIO()
    crop.save(buf, format="PNG")
    out = client.parse(buf.getvalue(), "region.png", options={"inline_markdown": False, "atomic_grounding": False})
    cw, ch = x1 - x0, y1 - y0
    found = []
    for el in iter_nodes(out.get("structure") or {}):
        if el.get("type") == "figure":
            g = el["grounding"]["box"]
            found.append((Box(x0 + g["xmin"] * cw, y0 + g["ymin"] * ch, x0 + g["xmax"] * cw, y0 + g["ymax"] * ch),
                          f"repass:{el['id']}"))
    return found


def question_visuals(client, images, page: int, stem_box: Box, stem_texts, stem_figs, cfg: Settings,
                     flags: list[str]) -> list[VisualRef]:
    refs = [VisualRef(f.box, [f.uid], "parse_figure") for f in stem_figs]
    gray = images.gray(page)
    mask = [t.box for t in stem_texts] + [f.box for f in stem_figs]
    sig, ink_box = residual_ink(gray, stem_box, mask, cfg)
    if sig and not refs:
        found = repass_region(client, images.rgb(page), ink_box.pad(0.01 * gray.shape[1]), cfg)
        if found:
            refs = [VisualRef(b, [sid], "repass_figure") for b, sid in found]
            flags.append("figure_found_by_repass")
        else:
            refs = [VisualRef(ink_box, [], "ink_fallback")]
            flags.append("ink_fallback_question")
    elif sig and refs:
        flags.append("unexplained_ink_in_stem")
    return refs


def option_visual(client, images, page: int, opt: OptionRegion, text: str, cfg: Settings,
                  flags: list[str]) -> tuple[Optional[VisualRef], bool]:
    """Returns (visual ref or None, image_only)."""
    gray = images.gray(page)
    W = gray.shape[1]
    label_box = [opt.anchor.box] if opt.anchor is not None else []
    has_text = bool(text.strip())
    ref: Optional[VisualRef] = None
    if opt.figs:
        ub = Box.union_all([f.box for f in opt.figs])
        ref = VisualRef(ub, [f.uid for f in opt.figs], "parse_figure")
    else:
        mask = label_box + [t.box for t in opt.texts]
        area = Box(opt.box.x0 + 3, opt.box.y0 + 3, opt.box.x1 - 3, opt.box.y1 - 3)
        sig, ink_box = residual_ink(gray, area, mask, cfg)
        if sig:
            found = repass_region(client, images.rgb(page), ink_box.pad(0.008 * W), cfg)
            if found:
                ub = Box.union_all([b for b, _ in found])
                ref = VisualRef(ub, [s for _, s in found], "repass_figure")
                flags.append(f"opt_{opt.label}_figure_by_repass")
            else:
                ref = VisualRef(ink_box, [], "ink_fallback")
                flags.append(f"opt_{opt.label}_ink_fallback")
    mode = cfg.option_image_mode
    if ref is None and mode != "visual_only":
        want = (mode == "always") or (not has_text) or bool(MATH_RE.search(text))
        if want:
            cb = opt.content_box or Box(opt.box.x0 + 3, opt.box.y0 + 3, opt.box.x1 - 3, opt.box.y1 - 3)
            ref = VisualRef(cb, [t.uid for t in opt.texts], "math_text_crop" if has_text else "option_region")
    return ref, (ref is not None and not has_text)
