"""CET PCB 2024 pipeline runner: S3 DeepSeek -> S4 gates -> S6 Claude detector/applier -> S8 build -> S9 review sheet -> S10 human-note patches.
  python claude_pipeline/cet_run.py deepseek PAPER            # needs DEEPSEEK_API_KEY; one call per OCR page, validated, retried once, raw saved
  python claude_pipeline/cet_run.py gate PAPER                # v4: word guard, repair gate, answer-key check, counts, watchlist, figures
  python claude_pipeline/cet_run.py claude PAPER [--all]      # v5: Haiku detector + code applier on needs_claude items; needs ANTHROPIC_API_KEY
  python claude_pipeline/cet_run.py review PAPER              # S9 HTML review sheet (high flags first)
  python claude_pipeline/cet_run.py feedback PAPER             # S10 reads human/errors_paperPP.txt, DeepSeek -> patches, code applies + verifies
  python claude_pipeline/cet_run.py build PAPER               # S8 DocumentOut json + image zips from the newest stage file
Keys come only from the environment. Every stage writes a new file and a timestamped copy in backup/.
Files per paper PP: deepseek/page_NNN.json, checked/paper_PP.json (v4), checked/paper_PP_v5.json, final/paper_PP_v7.json (after patches).
"""
import base64, json, os, re, shutil, sys, time, zipfile
from pathlib import Path
import fitz
from pydantic import BaseModel

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE)); sys.path.insert(0, str(HERE.parent)); sys.path.insert(0, str(Path.home() / ".claude/skills/pdf-extract"))
from cet_tools import OUT, PAPERS, PDF, DOC, SUBJ, EXPECT, CetDocument, CetQuestion  # noqa: E402
from deepseek_schema import PageResult  # noqa: E402
from cet_options import split_options  # noqa: E402
from cet_figures import split_box, grow  # noqa: E402
import confusion_watch, cet_answers  # noqa: E402
from pipeline.models import AssetRef, OptionOut  # noqa: E402
import pdfkit  # noqa: E402

OCR = OUT / "ocr"
PAGES = OUT / "pages"
HEAD = re.compile(r"^(\w+)\[\[(\d+), (\d+), (\d+), (\d+)\]\]", re.M)
SUBJECTS = ["Physics", "Chemistry", "Biology"]
# answer-book pages per paper: paper 1 = 220-237 (measured); others are given with --answers A-B and stored in answer_pages.json
ANS_PAGES = OUT / "answer_pages.json"


def paper_row(p):
    return next(r for r in PAPERS if r[0] == p)


def stamp(path):
    """timestamped copy of a stage output before it is replaced"""
    if Path(path).exists():
        bk = OUT / "backup" / "stages"; bk.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, bk / f"{time.strftime('%Y%m%d_%H%M%S')}_{Path(path).name}")


def save(path, obj):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True); stamp(path)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def load(path, default=None):
    path = Path(path)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def latest(p):
    """newest stage file of a paper: (path, stage name)"""
    for sub, name, st in (("final", f"paper_{p:02d}_v7.json", "v7"), ("checked", f"paper_{p:02d}_v5.json", "v5"), ("checked", f"paper_{p:02d}.json", "v4")):
        if (OUT / sub / name).exists():
            return OUT / sub / name, st
    sys.exit(f"paper {p}: nothing built yet; run deepseek and gate first")


def qid_of(p, subject, number):
    return f"{DOC}_P{p:02d}_{SUBJ[subject]}_q{number:03d}"


# ---------------------------------------------------------------- S3 DeepSeek
def system_prompt():
    t = (HERE / "deepseek_prompt_FINAL.md").read_text(encoding="utf-8")
    return t.split("## SYSTEM PROMPT (copy from here)")[1].split("## USER MESSAGE TEMPLATE")[0].rsplit("-----", 1)[0].strip()


def page_blocks(pg):
    """OCR blocks of a page for the model (same shape as make_deepseek_input.py)"""
    txt = (OCR / f"page_{pg:03d}.txt").read_text(encoding="utf-8")
    hs = list(HEAD.finditer(txt)); blocks, img, prev = [], 0, 0
    for i, h in enumerate(hs):
        body = txt[h.end():hs[i + 1].start() if i + 1 < len(hs) else len(txt)].strip()
        b = {"i": i, "kind": h.group(1), "bbox": [int(v) for v in h.groups()[1:]]}
        if h.group(1) == "image":
            img += 1; b["id"] = f"p{pg}_img{img}"; b["text"] = ""
        else:
            m = re.match(r"^(\d{1,3})\s*[.)]\s+(.*)", body, re.S)
            if m and h.group(1) in ("text", "sub_title", "title") and int(m[1]) <= 100 and (int(m[1]) > prev or int(m[1]) == 1):
                b["starts_question"] = int(m[1]); prev = int(m[1]); body = m[2]
            b["text"] = body
        blocks.append(b)
    return blocks


def page_message(pg, blocks):
    return (f"Page {pg}. Repair this OCR page and return the JSON described in the system prompt.\n"
            "Each question block starts with \"starts_question\": N and its text already has the leading number removed.\n"
            f"OCR blocks (JSON):\n{json.dumps(blocks, ensure_ascii=False)}")


CTX = {"paper": 0, "step": "", "ref": ""}  # who is calling: set by each stage before its API calls
USAGE = OUT / "usage" / "usage.jsonl"


