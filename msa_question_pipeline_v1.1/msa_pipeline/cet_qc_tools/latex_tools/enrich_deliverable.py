"""Carry the decision fields the build step drops (release, book_key, review_note, review flags) from the newest stage
file into the built deliverables papers/paper_PP_{original,regenerated}.json. Content is checked to be identical first.
python enrich_deliverable.py P"""
import json, re, sys
from pathlib import Path

R = Path(r"D:\MSA\outputs\cet_pcb_2024"); P = int(sys.argv[1]); PP = f"{P:02d}"
stage = sorted((R / "final").glob(f"paper_{PP}_v*.json"), key=lambda f: int(re.search(r"_v(\d+)", f.name)[1]))[-1]
S = {q["qid"]: q for q in json.load(open(stage, encoding="utf-8"))["questions"]}
for kind in ("original", "regenerated"):
    f = R / "papers" / f"paper_{PP}_{kind}.json"
    d = json.load(open(f, encoding="utf-8"))
    assert d["run"].get("stage") == re.search(r"_(v\d+)", stage.name)[1], f"{f.name} was built from {d['run'].get('stage')}, not {stage.name}: rebuild first"
    n = 0
    for q in d["questions"]:
        s = S[q["qid"]]
        assert q["text"] == s["stem"] and q["correct_option"] == s["correct_option"], q["qid"]  # same content, only metadata added
        q["release"] = s.get("release", True)
        q["book_key"] = s.get("book_key", "")
        q["review_note"] = s.get("v11_note") or s.get("v10_note") or s.get("v9_note") or ""
        q["flags"] = list(dict.fromkeys(q.get("flags", []) + [x for x in s.get("flags", []) if isinstance(x, str)]))
        n += (not q["release"]) + bool(q["book_key"]) + bool(q["review_note"])
    d["run"]["decisions_from"] = stage.name
    f.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
    held = [q["qid"].split("_P")[-1] for q in d["questions"] if not q["release"]]
    print(f"{f.name}: {len(d['questions'])} questions | held back {held} | book_key {sum(bool(q['book_key']) for q in d['questions'])} | notes {sum(bool(q['review_note']) for q in d['questions'])}")
