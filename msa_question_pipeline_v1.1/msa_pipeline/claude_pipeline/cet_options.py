"""Option splitter for OCR question text (code only). split_options(text) -> dict(stem, options{A..D}, status, markers)
status: ok (four non-empty options, markers in order) | bare (some markers were bare letters, recovered) | table (display/array cells in row order)
        | numeric ((1)..(4) style) | incomplete (markers found but an option is empty/missing: OCR lost content, route to review) | none (no markers)
"""
import re

_OPEN = r"(?:\\(?:mathrm|mathbf|bf|rm|textbf)\s*\{\s*|\{\\(?:bf|rm)\s+|\{\{\\bf\s+)?"
MARK = re.compile(r"\([\s~]*" + _OPEN + r"([A-D])\s*\}*[\s~]*\)" + r"|\\mathrm\{\(([A-D])\)\}" + r"|(?:\{\\bf\s+|\{\\rm\s+)([A-D])\}*\s*\)")
MARK_N = re.compile(r"\(\s*([1-4])\s*\)")
JUNK = re.compile(r"(\\\\|\\quad|\\qquad|\\end\{array\}|\\begin\{array\}\{[^}]*\}|\\\]|\\\[|\\displaystyle|(?<!\\)&)")


def _clean(t):
    t = JUNK.sub(" ", t)
    t = re.sub(r"\{\s*\}", " ", t).strip()
    t = re.sub(r"^\\\)", "", t).strip()
    t = re.sub(r"\\\($", "", t).strip()
    if t.count("\\(") > t.count("\\)"):
        t = t.rstrip() + " \\)"
    elif t.count("\\)") > t.count("\\("):
        t = "\\( " + t
    t = re.split(r"(?:^|\s)Time\s*:\s*\d+|Total Marks|MHT[- ]?CET|Space for rough|<table", t)[0]  # page chrome glued on the last option
    d = t.count("{") - t.count("}")
    if d > 0:
        t += "}" * d  # OCR dropped a closing brace (\mathrm{COOH)
    return re.sub(r"\s+", " ", t).strip()


def _greedy(text, marks):
    """last D, then the last C before it, ... (a stem may itself contain '(A)' in match-the-column questions)"""
    pick, lim = {}, len(text) + 1
    for L in "DCBA":
        c = [x for x in marks if x[2] == L and x[0] < lim]
        if not c:
            continue
        pick[L] = c[-1]; lim = c[-1][0]
    return pick


def _bare(text, pick):
    """a lost bracket: 'C It is ...', 'D Trichloro..' between two known markers (never for A: 'A particle...' is a stem word)"""
    order = "ABCD"
    for L in "BCD":
        if L in pick:
            continue
        before = [pick[k] for k in order[:order.index(L)] if k in pick]
        after = [pick[k] for k in order[order.index(L) + 1:] if k in pick]
        if not before:
            continue
        lo = before[-1][1]; hi = after[0][0] if after else len(text)
        c = list(re.finditer(rf"\(\s*{L.lower()}\s*\)", text[lo:hi]))  # lower-case (d)
        if c:
            m = c[-1]; pick[L] = (lo + m.start(), lo + m.end(), L); continue
        c = list(re.finditer(rf"(?<![A-Za-z0-9\\{{_^])\s*({L})(?=\\\(|\$|[A-Z0-9]|\s+(?:\\\(|\$|[A-Z0-9]))", text[lo:hi]))
        if c:
            m = c[-1]; pick[L] = (lo + m.start(), lo + m.end(), L)
    return pick


def _cut(text, pick):
    order = sorted(pick.items(), key=lambda kv: kv[1][0])
    stem = _clean(text[:order[0][1][0]])
    opts = {}
    for i, (L, (s, e, _)) in enumerate(order):
        seg = text[e:order[i + 1][1][0] if i + 1 < len(order) else len(text)]
        dup = MARK.search(seg)  # a repeated later marker inside this option = OCR duplicated lines; cut there
        opts[L] = _clean(seg[:dup.start()] if dup else seg)
    return stem, opts


def _table(text, marks):
    """options laid out as display/array cells: split the LAST display block on markers, \\quad, \\\\, &; four non-empty cells = A,B,C,D in row order.
    Markers found inside the cells must agree with the cell index; markers after the block (a displaced (A)/(C)) are ignored."""
    # try display starts from the earliest (options split over several displays) to the last (a stem equation comes first)
    for i in sorted({m.start() for m in re.finditer(r"\\\[|\\begin\{array\}|\\\(\\begin\{array\}", text)}):
        r = _table_from(text, i)
        if r:
            return r
    return None


