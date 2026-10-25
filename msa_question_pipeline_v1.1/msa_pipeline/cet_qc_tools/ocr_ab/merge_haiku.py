"""Merge haiku_review_*.json into paper_01_v9.json.
pass 1 (python merge_haiku.py 1): apply latex-only edits that keep every letter/digit; collect text edits + doubts into layer2_batch.json
pass 2 (python merge_haiku.py 2): apply only the text edits layer 2 CONFIRMED; flag the rest; write final/paper_01_v9.json
"""
import json, re, sys
from pathlib import Path
from collections import Counter

R = Path(r"D:\MSA\outputs\cet_pcb_2024"); AB = R / "ocr_ab"
path = R / "final" / "paper_01_v9.json"
g = json.load(open(path, encoding="utf-8")); qs = {q["qid"]: q for q in g["questions"]}
key = lambda s: re.sub(r"[^A-Za-z0-9]", "", re.sub(r"\\[A-Za-z]+", "", s))


def get(q, f):
    return q["stem"] if f == "stem" else (q["options"] or {}).get(f.split(".")[-1], "")


def put(q, f, v):
    if f == "stem": q["stem"] = v
    else: q["options"][f.split(".")[-1]] = v


def apply(q, e, basis):
    cur = get(q, e["field"])
    if not e.get("before") or not isinstance(cur, str) or cur.count(e["before"]) != 1:
        return False
    put(q, e["field"], cur.replace(e["before"], e["after"]))
    q["edits"].append(dict(field=e["field"], type="repair_v9", basis=basis, before=e["before"], after=e["after"]))
    return True


stat = Counter()
if sys.argv[1] == "1":
    reviews = [r for k in range(4) if (AB / f"haiku_review_{k}.json").exists() for r in json.load(open(AB / f"haiku_review_{k}.json", encoding="utf-8"))]
    batch = {json.loads(l)["qid"]: json.loads(l) for l in []}
    items = {i["qid"]: i for k in range(4) for i in json.load(open(AB / f"haiku_batch_{k}.json", encoding="utf-8"))}
    layer2 = []
    for r in reviews:
        q = qs.get(r["qid"])
        if not q: continue
        stat["reviewed"] += 1
        pending = []
        human = {p.get("field") for p in q.get("patches", []) if p.get("op") in ("replace_text", "set_option_text", "set_stem")}
        human |= {"stem"} if any(p.get("op") == "set_stem" for p in q.get("patches", [])) else set()
        for e in r.get("edits") or []:
            if e.get("field") in human:  # the user rewrote this field on purpose: models never undo it
                stat["skipped_human_field"] += 1; continue
            cur = get(q, e.get("field", ""))
            safe_latex = isinstance(cur, str) and e.get("before") and key(cur.replace(e["before"], e.get("after", ""), 1)) == key(cur)
            if safe_latex and apply(q, e, "haiku_latex_v9"):
                stat["latex_applied"] += 1
            else:
                pending.append(e)
        doubt = (not r.get("valid", True)) or (r.get("solved_option") and r["solved_option"] != q.get("correct_option")) \
            or len(items[r["qid"]]["prior_flags"]) >= 2 or pending
        if doubt:
            it = dict(items[r["qid"]]); it.update(stem=q["stem"], options=q["options"], layer1=dict(
                proposed_edits=pending, valid=r.get("valid"), solved_option=r.get("solved_option"),
                solved_confidence=r.get("solved_confidence"), issue=r.get("issue", "")))
            layer2.append(it)
    (AB / "layer2_batch.json").write_text(json.dumps(layer2, ensure_ascii=False, indent=1), encoding="utf-8")
    stat["to_layer2"] = len(layer2); stat["text_edits_pending"] = sum(len(i["layer1"]["proposed_edits"]) for i in layer2)
else:
    for r in json.load(open(AB / "layer2_review.json", encoding="utf-8")):
        q = qs.get(r["qid"])
        if not q: continue
        for e in r.get("confirmed_edits") or []:
            stat["text_applied" if apply(q, e, "haiku_layer2_confirmed_v9") else "text_not_found"] += 1
        stat["rejected_edits"] += len(r.get("rejected_edits") or [])
        if r.get("verdict") in ("invalid", "unsolvable", "key_doubt", "needs_human"):
            q.setdefault("flags", []).append("V9_" + r["verdict"].upper()); stat["flag_" + r["verdict"]] += 1
            q["v9_note"] = r.get("note", "")
path.write_text(json.dumps(g, ensure_ascii=False, indent=1), encoding="utf-8")
print(dict(stat))
