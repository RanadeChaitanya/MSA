"""Deterministic OCR-confusion watchlist: flags questions where a symbol was probably misread or dropped. Code only, no model.
  python claude_pipeline/confusion_watch.py OCR_DIR FIRST_PAGE LAST_PAGE
Rules (each returns a flag code from deepseek_schema.FlagCode):
  PI_N          pi appears in the question but a bare 'n' sits in a formula and 'n' is never introduced in the words (Q12: sqrt(ka/nm) vs options with pi)
  SUBSCRIPT     one letter appears both bare and subscripted across stem+options (D / D_1, i / i_p, l / l_1)
  FRICTION_MU   'coefficient of static/kinetic/rolling friction' with a mu that has no subscript (Q28)
  GAMMA_PRIME   a prime or apostrophe exponent next to p v in an adiabatic / gas context (Q26)
  VECTOR_HATS   vector wording and plain i, j (unit vectors lose their hats) (Q3)
"""
import re, sys
from pathlib import Path

HEAD = re.compile(r"^(\w+)\[\[(\d+), (\d+), (\d+), (\d+)\]\]", re.M)


def blocks_box(txt):
    hs = list(HEAD.finditer(txt))
    for i, h in enumerate(hs):
        yield h.group(1), txt[h.end():hs[i + 1].start() if i + 1 < len(hs) else len(txt)].strip(), [int(h.group(k)) for k in (2, 3, 4, 5)]


def blocks(txt):
    for k, b, _ in blocks_box(txt):
        yield k, b


CHROME = re.compile(r"MHT|Time\s*:|Total Marks|Physics, Chemistry|^\s*(PHYSICS|CHEMISTRY|BIOLOGY)\s*$|Solved Papers|April|Shift", re.I)


def questions(ocr_dir, p0, p1):
    """Segment OCR blocks into questions. A question starts at a numbered block, OR (structure rule) at a text block that follows a
    complete option set (A..D seen) with no number: the number is then inferred as previous+1 and the question is marked inferred.
    Nothing is merged silently: every inferred start is reported by the caller (q['inferred'])."""
    qs, cur, prev, subj = [], None, 0, 0
    # an option set is complete when both (A) and (D) markers are present (also in the (\mathrm{A}) form the OCR sometimes writes)
    # a question is "closed" once its D marker is present (A may be lost or be a picture); the (A) form with \mathrm{} is accepted too
    has_options = lambda t: bool(re.search(r"\(\s*(?:\\mathrm\{)?\s*D\s*\}?\s*\)", t))
    for pg in range(p0, p1 + 1):
        for kind, body, box in blocks_box((Path(ocr_dir) / f"page_{pg:03d}.txt").read_text(encoding="utf-8")):
            m = re.match(r"^(\d{1,3})\s*[.)]\s+(.*)", body, re.S)
            if (m and kind in ("text", "sub_title", "title") and cur and cur["inferred"] and int(m[1]) == cur["number"] and len(qs) > 1):
                # the real number arrived: the earlier inferred start was a false alarm (continuation text), so undo it
                qs.pop(); qs[-1]["text"] += " " + cur["text"]; qs[-1]["images"] += cur["images"]; prev -= 1; cur = qs[-1]
            if m and kind in ("text", "sub_title", "title") and int(m[1]) <= 100 and (int(m[1]) > prev or int(m[1]) == 1):
                if int(m[1]) == 1 and prev > 5:
                    subj = min(subj + 1, 2)
                cur = dict(subject=["Physics", "Chemistry", "Biology"][subj], number=int(m[1]), page=pg, text=m[2], inferred=False, images=[], box=[pg] + box); qs.append(cur); prev = int(m[1])
            elif (cur and kind == "text" and has_options(cur["text"]) and not re.match(r"^\s*\(", body) and len(body.split()) >= 4 and re.match(r"^[A-Z]", body)
                  and not re.match(r"^(Fig|Figure|\[|Given|Note)", body) and not CHROME.search(body)
                  and not has_options(body.split("(A)")[0]) and prev < 100):
                cur = dict(subject=["Physics", "Chemistry", "Biology"][subj], number=prev + 1, page=pg, text=body, inferred=True, images=[], box=[pg] + box); qs.append(cur); prev += 1
            elif cur and kind in ("text", "equation", "table"):
                cur["text"] += " " + body
                if cur["box"][0] == pg:  # union of the text blocks on the first page (for crops)
                    cur["box"] = [pg, min(cur["box"][1], box[0]), min(cur["box"][2], box[1]), max(cur["box"][3], box[2]), max(cur["box"][4], box[3])]
            elif cur and kind == "image":
                cur["images"].append(dict(page=pg, box=box, after_chars=len(cur["text"])))  # figure box seen while this question is open
    return qs


