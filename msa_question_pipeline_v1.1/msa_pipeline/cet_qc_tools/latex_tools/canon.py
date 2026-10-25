"""Step 2: canonical LaTeX (one house style) + option-consistency check. v9 -> v10. Code only, every change logged.
Rules never change a letter/digit of the content (word guard), except AA -> \\mathring{A} which keeps the letter A.
python canon.py   -> final/paper_01_v10.json, latex_tools/canon_report.json
"""
import json, re, sys
from pathlib import Path
from collections import Counter

R = Path(r"D:\MSA\outputs\cet_pcb_2024")
SRC = Path(sys.argv[1]) if len(sys.argv) > 1 else R / "final" / "paper_01_v9.json"
DST = Path(sys.argv[2]) if len(sys.argv) > 2 else R / "final" / "paper_01_v10.json"
g = json.load(open(SRC, encoding="utf-8"))
ELEM = r"(?:He|Li|Be|Ne|Na|Mg|Al|Si|Cl|Ar|Ca|Sc|Ti|Cr|Mn|Fe|Co|Ni|Cu|Zn|Ga|Ge|As|Se|Br|Kr|Rb|Sr|Ag|Cd|Sn|Sb|Xe|Cs|Ba|Pt|Au|Hg|Pb|Bi|U|H|B|C|N|O|F|P|S|K|V|I|Y|W)"
FORMULA = re.compile(rf"^(?:{ELEM}|[()\[\]]|_\{{?\d+\}}?|\^\{{?\d*[+-]\}}?|\^[+-]|\d+)+$")
MULTI_UNITS = r"(?:cm|mm|km|nm|kg|mg|mol|Hz|kHz|MHz|Pa|kPa|eV|MeV|keV|mL|Wb|atm|bar|cal|kcal|kJ|kW|mA|mV|kV|dm)"
DESCRIPTIVE = r"(?:rms|max|min|eq|eff|net|ext|avg|sat|in|out|total|obs|cal|ice|water|app)"
counts, log = Counter(), []


def inside(t, fn):
    return re.sub(r"\$([^$]+)\$", lambda m: "$" + fn(m.group(1)) + "$", t)


def rule(name, pat, rep, s, flags=0):
    new = re.sub(pat, rep, s, flags=flags)
    if new != s:
        counts[name] += 1
    return new


def to_ce(body):
    """\\mathrm{H_2SO_4} body -> mhchem H2SO4"""
    b = re.sub(r"_\{?(\d+)\}?", r"\1", body)
    b = re.sub(r"\^\{(\d*[+-])\}", r"^{\1}", b)
    b = re.sub(r"\^([+-])", r"\1", b)
    return b


def canon_math(s, subject):
    # arrow trapped inside \mathrm{...}: \mathrm{A\xrightarrow{X}B} -> \mathrm{A} \xrightarrow{\mathrm{X}} \mathrm{B}
    s = rule("arrow_out_of_mathrm", r"\\mathrm\{([^{}]*?)\\(xrightarrow|rightarrow|rightleftharpoons)(?:\{([^{}]*)\})?((?:[^{}]|\{[^{}]*\})*)\}",
             lambda m: (r"\mathrm{" + m.group(1) + "} " if m.group(1).strip() else "") + "\\" + m.group(2)
             + ("{\\mathrm{" + m.group(3) + "}}" if m.group(3) else "") + (r" \mathrm{" + m.group(4) + "}" if m.group(4).strip() else ""), s)
    s = rule("AA_in_math", r"\\mathrm\{\\AA\}", r"\\mathring{\\mathrm{A}}", s)
    s = rule("degree_no_base", r"(\\,|\s|^|\()\^\{?\\circ\}?", lambda m: m.group(1) + r"{}^{\circ}", s)
    s = rule("word_in_mathrm", r"\\mathrm\{([A-Za-z]*[a-z]{3,}[A-Za-z]*)\}", lambda m: m.group(0) if re.fullmatch(MULTI_UNITS, m.group(1)) else r"\text{" + m.group(1) + "}", s)
    s = rule("unit_mathrm", rf"(?<=\d)(?:\\,|\s)*(?<!\\mathrm\{{)\b({MULTI_UNITS})\b(?![}}a-z])", r"\\,\\mathrm{\1}", s)
    s = rule("descriptive_subscript", rf"_\{{({DESCRIPTIVE})\}}|_({DESCRIPTIVE})\b", lambda m: r"_{\mathrm{" + (m.group(1) or m.group(2)) + "}}", s)
    s = rule("brace_scripts", r"\^(-\d+)", r"^{\1}", s)
    s = rule("left_right", r"(?<!\\left)(?<![A-Za-z\\])\(([^()$]*?\\(?:frac|sqrt)[^()$]*?)\)", r"\\left(\1\\right)", s)
    if subject in ("Chemistry", "Biology"):
        for _ in range(3):  # \mathrm{C_2}\mathrm{H_5} -> \mathrm{C_2H_5} so mhchem sees one formula
            s = rule("merge_mathrm", r"\\mathrm\{([^{}]*)\}\\mathrm\{([^{}]*)\}", r"\\mathrm{\1\2}", s)
        s = rule("mhchem", r"\\mathrm\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}",
                 lambda m: r"\ce{" + to_ce(m.group(1)) + "}" if FORMULA.match(m.group(1)) and re.search(r"[A-Z]", m.group(1)) and len(m.group(1)) > 1 else m.group(0), s)
    if subject in ("Chemistry", "Biology"):  # charge belongs inside the formula: \ce{HCO3}^- -> \ce{HCO3-}, \mathrm{H}^{+} -> \ce{H+}
        # a charge is (digits)+sign as the LAST thing in the superscript: ^{2+}, ^{-}, ^+ ; never ^{-1} (that is a unit power)
        CH = r"\^(?:\{(\d*)([+-])\}|([+-])(?!\d))"
        ch = lambda m, base: r"\ce{" + base + (("^{" + m.group(2) + m.group(3) + "}") if m.group(2) else (m.group(3) or m.group(4))) + "}"
        s = rule("charge_inside_ce", r"\\ce\{([^{}]*)\}" + CH, lambda m: ch(m, m.group(1)), s)
        s = rule("ion_to_ce", r"\\mathrm\{([A-Z][a-z]?)\}" + CH, lambda m: ch(m, m.group(1)), s)
    return re.sub(r"\s{2,}", " ", s)


