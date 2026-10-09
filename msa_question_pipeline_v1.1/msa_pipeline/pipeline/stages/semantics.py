"""STAGE 2 - Question semantics via Landing AI Extract, grounded CLIENT-SIDE. (Semantic-first)

Extract(markdown, schema) -> per-field {value, spans}; spans are [start,end) code-point offsets into the markdown
we sent. Parse blocks (and their atomic lines) carry ranges in the same coordinate system, so each field is
grounded to pixel boxes by overlapping ranges. Precision ladder per span:
    atomic line/word (precise, proportionally sliced when a line holds several fields)
  > leaf block (text / table_cell / marginalia / figure)
  > opaque table block (coarse -> flagged)

Extract is the SOURCE OF TRUTH for which questions exist. Geometry is attached afterwards:
  * items that land in an anchor-based Region are bound to it (votes by grounded centres)
  * all other items are returned in `unmatched` so the assembler can build a span-derived region for them.
No extracted question is ever dropped for lack of geometry.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Optional

from ..lat.schemas import QUESTION_SCHEMA
from ..utils.geometry import Box
from .parse_stage import BatchResult
from .segment import Region
from .structure import key_table_ids

log = logging.getLogger("pipeline.semantics")


@dataclass
class Piece:
    page: int
    box: Box
    precise: bool          # False = coarse (whole block / opaque table)
    sliced: bool = False   # horizontally interpolated inside a shared line
    line: Optional[Box] = None   # FULL atomic line/block box: used for ink masking (never the sliced box)


@dataclass
class ItemGeo:
    fields: dict[str, list[Piece]] = field(default_factory=dict)
    page: Optional[int] = None
    method: str = "spans"            # spans | text   (how the geometry was obtained)
    page_inferred: bool = False      # page guessed from neighbouring questions (no geometry at all)

    def boxes(self, name: str) -> list[Box]:
        return [p.box for p in self.fields.get(name, [])]

    def all_pieces(self) -> list[Piece]:
        return [p for ps in self.fields.values() for p in ps]

    @property
    def coarse(self) -> bool:
        ps = self.all_pieces()
        return bool(ps) and any(not p.precise for p in ps)


@dataclass
class SemanticResult:
    items: list[dict] = field(default_factory=list)
    geos: list[ItemGeo] = field(default_factory=list)
    matched: dict[int, int] = field(default_factory=dict)       # region.idx -> item index
    unmatched: list[int] = field(default_factory=list)          # item indices without a region
    notes: list[str] = field(default_factory=list)

    def item_for_region(self, ridx: int) -> Optional[dict]:
        i = self.matched.get(ridx)
        return self.items[i] if i is not None else None


def _spans_of(leaf) -> list[tuple[int, int]]:
    """Accept both documented shapes: spans=[[s,e],...] and ranges=[{start,end},...]."""
    out: list[tuple[int, int]] = []
    if not isinstance(leaf, dict):
        return out
    for s in leaf.get("spans") or []:
        if isinstance(s, (list, tuple)) and len(s) == 2:
            out.append((int(s[0]), int(s[1])))
    for r in leaf.get("ranges") or []:
        if isinstance(r, dict) and "start" in r and "end" in r:
            out.append((int(r["start"]), int(r["end"])))
    return out


def narrow_option_span(md: str, s: int, e: int, label: str, value) -> tuple[int, int]:
    """The live Extract returns the WHOLE shared line ("a) X  b) Y  c) Z  d) W") as the range of every option.
    Narrow it to this option's own text so ground_span can slice the line per option. Label-anchored match
    first ("b) Y", "(b) Y", "B. Y"), then a unique plain match; otherwise the range is left as it was."""
    v = str(value or "").strip()
    if not v or e - s <= len(v) + 4:
        return s, e
    seg = md[s:e]
    m = re.search(r"[\(\[]?[" + label.lower() + label.upper() + r"][\)\].]\s*(" + re.escape(v) + ")", seg)
    if m:
        return s + m.start(1), s + m.end(1)
    if seg.count(v) == 1:
        i = seg.index(v)
        return s + i, s + i + len(v)
    return s, e


def ground_span(br: BatchResult, s: int, e: int) -> list[Piece]:
    """Pixel pieces covering markdown [s,e). Most specific geometry wins."""
    hits = [b for b in br.blocks if not b.watermark and b.rng[0] < e and b.rng[1] > s and b.type != "figure"]
    if not hits:
        # Parse sometimes swallows a whole question (text + diagram) into one figure; its box is still where the
        # question is (coarse), which beats having no geometry at all
        figs = [b for b in br.blocks if not b.watermark and b.type == "figure" and b.rng[0] < e and b.rng[1] > s]
        return [Piece(b.page, b.box, False, False, line=None) for b in figs]
    child_parents = {b.parent for b in hits if b.parent}
    pieces: list[Piece] = []
    for b in hits:
        if b.id in child_parents:                       # a more specific child overlaps -> skip the container
            continue
        atoms = [a for a in b.atomic if a.get("rng") and a["rng"][0] is not None
                 and a["rng"][0] < e and a["rng"][1] > s]
        if atoms:
            for a in atoms:
                a0, a1 = a["rng"]
                box = a["box"]
                n = max(1, a1 - a0)
                lo, hi = max(s, a0), min(e, a1)
                sliced = False
                if (hi - lo) < 0.85 * n and box.w > 2 * box.h:      # several fields share this line
                    f0, f1 = (lo - a0) / n, (hi - a0) / n
                    box = Box(box.x0 + f0 * box.w, box.y0, box.x0 + f1 * box.w, box.y1)
                    sliced = True
                pieces.append(Piece(b.page, box, True, sliced, line=a["box"]))
        else:
            coarse = b.type == "table"
            pieces.append(Piece(b.page, b.box, not coarse, False, line=None if coarse else b.box))
    return pieces


# ----------------------------------------------------------------------------------------------
# Fallback grounding: align the extracted text with the parsed lines in reading order.
# Used for items whose Extract spans did not ground (spans are null when Extract rewrote the text).
# ----------------------------------------------------------------------------------------------
_STRIP = re.compile(r"<[^>]+>|\\[a-zA-Z]+|[$\{\}_^\\]")


def _norm(t) -> str:
    return re.sub(r"[^a-z0-9]+", "", _STRIP.sub("", str(t or "").lower()))


@dataclass
class _Line:
    page: int
    box: Box
    norm: str
    precise: bool


def _lines(br: BatchResult) -> list[_Line]:
    parents = {b.parent for b in br.blocks if b.parent}
    out: list[_Line] = []
    for b in br.blocks:
        if b.watermark or b.type == "figure" or b.id in parents:
            continue
        atoms = [a for a in b.atomic if a.get("rng") and a["rng"][0] is not None]
        if atoms:
            for a in atoms:
                out.append(_Line(b.page, a["box"], _norm(br.markdown[a["rng"][0]:a["rng"][1]]), True))
        elif b.md:
            out.append(_Line(b.page, b.box, _norm(b.md), b.type != "table"))
    return [ln for ln in out if ln.norm]


def _first(lines: list[_Line], start: int, key: str, stop: Optional[int] = None) -> int:
    for i in range(start, min(len(lines), stop if stop else len(lines))):
        if key in lines[i].norm:
            return i
    return -1


def locate_by_text(lines: list[_Line], item: dict, start: int) -> tuple[Optional[ItemGeo], int]:
    stem = _norm(item.get("stem"))
    if len(stem) < 8:
        return None, start
    i = -1
    for n in (28, 18, 12):
        key = stem[:min(n, len(stem))]
        i = _first(lines, start, key)
        if i < 0:
            i = _first(lines, 0, key)
        if i >= 0:
            break
    if i < 0:
        return None, start
    geo = ItemGeo(method="text")
    pieces = [Piece(lines[i].page, lines[i].box, lines[i].precise, False, line=lines[i].box)]
    acc, j, end = len(lines[i].norm), i + 1, i
    while j < len(lines) and j < i + 12 and acc < 0.9 * len(stem) and lines[j].norm in stem:
        pieces.append(Piece(lines[j].page, lines[j].box, lines[j].precise, False, line=lines[j].box))
        acc += len(lines[j].norm)
        end = j
        j += 1
    geo.fields["stem"] = pieces
    pos = end + 1
    for L in "abcd":
        t = _norm(item.get(f"option_{L}"))
        if not t:
            continue
        k = -1
        for i2 in range(pos, min(len(lines), pos + 40)):          # exact (label + text) first, then containment
            if lines[i2].norm in (t, "a" + t, "b" + t, "c" + t, "d" + t):
                k = i2
                break
        if k < 0:
            k = _first(lines, pos, t, pos + 40)
        if k < 0:
            continue
        ln = lines[k]
        box, sliced = ln.box, False
        if len(t) < 0.8 * len(ln.norm) and ln.box.w > 2 * ln.box.h:    # several options share this line
            idx = max(0, ln.norm.find(t))
            f0, f1 = idx / len(ln.norm), (idx + len(t)) / len(ln.norm)
            box = Box(ln.box.x0 + f0 * ln.box.w, ln.box.y0, ln.box.x0 + f1 * ln.box.w, ln.box.y1)
            sliced = True
        geo.fields[f"option_{L}"] = [Piece(ln.page, box, ln.precise, sliced, line=ln.box)]
        pos = k if sliced else k + 1
        end = max(end, k)
    pages = [p.page for p in geo.all_pieces()]
    geo.page = max(set(pages), key=pages.count)
    return geo, end + 1


def run_semantics(client, br: BatchResult, regions: list[Region], sizes: dict[int, tuple[int, int]]) -> SemanticResult:
    res = SemanticResult()
    if len((br.markdown or "").strip()) < 20:
        return res
    try:
        ext = client.extract(br.markdown, QUESTION_SCHEMA)
    except Exception as e:
        res.notes.append(f"extract_failed:{str(e)[:100]}")
        log.error("Extract failed: %s", e)
        return res
    if ext.get("schema_violation_error"):
        res.notes.append("schema_violation:" + str(ext["schema_violation_error"])[:100])
    items = (ext.get("extraction") or {}).get("questions") or []
    meta_items = (ext.get("extraction_metadata") or {}).get("questions") or []
    res.items = [it for it in items if isinstance(it, dict)]
    log.info("Extract: %d questions in batch %d (pages %s-%s)", len(res.items), br.batch_id,
             br.page_nums[0], br.page_nums[-1])

    keys = key_table_ids(br.blocks)
    key_boxes = [(b.page, b.box) for b in br.blocks if b.id in keys]

    def in_key_table(pc: Piece) -> bool:
        return any(pg == pc.page and bx.contains_point(pc.box.cx, pc.box.cy) for pg, bx in key_boxes)

    for i, item in enumerate(res.items):
        meta = meta_items[i] if i < len(meta_items) and isinstance(meta_items[i], dict) else {}
        geo = ItemGeo()
        for name, leaf in meta.items():
            if name == "correct_option":
                continue
            pcs: list[Piece] = []
            for s, e in _spans_of(leaf):
                if name.startswith("option_"):
                    s, e = narrow_option_span(br.markdown, s, e, name[-1], item.get(name))
                pcs.extend(ground_span(br, s, e))
            pcs = [pc for pc in pcs if not in_key_table(pc)]      # "1" also matches the answer-key grid
            if pcs:
                geo.fields[name] = pcs
        # the page is where the question's TEXT is: the stem decides, numbers/options only break ties
        stem_pages = [p.page for p in geo.fields.get("stem", [])]
        pages = stem_pages or [p.page for p in geo.all_pieces()]
        geo.page = max(set(pages), key=pages.count) if pages else None
        res.geos.append(geo)

    # items whose spans did not ground: align by text
    lines: Optional[list[_Line]] = None
    cursor = 0
    n_text = 0
    for i, geo in enumerate(res.geos):
        if geo.page is not None:
            continue
        if lines is None:
            lines = _lines(br)
        g, cursor = locate_by_text(lines, res.items[i], cursor)
        if g is not None:
            res.geos[i] = g
            n_text += 1
    if n_text:
        res.notes.append(f"grounded_by_text_match:{n_text}")
    # pages for items with no geometry at all: carry the neighbouring question's page (reading order)
    last = None
    for geo in res.geos:
        if geo.page is not None:
            last = geo.page
        elif last is not None:
            geo.page, geo.page_inferred = last, True
    nxt = None
    for geo in reversed(res.geos):
        if geo.page is not None and not geo.page_inferred:
            nxt = geo.page
        elif geo.page is None and nxt is not None:
            geo.page, geo.page_inferred = nxt, True
    for geo in res.geos:
        if geo.page is None:
            geo.page, geo.page_inferred = br.page_nums[0], True

    # bind items to anchor-based regions. The printed question number is the primary key when exactly one
    # region carries it (PDF-layer numbers are exact; OCR boxes can sit a line off and mis-vote on dense pages);
    # otherwise votes of grounded piece centres decide.
    taken: dict[int, tuple[int, int]] = {}
    by_num: dict[str, list[Region]] = {}
    for r in regions:
        if r.number:
            by_num.setdefault(str(r.number), []).append(r)
    for i, geo in enumerate(res.geos):
        num = "".join(ch for ch in str(res.items[i].get("number", "")) if ch.isdigit())
        same = by_num.get(num.lstrip("0") or num, []) if num else []
        # the number only decides within the page the item's own text was found on: numbering restarts per
        # chapter and answer-key tables repeat every number, so a page-less match would bind across pages
        if geo.page is not None and not geo.page_inferred:
            same = [r for r in same if r.page == geo.page]
        elif not (len(same) == 1 and same[0].anchor is not None and same[0].anchor.uid.startswith("pdf")):
            same = []                           # page unknown: only an exact PDF-text-layer number is unambiguous
        if len(same) == 1 and same[0].idx not in taken:
            taken[same[0].idx] = (10 ** 6, i)              # a number match outranks any vote count
            continue
        votes: dict[int, int] = {}
        for pc in geo.all_pieces():
            for r in regions:
                if r.page == pc.page and r.box.contains_point(pc.box.cx, pc.box.cy):
                    votes[r.idx] = votes.get(r.idx, 0) + 1
                    break
        if votes:
            ridx = max(votes, key=votes.get)
            if ridx not in taken or votes[ridx] > taken[ridx][0]:
                if ridx in taken:
                    res.unmatched.append(taken[ridx][1])
                taken[ridx] = (votes[ridx], i)
                continue
        res.unmatched.append(i)
    res.matched = {r: i for r, (_, i) in taken.items()}
    return res