def log_usage(api, model, tin, tout, cached=0):
    """one line per API call: tokens only (cost is computed later from prices.json, so a price change never rewrites history)"""
    USAGE.parent.mkdir(parents=True, exist_ok=True)
    with USAGE.open("a", encoding="utf-8") as f:
        f.write(json.dumps(dict(t=time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()), api=api, model=model, tin=tin, tout=tout, cached=cached, **CTX)) + "\n")


def ask_deepseek(system, user, model):
    """one DeepSeek call in JSON mode. model = 'flash' (no thinking) or 'flash-think' (thinking on; billed as output tokens). Tests replace this function."""
    from openai import OpenAI
    key = os.environ.get("DEEPSEEK_API_KEY") or sys.exit("set DEEPSEEK_API_KEY")
    think = model.endswith("-think")
    r = OpenAI(api_key=key, base_url="https://api.deepseek.com", timeout=900).chat.completions.create(
        model="deepseek-flash", response_format={"type": "json_object"}, max_tokens=32000,
        messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
        extra_body={"thinking": {"type": "enabled" if think else "disabled"}}, **({"reasoning_effort": "high"} if think else {"temperature": 0}))
    u = r.usage
    log_usage("deepseek", model, u.prompt_tokens, u.completion_tokens, getattr(u, "prompt_cache_hit_tokens", 0) or 0)
    return r.choices[0].message.content


def ask_validated(system, user, model, schema):
    """call, validate with the pydantic schema, retry once with the error text; returns (obj, raw, error)"""
    raw = ask_deepseek(system, user, model)
    for attempt in (0, 1):
        try:
            return schema.model_validate_json(raw), raw, ""
        except Exception as e:
            err = str(e)[:1500]
            if attempt == 0:
                raw = ask_deepseek(system, user + f"\n\nYour previous reply failed validation:\n{err}\nReturn the corrected JSON only.", model)
    return None, raw, err


def run_deepseek(p):
    sysmsg = system_prompt(); _, _, _, p0, p1 = paper_row(p)
    d = OUT / "deepseek"; d.mkdir(exist_ok=True)
    for pg in range(p0, p1 + 1):
        f = d / f"page_{pg:03d}.json"
        if f.exists() or not (OCR / f"page_{pg:03d}.txt").exists():
            continue
        blocks = page_blocks(pg)
        model = "flash-think" if re.search(r"\\\(|\$|<table|\\frac", json.dumps(blocks)) else "flash"
        CTX.update(paper=p, step="S3_deepseek", ref=f"page_{pg:03d}")
        res, raw, err = ask_validated(sysmsg, page_message(pg, blocks), model, PageResult)
        (d / f"page_{pg:03d}_raw.txt").write_text(raw, encoding="utf-8")
        if res:
            f.write_text(res.model_dump_json(indent=1), encoding="utf-8")
        else:
            (d / f"page_{pg:03d}.failed").write_text(err, encoding="utf-8")
        print(pg, model, "ok" if res else "FAILED " + err[:80])


# ---------------------------------------------------------------- S4 gates
def words(t):
    t = re.sub(r"\\(?:mathrm|mathbf|text|bf|rm|left|right|displaystyle|quad|qquad)(?![a-z])", "", t or "")
    return re.sub(r"[^0-9A-Za-z]+", "", t).lower()


def undo(q, orig):
    """apply each edit backwards (after -> before) to the returned text; returns {field: text}"""
    f = {"stem": q["stem"], **{f"options.{k}": v for k, v in q["options"].items()}}
    for e in reversed(q["edits"]):
        if e["field"] == "options" and e["type"] == "split_options":
            continue
        k = e["field"]
        if k in f and e["after"] and e["after"] in f[k]:
            f[k] = f[k].replace(e["after"], e["before"], 1)
    return f


def word_guard(q, ocr_text):
    """declared edits undone must reproduce the OCR words; True when no words were invented or dropped"""
    f = undo(q, ocr_text)
    got = words(f["stem"] + "".join(f[f"options.{L}"] for L in "ABCD" if f"options.{L}" in f))
    want = words(re.sub(r"\(\s*(?:\\mathrm\{)?\s*[A-D]\s*\}?\s*\)|\\mathrm\{\s*\(\s*[A-D]\s*\)\s*\}", " ", ocr_text))  # option labels are not words
    if got == want:
        return True
    return want.replace("figure", "") == got.replace("figure", "") or (len(got) and abs(len(got) - len(want)) / len(want) < 0.01 and sum(a != b for a, b in zip(got, want)) <= 2)


DATE_HEAD = re.compile(r"^#+[\s\\(]*(\d{1,2})\D.*?April.*?Shift\s*-\s*(I{1,2})\b", re.M)


def answer_pages(p):
    """pages of paper P in the answer book, found from the '22nd April (Shift - I)' heading that opens each paper; None until the next paper's start (or page 427) is OCR'd"""
    starts = {}
    for pg in range(220, 428):
        f = OCR / f"page_{pg:03d}.txt"
        m = DATE_HEAD.search(f.read_text(encoding="utf-8")) if f.exists() else None
        if m:
            starts[(int(m[1]), m[2])] = pg
    day, sh = int(paper_row(p)[1][-2:]), paper_row(p)[2]
    if (day, sh) not in starts:
        return None
    nxt = [v for v in starts.values() if v > starts[(day, sh)]]
    end = min(nxt) - 1 if nxt else 427
    return (starts[(day, sh)], end) if (nxt or (OCR / "page_427.txt").exists()) else None


