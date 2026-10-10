"""Cost cut B: born-digital pages are READ from the PDF itself - no Landing AI Parse call.

The PDF text layer already holds every word, number and option label with exact positions (pdf_layer.py). This
module lays those lines out as markdown in reading order (columns, then rows; labels rendered as "A)") and records
each line's [start, end) range, so the result is a BatchResult exactly like one coming from Parse: the local
splitter, the geometric verification and the per-page Extract fallback all work unchanged on it.
"""
from __future__ import annotations

import re
from pathlib import Path

import pymupdf

from .parse_stage import BatchResult
from .pdf_layer import page_blocks
from ..utils.geometry import Box
from .structure import Block

_BARE_LABEL = re.compile(r"^\(?([A-Da-d])[).]?$")
_QSTART = re.compile(r"^\s*\(?\d{1,3}\s*[.)]")           # a question number at the start of a line
_LEAD_LABEL = re.compile(r"^([A-D])\s+(?=\S)")
_LEAD_WIDE = re.compile(r"^([A-D])\s{2,}(?=\S)")          # boxed label printed with wide spacing: "A   8 m/s"
# Symbol-font glyphs arrive as private-use code points (U+F000 + Symbol code): map the common ones
_SYMBOL = {0xF0B4: "×", 0xF02D: "−", 0xF03D: "=", 0xF0AE: "→", 0xF0AC: "←", 0xF0B0: "°", 0xF0B1: "±",
           0xF0A3: "≤", 0xF0B3: "≥", 0xF0B8: "÷", 0xF0D6: "√", 0xF070: "π", 0xF071: "θ", 0xF061: "α",
           0xF062: "β", 0xF067: "γ", 0xF044: "Δ", 0xF06C: "λ", 0xF06D: "μ", 0xF077: "ω", 0xF057: "Ω",
           0xF0BB: "≈", 0xF0B9: "≠", 0xF0A5: "∞", 0xF0B6: "∂", 0xF0E5: "∑", 0xF0F2: "∫", 0xF0A2: "′",
           0xF0AB: "↔", 0xF0DE: "⇒", 0xF0DB: "⇔", 0xF0D7: "·", 0xF0BE: "—", 0xF02B: "+", 0xF028: "(",
           0xF029: ")", 0xF05B: "[", 0xF05D: "]"}


# large-bracket pieces of the Symbol font (top / extension / bottom of a tall bracket): keep ONE bracket
_SYMBOL.update({0xF8EB: "(", 0xF8EC: "", 0xF8ED: "", 0xF8F6: ")", 0xF8F7: "", 0xF8F8: "",
                0xF8EE: "[", 0xF8EF: "", 0xF8F0: "", 0xF8F9: "]", 0xF8FA: "", 0xF8FB: "",
                0xF8F1: "{", 0xF8F2: "", 0xF8F3: "", 0xF8FC: "}", 0xF8FD: "", 0xF8FE: ""})


def clean_symbols(t: str) -> str:
    return "".join(_SYMBOL.get(ord(c), "" if 0xF000 <= ord(c) <= 0xF8FF else c) for c in t)


def _columns(blocks: list[Block], W: int) -> list[list[Block]]:
    """Two-column pages: left column first, then right; full-width lines go with the column they start in."""
    nums = [b for b in blocks if _QSTART.match(b.plain)]
    crossing = [b for b in blocks if b.box.x0 < 0.5 * W < b.box.x1]          # text running over the gutter
    # two columns: questions START in both halves and almost nothing runs across the middle of the page
    # (options spread over the full width of a one-column page must not be split)
    two = any(b.box.x1 <= 0.55 * W for b in nums) and any(b.box.x0 >= 0.45 * W for b in nums) \
        and len(crossing) <= 0.1 * len(blocks)
    if not two:
        return [blocks]
    cols: list[list[Block]] = [[], []]
    for b in blocks:
        cols[0 if b.box.cx < 0.5 * W and b.box.x0 < 0.48 * W else 1].append(b)
    return cols


def _rows(blocks: list[Block]) -> list[list[Block]]:
    """Group lines that overlap vertically (a label and its value, a superscript and its base) into rows."""
    rows: list[list[Block]] = []
    for b in sorted(blocks, key=lambda b: b.box.cy):
        if rows:
            r = rows[-1]
            top, bot = min(x.box.y0 for x in r), max(x.box.y1 for x in r)
            ov = min(bot, b.box.y1) - max(top, b.box.y0)
            if ov > 0.4 * min(b.box.h, bot - top):
                r.append(b)
                continue
        rows.append([b])
    return [sorted(r, key=lambda b: b.box.x0) for r in rows]


