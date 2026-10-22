"""Offline end-to-end check of cet_run.py with a stub DeepSeek (no keys, no network): python claude_pipeline/test_cet_run.py"""
import json, sys, tempfile
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent))
import cet_run as R

# --- stub: a 'perfect clean' DeepSeek built from the blocks with the code splitter
def stub(system, user, model):
    if user.startswith("NOTE:"):
        note = user.split("\n")[0][6:]
        return json.dumps({"patches": [{"note": note, "qid": "x", "op": "replace_text", "field": "stem", "before": "mass", "after": "MASS", "confidence": 0.9, "ambiguity": ""}]})
    R.log_usage("deepseek", model, 4000, 3000, 0)
    pg = int(user.split(".")[0].split()[1]); bl = R.page_blocks(pg); qs = []
    for k, b in enumerate(bl):
        if "starts_question" not in b: continue
        txt = b["text"]; j = k + 1
        while j < len(bl) and "starts_question" not in bl[j]: txt += " " + bl[j]["text"]; j += 1
        sp = R.split_options(txt)
        qs.append(dict(subject=None, number=b["starts_question"], source_blocks=list(range(k, j)), stem=sp["stem"], options={L: sp["options"].get(L, "") for L in "ABCD"},
                       figure_ids={}, status="clean", solvable="yes", solvable_reason="", solved_option=None, solved_confidence=0, edits=[], flags=[], confidence=0.9))
    return json.dumps(dict(page=pg, questions=qs, dropped_blocks=[], page_flags=[]))

R.ask_deepseek = stub
import shutil
TMP = Path(tempfile.mkdtemp()); R.OUT = TMP; R.USAGE = TMP / 'usage' / 'usage.jsonl'; (TMP / 'raw').mkdir()  # never write into the real outputs
shutil.copy(R.OCR.parent / 'raw/paper_01_answers.json', TMP / 'raw')
# --- unit checks
assert R.apply_errors({"stem": "a b a", "options": {}}, [{"field": "stem", "before": "a", "after": "x", "confidence": 0.9}])[1][0]["reason"] == "ambiguous"
q = {"stem": "The mass m", "options": {"A": "1", "B": "2", "C": "3", "D": "4"}, "correct_option": "A", "edits": []}
pt = R.Patch(note="n", qid="q", op="replace_text", field="stem", before="mass", after="MASS")
assert R.apply_patch(q, pt) == (True, "") and q["stem"] == "The MASS m"
assert R.apply_patch(q, pt)[0] is False  # before no longer present: rejected, not guessed
assert R.apply_patch(q, R.Patch(note="n", qid="q", op="set_correct_option", field="correct_option", after="Z"))[0] is False
assert R.parse_notes(1, "paper 1 Physics 12 stem: n should be pi\nrandom text")[0][0][0].endswith("P01_Phy_q012")
# --- pipeline on paper 1 pages 1-2 only
R.PAPERS[0] = (1, "2024-04-22", "I", 1, 2)
R.run_deepseek(1); R.run_gate(1)
g = R.load(R.OUT / "checked" / "paper_01.json"); assert g["questions"], "no questions gated"
print("gate ok:", len(g["questions"]), "questions;", g["summary"])

q0 = g["questions"][0]
(R.OUT / "human").mkdir()
note = f"paper 1 Physics {q0['number']} stem: mass should be capitalised\nfree text line\n"
(R.OUT / "human" / "errors_paper01.txt").write_text(note, encoding="utf-8")
R.run_feedback(1)
v7 = R.load(R.OUT / "final" / "paper_01_v7.json"); assert len(v7["questions"]) == len(g["questions"])
R.run_review(1); R.run_reconstruct(1); R.run_build(1)
assert (R.OUT / "reconstructed" / "paper_01_v7.pdf").exists()
R.CTX.update(paper=1, step='S6_claude', ref=g['questions'][0]['qid']); R.log_usage('anthropic', 'claude-haiku-5-5', 2500, 200)
R.run_cost(1)
R.run_preflight(1)
print("feedback, review, build ok")