def answer_key(p):
    """({(subject, n): letter}, {(subject, n): 'Std.12 | Ch-2'}); a hand-checked raw/paper_PP_answers.json wins over the OCR parse"""
    f = OUT / "raw" / f"paper_{p:02d}_answers.json"
    if f.exists():
        a = load(f)
        return {(s, int(n)): v for s, d in a["answers"].items() for n, v in d.items()}, {(s, int(n)): v for s, d in a.get("chapters", {}).items() for n, v in d.items()}
    pages = answer_pages(p)
    if not pages:
        return {}, {}
    tags = {}
    ans, an = cet_answers.parse_pages(OCR, range(pages[0], pages[1] + 1), tags)
    return ans, tags


def assign_figures(q, blocks_by_i, pg_png):
    """image blocks after the question's last block and before the next question go to the question or, for picture options, to A-D"""
    imgs = q.pop("_images")
    empties = [L for L in "ABCD" if not q["options"].get(L, "").strip()]
    out = {"question": [], "A": [], "B": [], "C": [], "D": []}; flags = []
    if not imgs:
        return out, flags
    if len(empties) >= 2 and len(imgs) == 1:
        sp = split_box(pg_png(imgs[0]["page"]), imgs[0]["box"])
        if sp and sp["layout"]:
            for L, b in sp["cells"].items():
                out[L].append(dict(page=imgs[0]["page"], box=b, id=imgs[0]["id"] + L))
        else:
            out["question"].append(imgs[0]); flags.append(("FIGURE_SPLIT_FAILED", "merged option picture could not be split", "high"))
    elif len(empties) == len(imgs) >= 2:
        for L, im in zip(empties, imgs):
            out[L].append(im)
    else:
        out["question"] += imgs
        if empties:
            flags.append(("FIGURE_OPTION_MISMATCH", f"{len(imgs)} picture(s) for {len(empties)} empty option(s)", "high"))
    return out, flags


def run_gate(p):
    _, date, shift, p0, p1 = paper_row(p)
    key, chap = answer_key(p)
    res, queue = [], []
    png = lambda pg: PAGES / f"page_{pg:03d}.png"
    for pg in range(p0, p1 + 1):
        pr = load(OUT / "deepseek" / f"page_{pg:03d}.json")
        if pr is None:
            queue.append(dict(page=pg, reason="no valid DeepSeek result")); continue
        blocks = page_blocks(pg)
        qs = pr["questions"]
        for k, q in enumerate(qs):
            res.append(dict(q, page=pg, _blocks={b["i"]: b for b in blocks}, _next=min([x["source_blocks"][0] for x in qs[k + 1:] if x["source_blocks"]] or [10**6])))
    # subject assignment: DeepSeek value, else numbering reset (n==1 after > 5 starts the next subject)
    si, prev = 0, 0
    for q in res:
        if q["subject"]:
            si = SUBJECTS.index(q["subject"])
        elif q["number"] == 1 and prev > 5:
            si = min(si + 1, 2)
        q["subject"] = SUBJECTS[si]; prev = q["number"]
    final, counts = [], {s: [] for s in SUBJECTS}
    for q in res:
        blocks = q.pop("_blocks"); nxt = q.pop("_next"); pg = q["page"]
        first = min(q["source_blocks"] or [10**6])  # image blocks inside the question's own blocks or between it and the next question
        q["_images"] = [dict(page=pg, box=b["bbox"], id=b["id"]) for i, b in blocks.items() if b["kind"] == "image" and first <= i < nxt]
        bx = [blocks[i]["bbox"] for i in q["source_blocks"] if i in blocks] + [im["box"] for im in q["_images"]]
        q["bbox"] = [min(b[0] for b in bx), min(b[1] for b in bx), max(b[2] for b in bx), max(b[3] for b in bx)] if bx else [0, 0, 1000, 1000]  # permille of the first page
        figs, fl = assign_figures(q, blocks, png)
        q["figures"] = figs
        reasons = [(c, d, s) for c, d, s in fl]
        ocr_text = " ".join(blocks[i]["text"] for i in q["source_blocks"] if i in blocks)
        # repair gate
        for e in q["edits"]:
            if e["type"].startswith("repair"):
                ok = e["evidence"] and e["risk"] in ("low", "medium") and all(ev["kind"] == "internal" for ev in e["evidence"]) and q["solvable"] == "yes"
                if not ok:
                    reasons.append(("REPAIR_NEEDS_CHECK", f"{e['type']} {e['before']!r}->{e['after']!r} risk {e['risk']}", "high" if e["risk"] == "high" else "medium"))
        if not word_guard(q, ocr_text):
            reasons.append(("WORD_GUARD", "returned words differ from the OCR words beyond the declared edits", "high"))
        if q["solvable"] != "yes":
            reasons.append(("NOT_SOLVABLE", q["solvable_reason"], "high"))
        for f in q["flags"]:
            if f["severity"] == "high" or f["needs_image"] or q["status"] == "flag":
                reasons.append((f["code"], f["detail"], f["severity"]))
        k = key.get((q["subject"], q["number"]))
        q["correct_option"] = None if k in (None, "*") else k
        if k is None:
            reasons.append(("NO_ANSWER_KEY", "no key letter for this question", "high"))
        elif k not in ("*",) and q["solved_option"] and q["solved_option"] != k:
            reasons.append(("ANSWER_MISMATCH", f"DeepSeek solved {q['solved_option']}, book key {k}: check the question AND the key", "high"))
        o = q["options"]
        for L in "ABCD":
            if not o.get(L, "").strip() and not q["figures"][L]:
                reasons.append(("OPT_EMPTY", f"option {L} empty and no picture", "high"))
        # code watchlist on the raw OCR; a hit nobody repaired or flagged goes to Claude
        for code, why in confusion_watch.check(ocr_text):
            if not any(code.split("_")[0].lower() in (f["code"] + f["detail"]).lower() for f in q["flags"]) and not q["edits"]:
                reasons.append(("WATCH_" + code, why, "medium"))
        q["qid"] = qid_of(p, q["subject"], q["number"])
        q["reasons"] = [dict(code=c, detail=d, severity=s) for c, d, s in reasons]
        q["verdict"] = "needs_claude" if any(s in ("high", "medium") for _, _, s in reasons) else "ok"
        q["chapter_tag"] = chap.get((q["subject"], q["number"]), "")
        q["paper"], q["exam_date"], q["shift"] = p, date, shift
        counts[q["subject"]].append(q["number"]); final.append(q)
    gaps = {s: sorted(set(range(1, n + 1)) - set(counts[s])) for s, n in EXPECT.items()}
    dups = {s: sorted({x for x in counts[s] if counts[s].count(x) > 1}) for s in SUBJECTS}
    gate = dict(paper=p, questions=final, page_queue=queue, missing=gaps, duplicates=dups,
                summary=dict(total=len(final), needs_claude=sum(q["verdict"] == "needs_claude" for q in final), expected=sum(EXPECT.values())))
    save(OUT / "checked" / f"paper_{p:02d}.json", gate)
    print(p, gate["summary"], "missing", {s: v for s, v in gaps.items() if v}, "dup", {s: v for s, v in dups.items() if v}, "pages without result", [x["page"] for x in queue])


