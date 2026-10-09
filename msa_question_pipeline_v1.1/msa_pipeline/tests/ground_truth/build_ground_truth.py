"""Writes tests/ground_truth/<sample>.json.

Ground truth was read by eye from 110-dpi renders of the ORIGINAL PDF pages (not from pipeline output).
Per question:
  page, number (null when the PDF prints none), order (1-based position among questions starting on that page),
  complete    : all options are on this page (false = continues on the next page; excluded from option metrics)
  stem_images : number of separate pictures belonging to the stem (diagrams, circuits, schemes)
  opt_images  : {label: number of pictures} for options whose content is a picture (absent = text only)
  options     : expected option labels
  texts       : expected option texts for unambiguous plain-text options (subset, used for text accuracy)
  table       : the stem contains a table
Pictures of other questions that only CONTINUE on this page are listed in `foreign_images` (must not be
attached to any question of this page).
"""
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ABCD = ["A", "B", "C", "D"]


def q(page, number, order, complete=True, stem_images=0, opt_images=None, texts=None, table=False, note=""):
    d = {"page": page, "number": number, "order": order, "complete": complete, "stem_images": stem_images,
         "opt_images": opt_images or {}, "options": ABCD if complete else [], "table": table}
    if texts:
        d["texts"] = dict(zip(ABCD, texts))
    if note:
        d["note"] = note
    return d


def seq(page, first, last, special=None, start_order=1):
    out = []
    for k, n in enumerate(range(first, last + 1), start_order):
        out.append(q(page, n, k, **(special or {}).get(n, {})))
    return out