def plain(t):
    for _ in range(3):
        t = re.sub(r"\\(?:mathrm|mathbf|text|bf|rm|mathfrak|mathcal)\{([^{}]*)\}", r"\1", t)
    return t


def check(text):
    t, flags = plain(text), []
    t = re.sub(r"\(\s*[A-D]\s*\)", " ", t)  # option labels (A)..(D) are not variables (Che3 B_2A, Phy20 B_axis false flags)
    words = re.sub(r"\\\(.*?\\\)|\$.*?\$|\\\[.*?\\\]", " ", t, flags=re.S)  # prose only
    maths = " ".join(re.findall(r"\\\((.*?)\\\)|\$(.*?)\$|\\\[(.*?)\\\]", t, flags=re.S) and [a or b or c for a, b, c in re.findall(r"\\\((.*?)\\\)|\$(.*?)\$|\\\[(.*?)\\\]", t, flags=re.S)])
    # PI_N
    bare_maths = re.sub(r"sin|cos|tan|cot|sec|log|ln|min|exp", " ", re.sub(r"\\[A-Za-z]+", " ", maths))  # drop \sqrt \frac \sin ... so their letters do not count; products like nm remain
    if "\\pi" in t and re.search(r"n", bare_maths) and not re.search(r"(?<![A-Za-z])['\"]?n['\"]?(?![A-Za-z])", words) and not re.search(r"(?<![A-Za-z])n\s*=\s*(?:\\\)\s*)?[A-Za-z]{4,}", t):
        flags.append(("PI_N", "a bare n appears in a formula, n is never introduced in the words, and pi appears elsewhere in the question"))
    # SUBSCRIPT: letter bare and subscripted
    for L in sorted(set(re.findall(r"(?<![A-Za-z\\])([A-Za-z])_\{?[A-Za-z0-9]+\}?", t))):
        if re.search(rf"(?<![A-Za-z\\_^]){L}(?![A-Za-z_0-9])(?!\^\{{?[+\-−])", re.sub(rf"{L}_\{{?[A-Za-z0-9]+\}}?", "", t)):
            flags.append(("SUBSCRIPT", f"letter {L} appears both bare and with a subscript"))
    # FRICTION_MU
    if re.search(r"coefficient of (static|kinetic|rolling|limiting) friction", t, re.I) and re.search(r"\\mu(?!_)", t):
        flags.append(("FRICTION_MU", "a friction coefficient mu appears without a subscript"))
    # GAMMA_PRIME
    if re.search(r"(?:adiabatic|isothermal|gas)", t, re.I) and re.search(r"[pP]\s*[vV]\^\{?\\prime\}?|[pP][vV]'", t):
        flags.append(("GAMMA_PRIME", "a prime appears as an exponent of pv in a gas context; gamma was probably misread"))
    # THETA_ZERO: an angle named '0' in the prose (theta misread as zero)
    if re.search(r"['‘\"]\s*0\s*['’\"]", words) and re.search(r"angle|degree|vertical|horizontal|incline|incidence|refraction", words, re.I):
        flags.append(("THETA_ZERO", "a quoted 0 appears where an angle symbol (theta) is expected"))
    # MISSING_COMMA: Q_1 Q_2 / E_1 E_2 written without a separator
    segs = [a or b or c for a, b, c in re.findall(r"\\\((.*?)\\\)|\$(.*?)\$|\\\[(.*?)\\\]", t, flags=re.S)]
    if any(re.search(r"(?<![A-Za-z])([A-Za-z])_\{?1\}?\s*\1_\{?2\}?", s) for s in segs):
        flags.append(("MISSING_COMMA", "two subscripted variables sit side by side with no comma (Q_1 Q_2)"))
    # VECTOR_HATS
    if re.search(r"position vector|unit vector|vector", t, re.I) and re.search(r"[0-9.]\s*[ij]\b", maths):
        flags.append(("VECTOR_HATS", "plain i, j next to numbers in a vector question; hats may be lost"))
    return flags


if __name__ == "__main__":
    ocr_dir, p0, p1 = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    qs = questions(ocr_dir, p0, p1)
    hits = [(q, check(q["text"])) for q in qs]
    total = {}
    for q, fl in hits:
        for code, why in fl:
            total[code] = total.get(code, 0) + 1
            print(f"{q['subject'][:3]} Q{q['number']:<3} p{q['page']:<2} {code:<12} {why}")
    print("questions", len(qs), "| flagged", sum(1 for _, f in hits if f), "| by rule", total)