# ---------------------------------------------------------------- S6 Claude detector + applier
DETECT_RULES = """You are the DETECTOR. You see the page crop of ONE question and its current text. List errors only; never write a corrected question.
Assume the text has 0-3 errors. Report an error only when the crop shows it or a notation rule is certain. Keep the book's own typos; no style fixes (spacing, \\mathrm, \\left/\\right).
Notation: units roman (`$1\\,\\mathrm{g}$` never "lg"), variables italic with subscripts (`$T_1$`), vectors with hats, Greek as Greek (rho not p, pi not n, mu_s, theta, gamma in pV^gamma),
chemistry formulas with subscripts (PCl_5) and charges as superscripts, lists with commas, a leading + or - before an option fraction.
Return ONE JSON object: {"errors":[{"field":"stem"|"options.A"|"options.B"|"options.C"|"options.D","before":"EXACT substring that occurs once in that field","after":"what the page shows, LaTeX in $...$",
"kind":"sign|subscript|superscript|greek|hat|comma|digit|letter|word|unit|option_text|other","evidence":"crop|internal|notation","confidence":0.0-1.0}],"needs_human":false,"note":""}
No errors: {"errors":[],"needs_human":false,"note":""}. Also check the stated flags listed in the message first."""


def crop_png(q, dpi=170, pad=25):
    """tight page crop (base64 png) of one question from the source PDF: its text and figure boxes plus a margin"""
    page = fitz.open(PDF)[q["page"] - 1]; r = page.rect; b = q.get("bbox") or [0, 0, 1000, 1000]
    clip = fitz.Rect(max(b[0] - pad, 0) / 1000 * r.width, max(b[1] - pad, 0) / 1000 * r.height, min(b[2] + pad, 1000) / 1000 * r.width, min(b[3] + pad, 1000) / 1000 * r.height)
    return base64.b64encode(page.get_pixmap(dpi=dpi, clip=clip, colorspace=fitz.csGRAY).tobytes("png")).decode(), clip


def ask_claude(system, text, png_b64, model):
    import anthropic
    key = os.environ.get("ANTHROPIC_API_KEY") or sys.exit("set ANTHROPIC_API_KEY")
    m = anthropic.Anthropic(api_key=key).messages.create(
        model=model, max_tokens=2000, system=system,
        messages=[{"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": png_b64}}, {"type": "text", "text": text}]}])
    log_usage("anthropic", model, m.usage.input_tokens, m.usage.output_tokens)
    return m.content[0].text


def apply_errors(q, errors, minconf=0.6):
    """applier rules: replace `before` only when it occurs exactly once; confidence < 0.6 is held; nothing else changes"""
    fields = {"stem": q["stem"], **{f"options.{L}": q["options"].get(L, "") for L in "ABCD"}}
    applied, unapplied, held = [], [], []
    for e in errors:
        if e.get("confidence", 0) < minconf:
            held.append(e); continue
        k = e.get("field"); t = fields.get(k)
        if t is None:
            unapplied.append(dict(error=e, reason="bad_field")); continue
        if e["before"] == "" and k.startswith("options.") and not t.strip():
            fields[k] = e["after"]; applied.append(e); continue
        n = t.count(e["before"]) if e["before"] else 0
        if n != 1:
            unapplied.append(dict(error=e, reason="not_found" if n == 0 else "ambiguous")); continue
        fields[k] = t.replace(e["before"], e["after"], 1); applied.append(e)
    q["stem"] = fields["stem"]; q["options"] = {L: fields[f"options.{L}"] for L in "ABCD"}
    return applied, unapplied, held


