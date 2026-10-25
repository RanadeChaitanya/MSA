"""v10 -> v11 content fixes (then cet_qc.py runs every check on top):
1. picture-only options: crop each option picture (boxes already in q['figures']) at 250 dpi, attach as q['option_images']
2. Bio 16: restore the printed blank
3. flag decisions: book misprint corrected (Chem 40), source defects held back, judgement calls kept with notes
python fix_v11.py <in.json> <out.json>"""
import json, sys
from pathlib import Path
import fitz

R = Path(r"D:\MSA\outputs\cet_pcb_2024"); IMG = R / "images_v11"; IMG.mkdir(exist_ok=True)
doc = fitz.open(r"D:\MyDataset\CET PCB 2024 (2).pdf")
g = json.load(open(sys.argv[1], encoding="utf-8")); Q = {q["qid"]: q for q in g["questions"]}
P = "CET_PCB_2024_P01_"
log = []

# 1 picture-only options
for q in g["questions"]:
    figs = q.get("figures") or {}
    for L in "ABCD":
        if (q["options"].get(L) or "").strip() or not figs.get(L):
            continue
        b = list(figs[L][0]["box"]); pg = doc[int(figs[L][0]["page"]) - 1]; W, H = pg.rect.width, pg.rect.height
        bottom_row = b[3] >= max(figs[x][0]["box"][3] for x in "ABCD" if figs.get(x))
        b = [b[0] - 4, b[1] - 3, b[2] + 4, b[3] + (40 if bottom_row else 4)]  # OCR boxes clip the last row of drawings
        r = fitz.Rect(b[0] / 999 * W, b[1] / 999 * H, b[2] / 999 * W, b[3] / 999 * H) & pg.rect
        f = IMG / f"{q['qid']}_opt{L}.png"
        pix = pg.get_pixmap(dpi=250, clip=r, colorspace=fitz.csGRAY)
        if bottom_row:  # cut at the first white gap (>= 10 px) below the original box height: drops the next question's text
            import numpy as np
            from PIL import Image
            a = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.width)
            ink = (a < 160).sum(1) > 2
            start = int(pix.height * (figs[L][0]["box"][3] - b[1]) / (b[3] - b[1])) - 10
            run = 0
            for y in range(max(start, 0), pix.height):
                run = run + 1 if not ink[y] else 0
                if run >= 10:
                    a = a[:y - 5]; break
            Image.fromarray(a).save(f)
        else:
            pix.save(f)
        q.setdefault("option_images", {})[L] = f"images_v11/{f.name}"
        log.append((q["qid"], f"option {L}: picture attached ({f.name})"))
    if q.get("option_images"):
        q["stem"] = q["stem"].replace("\n[figure]", "").replace("[figure]", "").rstrip()  # the figure IS the options
        q["flags"] = [x for x in q.get("flags", []) if not str(x).startswith(("V9_NEEDS_HUMAN", "OPTION_EMPTY", "OPT_EMPTY"))]

# 2 Bio 16 blank (scan: "Accessory cells of stomata are reservoirs of ______.")
q = Q[P + "Bio_q016"]
if q["stem"].rstrip().endswith("reservoirs of"):
    before = q["stem"]; q["stem"] = q["stem"].rstrip() + " ______."
    q["edits"].append(dict(field="stem", type="repair_v11", basis="scan_crop", before=before, after=q["stem"]))
    log.append((q["qid"], "stem: blank restored"))

# 3 flag decisions
def flag(qid, f, note):
    q = Q[P + qid]; q.setdefault("flags", []).append(f); q["v11_note"] = note; log.append((q["qid"], f"{f}: {note}"))

q = Q[P + "Che_q040"]
if q["correct_option"] == "D":
    q["book_key"] = "D"; q["correct_option"] = "C"
    q["edits"].append(dict(field="answer key", type="repair_key_v11", basis="science+3_solvers", before="D", after="C"))
    flag("Che_q040", "BOOK_KEY_MISPRINT_CORRECTED", "Three bidentate oxalates give coordination number 6 (C); Sonnet 0.97, DeepSeek and Haiku all C; book prints D")
for k, n in (("Phy_q003", "Book gives 3 coordinates for 4 masses; no option can be verified"),
             ("Phy_q004", "Premise impossible: the stated result needs K < 1 for a dielectric"),
             ("Bio_q089", "No option has capping first, which is the accepted order")):
    Q[P + k]["release"] = False; flag(k, "SOURCE_DEFECT_HOLD", n)
for k in ("Bio_q019", "Phy_q032", "Che_q021", "Bio_q052", "Bio_q053", "Bio_q055", "Bio_q066", "Bio_q092"):
    flag(k, "REVIEW_JUDGEMENT", Q[P + k].get("v9_note") or "solvers disagree with the printed key; textbook convention decides")
# 4 rendered-vs-scan check (Haiku, confirmed by eye on the scan)
def fix(qid, field, before, after, basis="rendered_vs_scan_v11"):
    q = Q[P + qid]; cur = q["stem"] if field == "stem" else q["options"][field[-1]]
    if cur.count(before) != 1:
        log.append((q["qid"], f"SKIP {field}: text not found")); return
    new = cur.replace(before, after)
    if field == "stem": q["stem"] = new
    else: q["options"][field[-1]] = new
    q["edits"].append(dict(field=field, type="repair_v11", basis=basis, before=before, after=after)); log.append((q["qid"], f"{field}: {before} -> {after}"))

fix("Phy_q040", "options.C", r"\sqrt{\frac{A^3}{2B\sqrt{\pi}}}", r"\sqrt{A^3}")      # scan: (2pi/mu0) sqrt(A^3)
fix("Che_q008", "stem", r"\xrightarrow{\ce{Mg}}", r"\xrightarrow[\text{Dry ether}]{\ce{Mg}}")
fix("Che_q008", "stem", r"\xrightarrow{\ce{HOH}}", r"\xrightarrow[\text{Dry ether}]{\ce{HOH}}")
fix("Che_q008", "stem", r"\xrightarrow{\ce{Br2}}", r"\xrightarrow[\text{UV light}]{\ce{Br2}}")
flag("Che_q024", "BOOK_MISPRINT_KEPT_CORRECT", "Book prints NO3 -> NH4+ in option D; kept as NO3^- (nitrate), the only chemically meaningful reading")
flag("Phy_q037", "BOOK_MISPRINT_KEPT_CORRECT", "Book prints the unit as degC; a linear expansion coefficient is per degC, so kept as degC^-1")
json.dump(g, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for x in log: print(" ", x[0].split("_P01_")[-1], x[1][:120])
