"""Born-digital fast path: exact geometry from the PDF itself (zero API calls).

For pages that were RENDERED (no full-page photo), the PDF already knows where every glyph and every vector
stroke is. That is better geometry than any OCR model can return, so for those pages:
  * text lines            -> Block(type="text")      (question numbers "568." start a line)
  * option labels a)/(b)  -> Block(type="text", plain="a)")  separate anchors, like printed A/B/C/D glyphs
  * option text segments  -> Block(type="text")      the text between two labels on a shared line
  * figures               -> Block(type="figure")    clustered vector strokes (+ raster images) with their atom
                                                     labels (OH, Br, NO2 ...) merged in; one box per structure
These Blocks feed the existing anchor-based segmentation (segment.py) and option layout (options.layout_region).
Figures are exact, so these pages never need residual-ink analysis or a region repass.

Coordinates: PDF points of the displayed (rotated) page, scaled to the canonical render (W x H px).
"""
from __future__ import annotations

import math
import re
from statistics import median
from typing import Optional

import pymupdf

from ..utils.geometry import Box
from .structure import Block

# option label at a word start: "a)", "(a)", "a." followed by content or end of line
# not followed by "," / ")" / "." so prose like "(a), (b), (d) may be formed" is not read as option labels
_LABEL = re.compile(r"(?:(?<=\s)|^)(\(?([a-dA-D])[).])(?=\s*[^\s,).]|\s*$)")
_NUM = re.compile(r"^\s*\(?(\d{1,3})\s*[.)]")       # "12." "12)" "(12)": question numbers, never atom labels


def _line_chars(line: dict) -> list[tuple[str, tuple[float, float, float, float]]]:
    out = []
    for sp in line["spans"]:
        for ch in sp["chars"]:
            out.append((ch["c"], ch["bbox"]))
    return out


def _segments(chars, W_scale: float, H_scale: float):
    """Split one PDF line into (label, label_box) / (text, text_box) pieces at option labels."""
    s = "".join(c for c, _ in chars)
    cuts = []
    for m in _LABEL.finditer(s):
        if m.start(1) > 0 and not s[m.start(1) - 1].isspace() and m.start(1) != 0:
            continue
        cuts.append((m.start(1), m.end(1), m.group(2).lower()))
    # a real option line has labels in increasing order (a, b, c, d); drop stray "(b)" inside option text
    keep, last = [], ""
    for c in cuts:
        if c[2] > last:
            keep.append(c)
            last = c[2]
    pieces = []

    def box_of(i0, i1):
        bs = [chars[i][1] for i in range(i0, i1) if not chars[i][0].isspace()]
        if not bs:
            return None
        return Box(min(b[0] for b in bs) * W_scale, min(b[1] for b in bs) * H_scale,
                   max(b[2] for b in bs) * W_scale, max(b[3] for b in bs) * H_scale)

    pos = 0
    for a, b, L in keep:
        if a > pos and s[pos:a].strip():
            pieces.append(("text", s[pos:a].strip(), box_of(pos, a)))
        pieces.append(("label", s[a:b], box_of(a, b)))
        pos = b
    if s[pos:].strip():
        pieces.append(("text", s[pos:].strip(), box_of(pos, len(s))))
    return [p for p in pieces if p[2] is not None], bool(keep)


def _seg_is_strong(p1, p2, tol_deg: float = 8.0) -> bool:
    dx, dy = p2.x - p1.x, p2.y - p1.y
    if math.hypot(dx, dy) < 1.0:
        return False
    a = abs(math.degrees(math.atan2(dy, dx))) % 180
    return min(a, abs(a - 90), abs(180 - a)) > tol_deg     # diagonal stroke (bond, arrow head, ring edge)