GT = {
    "alcohols": {"foreign_images": {"6": 4}, "questions":
        seq(6, 59, 68, {63: dict(opt_images={"A": 1, "B": 1, "C": 1}),
                          65: dict(opt_images={L: 1 for L in ABCD}),
                          66: dict(texts=["Alcohols", "Ethers", "Ethers and alcohols", "Alcohols and acetone"]),
                          68: dict(complete=False, table=True)})
        + seq(42, 517, 530, {517: dict(texts=["Methane thiol", "Ethane thiol", "Isobutyl thiol", "Propyl thiol"]),
                               525: dict(texts=["Acetone", "Ethanol", "Glycerol", "Glycol"]),
                               528: dict(stem_images=1, opt_images={L: 1 for L in ABCD}),
                               530: dict(complete=False)})},
    "chem2020": {"questions":
        seq(5, 1, 10, {2: dict(texts=["0.9, 0.1", "0.78, 0.22", "0.1, 0.9", "0.22, 0.78"]),
                         3: dict(texts=["0.10 M", "0.02 M", "0.01 M", "0.001 M"]),
                         4: dict(texts=["36 g", "72 g", "162 g", "144 g"]),
                         5: dict(texts=["13.67 %", "7.0 %", "8.5 %", "10.0"]),
                         6: dict(texts=["15.8 g", "19.5 g", "22.4 g", "10 g"]),
                         7: dict(texts=["1.0 M", "0.1 M", "0.001 M", "0.01 M"]),
                         9: dict(texts=["0.75", "1.0", "0.50", "0.25"]),
                         10: dict(texts=["6.25 %", "35 %", "12.50 %", "25 %"])})
        + seq(55, 27, 37, {28: dict(texts=["3 and 2", "3 and 1", "2 and 2", "4 and 2"]),
                             29: dict(texts=["100 %", "12.5 %", "25 %", "75 %"]),
                             31: dict(texts=["42.4 sec", "41.4 sec", "43.4 sec", "44.4 sec"]),
                             32: dict(note="options are at the top of the right column"),
                             33: dict(texts=["transition state of 1st step", "formation of carbonation",
                                             "transition state of 2nd step", "products"]),
                             34: dict(texts=["62.12 min", "38.31 min", "12.77 min", "48.12 min"]),
                             35: dict(texts=["1", "4", "3", "2"])})},
    "pcm2020": {"questions":
        seq(7, 1, 8, {2: dict(texts=["480 N", "240 N", "360 N", "120 N"]),
                        5: dict(texts=["63 J", "42 J", "31.5 J", "21 J"]),
                        7: dict(stem_images=1),
                        8: dict(texts=["0.8 m", "0.6 m", "0.2 m", "0.4 m"])})
        + seq(8, 9, 17, {10: dict(texts=["0.4", "0.8", "0.5", "0.6"]),
                           11: dict(texts=["12 m/s", "3 m/s", "6 m/s", "18 m/s"]),
                           15: dict(stem_images=1)})},
    "chem2021": {"questions":
        seq(3, 1, 4, {1: dict(texts=["14", "12", "7", "10"]),
                        4: dict(texts=["Hexagonal cub", "Simple cubic", "FCC", "BCC"])})
        + seq(4, 5, 8, {6: dict(note="labels laid out A C / B D"),
                          8: dict(texts=["Ni", "Zn", "Cu", "Sc"])})},
    "chem2022": {"questions":
        seq(3, 1, 5, {3: dict(texts=["30 %", "26 %", "47.64 %", "32 %"])})
        + seq(4, 6, 10, {7: dict(texts=["74 %", "52.36 %", "68 %", "80 %"]),
                           9: dict(texts=["6", "8", "4", "12"])})},
    "maths2021": {"questions":
        seq(3, 1, 4, {2: dict(texts=["T", "F", "p V q", "p Λ q"])})
        + seq(4, 5, 8, {7: dict(texts=["T , F", "F , F", "F , T", "T , T"])})},
    "maths2022": {"questions":
        seq(3, 1, 5, {1: dict(texts=["T , T", "F , T", "T , F", "F , F"]),
                        2: dict(texts=["I do not study and I do not fail.", "I study and I fail.",
                                       "I do not study and I fail.", "I study and I do not fail."]),
                        3: dict(note="stem lists statements a)-d); the options A-D are on the right")})
        + seq(4, 6, 10)},
    "physics2021": {"questions":
        seq(3, 1, 5, {1: dict(texts=["1 N", "8 N", "4 N", "2 N"]),
                        3: dict(note="stem text cut at the right edge in the source"),
                        4: dict(note="stem text cut at the right edge in the source")})
        + seq(73, 18, 19, {18: dict(stem_images=1, texts=["1.6 A", "3.0 A", "2.4 A", "2.0 A"],
                                      note="option labels almost invisible"),
                             19: dict(stem_images=1, texts=["3.4 A", "2.5 A", "3.0 A", "1.5 A"],
                                      note="option labels almost invisible")})},
    "physics2022": {"questions":
        seq(3, 1, 4)
        + seq(83, 19, 21, {19: dict(texts=["Decrease applied e.m.f.", "Increase resistance in main circuit.",
                                             "Attach resistance in series with a cell.",
                                             "Decrease resistance in main circuit."]),
                             21: dict(texts=["80 cm", "60 cm", "40 cm", "50 cm"])})},
    "msa2023phy": {"questions":
        seq(1, 1, 9, {6: dict(stem_images=1), 7: dict(stem_images=1, texts=["1:2", "1:1", "2:3", "2:5"]),
                        8: dict(note="printed 'Correct Option: 13MR2/32' = A")})
        + seq(2, 10, 19, {10: dict(texts=["1 : 2", "√2 : 1", "2 : 1", "1 : √2"]),
                            11: dict(stem_images=1, texts=["9", "12", "15", "17"])})},
    "chemcet23": {"questions": [
        q(3, None, 1, table=True, texts=["A", "B", "C", "D"]),
        q(3, None, 2),
        q(3, None, 3, texts=["Glass", "Plastic", "Rubber", "Diamond"]),
        q(4, None, 1, texts=["ionic solid", "covalent network solid", "molecular solid", "metallic solid"]),
        q(4, None, 2),
        q(4, None, 3, texts=["473 · 2 pm", "334 · 6 pm", "386 · 3 pm", "836 · 5 pm"])]},
}

if __name__ == "__main__":
    for sid, g in GT.items():
        g = {"id": sid, "method": "manual reading of 110-dpi renders of the original PDF pages", **g}
        (HERE / f"{sid}.json").write_text(json.dumps(g, indent=1, ensure_ascii=False), encoding="utf-8")
    print(len(GT), "files,", sum(len(g["questions"]) for g in GT.values()), "questions")
