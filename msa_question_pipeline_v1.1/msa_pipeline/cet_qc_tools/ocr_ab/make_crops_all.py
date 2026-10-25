"""250 dpi crop of every paper-1 question: from its bbox top to the next question's top in the same column (keeps options/figures).
Writes ocr_ab/crops_all/<qid>.png and ocr_ab/haiku_batch_<k>.json (4 batches of 50)."""
import json, sys
from pathlib import Path
import fitz

R = Path(r"D:\MSA\outputs\cet_pcb_2024"); AB = R / "ocr_ab"; OUT = AB / "crops_all"; OUT.mkdir(exist_ok=True)
doc = fitz.open(r"D:\MyDataset\CET PCB 2024 (2).pdf")
g = json.load(open(Path(sys.argv[1]) if len(sys.argv) > 1 else R / "final" / "paper_01_v9.json", encoding="utf-8"))["questions"]
col = lambda b: 0 if b[0] < 480 else 1                      # bboxes are DeepSeek-OCR 0-999 page coordinates
by = {}
for q in g:
    if q.get("bbox"):
        by.setdefault((int(q["page"]), col(q["bbox"])), []).append(q)
for k in by:
    by[k].sort(key=lambda q: q["bbox"][1])
items = []
for q in g:
    pg = doc[int(q["page"]) - 1]; W, H = pg.rect.width, pg.rect.height
    b = q.get("bbox") or [0, 0, 999, 999]
    sib = by.get((int(q["page"]), col(b)), [])
    nxt = [s["bbox"][1] for s in sib if s["bbox"][1] > b[1] + 5]
    bottom = min(nxt) - 3 if nxt else min(b[3] + 180, 975)
    bottom = max(bottom, b[3] + 8)
    r = fitz.Rect((b[0] - 25) / 999 * W, (b[1] - 12) / 999 * H, (b[2] + 25) / 999 * W, bottom / 999 * H) & pg.rect
    f = OUT / f"{q['qid']}.png"
    pg.get_pixmap(dpi=250, clip=r, colorspace=fitz.csGRAY).save(f)
    crop2 = None
    if not nxt:  # last question of its column: options may continue at the top of the next column / next page
        p2, c2 = (int(q["page"]), 1) if col(b) == 0 else (int(q["page"]) + 1, 0)
        first = [s["bbox"][1] for s in by.get((p2, c2), [])]
        if p2 <= len(doc):
            pg2 = doc[p2 - 1]; x0, x1 = (0, 505) if c2 == 0 else (475, 999)
            top2 = min(first) - 3 if first else 300
            if top2 > 60:
                r2 = fitz.Rect(x0 / 999 * W, 30 / 999 * H, x1 / 999 * W, top2 / 999 * H)
                crop2 = str(OUT / f"{q['qid']}_cont.png")
                pg2.get_pixmap(dpi=250, clip=r2, colorspace=fitz.csGRAY).save(crop2)
    flags = sorted({x if isinstance(x, str) else x.get("code", "") for x in q.get("flags", []) + q.get("reasons", [])} - {""})
    items.append(dict(qid=q["qid"], crop=str(f), crop_continued=crop2, subject=q["subject"], stem=q["stem"], options=q["options"],
                      book_key=q.get("correct_option"), prior_flags=flags))
for k in range(4):
    (AB / f"haiku_batch_{k}.json").write_text(json.dumps(items[k * 50:(k + 1) * 50], ensure_ascii=False, indent=1), encoding="utf-8")
print(len(items), "crops; heavy-flagged (>=2 prior flags):", sum(len(i["prior_flags"]) >= 2 for i in items))