def run_claude(p, everything=False, model="claude-haiku-5-5"):
    gate = load(OUT / "checked" / f"paper_{p:02d}.json") or sys.exit("run gate first")
    log = load(OUT / "checked" / f"paper_{p:02d}_claude.json", {})
    for q in gate["questions"]:
        if not (everything or q["verdict"] == "needs_claude") or q["qid"] in log:
            continue
        png, _ = crop_png(q)
        CTX.update(paper=p, step="S6_claude", ref=q["qid"])
        msg = (f"Question {q['qid']} on page {q['page']} (locate it on the page image).\nCURRENT TEXT:\nSTEM: {q['stem']}\n" +
               "\n".join(f"({L}) {q['options'].get(L, '')}" for L in "ABCD") +
               "\nBook answer key: " + str(q["correct_option"]) + "\nReasons it was sent to you:\n" + "\n".join(f"- {r['code']}: {r['detail']}" for r in q["reasons"]))
        raw = ask_claude(DETECT_RULES, msg, png, model)
        try:
            d = json.loads(re.search(r"\{.*\}", raw, re.S).group(0))
        except Exception:
            log[q["qid"]] = dict(error="unparseable", raw=raw[:500]); continue
        applied, unapplied, held = apply_errors(q, d.get("errors", []))
        log[q["qid"]] = dict(applied=applied, unapplied=unapplied, held=held, needs_human=d.get("needs_human", False), note=d.get("note", ""))
        if applied:
            q["edits"] += [dict(field=a["field"], type="repair_symbol", before=a["before"], after=a["after"], basis="claude detector: " + a["kind"], evidence=[], risk="low") for a in applied]
        q["claude_checked"] = True
        if unapplied or held or d.get("needs_human"):
            q["verdict"] = "needs_human"
        save(OUT / "checked" / f"paper_{p:02d}_claude.json", log)
    save(OUT / "checked" / f"paper_{p:02d}_v5.json", gate)
    print(p, "claude checked", len(log), "| need human:", sum(q["verdict"] == "needs_human" for q in gate["questions"]))


# ---------------------------------------------------------------- S9 review sheet
def run_review(p):
    path, st = latest(p); g = load(path)
    d = OUT / "review" / f"paper_{p:02d}"; d.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(PDF); rank = {"high": 0, "medium": 1, "low": 2}
    esc = lambda s: (s or "").replace("&", "&amp;").replace("<", "&lt;")
    rows = []
    for q in sorted(g["questions"], key=lambda q: (min([rank[r["severity"]] for r in q["reasons"]] or [3]), q["subject"], q["number"])):
        im = f"{q['qid']}.png"
        pg = doc[q["page"] - 1]
        if not (d / im).exists():
            pg.get_pixmap(dpi=110, colorspace=fitz.csGRAY).save(d / im)
        flags = "".join(f"<li class={r['severity']}><b>{r['code']}</b> {esc(r['detail'])}</li>" for r in q["reasons"])
        edits = "".join(f"<li>{e['field']}: <code>{esc(e['before'])}</code> &rarr; <code>{esc(e['after'])}</code></li>" for e in q["edits"] if e["type"].startswith("repair") or e["type"] == "restore_symbol")
        opts = "".join(f"<li>({L}) {esc(q['options'].get(L, ''))}{' [picture]' if q['figures'][L] else ''}</li>" for L in "ABCD")
        rows.append(f"<section><h3>{q['qid']} &middot; page {q['page']} &middot; key <b>{q['correct_option']}</b> &middot; {q['verdict']}</h3>"
                    f"<div class=g><div><p>{esc(q['stem'])}</p><ul>{opts}</ul><ul>{flags}</ul>{'<h4>repairs</h4><ul>' + edits + '</ul>' if edits else ''}</div>"
                    f"<img loading=lazy src='{im}'></div></section>")
    css = "body{font:14px sans-serif;max-width:1400px;margin:auto}.g{display:grid;grid-template-columns:1fr 1fr;gap:12px}img{width:100%;border:1px solid #888}.high{color:#b00}.medium{color:#a60}section{border-bottom:2px solid #ccc;padding:8px}"
    (d / "index.html").write_text(f"<!doctype html><meta charset=utf-8><style>{css}</style><h1>Paper {p} review ({st}) &middot; {len(rows)} questions</h1>" + "".join(rows), encoding="utf-8")
    print("review sheet:", d / "index.html")


# ---------------------------------------------------------------- S10 human notes -> DeepSeek patches -> code
class Patch(BaseModel):
    note: str
    qid: str
    op: str  # replace_text | set_option_text | set_correct_option | set_stem | delete_question | figure_fix | needs_clarification
    field: str = ""
    before: str = ""
    after: str = ""
    confidence: float = 0.0
    ambiguity: str = ""


class PatchSet(BaseModel):
    patches: list[Patch]


NOTE = re.compile(r"^\s*(?:paper\s*(\d+))?\s*(phy\w*|che\w*|bio\w*)\s*(\d+)\s*(.*)$", re.I)


def parse_notes(p, text):
    """'paper 3 Physics 12 stem: n should be pi' -> (qid, note); lines that do not start with subject+number are returned as unparsed"""
    ok, bad = [], []
    for ln in text.splitlines():
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        m = NOTE.match(ln)
        if not m or (m[1] and int(m[1]) != p):
            bad.append(ln); continue
        s = next(x for x in SUBJECTS if x.lower().startswith(m[2][:3].lower()))
        ok.append((qid_of(p, s, int(m[3])), ln.strip()))
    return ok, bad