def _path_strength(path: dict) -> bool:
    """True if a drawing contains curves or diagonal strokes (ring/bond/structure), False for rules/frames."""
    for it in path.get("items", []):
        op = it[0]
        if op == "c":
            return True
        if op == "l" and _seg_is_strong(it[1], it[2]):
            return True
        if op == "qu":
            q = it[1]
            if _seg_is_strong(q.ul, q.ur) or _seg_is_strong(q.ul, q.ll):
                return True
    return False


def _merge(boxes: list[Box], gap: float) -> list[Box]:
    boxes = list(boxes)
    changed = True
    while changed:
        changed = False
        out: list[Box] = []
        for b in boxes:
            for i, o in enumerate(out):
                if b.pad(gap).intersect(o) is not None:
                    out[i] = Box.union_all([b, o])
                    changed = True
                    break
            else:
                out.append(b)
        boxes = out
    return boxes


def page_blocks(page: pymupdf.Page, W: int, H: int, page_no: int, batch_id: int = 0) -> list[Block]:
    sx, sy = W / page.rect.width, H / page.rect.height
    rot = page.rotation_matrix
    raw = page.get_text("rawdict", flags=pymupdf.TEXTFLAGS_RAWDICT & ~pymupdf.TEXT_PRESERVE_IMAGES)
    blocks: list[Block] = []
    lines: list[tuple[Box, str]] = []
    k = 0

    def add(kind: str, text: str, box: Box) -> None:
        nonlocal k
        blocks.append(Block(uid=f"pdf{page_no}/{kind}-{k}", id=f"{kind}-{k}", type=kind, page=page_no,
                            local_page=1, box=box, md=text, plain=text, rng=(-1, -1), batch_id=batch_id))
        k += 1

    for b in raw.get("blocks", []):
        if b.get("type") != 0:
            continue
        for ln in b.get("lines", []):
            chars = _line_chars(ln)
            if rot is not None and page.rotation:
                chars = [(c, tuple(pymupdf.Rect(bb) * rot)) for c, bb in chars]
            if not "".join(c for c, _ in chars).strip():
                continue
            pieces, has_labels = _segments(chars, sx, sy)
            line_box = Box.union_all([p[2] for p in pieces])
            if line_box is None or line_box.y0 > 0.955 * H or line_box.y1 < 0.03 * H:
                continue                          # running header / footer ("Page | 6")
            line_text = " ".join(p[1] for p in pieces)
            lines.append((line_box, line_text))
            if not has_labels:
                add("text", line_text, line_box)
                continue
            for kind, text, box in pieces:
                add("text", text, box)            # labels are text blocks whose plain is exactly "a)" etc.

    # ---- figures: clustered vector strokes + raster images
    line_h = median([bx.h for bx, _ in lines]) if lines else 0.012 * H
    line_boxes = [b.box for b in blocks]
    strokes: list[tuple[Box, bool]] = []                  # (box, strong = curve/diagonal)
    for path in page.get_drawings():
        # one PDF path can hold several unconnected bonds (even of two different structures): split it into
        # its segments, otherwise the path's bounding box glues neighbouring structures together
        for it in path.get("items", []):
            op = it[0]
            if op == "l":
                pts, strong = [it[1], it[2]], _seg_is_strong(it[1], it[2])
            elif op == "c":
                pts, strong = list(it[1:5]), True
            elif op == "re":
                pts, strong = [it[1].tl, it[1].br], False
            elif op == "qu":
                q = it[1]
                pts, strong = [q.ul, q.ur, q.ll, q.lr], _seg_is_strong(q.ul, q.ur) or _seg_is_strong(q.ul, q.ll)
            else:
                continue
            r = pymupdf.Rect(min(p.x for p in pts), min(p.y for p in pts), max(p.x for p in pts), max(p.y for p in pts))
            if page.rotation:
                r = r * rot
            # +1px each side: a perfectly horizontal/vertical stroke has zero area and would never "touch"
            bx = Box(r.x0 * sx - 1, r.y0 * sy - 1, r.x1 * sx + 1, r.y1 * sy + 1)
            if bx.w > 0.5 * W or bx.h > 0.5 * H:      # page frames / long rules
                continue
            strokes.append((bx, strong))
    images: list[Box] = []
    for info in page.get_image_info():
        r = pymupdf.Rect(info["bbox"])
        if page.rotation:
            r = r * rot
        bx = Box(r.x0 * sx, r.y0 * sy, r.x1 * sx, r.y1 * sy)
        if (bx.w > 0.9 * W and bx.h > 0.9 * H) or bx.w < 4 or bx.h < 4:
            continue
        images.append(bx)
    # each embedded image is an author-placed figure: images stay ATOMIC (neighbouring structures are often
    # placed a few px apart); vector strokes cluster among themselves and may join an image they touch
    groups: list[list[tuple[Box, bool]]] = [[(im, True)] for im in images]
    n_img = len(groups)
    for st in strokes:
        # diagonal/curved strokes join within a small margin (bonds meet at atoms); straight rules only when
        # they really touch - otherwise neighbouring label boxes and banner frames chain into one big "figure"
        reach = 0.35 * line_h if st[1] else 1.5
        hit = [k for k, g in enumerate(groups)
               if any(st[0].pad(reach if not m[1] else 0.35 * line_h).intersect(m[0]) is not None for m in g)]
        img_hits = [k for k in hit if k < n_img]
        if img_hits:                                   # a stroke touching an image belongs to (one) image
            groups[img_hits[0]].append(st)
            continue
        merged = [st] + [m for k in hit for m in groups[k]]
        groups = [g for k, g in enumerate(groups) if k not in hit] + [merged]
    figs, tables = [], []
    for k, g in enumerate(groups):
        box = Box.union_all([m[0] for m in g])
        if k < n_img:                                  # embedded image: a figure unless it is a speck
            if box.h >= 0.9 * line_h and box.w >= 0.9 * line_h:
                figs.append(box)
            continue
        if box.h < 1.8 * line_h or box.w < 1.8 * line_h:          # arrows, underlines, tiny marks
            continue
        if _is_table_grid(g, box):
            tables.append(box)                         # kept as a table block (cropped as an image later)
            continue
        has_strong = any(m[1] for m in g)
        vt = [m[0] for m in g if m[0].w <= 4 and m[0].h > 6]
        # without curves/diagonals a cluster is a 2-D formula only if a vertical stroke is a BOND: atom text
        # directly above and below it ("CH3 / | / CH"). Boxes around option letters and banner frames are not.
        if has_strong or any(_is_bond(v, line_boxes, line_h) for v in vt):
            figs.append(box)
    # atom labels (OH, Br, NO2, CH3 ...) near a structure belong to the NEAREST one only (a label between two
    # stacked structures must not glue them together)
    label_blocks = [b for b in blocks if len(b.plain) <= 8 and b.plain and not _is_option_label(b.plain)
                    and not _NUM.match(b.plain)]
    grow: list[list[Box]] = [[f] for f in figs]
    absorbed = set()
    for b in label_blocks:
        cand = [(f.dist_to(b.box.cx, b.box.cy) if b.box.intersect(f) is None else 0.0, i)
                for i, f in enumerate(figs) if b.box.intersect(f.pad(0.6 * line_h)) is not None]
        if cand:
            grow[min(cand)[1]].append(b.box)
            absorbed.add(b.uid)
    figs = [Box.union_all(g) for g in grow]
    blocks = [b for b in blocks if b.uid not in absorbed]
    for f in figs:
        add("figure", "", f)
    for t in tables:
        add("table", "", t)
    return blocks


