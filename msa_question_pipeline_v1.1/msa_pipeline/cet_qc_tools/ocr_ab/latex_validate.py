"""Parse every $...$ span of a paper JSON with latex2mathml; report odd $ counts and parse failures.
python latex_validate.py final/paper_01_v9.json"""
import json, re, sys
from pathlib import Path
from latex2mathml.converter import convert

g = json.load(open(Path(r"D:\MSA\outputs\cet_pcb_2024") / sys.argv[1], encoding="utf-8"))
n = bad = 0
for q in g["questions"]:
    for t in [q["stem"]] + [v for v in (q["options"] or {}).values() if isinstance(v, str)]:
        if t.count("$") % 2:
            bad += 1; print("ODD $", q["qid"][-8:], t[:80])
        for s in re.findall(r"\$([^$]+)\$", t):
            n += 1
            try:
                convert(s)
            except Exception as e:
                bad += 1; print("BAD", q["qid"][-8:], s[:80], type(e).__name__)
print(n, "formulas,", bad, "problems")