PATCH_SYS = """You turn a human reviewer's rough note about ONE exam question into exact patches. You get the note and the CURRENT question JSON.
Rules: translate only what the note says, add nothing else. `before` must be an EXACT substring copied from the current text of `field` (extend it until it is unique); `after` is the new text (LaTeX inside $...$, units with \\mathrm).
If the note names a whole field, put the whole current text in `before`. Ops: replace_text (field stem or options.A..D), set_option_text, set_stem, set_correct_option (field correct_option, after = A|B|C|D),
delete_question, figure_fix (describe in `after`; code applies it), needs_clarification (note too vague, or `before` cannot be found: explain in `ambiguity`).
One patch per change in the note. Return ONLY JSON: {"patches":[{"note":"<the human line verbatim>","qid":"...","op":"...","field":"...","before":"...","after":"...","confidence":0-1,"ambiguity":""}]}"""


def apply_patch(q, pt):
    """returns (applied: bool, reason). `before` must match exactly once; answer letters are validated; nothing else is touched"""
    if pt.op == "needs_clarification":
        return False, pt.ambiguity or "unclear note"
    if pt.op == "set_correct_option":
        if pt.after.strip().upper() in ("A", "B", "C", "D"):
            q["correct_option"] = pt.after.strip().upper(); return True, ""
        return False, "answer letter must be A-D"
    if pt.op == "figure_fix":
        return False, "figure edits are done by hand/re-crop: " + pt.after
    if pt.op == "delete_question":
        q["deleted"] = True; return True, ""
    if pt.field == "stem":
        t = q["stem"]
    elif re.fullmatch(r"options\.[A-D]", pt.field):
        t = q["options"].get(pt.field[-1], "")
    else:
        return False, "unknown field " + pt.field
    if pt.op == "set_stem" or (pt.before and pt.before.strip() == t.strip()) or (pt.op == "set_option_text" and not pt.before):
        new = pt.after
    elif pt.before and t.count(pt.before) == 1:
        new = t.replace(pt.before, pt.after, 1)
    else:
        return False, f"before not found exactly once in {pt.field}"
    if pt.field == "stem":
        q["stem"] = new
    else:
        q["options"][pt.field[-1]] = new
    return True, ""


def run_feedback(p):
    """reads human/errors_paperPP.txt; DeepSeek makes patches; code applies; every note ends as applied | returned to the human"""
    src, st = latest(p); g = load(src)
    notes_f = OUT / "human" / f"errors_paper{p:02d}.txt"
    notes, unparsed = parse_notes(p, notes_f.read_text(encoding="utf-8")) if notes_f.exists() else sys.exit(f"write the notes to {notes_f}")
    by = {q["qid"]: q for q in g["questions"]}
    log, report = load(OUT / "human" / "review_log.json", []), dict(applied=[], returned=[], unparsed=unparsed, verify=[])
    for qid, note in notes:
        q = by.get(qid)
        if not q:
            report["returned"].append(dict(note=note, reason=f"{qid} not in paper")); continue
        CTX.update(paper=p, step="S10_patch", ref=qid)
        cur = dict(qid=qid, stem=q["stem"], options=q["options"], correct_option=q["correct_option"], flags=[r["code"] for r in q["reasons"]])
        ps, raw, err = ask_validated(PATCH_SYS, f"NOTE: {note}\nCURRENT QUESTION:\n{json.dumps(cur, ensure_ascii=False)}", "flash", PatchSet)
        if not ps:
            report["returned"].append(dict(note=note, reason="DeepSeek reply invalid: " + err[:100])); continue
        for pt in ps.patches:
            pt.qid = qid
            before_state = dict(stem=q["stem"], options=dict(q["options"]), correct_option=q["correct_option"])
            ok, why = apply_patch(q, pt)
            if not ok:
                report["returned"].append(dict(note=note, patch=pt.model_dump(), reason=why)); continue
            q["human_verified"] = True; q.setdefault("patches", []).append(pt.model_dump())
            # verification: the new text must really contain the human's correction
            now = q["stem"] if pt.field == "stem" else q["options"].get(pt.field[-1], "") if pt.field.startswith("options") else str(q["correct_option"])
            fixed = pt.op in ("delete_question", "set_correct_option") or pt.after in now
            log.append(dict(time=time.strftime("%Y-%m-%d %H:%M:%S"), qid=qid, note=note, op=pt.op, field=pt.field, before=pt.before, after=pt.after, previous=before_state))
            (report["applied"] if fixed else report["verify"]).append(dict(note=note, qid=qid, op=pt.op, before=pt.before, after=pt.after))
    g["questions"] = [q for q in g["questions"] if not q.get("deleted")]
    save(OUT / "final" / f"paper_{p:02d}_v7.json", g)
    save(OUT / "human" / "review_log.json", log)
    save(OUT / "patches" / f"paper{p:02d}_patches.json", report)
    print(f"applied {len(report['applied'])} | returned to human {len(report['returned'])} | unparsed lines {len(unparsed)} | applied but not verified {len(report['verify'])}")
    for r in report["returned"]:
        print("  RETURNED:", r["note"], "->", r["reason"])
    for ln in unparsed:
        print("  UNPARSED:", ln)