def _is_bond(v: Box, texts: list[Box], line_h: float) -> bool:
    """A vertical stroke with text touching its top end and text touching its bottom end."""
    def near(t: Box, y: float, above: bool) -> bool:
        if not (t.x0 - 0.3 * line_h <= v.cx <= t.x1 + 0.3 * line_h):
            return False
        return (abs(t.y1 - y) <= 0.6 * line_h) if above else (abs(t.y0 - y) <= 0.6 * line_h)
    inside = [t for t in texts if t.contains_point(v.cx, v.cy)]
    if inside:                                         # a stroke drawn through text is a frame side, not a bond
        return False
    return any(near(t, v.y0, True) for t in texts) and any(near(t, v.y1, False) for t in texts)


def _levels(vals: list[tuple[float, float]], tol: float = 3.0) -> list[float]:
    """Group (position, length) of parallel rulings into levels; return the total length per level."""
    out: list[list[float]] = []
    for pos, ln in sorted(vals):
        if out and abs(pos - out[-1][0]) <= tol:
            out[-1][1] += ln
        else:
            out.append([pos, ln])
    return [ln for _, ln in out]


def _is_table_grid(members: list[tuple[Box, bool]], box: Box) -> bool:
    """A ruled table: >=2 horizontal levels covering most of its width and >=2 vertical levels covering most of
    its height, >=5 such levels in total. Rulings may be drawn as one line or cell by cell."""
    if any(strong for _, strong in members):
        return False
    rows = _levels([(m.cy, m.w) for m, _ in members if m.h <= 5 and m.w > 5])
    cols = _levels([(m.cx, m.h) for m, _ in members if m.w <= 5 and m.h > 5])
    good_r = sum(1 for ln in rows if ln >= 0.6 * box.w)
    good_c = sum(1 for ln in cols if ln >= 0.5 * box.h)
    rects = sum(1 for m, _ in members if m.w >= 0.8 * box.w and m.h >= 0.8 * box.h)
    return (good_r >= 2 and good_c >= 2 and good_r + good_c >= 5) or (rects >= 1 and good_r + good_c >= 3)