def key(t):  # letters/digits outside LaTeX commands
    t = t.replace(r"\AA", "A")
    return re.sub(r"[^A-Za-z0-9]", "", re.sub(r"\\(?:ce|mathrm|text|mathring|left|right|frac|sqrt|circ)\b", "", t))


def consistency(q):
    """sibling options should share one notation family"""
    opts = [v for v in (q["options"] or {}).values() if isinstance(v, str) and v]
    out = []
    if len(opts) == 4:
        has = lambda p: [bool(re.search(p, o)) for o in opts]
        for name, p in (("units", r"\\mathrm\{(?:" + MULTI_UNITS + r"|[A-Za-z]{1,2})\b"), ("math", r"\$"), ("frac", r"\\frac"), ("ce", r"\\ce\{")):
            h = has(p)
            if any(h) and not all(h) and sum(h) in (1, 3):
                out.append(f"options mix {name}: {''.join('ABCD'[i] if x else '.' for i, x in enumerate(h))}")
        if len(set(opts)) < 4:
            out.append("duplicate options")
    return out


report = []
def statement_lines(t):
    """book prints 'i. ... ii. ... iii. ...' statements one per line: put each on its own line (outside maths only)"""
    if not re.search(r"(?:^|\s)i\.\s", t) or not re.search(r"\sii\.\s", t):
        return t
    parts = re.split(r"(\$[^$]+\$)", t)
    return "".join(p if p.startswith("$") else re.sub(r"[ \t]+(?=(?:i|ii|iii|iv|v|vi|vii|viii)\.\s)", "\n", p) for p in parts)


for q in g["questions"]:
    s2 = statement_lines(q["stem"])
    if s2 != q["stem"]:
        counts["statement_lines"] += 1
        q.setdefault("latex_fixes", []).append(dict(field="stem", before=q["stem"], after=s2, rule="canon_v10_lines"))
        q["stem"] = s2
    for fld in ["stem"] + [f"options.{L}" for L in "ABCD"]:
        t = q["stem"] if fld == "stem" else (q["options"] or {}).get(fld[-1], "")
        if not isinstance(t, str) or not t:
            continue
        new = inside(t, lambda s: canon_math(s, q["subject"]))
        if new == t:
            continue
        if key(new) != key(t):
            report.append(dict(qid=q["qid"], field=fld, problem="canon skipped (word guard)", before=t[:100], after=new[:100])); continue
        q.setdefault("latex_fixes", []).append(dict(field=fld, before=t, after=new, rule="canon_v10"))
        if fld == "stem": q["stem"] = new
        else: q["options"][fld[-1]] = new
        log.append((q["qid"], fld, t, new))
    for c in consistency(q):
        report.append(dict(qid=q["qid"], field="options", problem=c))

DST.write_text(json.dumps(g, ensure_ascii=False, indent=1), encoding="utf-8")
(R / "latex_tools" / "canon_report.json").write_text(json.dumps(dict(rules=counts, changes=log, report=report), ensure_ascii=False, indent=1), encoding="utf-8")
print("fields changed:", len(log), "| rule hits:", dict(counts))
for r in report: print(" ", r["qid"].split("_P01_")[-1], r["field"], r["problem"], r.get("before", "")[:60], "=>", r.get("after", "")[:60])
for qid, f, a, b in log[:14]: print("  ", qid.split("_P01_")[-1], f, ":", a[:60], "=>", b[:60])