def _render_row(row: list[Block], line_h: float) -> list[tuple[Block, str]]:
    """Text of one row as (block, rendered text) in reading order, with
    stacked fractions -> $\frac{num}{den}$, raised/lowered small pieces -> <sup>/<sub>, labels -> "A)"."""
    out: list[tuple[Block, str]] = []
    used: set[int] = set()
    texts = [b for b in row if b.type == "text"]
    # only boxed bare letters ("A", "B") make a leading "A 6.022" a label; with "a)"-style labels a leading
    # capital is option text ("a) A and B")
    row_has_label = any(re.fullmatch(r"[A-D]", b.plain.strip()) for b in texts)
    base = max((b.box.h for b in texts), default=line_h)
    for i, b in enumerate(row):
        if id(b) in used:
            continue
        if b.type == "figure":
            out.append((b, "<figure></figure>"))
            continue
        t = b.plain.strip()
        # stacked fraction: a short piece directly above another short piece, overlapping horizontally
        den = next((c for c in row[i + 1:] + row[:i] if c is not b and c.type == "text" and id(c) not in used
                    and c.box.cy > b.box.cy + 0.35 * min(b.box.h, c.box.h)
                    and min(b.box.x1, c.box.x1) - max(b.box.x0, c.box.x0) > 0.5 * min(b.box.w, c.box.w)
                    and len(t) <= 24 and len(c.plain.strip()) <= 24 and not _BARE_LABEL.match(t)), None)
        if den is not None and not any(ch.isalpha() and ch.isupper() and len(t) == 1 for ch in t[:1]):
            used.add(id(den))
            lab = _LEAD_WIDE.match(t) or (row_has_label and _LEAD_LABEL.match(t))
            prefix = ""
            if lab:                                    # "A   m + M" over "m": the label, then the fraction
                prefix, t = f"{lab.group(1)}) ", t[lab.end():].strip()
            out.append((b, prefix + "$\\frac{" + t + "}{"))
            out.append((den, den.plain.strip() + "}$"))
            continue
        m = _BARE_LABEL.match(t)
        if m:
            t = f"{m.group(1)})"                       # boxed "A" / "(a)" -> one label form the splitter knows
        elif _LEAD_WIDE.match(t) or (row_has_label and _LEAD_LABEL.match(t) and not re.match(r"^[A-D]\s+[a-z]", t)):
            t = re.sub(r"^([A-D])\s+", lambda mm: f"{mm.group(1)}) ", t, count=1)    # "A   8 m/s", "A 6.022" by "B)"
        elif out and b.box.h < 0.75 * base and len(t) <= 6:
            prev = out[-1][0]
            if prev.type == "text" and b.box.x0 >= prev.box.x1 - 2:
                if b.box.cy < prev.box.cy - 0.15 * prev.box.h:
                    t = f"<sup>{t}</sup>"
                elif b.box.cy > prev.box.cy + 0.15 * prev.box.h:
                    t = f"<sub>{t}</sub>"
        out.append((b, t))
    return out