def _is_option_label(t: str) -> bool:
    return bool(re.fullmatch(r"\(?[a-dA-D][).]", t.strip()))


_KEYLINE = re.compile(r"answer\s*key|q\s*\.\s*no\b|\bans\b", re.I)
_ANCHOR_NUM = re.compile(r"^\s*\(?\d{1,3}\s*[.)]?\s*$")


def number_anchors(page: pymupdf.Page, W: int, H: int, page_no: int) -> list[Block]:
    """Question-number lines of a page whose questions are pictures (screenshot books, MSA scans): the text layer
    still holds the printed numbers at their exact place. Numbers inside / below an answer-key grid are skipped."""
    sx, sy = W / page.rect.width, H / page.rect.height
    lines = [(ln["bbox"], " ".join(sp["text"] for sp in ln["spans"]).strip())
             for blk in page.get_text("dict")["blocks"] for ln in blk.get("lines", [])]
    key_top = min((bb[1] for bb, t in lines if _KEYLINE.search(t)), default=page.rect.height + 1)
    out: list[Block] = []
    for k, (bb, t) in enumerate(lines):
        if not _ANCHOR_NUM.match(t) or bb[1] >= key_top - 2 or bb[0] > 0.62 * page.rect.width:
            continue
        r = pymupdf.Rect(bb)
        if page.rotation:
            r = r * page.rotation_matrix
        out.append(Block(uid=f"pdfnum{page_no}/n-{k}", id=f"n-{k}", type="text", page=page_no, local_page=1,
                         box=Box(r.x0 * sx, r.y0 * sy, r.x1 * sx, r.y1 * sy), md=t, plain=t, rng=(-1, -1)))
    return out


def is_digital(page: pymupdf.Page) -> bool:
    """A page whose content comes from the PDF itself (text layer present, not a full-page photo)."""
    return bool(page.get_text("text").strip())


def doc_page_blocks(pdf_path, page_no: int, W: int, H: int, batch_id: int = 0) -> Optional[list[Block]]:
    with pymupdf.open(str(pdf_path)) as doc:
        page = doc[page_no - 1]
        if not is_digital(page):
            return None
        return page_blocks(page, W, H, page_no, batch_id)
