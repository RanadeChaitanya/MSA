"""Code-only LaTeX lint of a paper JSON against latex_style_guide.md. Prints counts + examples; --fix applies the safe, word-preserving fixes.
python latex_lint.py final/paper_01_v8.json [--fix]
"""
import json, re, sys
from collections import Counter, defaultdict
from pathlib import Path

R = Path(r"D:\MSA\outputs\cet_pcb_2024")
path = R / sys.argv[1]
g = json.load(open(path, encoding="utf-8"))

RULES = {  # name: (pattern, safe replacement or None = flag only)
    "curly_double_quotes": (r"[\u201c\u201d]", '"'),
    "curly_single_quotes": (r"[\u2018\u2019]", "'"),
    "latex_quotes_``''": (r"``|''", None),
    "spaced_quote_mark": (r"' (?=[^']{1,30} ')", None),          # ' V_0 ' style quoting around a symbol
    "dfrac": (r"\\dfrac", r"\\frac"),
    "paren_delims": (r"\\\(|\\\)|\\\[|\\\]", None),
    "tilde_space": (r"~", None),
    "mathrm_tilde": (r"\\mathrm\{~", None),
    "double_space": (r"  +", " "),
    "space_before_punct": (r" +(?=[,.;:?](\s|$))", ""),
    "bare_script_outside_math": (None, None),                     # computed below
    "odd_dollars": (None, None),
    "unbalanced_braces": (None, None),
    "left_without_right": (None, None),
    "times_no_spaces": (r"(?<=\d)\\times(?=\d)", None),
    "unicode_math": (r"[×−≤≥≈→←⇌°µΩαβγδθλμπρσφωΔ]", None),        # should be LaTeX inside $...$
}


def outside_math(t):
    return re.sub(r"\$[^$]*\$", " ", t)


def check(t):
    hits = []
    for name, (pat, _) in RULES.items():
        if pat and re.search(pat, t):
            hits.append(name)
    if t.count("$") % 2:
        hits.append("odd_dollars")
    for span in re.findall(r"\$([^$]*)\$", t):
        if span.count("{") != span.count("}"):
            hits.append("unbalanced_braces"); break
    for span in re.findall(r"\$([^$]*)\$", t):
        if len(re.findall(r"\\left\b", span)) != len(re.findall(r"\\right\b", span)):
            hits.append("left_without_right"); break
    if re.search(r"[A-Za-z0-9\)][_^]\{?[A-Za-z0-9+-]", outside_math(t)):
        hits.append("bare_script_outside_math")
    return hits


def fix(t):
    for name, (pat, rep) in RULES.items():
        if pat and rep is not None:
            t = re.sub(pat, rep, t)
    return t.strip()


count, examples, fixed = Counter(), defaultdict(list), 0
for q in g["questions"]:
    for fld in ["stem"] + [f"options.{L}" for L in "ABCD"]:
        t = q["stem"] if fld == "stem" else (q["options"] or {}).get(fld[-1], "")
        if not isinstance(t, str) or not t:
            continue
        for h in check(t):
            count[h] += 1
            if len(examples[h]) < 3:
                examples[h].append(f"{q['qid'].split('_P01_')[-1]} {fld}: {t[:110]}")
        if "--fix" in sys.argv:
            new = fix(t)
            if new != t:
                fixed += 1
                q.setdefault("latex_fixes", []).append(dict(field=fld, before=t, after=new, rule="latex_lint"))
                if fld == "stem": q["stem"] = new
                else: q["options"][fld[-1]] = new
print(dict(count.most_common()))
for k, v in examples.items():
    print(f"\n[{k}]"); [print("  ", e) for e in v]
if "--fix" in sys.argv:
    path.write_text(json.dumps(g, ensure_ascii=False, indent=1), encoding="utf-8"); print("\nfixed fields:", fixed)
