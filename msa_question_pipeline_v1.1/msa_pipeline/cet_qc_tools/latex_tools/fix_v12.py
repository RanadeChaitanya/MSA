"""v11 -> v12: fixes from the rendered-vs-scan image check on the remaining 150 questions (each confirmed against the scan).
python fix_v12.py <in.json> <out.json>"""
import json, re, sys

g = json.load(open(sys.argv[1], encoding="utf-8")); Q = {q["qid"]: q for q in g["questions"]}
P, B = "CET_PCB_2024_P01_", "______"
log = []


def edit(qid, field, before, after):
    q = Q[P + qid]; cur = q["stem"] if field == "stem" else q["options"][field[-1]]
    if cur.count(before) != 1:
        log.append((qid, f"SKIP {field}: '{before[:40]}' found {cur.count(before)}x")); return
    new = cur.replace(before, after)
    if field == "stem": q["stem"] = new
    else: q["options"][field[-1]] = new
    q["edits"].append(dict(field=field, type="repair_v12", basis="rendered_vs_scan_v12", before=before, after=after))
    log.append((qid, f"{field}: ...{before[-30:]} -> ...{after[-36:]}"))


# blanks at the end of the stem (scan: '... is ______.')
for n in (1, 2, 18, 38, 42, 44, 45, 46, 47, 66, 69, 72, 73, 74, 76, 78, 84, 90, 93, 99):
    s = Q[P + f"Bio_q{n:03d}"]["stem"]
    edit(f"Bio_q{n:03d}", "stem", s, s.rstrip().rstrip(".") + f" {B}.")
s = Q[P + "Bio_q070"]["stem"]; edit("Bio_q070", "stem", s, s.rstrip() + f" {B}")          # scan has no full stop here
# blanks inside the stem
edit("Bio_q007", "stem", "with the help of enzyme.", f"with the help of {B} enzyme.")
edit("Bio_q013", "stem", "show type of selection respectively.", f"show {B} type of selection respectively.")
edit("Bio_q015", "stem", "arrested at before fertilization.", f"arrested at {B} before fertilization.")
edit("Bio_q041", "stem", "The gene from Bacillus thuringiensis", f"The {B} gene from Bacillus thuringiensis")
edit("Bio_q048", "stem", "::PCT: epithelium.", f" :: PCT : {B} epithelium.")
# analogy notation and spelling as printed
edit("Bio_q050", "stem", "Marsupials: (i): monotremes: (ii)", "Marsupials : (i) :: monotremes : (ii)")
edit("Bio_q051", "options.B", "soybean", "soyabean")
edit("Bio_q051", "options.C", "soybean", "soyabean")
json.dump(g, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(len([x for x in log if "SKIP" not in x[1]]), "edits;", sum("SKIP" in x[1] for x in log), "skipped")
for x in log: print(" ", x[0], x[1][:110])