def _table_from(text, i):
    body = text[i:]
    end = max(body.rfind("\\end{array}"), body.rfind("\\]"))
    inner = body[:end] if end > 0 else body
    toks, pos = [], 0
    cuts = [(m.start(), m.end(), next((g for g in m.groups() if g), None)) for m in re.finditer(MARK, inner)]
    seq = sorted(cuts + [(m.start(), m.end(), None) for m in re.finditer(r"\\\\|&|\\quad|\\qquad|\\\]|\\\[", inner)])
    lab = None
    for s, e, L in seq:
        toks.append((lab, inner[pos:s])); lab, pos = L, e
    toks.append((lab, inner[pos:]))
    cells = []
    for lab, t in toks:
        t = _clean(re.sub(r"^\\\[|^\\begin\{array\}\{[^}]*\}|^\\\(\\begin\{array\}\{[^}]*\}", "", t.strip()))
        if t and not re.fullmatch(r"[{}\s]*", t):
            cells.append((lab, t))
    # a bare marker split off its own cell ("(A)" then content): attach label to the next cell
    if len(cells) != 4:
        return None
    for k, (lab, _) in enumerate(cells):
        if lab and lab != "ABCD"[k]:
            return None
    return _clean(text[:i]), {"ABCD"[k]: c[1] for k, c in enumerate(cells)}


def split_options(text):
    ms = [(m.start(), m.end(), next(g for g in m.groups() if g)) for m in MARK.finditer(text)]
    pick = _greedy(text, ms)
    status = "ok"
    if len(pick) < 4:
        p2 = _bare(text, dict(pick))
        if len(p2) == 4:
            pick, status = p2, "bare"
        else:
            nm = [(m.start(), m.end(), m.group(1)) for m in MARK_N.finditer(text)]
            pk, lim = {}, len(text) + 1
            for L in "4321":
                c = [x for x in nm if x[2] == L and x[0] < lim]
                if not c:
                    break
                pk[L] = c[-1]; lim = c[-1][0]
            if len(pk) == 4:
                pick, status = {"ABCD"[int(k) - 1]: v for k, v in pk.items()}, "numeric"
    if len(pick) == 4:
        stem, opts = _cut(text, pick)
        if all(opts.values()):
            return dict(stem=stem, options=opts, status=status, markers=len(ms))
    t = _table(text, ms)
    if t:
        return dict(stem=t[0], options=t[1], status="table", markers=len(ms))
    if not ms and not pick:
        return dict(stem=_clean(text), options={}, status="none", markers=0)
    if pick:
        stem, opts = _cut(text, pick)
        return dict(stem=stem, options=opts, status="incomplete", markers=len(ms))
    return dict(stem=_clean(text), options={}, status="incomplete", markers=len(ms))


def norm(t, strict=False):
    """comparison form: no latex wrappers, spaces or case"""
    t = t or ""
    t = re.sub(r"\\(?:mathrm|mathbf|text|bf|rm|textbf)(?![a-z])", "", t)
    t = re.sub(r"\\left|\\right|\\displaystyle|\\,|\\;|\\!|\\quad|~", "", t)
    if not strict:
        t = re.sub(r"\\(?:hat|bar|vec|overline|underline|dot)(?![a-z])", "", t)
    t = t.replace("\\(", "").replace("\\)", "").replace("$", "")
    t = t.replace("\\dfrac", "\\frac").replace("\\ ", "").replace("^{\\circ}", "°")
    t = re.sub(r"[\s{}]", "", t)
    return t.lower()


def same(a, b, thr=0.9):
    import difflib
    a, b = norm(a), norm(b)
    return a == b or (bool(a) and bool(b) and difflib.SequenceMatcher(None, a, b).ratio() >= thr)


def option_flags(r):
    """HIGH severity integrity flags on a split result: (code, why). Route to page crop."""
    out, o = [], r["options"]
    for L in "ABCD":
        if not o.get(L):
            out.append(("OPT_EMPTY", f"option {L} has no text"))
    n = [norm(o[L], True) for L in "ABCD" if o.get(L)]
    if len(set(n)) < len(n):
        out.append(("OPT_DUPLICATE", "two options have identical text (lost sign/digit or copied option)"))
    return out
