"""Second-solver worklist for paper 1: key/solver disagreements + NOT_SOLVABLE, each with a 250 dpi crop from the scan."""
import json
from pathlib import Path
import fitz

R = Path(r"D:\MSA\outputs\cet_pcb_2024"); AB = R / "ocr_ab"; CROPS = AB / "crops"; CROPS.mkdir(exist_ok=True)
doc = fitz.open(r"D:\MyDataset\CET PCB 2024 (2).pdf")
v7 = json.load(open(R / "final" / "paper_01_v7.json", encoding="utf-8"))["questions"]
kc = {r["qid"]: r for r in json.load(open(AB / "key_check_paper01.json"))["issues"] if not r["qid"].endswith("Phy_q007")}
work = []
for x in v7:
    ns = "NOT_SOLVABLE" in x.get("flags", []) or x.get("solvable") not in (None, "yes")
    if x["qid"] not in kc and not ns:
        continue
    pg = doc[int(x["page"]) - 1]; W, H = pg.rect.width, pg.rect.height
    b = x.get("bbox") or [0, 0, 999, 999]  # DeepSeek-OCR grounding boxes are normalised to 0-999
    r = fitz.Rect((b[0] - 20) / 999 * W, (b[1] - 15) / 999 * H, (b[2] + 20) / 999 * W, (b[3] + 15) / 999 * H) & pg.rect
    crop = CROPS / f"{x['qid']}.png"
    pg.get_pixmap(dpi=250, clip=r, colorspace=fitz.csGRAY).save(crop)
    k = kc.get(x["qid"], {})
    work.append(dict(qid=x["qid"], crop=str(crop), stem=x["stem"], options=x.get("options"), book_key=x.get("correct_option"),
                     answer_page=k.get("ocr_answer_page"), deepseek=k.get("deepseek_solved"),
                     reason="KEY/SOLVER DISAGREE" if x["qid"] in kc else "NOT_SOLVABLE: " + str(x.get("solvable_reason", ""))[:120]))
json.dump(work, open(AB / "sonnet_worklist.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(len(work), "items:", [w["qid"].split("_P01_")[1] for w in work])