# ---------------------------------------------------------------- S8 build
def run_build(p):
    path, st = latest(p); g = load(path); _, date, shift, p0, p1 = paper_row(p)
    doc = fitz.open(PDF)
    var = {"original": [], "regenerated": []}
    for q in sorted(g["questions"], key=lambda q: (SUBJECTS.index(q["subject"]), q["number"])):
        nm = q["qid"]; qi, oi, flags = {"original": [], "regenerated": []}, {"original": {}, "regenerated": {}}, [r["code"] for r in q["reasons"]]
        for L, figs in q["figures"].items():
            for i, f in enumerate(figs, 1):
                page = doc[f["page"] - 1]; r = page.rect; b = f["box"]
                try:
                    clip = grow(page, fitz.Rect(b[0] / 1000 * r.width, b[1] / 1000 * r.height, b[2] / 1000 * r.width, b[3] / 1000 * r.height))
                    o, rg = pdfkit.crop_pair(doc, f["page"], list(clip), f"{nm}_{L if L != 'question' else 'q'}{i}.png", OUT)
                except Exception as e:
                    flags.append(f"crop_failed:{e}"[:60]); continue
                for v, ar in (("original", o), ("regenerated", rg)):
                    ar = AssetRef(**ar)
                    if L == "question":
                        qi[v].append(ar)
                    else:
                        oi[v][L] = ar
        for v in var:
            opts = {L: OptionOut(text=q["options"].get(L, ""), image=oi[v].get(L), image_only=not q["options"].get(L, "").strip() and L in oi[v]) for L in "ABCD"}
            var[v].append(CetQuestion(qid=nm, number=q["number"], number_text=str(q["number"]), page=q["page"], text=q["stem"], correct_option=q["correct_option"],
                                      question_images=qi[v], options=opts, confidence=q["confidence"], flags=flags + (["human_verified"] if q.get("human_verified") else []),
                                      paper=p, exam_date=date, shift=shift, subject=q["subject"], chapter_tag=q.get("chapter_tag", "")))
    (OUT / "papers").mkdir(exist_ok=True)
    for v, qs in var.items():
        d = CetDocument(doc_id=f"{DOC}_P{p:02d}", source_pdf=PDF, page_count=p1 - p0 + 1, questions=qs, run=dict(engine="deepseek_claude_pipeline", stage=st, images=v, paper=p))
        f = OUT / "papers" / f"paper_{p:02d}_{v}.json"; stamp(f); f.write_text(d.model_dump_json(indent=1), encoding="utf-8")
        with zipfile.ZipFile(OUT / "papers" / f"paper_{p:02d}_images_{v}.zip", "w", zipfile.ZIP_DEFLATED) as z:
            for im in sorted((OUT / f"images_{v}").glob(f"{DOC}_P{p:02d}_*.png")):
                z.write(im, f"images/{im.name}")
    print(p, st, {v: len(q) for v, q in var.items()}, "built")


# ---------------------------------------------------------------- reconstruction, preflight, whole pipeline
def run_reconstruct(p):
    """strict rebuild of every question of the newest stage file into one PDF (one page per question), from data only; for manual comparison with the book"""
    from cet_render import render_question
    path, st = latest(p); g = load(path); src = fitz.open(PDF); out = fitz.open()
    qs = sorted(g["questions"], key=lambda q: (SUBJECTS.index(q["subject"]), q["number"]))
    if "--flagged" in sys.argv:
        qs = [q for q in qs if q["verdict"] != "ok"]
    for q in qs:
        render_question(out, src, q)
    f = OUT / "reconstructed" / f"paper_{p:02d}_{st}.pdf"; f.parent.mkdir(exist_ok=True); stamp(f)
    out.save(f)
    (f.with_suffix(".index.txt")).write_text("\n".join(f"page {i + 1}: {q['qid']}" for i, q in enumerate(qs)), encoding="utf-8")
    print(f"reconstructed {len(qs)} questions ({st}) -> {f}")


def run_preflight(p, need_claude=True):
    """everything the whole pipeline needs for paper P; prints a checklist and returns True when ready"""
    _, _, _, p0, p1 = paper_row(p); ok = True
    def row(name, good, note=""):
        nonlocal ok
        ok &= bool(good); print(("  ok   " if good else "  MISSING ") + name + (f"  {note}" if note else ""))
    miss = [pg for pg in range(p0, p1 + 1) if not (OCR / f"page_{pg:03d}.txt").exists()]
    row(f"question OCR pages {p0}-{p1}", not miss, f"missing {miss}" if miss else "")
    row("page PNGs", all((PAGES / f"page_{pg:03d}.png").exists() for pg in range(p0, p1 + 1)))
    key, _ = answer_key(p)
    row("answer key (200 letters)", len(key) == 200, f"{len(key)} found; answer pages {answer_pages(p)}")
    row("DEEPSEEK_API_KEY", os.environ.get("DEEPSEEK_API_KEY"))
    if need_claude:
        row("ANTHROPIC_API_KEY", os.environ.get("ANTHROPIC_API_KEY"))
    if os.environ.get("DEEPSEEK_API_KEY"):
        import httpx
        try:
            r = httpx.get("https://api.deepseek.com/user/balance", headers={"Authorization": "Bearer " + os.environ["DEEPSEEK_API_KEY"]}, timeout=30).json()
            row("DeepSeek balance > 0", r.get("is_available"), str(r.get("balance_infos")))
            CTX.update(paper=p, step="balance", ref="preflight")
            USAGE.parent.mkdir(parents=True, exist_ok=True)
            with USAGE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(dict(t=time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()), api="deepseek", kind="balance", paper=p, balance=r.get("balance_infos"))) + "\n")
        except Exception as e:
            row("DeepSeek reachable", False, str(e)[:80])
    return ok


