"""v7 (human-reviewed) -> v8: answer-key check + Sonnet second-solver edits. Conservative rules, every change logged.
- text edit: applied only when `before` occurs exactly once in that field
- key change: only when the book's answer page disagrees with the key AND a solver (Sonnet or DeepSeek) agrees with the answer page
- solver disagreements with the printed key are flagged SOLVER_DISAGREES_WITH_BOOK, never auto-changed
"""
import copy, json
from pathlib import Path

R = Path(r"D:\MSA\outputs\cet_pcb_2024"); AB = R / "ocr_ab"
v7 = json.load(open(R / "final" / "paper_01_v7.json", encoding="utf-8"))
v8 = copy.deepcopy(v7)
qs = {q["qid"]: q for q in v8["questions"]}
review = {r["qid"]: r for r in json.load(open(AB / "sonnet_review.json", encoding="utf-8"))}
keys = {r["qid"]: r for r in json.load(open(AB / "key_check_paper01.json"))["issues"]}
log = []


def get(q, field):
    return q["stem"] if field == "stem" else q["options"][field.split(".")[1]]


def put(q, field, val):
    if field == "stem":
        q["stem"] = val
    else:
        q["options"][field.split(".")[1]] = val


for qid, r in review.items():
    q = qs[qid]
    for e in r.get("edits", []):
        f = e["field"]
        try:
            cur = get(q, f)
        except (KeyError, IndexError):
            log.append((qid, "SKIP", f, "unknown field")); continue
        if not e["before"] or cur.count(e["before"]) != 1:
            log.append((qid, "SKIP", f, f"'before' found {cur.count(e['before']) if e['before'] else 0}x"))
            q.setdefault("flags", []).append("V8_EDIT_NOT_APPLIED"); continue
        put(q, f, cur.replace(e["before"], e["after"]))
        q["edits"].append(dict(field=f, type="repair_v8", basis="claude_sonnet_crop_v8", before=e["before"], after=e["after"]))
        log.append((qid, "EDIT", f, f"{e['before']} -> {e['after']}"))
    k, s = q.get("correct_option"), r.get("solved_option")
    page = keys.get(qid, {}).get("ocr_answer_page")
    ds = keys.get(qid, {}).get("deepseek_solved")
    if page and page != k and page in (s, ds) and not qid.endswith("Phy_q007"):  # Phy 7 options were rewritten by the user
        q["edits"].append(dict(field="answer key", type="repair_key_v8", basis="claude_answer_page_v8", before=k, after=page))
        q["correct_option"] = page
        log.append((qid, "KEY", "correct_option", f"{k} -> {page} (answer page {page}, Sonnet {s}, DeepSeek {ds})"))
    elif s and s != k:
        q.setdefault("flags", []).append("SOLVER_DISAGREES_WITH_BOOK")
        log.append((qid, "FLAG", "correct_option", f"book {k} (answer page {page}), Sonnet {s} ({r.get('solved_confidence')}), DeepSeek {ds}: {r.get('note','')}"))

(R / "final" / "paper_01_v8.json").write_text(json.dumps(v8, ensure_ascii=False, indent=1), encoding="utf-8")
(AB / "v8_changes.json").write_text(json.dumps(log, ensure_ascii=False, indent=1), encoding="utf-8")
from collections import Counter
print(Counter(x[1] for x in log))
for x in log:
    if x[1] != "SKIP": print(x[0].split("_P01_")[1], x[1], x[3][:150])