def page_markdown(blocks: list[Block], W: int, start: int) -> str:
    """Render the text (and figure positions) of one page; sets every block's `rng` (offsets into the batch md)."""
    parts: list[str] = []
    pos = start
    texts = [b for b in blocks if (b.type == "text" and b.plain.strip()) or b.type == "figure"]
    for b in texts:
        if b.type == "text":
            b.plain = b.md = clean_symbols(b.plain)
    hs = sorted(b.box.h for b in texts if b.type == "text")
    line_h = hs[len(hs) // 2] if hs else 40.0
    for col in _columns(texts, W):
        for row in _rows(col):
            line = ""
            for b, t in _render_row(row, line_h):
                if line and not t.startswith(("<sup>", "<sub>")) and not line.endswith("{"):
                    line += " "
                if "$\\frac{" in t and t.endswith("}{"):         # numerator: its range is its own text only
                    b.rng = (pos + len(line) + t.index("$\\frac{") + len("$\\frac{"), pos + len(line) + len(t) - 2)
                elif t.endswith("}$") and line.endswith("{"):
                    b.rng = (pos + len(line), pos + len(line) + len(t) - 2)
                else:
                    b.rng = (pos + len(line), pos + len(line) + len(t))
                line += t
            parts.append(line)
            pos += len(line) + 1
    return "\n".join(parts) + "\n"


def _strokes(page: pymupdf.Page, W: int, H: int) -> list[Box]:
    sx, sy = W / page.rect.width, H / page.rect.height
    out = []
    for d in page.get_drawings():
        for it in d.get("items", []):
            if it[0] == "l":
                a, b = it[1], it[2]
                out.append(Box(min(a.x, b.x) * sx, min(a.y, b.y) * sy, max(a.x, b.x) * sx, max(a.y, b.y) * sy))
    return out


def _box_side(s: Box, strokes: list[Box], lh: float) -> bool:
    """Side of a small drawn rectangle (box around an option letter, a cell, a banner): both of its ends meet
    perpendicular strokes. Math marks (radical bars, overlines, arrows) are open shapes."""
    tol = 2.5
    if s.w <= tol and s.h > tol:                       # vertical side: horizontal strokes at top and bottom ends
        ends = [(s.cx, s.y0), (s.cx, s.y1)]
        perp = [o for o in strokes if o.h <= tol and o.w > tol]
    elif s.h <= tol and s.w > tol:                     # horizontal side: vertical strokes at left and right ends
        ends = [(s.x0, s.cy), (s.x1, s.cy)]
        perp = [o for o in strokes if o.w <= tol and o.h > tol]
    else:
        return False
    return all(any(o.pad(tol).contains_point(x, y) for o in perp) for x, y in ends)


def math_strokes(page: pymupdf.Page, blocks: list[Block], W: int, H: int) -> tuple[bool, int]:
    """Vector strokes drawn INTO the text (radicals, overlines, arrows): the text layer alone would lose them.
    Square roots are recognised and rewritten as $\\sqrt{...}$; any other stroke touching text makes the page
    unsafe (-> the page is read by Parse instead). Returns (safe, number of radicals rewritten)."""
    texts = [b for b in blocks if b.type == "text" and b.plain.strip()]
    if not texts:
        return True, 0
    hs = sorted(b.box.h for b in texts)
    lh = hs[len(hs) // 2]
    frames = [b.box.pad(0.3 * lh) for b in blocks if b.type in ("figure", "table")]
    labels = [b.box.pad(0.6 * lh) for b in texts if _BARE_LABEL.match(b.plain.strip()) or _LEAD_WIDE.match(b.plain.strip())]
    allst = _strokes(page, W, H)
    strokes = [s for s in allst
               if max(s.w, s.h) < 0.25 * W and max(s.w, s.h) > 0.15 * lh
               and not any(f.contains_point(s.cx, s.cy) for f in frames)
               and not _box_side(s, allst, lh)]
    horiz = [s for s in strokes if s.h <= 2 and s.w > 0.3 * lh]
    diag = [s for s in strokes if s.h > 2 and s.w > 2]
    radicals = 0
    done: set[int] = set()
    for i, s in enumerate(strokes):
        if i in done:
            continue
        near = [t for t in texts if t.box.pad(0.3 * lh).intersect(s.pad(1)) is not None]
        if not near:
            continue                                   # decoration away from any text
        if any(lb.contains_point(s.cx, s.cy) for lb in labels):
            continue                                   # box drawn around an option letter
        if s in horiz:
            above = [t for t in texts if t.box.y1 <= s.y0 + 0.3 * lh and s.y0 - t.box.y1 < 0.6 * lh
                     and min(t.box.x1, s.x1) - max(t.box.x0, s.x0) > 0.3 * min(t.box.w, s.w)]
            below = [t for t in texts if t.box.y0 >= s.y1 - 0.3 * lh and t.box.y0 - s.y1 < 0.6 * lh
                     and min(t.box.x1, s.x1) - max(t.box.x0, s.x0) > 0.3 * min(t.box.w, s.w)]
            if above and below:
                continue                               # fraction bar (rendered as \\frac)
            tick = [d for d in diag if abs(d.x1 - s.x0) < 0.25 * lh and abs(d.y0 - s.y0) < 0.25 * lh]
            under = [t for t in texts if t.box.y0 >= s.y0 - 0.2 * lh and t.box.y0 - s.y0 < 0.8 * lh
                     and t.box.x0 >= s.x0 - 0.3 * lh and t.box.x1 <= s.x1 + 0.3 * lh]
            if tick and len(under) == 1 and not under[0].plain.startswith("$\\sqrt"):
                t = under[0]
                t.plain = t.md = "$\\sqrt{" + t.plain.strip() + "}$"
                radicals += 1
                for d in tick:
                    done.add(strokes.index(d)) if d in strokes else None
                continue
            if tick:
                return False, radicals                 # radical over only part of a line: cannot rewrite safely
            if not below and above:
                continue                               # underline
            return False, radicals                     # overline / bar over text
        if any(s.intersect(h.pad(0.25 * lh)) is not None and abs(s.x1 - h.x0) < 0.3 * lh for h in horiz):
            continue                                   # radical tick, handled with its bar
        return False, radicals                         # arrow, hat or other drawn mark touching text
    return True, radicals


def build_pdf_batch(pdf_path: Path, page_nums: list[int], sizes: dict[int, tuple[int, int]],
                    batch_id: int) -> tuple[BatchResult, list[int]]:
    """PDF-text batch of the SAFE pages, and the pages that must be read by Parse instead."""
    md = ""
    blocks: list[Block] = []
    safe: list[int] = []
    unsafe: list[int] = []
    with pymupdf.open(str(pdf_path)) as doc:
        for p in page_nums:
            W, H = sizes[p]
            pb = page_blocks(doc[p - 1], W, H, p, batch_id)
            for b in pb:
                if b.type == "text":
                    b.plain = b.md = clean_symbols(b.plain)
            ok, _ = math_strokes(doc[p - 1], pb, W, H)
            if not ok:
                unsafe.append(p)
                continue
            if safe:
                md += "\n<!-- PAGE BREAK -->\n\n"
            md += page_markdown(pb, W, len(md))
            blocks.extend(pb)
            safe.append(p)
    br = BatchResult(batch_id=batch_id, page_nums=safe, markdown=md,
                     structure={"children": []}, metadata={"source": "pdf_text_layer"}, blocks=blocks)
    return br, unsafe