def run_all(p):
    """the whole pipeline for one paper; stops before the human step. --no-claude skips S6, --force ignores a failed preflight"""
    nc = "--no-claude" in sys.argv
    if not run_preflight(p, not nc) and "--force" not in sys.argv:
        sys.exit("preflight failed; fix the MISSING items (or --force)")
    run_deepseek(p); run_gate(p)
    if not nc:
        run_claude(p)
    run_review(p); run_reconstruct(p); run_build(p)
    run_cost(p)
    print(f"\nPaper {p} ready for the human step. Write notes to {OUT / 'human' / f'errors_paper{p:02d}.txt'} then: python claude_pipeline/cet_run.py feedback {p}")


def deepseek_peak(t):
    """peak = 01-04 and 06-10 UTC Mon-Fri (off-peak is half price); Chinese holidays are ignored (shortcut: a few calls may be priced high)"""
    g = time.strptime(t, "%Y-%m-%dT%H:%M:%S")
    return g.tm_wday < 5 and (1 <= g.tm_hour < 4 or 6 <= g.tm_hour < 10)


def call_cost(row, prices):
    pr = prices[row["model"] if row["api"] == "anthropic" else "deepseek-" + row["model"].split("-")[0]]
    if row["api"] == "deepseek":
        k = "peak" if deepseek_peak(row["t"]) else "offpeak"
        miss = row["tin"] - row["cached"]
        return (miss * pr[k]["in"] + row["cached"] * pr[k]["in_cached"] + row["tout"] * pr[k]["out"]) / 1e6
    return (row["tin"] * pr["in"] + row["tout"] * pr["out"]) / 1e6


def run_cost(p):
    """tokens and dollars for paper P: per step, per API/model, per question, per step per question; plus real DeepSeek spend from the balance snapshots"""
    prices = load(HERE / "prices.json")
    rows = [json.loads(l) for l in USAGE.read_text(encoding="utf-8").splitlines()] if USAGE.exists() else []
    calls = [r for r in rows if r.get("kind") != "balance" and r["paper"] == p]
    if not calls:
        sys.exit("no API calls logged for this paper yet")
    g = load(latest(p)[0]); nq = len(g["questions"])
    for c in calls:
        c["cost"] = call_cost(c, prices)
    tot = sum(c["cost"] for c in calls)
    print(f"Paper {p}: {nq} questions, {len(calls)} API calls, total ${tot:.4f}  =  ${tot / nq:.5f} per question")
    print(f"\n{'step':<14}{'api / model':<26}{'calls':>6}{'tokens in':>11}{'tokens out':>11}{'cached':>9}{'$ total':>10}{'$ / question':>14}")
    grp = {}
    for c in calls:
        grp.setdefault((c["step"], c["api"], c["model"]), []).append(c)
    for (st, api, md), cs in sorted(grp.items()):
        sc = sum(c["cost"] for c in cs)
        print(f"{st:<14}{api + ' / ' + md:<26}{len(cs):>6}{sum(c['tin'] for c in cs):>11}{sum(c['tout'] for c in cs):>11}{sum(c['cached'] for c in cs):>9}{sc:>10.4f}{sc / nq:>14.5f}")
    for api in ("deepseek", "anthropic"):
        sc = sum(c["cost"] for c in calls if c["api"] == api)
        print(f"{api:<14}{'(all models)':<26}{sum(c['api'] == api for c in calls):>6}{'':>11}{'':>11}{'':>9}{sc:>10.4f}{sc / nq:>14.5f}")
    per = {}
    for c in calls:  # a page call is shared by the questions on that page
        if c["ref"].startswith("page_"):
            pg = int(c["ref"][5:]); qs = [q["qid"] for q in g["questions"] if q["page"] == pg] or [c["ref"]]
            for q in qs:
                per[q] = per.get(q, 0) + c["cost"] / len(qs)
        else:
            per[c["ref"]] = per.get(c["ref"], 0) + c["cost"]
    top = sorted(per.items(), key=lambda x: -x[1])[:5]
    print("\nmost expensive questions:", ", ".join(f"{q} ${v:.4f}" for q, v in top), "| median ${:.5f}".format(sorted(per.values())[len(per) // 2]))
    bal = [r for r in rows if r.get("kind") == "balance" and r["paper"] == p]
    if bal and os.environ.get("DEEPSEEK_API_KEY"):
        import httpx
        now = httpx.get("https://api.deepseek.com/user/balance", headers={"Authorization": "Bearer " + os.environ["DEEPSEEK_API_KEY"]}, timeout=30).json()["balance_infos"][0]
        print(f"DeepSeek real spend since preflight: {float(bal[0]['balance'][0]['total_balance']) - float(now['total_balance']):.4f} {now['currency']} (other users of the key included)")
    print(f"Projection for 14 papers at this rate: ${tot * 14:.2f}  (prices.json: verify before trusting)")


if __name__ == "__main__":
    cmd, p = sys.argv[1], int(sys.argv[2])
    {"deepseek": run_deepseek, "gate": run_gate, "claude": lambda p: run_claude(p, "--all" in sys.argv), "review": run_review, "feedback": run_feedback,
     "build": run_build, "reconstruct": run_reconstruct, "preflight": run_preflight, "all": run_all, "cost": run_cost}[cmd](p)
