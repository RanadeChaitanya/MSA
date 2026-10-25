"""Step 4: dimension check across sibling options + empty/duplicate options. Code only.
Each option's units (\\mathrm{...}, \\Omega, ^\\circ) -> dimension vector over (M, L, T, I, K, mol); siblings must agree.
python units_check.py final/paper_01_v10.json"""
import json, re, sys
from collections import Counter
from pathlib import Path

R = Path(r"D:\MSA\outputs\cet_pcb_2024")
D = lambda M=0, L=0, T=0, I=0, K=0, n=0: (M, L, T, I, K, n)
U = {"m": D(L=1), "cm": D(L=1), "mm": D(L=1), "km": D(L=1), "nm": D(L=1), "dm": D(L=1), "s": D(T=1), "ms": D(T=1), "min": D(T=1), "h": D(T=1),
     "kg": D(M=1), "g": D(M=1), "mg": D(M=1), "N": D(M=1, L=1, T=-2), "J": D(M=1, L=2, T=-2), "kJ": D(M=1, L=2, T=-2), "eV": D(M=1, L=2, T=-2),
     "MeV": D(M=1, L=2, T=-2), "cal": D(M=1, L=2, T=-2), "kcal": D(M=1, L=2, T=-2), "W": D(M=1, L=2, T=-3), "kW": D(M=1, L=2, T=-3),
     "Pa": D(M=1, L=-1, T=-2), "atm": D(M=1, L=-1, T=-2), "bar": D(M=1, L=-1, T=-2), "Hz": D(T=-1), "A": D(I=1), "mA": D(I=1),
     "C": D(I=1, T=1), "V": D(M=1, L=2, T=-3, I=-1), "mV": D(M=1, L=2, T=-3, I=-1), "kV": D(M=1, L=2, T=-3, I=-1),
     "Omega": D(M=1, L=2, T=-3, I=-2), "T": D(M=1, T=-2, I=-1), "Wb": D(M=1, L=2, T=-2, I=-1), "H": D(M=1, L=2, T=-2, I=-2),
     "F": D(M=-1, L=-2, T=4, I=2), "K": D(K=1), "degC": D(K=1), "mol": D(n=1), "L": D(L=3), "mL": D(L=3), "M": D(n=1, L=-3), "AA": D(L=1)}


def dims(o):
    """dimension vector of the units in one option, or None if it has no recognisable unit"""
    o = o.replace(r"\Omega", r"\mathrm{Omega}").replace(r"\mathring{\mathrm{A}}", r"\mathrm{AA}")
    o = re.sub(r"\{\}\^\{\\circ\}\\mathrm\{C\}", r"\\mathrm{degC}", o)
    o = re.sub(r"\[\\mathrm\{[^{}]*\}\]", " ", o)  # [\mathrm{A}] is a concentration, not a unit
    found, vec = False, [0] * 6
    for body, after in re.findall(r"\\mathrm\{((?:[^{}]|\{[^{}]*\})*)\}(\^\{-?\d+\})?", o):
        parts = re.split(r"\\,|\s+", body)
        for i, part in enumerate(parts):
            m = re.match(r"^([A-Za-z]+)(?:\^\{?(-?\d+)\}?)?$", part.strip())
            if not m or m.group(1) not in U:
                if part.strip(): return None   # not a pure unit expression (formula, label): skip
                continue
            e = int(m.group(2) or 1)
            if i == len(parts) - 1 and after:
                e *= int(after.strip("^{}"))
            found = True
            vec = [a + e * b for a, b in zip(vec, U[m.group(1)])]
    return tuple(vec) if found else None


def norm(o):
    return re.sub(r"\s|\\,|\$", "", o)


g = json.load(open(R / sys.argv[1], encoding="utf-8"))
out = []
for q in g["questions"]:
    opts = {k: v for k, v in (q["options"] or {}).items()}
    empty = [k for k in "ABCD" if (not isinstance(opts.get(k), str) or not opts.get(k).strip()) and k not in (q.get("option_images") or {})]
    if empty:
        out.append((q["qid"], "EMPTY_OPTION", "".join(empty)))
    full = {k: v for k, v in opts.items() if isinstance(v, str) and v.strip()}
    dup = [v for v, c in Counter(norm(v) for v in full.values()).items() if c > 1]
    if dup:
        out.append((q["qid"], "DUPLICATE_OPTIONS", dup[0][:60]))
    d = {k: dims(v) for k, v in full.items()}
    with_units = {k: v for k, v in d.items() if v is not None}
    if len(with_units) >= 2 and len(set(with_units.values())) > 1:
        out.append((q["qid"], "UNIT_DIMENSION_MISMATCH", " | ".join(f"{k}:{full[k][:30]}" for k in with_units)))
    if 0 < len(with_units) < len(full) and len(full) == 4 and all(re.search(r"\d", v) for v in full.values()):
        missing = [k for k in full if k not in with_units]
        out.append((q["qid"], "UNITS_MISSING_ON_SOME_OPTIONS", "".join(missing)))
(R / "latex_tools" / "units_report.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
print(Counter(x[1] for x in out))
for x in out: print(" ", x[0].split("_P01_")[-1], x[1], x[2][:110])
