"""Apply Haiku step-5 'ours_wrong' edits to v10 (exact single match; never on fields the user patched). Logged in q['edits']."""
import json
from pathlib import Path
from collections import Counter

R = Path(r"D:\MSA\outputs\cet_pcb_2024"); p = R / "final" / "paper_01_v10.json"
g = json.load(open(p, encoding="utf-8")); qs = {q["qid"]: q for q in g["questions"]}
st, log = Counter(), []
for r in json.load(open(R / "latex_tools" / "step5_review.json", encoding="utf-8")):
    st[r["verdict"]] += 1
    q = qs[r["qid"]]
    human = {x.get("field") for x in q.get("patches", [])} | ({"stem"} if any(x.get("op") == "set_stem" for x in q.get("patches", [])) else set())
    if r["verdict"] == "unclear":
        q.setdefault("flags", []).append("V10_TEXT_UNCLEAR"); q["v10_note"] = r.get("note", "")
    for e in (r.get("edits") or []) if r["verdict"] == "ours_wrong" else []:
        f = e["field"]
        cur = q["stem"] if f == "stem" else (q["options"] or {}).get(f.split(".")[-1], "")
        if f in human or not e.get("before") or not isinstance(cur, str) or cur.count(e["before"]) != 1:
            st["edit_skipped"] += 1; log.append((r["qid"], "SKIP", f, e.get("before", "")[:50])); continue
        new = cur.replace(e["before"], e["after"])
        if f == "stem": q["stem"] = new
        else: q["options"][f.split(".")[-1]] = new
        q["edits"].append(dict(field=f, type="repair_v10", basis="text_compare+haiku_crop_v10", before=e["before"], after=e["after"]))
        st["edit_applied"] += 1; log.append((r["qid"], "EDIT", f, f"{e['before'][:50]} -> {e['after'][:50]}"))
p.write_text(json.dumps(g, ensure_ascii=False, indent=1), encoding="utf-8")
print(dict(st))
for x in log: print(" ", x[0].split("_P01_")[1], *x[1:])
