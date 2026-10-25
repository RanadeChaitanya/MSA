"""v8 -> v9 LaTeX pass (code only):
1. safe, word-preserving fixes from latex_style_guide.md (logged in q['latex_fixes'])
2. render check: every $...$ span must parse in latex2mathml AND matplotlib mathtext (\\text -> \\mathrm shim); failures listed
python latex_v9.py            -> writes final/paper_01_v9.json + ocr_ab/latex_report.json
"""
import json, re, sys, warnings
from pathlib import Path
from collections import Counter
from latex2mathml.converter import convert
from matplotlib.mathtext import MathTextParser

R = Path(r"D:\MSA\outputs\cet_pcb_2024")
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else R / "final" / "paper_01_v8.json"
DST = Path(sys.argv[2]) if len(sys.argv) > 2 else R / "final" / "paper_01_v9.json"
g = json.load(open(SRC, encoding="utf-8"))
MPL = MathTextParser("path")
GREEK = {"μ": r"\mu", "λ": r"\lambda", "θ": r"\theta", "ρ": r"\rho", "π": r"\pi", "Ω": r"\Omega", "Δ": r"\Delta",
         "α": r"\alpha", "β": r"\beta", "γ": r"\gamma", "ω": r"\omega", "σ": r"\sigma", "φ": r"\phi", "×": r"\times", "°": r"^\circ"}


def outside(t, fn):
    """apply fn only to text outside $...$"""
    parts = re.split(r"(\$[^$]*\$)", t)
    return "".join(p if p.startswith("$") else fn(p) for p in parts)


def inside(t, fn):
    return re.sub(r"\$([^$]*)\$", lambda m: "$" + fn(m.group(1)) + "$", t)


def fix(t):
    t = t.replace("\u201c", '"').replace("\u201d", '"').replace("\u2018", "'").replace("\u2019", "'")
    # quoted symbol in prose: ' V_0 ' / 'r' / '4L' -> '$V_0$' (book prints the quotes; the symbol itself is maths)
    t = re.sub(r"'\s*\$([^$]{1,20})\$\s*'", r"'$\1$'", t)
    # bare quoted symbol 'r' -> '$r$'; quotes touching maths or letters are left alone (else "'$A$' and '$B$'" -> "$and$")
    t = re.sub(r"(?<![$\w])'\s*([A-Za-z0-9]{1,4}(?:_[A-Za-z0-9]{1,3})?)\s*'(?![$\w])", r"'$\1$'", t)
    # Greek / maths symbols typed as Unicode in prose -> LaTeX
    t = outside(t, lambda s: re.sub("|".join(map(re.escape, GREEK)), lambda m: "$" + GREEK[m.group(0)] + "$", s))
    t = inside(t, lambda s: re.sub("|".join(map(re.escape, GREEK)), lambda m: GREEK[m.group(0)] + " ", s))
    t = inside(t, lambda s: re.sub(r"\s*\\times\s*", r" \\times ", s))            # 3.0 \times 10^{14}
    t = inside(t, lambda s: re.sub(r"(?<=\d)\s*~\s*(?=\\mathrm)", r"\\,", s))     # 482.5~\mathrm{C} -> 482.5\,\mathrm{C}
    t = inside(t, lambda s: s.replace(r"\dfrac", r"\frac"))
    t = inside(t, lambda s: re.sub(r"(?<=\d)\s*(?=\\mathrm\{|\\Omega)", r"\\,", s))  # 5200\mathrm{\AA} -> 5200\,\mathrm{\AA}
    t = re.sub(r"(?<=\$) +(?=[,.;:?](\s|$))", "", t)                               # "$...$ ." -> "$...$."
    t = outside(t, lambda s: re.sub(r" +(?=[,.;:?](\s|$))", "", s))
    t = re.sub(r"  +", " ", t).strip()
    return t


def render_errors(t):
    errs = []
    if t.count("$") % 2:
        return ["odd number of $"]
    for span in re.findall(r"\$([^$]*)\$", t):
        try:
            convert(span)
        except Exception as e:
            errs.append(f"latex2mathml: {span[:60]} -> {type(e).__name__}")
    return errs  # the strict KaTeX gate (latex_tools/katex_check.js) is the real compile check


# hand fixes for structural faults the rules cannot repair (same words, correct structure)
MANUAL = {
    ("CET_PCB_2024_P01_Che_q008", "stem"): (
        r"$\mathrm{Bromoethane} \xrightarrow{\mathrm{Mg}} \mathrm{Ethylmagnesium\\,bromide} \xrightarrow{\mathrm{HOH}} \mathrm{Ethane} \xrightarrow{\mathrm{Br_2}}\,\mathrm{C}$",
        r"$\text{Bromoethane} \xrightarrow{\mathrm{Mg}} \text{Ethylmagnesium bromide} \xrightarrow{\mathrm{HOH}} \text{Ethane} \xrightarrow{\mathrm{Br_2}} C$"),
    ("CET_PCB_2024_P01_Che_q033", "stem"): (
        r"$\mathrm{CH_3NH_2\xrightarrow{CH_3I}(CH_3)_4N^+I^-}$",
        r"$\mathrm{CH_3NH_2} \xrightarrow{\mathrm{CH_3I}} \mathrm{(CH_3)_4N^+I^-}$"),
}
for q in g["questions"]:
    for (qid, fld), (before, after) in MANUAL.items():
        if q["qid"] == qid and q["stem"].count(before) == 1:
            q["stem"] = q["stem"].replace(before, after)
            q.setdefault("latex_fixes", []).append(dict(field=fld, before=before, after=after, rule="latex_v9_manual"))

fixed, report, rule_count = 0, [], Counter()
for q in g["questions"]:
    for fld in ["stem"] + [f"options.{L}" for L in "ABCD"]:
        t = q["stem"] if fld == "stem" else (q["options"] or {}).get(fld[-1], "")
        if not isinstance(t, str) or not t:
            continue
        new = fix(t)
        if new != t:
            # word guard: letters/digits outside maths must be unchanged (ignore spacing and the added $ around symbols)
            key = lambda s: re.sub(r"[^A-Za-z0-9]", "", re.sub(r"\\[A-Za-z]+", "", s))
            if key(new) != key(t):
                report.append(dict(qid=q["qid"], field=fld, problem="fix skipped by word guard", text=t[:120])); new = t
            else:
                fixed += 1
                q.setdefault("latex_fixes", []).append(dict(field=fld, before=t, after=new, rule="latex_v9"))
                if fld == "stem": q["stem"] = new
                else: q["options"][fld[-1]] = new
        for e in render_errors(new):
            report.append(dict(qid=q["qid"], field=fld, problem=e))

DST.write_text(json.dumps(g, ensure_ascii=False, indent=1), encoding="utf-8")
(R / "ocr_ab" / "latex_report.json").write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
n_spans = sum(len(re.findall(r"\$[^$]*\$", (q["stem"] or "") + " ".join(v for v in (q["options"] or {}).values() if isinstance(v, str)))) for q in g["questions"])
print(f"fields fixed: {fixed}; maths spans checked: {n_spans}; problems left: {len(report)}")
for r in report:
    print(" ", r["qid"].split("_P01_")[-1], r["field"], r["problem"][:120])
