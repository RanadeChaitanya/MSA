"""Step 3 (no OCR): compare each question in our JSON with the book page's existing text file (ocr/page_NNN.txt).
Compared tokens: numbers (with sign), Greek/maths commands. Any difference = our text may not say what the book says.
python text_compare.py final/paper_01_v10.json"""
import json, re, sys
from collections import Counter
from pathlib import Path

R = Path(r"D:\MSA\outputs\cet_pcb_2024")
GREEK = r"\\(?:alpha|beta|gamma|delta|Delta|theta|lambda|mu|nu|pi|rho|sigma|omega|Omega|phi|epsilon|varepsilon|eta|tau|infty|propto|leq|geq|sqrt|frac)(?![A-Za-z])"


def clean(s):
    s = re.sub(r"<\|ref\|>.*?<\|/ref\|><\|det\|>.*?<\|/det\|>", " ", s, flags=re.S)
    s = re.sub(r"(?m)^\w+\[\[[\d,\s\]\[]+\]\]\s*$", " ", s)  # block position tags: text[[115, 82, 508, 133]]
    s = re.sub(r"<[^>]+>", " ", s)                                      # HTML table markup in the book text
    s = re.sub(r"\\(?:begin|end)\{(?:array|tabular|center)\}(\{[^{}]*\})?", " ", s)
    s = s.replace("−", "-").replace("–", "-").replace(r"\varepsilon", r"\epsilon")
    s = re.sub(r"\\ce\{([^{}]*)\}", lambda m: re.sub(r"(?<=[A-Za-z)\]])(\d+)", r"_\1", m.group(1)), s)  # \ce{H2SO4} -> H_2SO_4
    s = re.sub(r"\\(?:mathrm|text|mathring|operatorname)\b", " ", s)
    return s


def tokens(s):
    s = clean(s)
    s = re.sub(r"(?m)^\s*\(?[A-D]\)\s*", " ", s)                 # option labels
    nums = re.findall(r"-?\d+(?:\.\d+)?", s)
    return Counter(n.lstrip("-") if n.startswith("-") and False else n for n in nums) + Counter(re.findall(GREEK, s))


def book_segment(q):
    n, pg = int(q["number"]), int(q["page"])
    text = "".join((R / "ocr" / f"page_{x:03d}.txt").read_text(encoding="utf-8") + "\n" for x in (pg, pg + 1) if (R / "ocr" / f"page_{x:03d}.txt").exists())
    text = clean(text)
    text = re.sub(r"(?m)^\s*\d{1,3}\s*$", " ", text)                     # page-number footers
    text = re.sub(r"(?mi)^.*(MHT\s*-\s*CET|April\s*\(Shift).*$", " ", text)  # running headers
    # segment ends at the next question, a subject heading, or question 1 of the next subject
    m = re.search(rf"(?m)^\W*{n}\s*\.(.*?)(?=^\W*{n + 1}\s*\.|^\W*1\s*\.\s|^#+\s*(?:PHYSICS|CHEMISTRY|BIOLOGY)|\Z)", text, flags=re.S | re.I)
    return m.group(1) if m else None


def words(s):
    """lower-case words (3+ letters) outside maths and LaTeX commands; \\text{...} contents count as words"""
    s = clean(s)
    s = re.sub(r"\\text\{([^{}]*)\}", r" \1 ", s)
    s = re.sub(r"\\\(|\\\)", "$", s)                                     # book uses \( \) for maths
    s = re.sub(r"\$[^$]*\$", " ", s)
    s = re.sub(r"\\[A-Za-z]+", " ", s)
    return Counter(w.lower() for w in re.findall(r"[A-Za-z]{3,}", s))


BLANK = r"_{3,}|\\_\\_|\.{4,}|-{4,}"
DANGLING = re.compile(r"\b(in|by|of|to|at|is|are|was|were|from|with|on|for|as|into|EXCEPT|usually|separates|be|show|between|called)\s*\.?$", re.I)
IGNORE = {"the", "and", "statement", "statements", "correct", "incorrect", "following", "option", "options", "given", "below", "choose", "answer", "figure", "fig", "center", "column", "table", "array"}

g = json.load(open(R / sys.argv[1], encoding="utf-8"))
rows = []
for q in g["questions"]:
    seg = book_segment(q)
    if seg is None:
        rows.append(dict(qid=q["qid"], status="not_found")); continue
    ours = q["stem"] + "\n" + "\n".join(v for v in (q["options"] or {}).values() if isinstance(v, str))
    a, b = tokens(ours), tokens(seg)
    oa, ob = dict(a - b), dict(b - a)
    wa, wb = words(ours), words(seg)
    # word check: a word we have that the book never has (or vice versa), ignoring very common words
    # glued table cells ('thalassemiaa', 'monosomyii'): a word inside a longer token on the other side counts as present
    inside = lambda w, other: any(len(w) >= 4 and w != t and w in t for t in other)
    wo = {w: c for w, c in (wa - wb).items() if w not in IGNORE and w not in wb and not inside(w, wb)}
    wbk = {w: c for w, c in (wb - wa).items() if w not in IGNORE and w not in wa and not inside(w, wa)
           and not any(len(t) >= 4 and t in w for t in wa)}  # glued token made of our words
    blank = bool(re.search(BLANK, clean(seg))) and not re.search(BLANK, ours)
    # the book text often loses blanks too: a stem ending on a dangling word with no blank goes to the image check
    suspect = "?" not in q["stem"] and not re.search(BLANK, q["stem"]) and bool(DANGLING.search(q["stem"].rstrip()))
    bad = oa or ob or wo or wbk or blank
    rows.append(dict(qid=q["qid"], status="MISMATCH" if bad else "match", only_ours=oa, only_book=ob,
                     words_only_ours=wo, words_only_book=wbk, blank_lost=blank, blank_suspect=suspect))
(R / "latex_tools" / "text_compare_report.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
print(dict(Counter(r["status"] for r in rows)))
for r in rows:
    if r["status"] == "MISMATCH":
        print(" ", r["qid"].split("_P01_")[-1], "sym+", r["only_ours"], "book", r["only_book"], "| words+", r["words_only_ours"],
              "book", r["words_only_book"], "| BLANK LOST" if r["blank_lost"] else "")
