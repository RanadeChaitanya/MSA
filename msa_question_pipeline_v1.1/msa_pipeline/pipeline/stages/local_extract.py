"""Local replacement for Landing AI Extract (cost cut): split Parse markdown into questions without an API call.

Returns exactly the shape of an Extract response, so grounding/layout/visuals are unchanged:
    {"extraction": {"questions": [{number, stem, option_a..option_d, correct_option}]},
     "extraction_metadata": {"questions": [{field: {"value": v, "ranges": [{start, end}]}}]}}
Ranges are [start, end) offsets into the SAME markdown, so every value is grounded exactly like Extract's.

The markdown is read as a stream of units: text lines, and the cells of HTML tables (MSA prints each question as
a table). Events: a question number at the start of a unit, an option label (A) / (a) / [ ] A / a table cell "A"),
figures (kept out of the text). `validate()` is deliberately strict: when anything is doubtful the caller falls
back to the paid Extract call, so this path can only remove cost, never silently lower quality.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional

_TABLE = re.compile(r"<table\b.*?</table>", re.S | re.I)
_CELL = re.compile(r"<t[dh]\b[^>]*>(.*?)</t[dh]>", re.S | re.I)
_FIG = re.compile(r"<figure\b.*?</figure>", re.S | re.I)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
NOISE = re.compile(r"^\s*(?:impulse\s*batch|matrix\s*science|msa\s*$|rabhchemia|active\s*site|page\s*\|?\s*\d+\s*$|"
                   r"article\s+deleted|\*\*?l\.?\s*no\.?|l\.?\s*no\.?\s*\d|mht-cet\s*20\d\d\s*$|pyq\s*$|"
                   r"q\d{3,6}\s*$|\[\d+\]\s*$|●)", re.I)
HEADER = re.compile(r"^\s*\d{1,2}\s*[-–]\s*[A-Z][A-Za-z ,&'()/-]{2,}$")          # "1-ROTATIONAL DYNAMICS"
QNUM = re.compile(r"^\s*(?:\*\*)?\(?(\d{1,3})(?:\*\*)?(?:\s*[.)](?:\*\*)?(?=\s|$|[^\d\s.,])|(?=\s|$))")
LABEL = re.compile(r"(?:(?<=[\s,;])|^)(?:\[\s?\]\s*)?\(?([A-Da-d])\)?[).](?=\s|$)|^\s*\[\s?\]\s*([A-D])(?=\s)")
CORRECT = re.compile(r"correct\s*option\s*[:.\-]?\s*\(?([A-D])\b", re.I)
ORDER = "ABCD"
_KEY = re.compile(r"answer\s*key|q\s*\.\s*no\b|\bans\b", re.I)


def _style(t: str) -> str:
    t = t.strip().lstrip("*")
    if t.startswith("("):
        return "paren"
    m = re.match(r"^\d{1,3}\s*([.)])", t)
    return {".": "dot", ")": "bracket"}[m.group(1)] if m else "bare"


@dataclass
class _Unit:
    text: str
    start: int            # offset of text[0] in the markdown
    cell: bool = False
    figure: bool = False
    page_break: bool = False


@dataclass
class _Q:
    number: Optional[int]
    num_rng: Optional[tuple[int, int]]
    stem: list[tuple[str, int, int]] = field(default_factory=list)
    opts: dict[str, list[tuple[str, int, int]]] = field(default_factory=dict)
    opt_fig: dict[str, bool] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    stem_fig: bool = False
    cur: Optional[str] = None
    correct: Optional[str] = None
    tail: bool = False         # last question before a page break / end: may continue on the next page


def _units(md: str) -> list[_Unit]:
    out: list[_Unit] = []
    spans = []
    for rx, kind in ((_TABLE, "table"), (_FIG, "figure"), (_COMMENT, "comment")):
        spans += [(m.start(), m.end(), kind, m) for m in rx.finditer(md)]
    spans.sort(key=lambda t: t[0])
    pos = 0

    def lines(a: int, b: int):
        i = a
        for ln in md[a:b].split("\n"):
            if ln.strip():
                lead = len(ln) - len(ln.lstrip())
                out.append(_Unit(ln.strip(), i + lead))
            i += len(ln) + 1

    for s, e, kind, m in spans:
        if s < pos:
            continue
        lines(pos, s)
        if kind == "figure":
            out.append(_Unit("", s, figure=True))
        elif kind == "comment":
            if "PAGE BREAK" in m.group(0).upper():
                out.append(_Unit("", s, page_break=True))
        elif _KEY.search(re.sub(r"<[^>]+>", " ", m.group(0))):
            pass                                   # answer-key grid: numbers there are not questions
        else:
            for c in _CELL.finditer(m.group(0)):
                raw = c.group(1)
                txt = re.sub(r"<br\s*/?>", " ", raw).strip()
                if txt:
                    off = s + c.start(1) + (len(raw) - len(raw.lstrip()))
                    out.append(_Unit(txt, off, cell=True))
        pos = e
    lines(pos, len(md))
    return out


def _labels(u: _Unit) -> list[tuple[str, int, int]]:
    """(letter, label start, label end) inside a unit; a table cell that IS a letter is a label."""
    if u.cell:
        return [(u.text.upper(), 0, len(u.text))] if re.fullmatch(r"\(?[A-Da-d][).]?", u.text) else []
    return [((m.group(1) or m.group(2)), m.start(), m.end()) for m in LABEL.finditer(u.text)]


def split(md: str) -> list[_Q]:
    units = _units(md)
    cands = [u for u in units if u.text and QNUM.match(u.text) and not _labels(u)]
    numbered = len(cands) >= 2
    styles = [_style(u.text) for u in cands]
    marked = [x for x in styles if x != "bare"]
    dominant = max(set(marked), key=marked.count) if len(marked) >= 3 else None
    qs: list[_Q] = []
    last: Optional[int] = None
    after_break = True
    for u in units:
        if u.page_break:
            after_break = True
            if qs:
                qs[-1].tail = True
            continue
        if u.figure:
            if qs:
                q = qs[-1]
                if q.cur:
                    q.opt_fig[q.cur] = True
                else:
                    q.stem_fig = True
            continue
        t = u.text
        if not t or NOISE.match(t) or (HEADER.match(t) and not u.cell):
            continue
        m = QNUM.match(t)
        if numbered and m and not (u.cell and re.search(r"\d\s+\d", t)):
            n = int(m.group(1))
            ok = last is None or n == last + 1 or n == 1 or (after_break and n > last)
            if dominant and _style(t) != dominant:
                ok = False                         # "(1)" book: a bare "1" is a chapter badge, not a question
            if ok and (not u.cell or re.fullmatch(r"\(?\d{1,3}[.)]?", t)):
                qs.append(_Q(n, (u.start + m.start(1), u.start + m.end(1))))
                last, after_break = n, False
                t_rest = t[m.end():]
                if not t_rest.strip():
                    continue
                u = _Unit(t_rest, u.start + m.end(), u.cell)
                t = u.text
        if not qs:
            if numbered:
                continue
            qs.append(_Q(None, None))
        q = qs[-1]
        q.tail = False
        labs = _labels(u)
        if not numbered and labs and labs[0][0].upper() == "A" and q.opts and len(q.opts) >= 4:
            q = _Q(None, None)                     # unnumbered layout: a new A after a full set = next question
            qs.append(q)
        cm = CORRECT.search(t)
        if cm:
            q.correct = cm.group(1).upper()
            continue
        pos = 0
        for L, a, b in labs:
            if a > pos:
                _add(q, t[pos:a], u.start + pos)
            q.cur = L
            q.order.append(L)
            q.opts.setdefault(L, [])
            pos = b
        if pos < len(t):
            _add(q, t[pos:], u.start + pos)
    if qs:
        qs[-1].tail = True
    return qs


def _add(q: _Q, text: str, start: int) -> None:
    s = text.strip()
    if not s:
        return
    off = start + (len(text) - len(text.lstrip()))
    piece = (s, off, off + len(s))
    if q.cur is None:
        q.stem.append(piece)
    else:
        q.opts[q.cur].append(piece)


def _choose(q: _Q) -> dict[str, list[tuple[str, int, int]]]:
    """Options = the label run in the question's case; lowercase a)-d) inside an uppercase question stay stem."""
    upper = [L for L in q.order if L.isupper()]
    use_upper = len(set(upper)) >= 2
    out: dict[str, list] = {}
    for L in ORDER:
        key = L if use_upper else L.lower()
        if key in q.opts:
            out[L] = q.opts[key]
    prev_key = None
    for L in ORDER:
        if not out.get(L) and prev_key is not None and len(out.get(prev_key) or []) >= 2:
            out[L] = [out[prev_key].pop()]          # neighbour holds two values: the second is this option's
        if out.get(L):
            prev_key = L
    if use_upper:                                   # lowercase sub-items belong to the stem, in order
        for L in "abcd":
            if L in q.opts:
                q.stem.extend(q.opts[L])
        q.stem.sort(key=lambda p: p[1])
    return out


