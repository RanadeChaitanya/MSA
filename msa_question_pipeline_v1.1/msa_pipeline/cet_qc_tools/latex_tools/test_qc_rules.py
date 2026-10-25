"""Corner-case tests for the QC LaTeX layers (latex_v9 rules -> canon -> strict KaTeX), run on a synthetic paper.
Each case: (subject, input stem, expected substring(s) in the output, forbidden substring(s)). Also checks idempotency.
python test_qc_rules.py"""
import json, subprocess, sys, tempfile
from pathlib import Path

T = Path(__file__).parent; A = T.parent / "ocr_ab"
CASES = [
    # quotes
    ("Physics", "Two stars '$A$' and '$B$' radiate", ["'$A$' and '$B$'"], ["$and$"]),
    ("Physics", "A rod of length 'L' and mass 'm' is bent", ["'$L$'", "'$m$'"], []),
    ("Physics", "Newton's law and Kepler's law hold", ["Newton's law and Kepler's law"], ["$s law"]),
    ("Biology", "The term 'Niche' denotes the role", ["'Niche'"], ["$Niche$"]),
    # Greek / unicode
    ("Physics", "A film of refractive index μ is placed", [r"$\mu$"], ["μ"]),
    ("Physics", "angle of 30° with the horizontal", ["30"], []),
    # units, ions, kelvin powers
    ("Chemistry", r"$\Delta S = 85\,\mathrm{J}\,\mathrm{K}^{-1}\,\mathrm{mol}^{-1}$", [r"\mathrm{K}^{-1}"], [r"\ce{K-}"]),
    ("Chemistry", r"ions $\mathrm{Fe}^{3+}$ and $\mathrm{H}^{+}$", [r"\ce{Fe^{3+}}", r"\ce{H+}"], []),
    ("Chemistry", r"the ion $\mathrm{NO_3^-}$ is reduced", [r"\ce{NO3-}"], []),
    ("Chemistry", r"ethanol $\mathrm{C_2}\mathrm{H_5}\mathrm{OH}$", [r"\ce{C2H5OH}"], [r"\ce{C2}\ce{H5}"]),
    ("Chemistry", r"$2.303\,\mathrm{kcal}$ and $\mathrm{Bromoethane}$", [r"\mathrm{kcal}", r"\text{Bromoethane}"], []),
    ("Physics", r"element $\mathrm{Ca}$ in a physics stem", [r"\mathrm{Ca}"], [r"\ce{"]),
    ("Biology", r"coenzyme $\mathrm{NADH}$ and $\mathrm{DNA}$", [r"\mathrm{NADH}", r"\mathrm{DNA}"], [r"\ce{NADH}", r"\text{DNA}"]),
    # degrees, angstrom, times, spacing
    ("Physics", r"at $30^\circ\mathrm{C}$ the rod", [r"30^\circ\mathrm{C}"], [r"{}^{\circ}"]),
    ("Physics", r"$2 \times 10^{-5}\,^\circ\mathrm{C}^{-1}$", [r"{}^{\circ}"], [r"\,^\circ"]),
    ("Physics", r"wavelength $6000\mathrm{\AA}$", [r"\mathring{\mathrm{A}}"], [r"\mathrm{\AA}"]),
    ("Physics", r"$3.0\times10^{14}\,\mathrm{Hz}$", [r"3.0 \times 10^{14}"], []),
    ("Physics", r"resistance $5\Omega$ .", [r"5\,\Omega$."], [r"$ ."]),
    ("Chemistry", r"$482.5~\mathrm{C}$", [r"482.5\,\mathrm{C}"], ["~"]),
    # statements, abbreviations
    ("Biology", "Select correct. i. Rat ii. Monkey iii. Llama", ["\nii. Monkey", "\niii. Llama"], []),
    ("Biology", "This is, i.e. the same as ii. above", ["i.e. the same"], ["\ni.e."]),
    # arrows
    ("Chemistry", r"$\mathrm{CH_3NH_2\xrightarrow{CH_3I}(CH_3)_4N^+I^-}$", [r"\ce{CH3NH2} \xrightarrow{\ce{CH3I}}"], [r"\mathrm{CH_3NH_2\xrightarrow"]),
    ("Biology", r"Fat $\xrightarrow{\mathrm{Lipases}}$ (Y)", [r"\text{Lipases}"], [r"\mathrm{Lipases}"]),
    # degenerate inputs
    ("Physics", "", [], []),
    ("Physics", "Price is $5 and odd dollar", [], []),
]


def paper(cases):
    qs = []
    for i, (subj, stem, _, _) in enumerate(cases):
        qs.append(dict(qid=f"T_q{i:03d}", subject=subj, number=i + 1, page=1, stem=stem, edits=[], flags=[], reasons=[],
                       options={"A": "x", "B": "", "C": None, "D": "$1$"} if i == len(cases) - 1 else {"A": "a", "B": "b", "C": "c", "D": "d"},
                       correct_option="A"))
    return dict(paper=0, questions=qs)


def run(*a):
    r = subprocess.run([*a], capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=T)
    if r.returncode:
        print("CRASH", a[1:], r.stdout[-600:], r.stderr[-900:]); sys.exit(1)
    return r.stdout


with tempfile.TemporaryDirectory() as d:
    d = Path(d); src = d / "in.json"; src.write_text(json.dumps(paper(CASES)), encoding="utf-8")
    run(sys.executable, str(A / "latex_v9.py"), str(src), str(d / "a.json"))
    run(sys.executable, str(T / "canon.py"), str(d / "a.json"), str(d / "b.json"))
    run(sys.executable, str(A / "latex_v9.py"), str(d / "b.json"), str(d / "c.json"))   # second pass: must change nothing
    run(sys.executable, str(T / "canon.py"), str(d / "c.json"), str(d / "e.json"))
    out = {q["qid"]: q["stem"] for q in json.load(open(d / "b.json", encoding="utf-8"))["questions"]}
    again = {q["qid"]: q["stem"] for q in json.load(open(d / "e.json", encoding="utf-8"))["questions"]}
    ktall = run("node", str(T / "katex_check.js"), str(d / "b.json")).splitlines(); kt = ktall[0]
    [print("KATEX", l) for l in ktall[1:]]
fails = 0
for i, (subj, stem, must, never) in enumerate(CASES):
    o = out[f"T_q{i:03d}"]; bad = [m for m in must if m not in o] + [f"FORBIDDEN {n}" for n in never if n in o]
    if again[f"T_q{i:03d}"] != o:
        bad.append(f"NOT IDEMPOTENT -> {again[f'T_q{i:03d}'][:80]}")
    if bad:
        fails += 1; print(f"FAIL {i:2d} {subj}: {stem[:60]!r}\n      got {o[:110]!r}\n      {bad}")
print(f"\n{len(CASES) - fails}/{len(CASES)} corner cases pass | KaTeX on output: {kt}")