def local_extract(md: str) -> dict:
    items, meta = [], []
    for q in split(md):
        opts = _choose(q)
        it = {"number": str(q.number) if q.number is not None else None,
              "stem": " ".join(p[0] for p in q.stem), "correct_option": q.correct}
        mt = {"number": {"value": it["number"], "ranges": [{"start": q.num_rng[0], "end": q.num_rng[1]}]
                         if q.num_rng else None},
              "stem": {"value": it["stem"], "ranges": [{"start": a, "end": b} for _, a, b in q.stem] or None}}
        for L in ORDER:
            ps = opts.get(L)
            k = f"option_{L.lower()}"
            it[k] = " ".join(p[0] for p in ps) if ps else (None if L not in opts else "")
            mt[k] = {"value": it[k], "ranges": [{"start": a, "end": b} for _, a, b in ps] if ps else None}
        it["_figs"] = {L: bool(q.opt_fig.get(L) or q.opt_fig.get(L.lower())) for L in ORDER}
        it["_tail"] = q.tail
        items.append(it)
        meta.append(mt)
    return {"extraction": {"questions": items}, "extraction_metadata": {"questions": meta}, "local": True}


def validate(ext: dict, region_numbers: set[int]) -> Optional[str]:
    """None if the local split can be trusted, else the reason (-> paid Extract fallback)."""
    qs = ext["extraction"]["questions"]
    if not qs:
        return "no questions"
    nums = [int(q["number"]) for q in qs if q.get("number")]
    missing = sorted(region_numbers - set(nums))
    if missing:
        return f"region numbers not found: {missing[:5]}"
    if len(nums) != len(set(nums)):
        return "duplicate numbers"
    for q in qs:
        tag = q.get("number") or "?"
        if q.get("_tail") and all(q.get(f"option_{L.lower()}") is None for L in ORDER):
            continue                     # continues on the next page (options there): Extract can't do better
        if not (q.get("stem") or "").strip():
            return f"q{tag}: empty stem"
        for L in ORDER:
            v = q.get(f"option_{L.lower()}")
            if v is None:
                if q.get("_tail"):
                    continue             # a page-end question cut after some options
                return f"q{tag}: option {L} missing"
            if not v.strip() and not q["_figs"][L]:
                return f"q{tag}: option {L} empty without a figure"
    return None
