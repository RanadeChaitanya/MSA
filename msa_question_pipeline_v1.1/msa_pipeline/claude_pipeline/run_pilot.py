"""LLM-heavy PDF question extraction pilot.

  code     : render page + text layer + vector-figure candidates (pdfkit)
  DeepSeek : cheap first draft per page from the text layer (JSON)
  Sonnet   : parent; page image + text + draft -> PageOut (structured output), Opus advisor for hard pages
  code     : crops (original + regenerated), DocumentOut validation
  Opus     : audits only pages where draft and Sonnet disagree or confidence is low

Usage (keys come from the environment only: ANTHROPIC_API_KEY, DEEPSEEK_API_KEY):
  python claude_pipeline/run_pilot.py PDF OUT_DIR [--gt GT.json] [--sync]
  python claude_pipeline/run_pilot.py --rescore OUT_DIR --gt GT.json     # no API calls
"""
import argparse, base64, difflib, json, os, re, sys, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal, Optional

import fitz
from pydantic import BaseModel

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path.home() / ".claude/skills/pdf-extract"))
from pipeline.models import AssetRef, DocumentOut, OptionOut, QuestionOut  # noqa: E402
import pdfkit  # noqa: E402

SONNET, OPUS, DEEPSEEK = "claude-sonnet-5-5", "claude-opus-5-5", "deepseek-chat"
VIEW_DPI, CROP_DPI = 130, 200
# $ per 1M tokens: input, output, cache write, cache read. shortcut: DeepSeek price hard-coded, check api-docs.deepseek.com before quoting.
PRICE = {SONNET: (2.0, 10.0, 2.5, 0.20), OPUS: (4.0, 20.0, 5.0, 0.20), DEEPSEEK: (0.28, 0.42, 0.28, 0.028)}
LOW_CONF = 0.85
LETTERS = ["A", "B", "C", "D"]


# ---------------- page output schema (what the LLMs return; code turns it into DocumentOut)
class Fig(BaseModel):
    belongs_to: Literal["question", "option_A", "option_B", "option_C", "option_D", "solution"]
    box_pt: list[float]  # x0, y0, x1, y1 in PDF points, origin top-left


class Opt(BaseModel):
    label: Literal["A", "B", "C", "D"]
    text: str


class Q(BaseModel):
    number: int
    text: str
    options: list[Opt]
    correct_option: Optional[Literal["A", "B", "C", "D"]]
    answer_text: str
    figures: list[Fig]
    confidence: float


class PageOut(BaseModel):
    page_kind: Literal["question_paper", "answer_key", "hint_solution", "mixed", "other"]
    questions: list[Q]
    notes: str


def strict_schema(model):
    """Pydantic JSON schema -> structured-outputs form: every object closed, every property required."""
    s = model.model_json_schema()
    def walk(n):
        if isinstance(n, dict):
            if n.get("type") == "object" and "properties" in n:
                n["additionalProperties"] = False
                n["required"] = list(n["properties"])
            n.pop("title", None); n.pop("default", None)
            for v in n.values(): walk(v)
        elif isinstance(n, list):
            for v in n: walk(v)
    walk(s)
    return s


SCHEMA = strict_schema(PageOut)

RULES = """You extract exam material (question papers, answer keys, hint/solution sheets) from one PDF page into JSON.
Rules:
- One entry per question number that appears on the page. Keep printed numbers exactly.
- Options printed as (1)(2)(3)(4) or (a)(b)(c)(d) map to A, B, C, D. correct_option only when the page states the answer
  (answer grid "Q./A." rows, "Ans." lines); otherwise null. Answer grids: pair each Q number with the A digit in the same column.
- text / options: the question stem and option texts when printed on this page, else "" / [].
- answer_text: the full hint/solution text for that question when printed on this page, else "". Math in LaTeX inside $...$,
  chemistry as linear formulas (CH$_{3}$–C(=O)–Cl). Write "[figure]" where a drawn figure sits inside the solution.
  A solution that continues in the next column still belongs to the same number.
- figures: every drawn diagram/structure/circuit belonging to a question, option or solution, as a tight box in PDF points
  (x0,y0,x1,y1, origin top-left). Prefer the vector-figure candidates given (they are exact), merge/trim them as needed.
  Never box plain text, page headers, logos or watermarks.
- confidence: 0-1, your honest probability that this entry is fully right (number, answer, text, figures).
- notes: anything unusual on the page (errata, unreadable parts), else "".
Text-layer blocks are exact characters but math in them is often garbled; trust the image for math and layout."""

SYSTEM = RULES + "\n\nOutput JSON schema:\n" + json.dumps(SCHEMA, sort_keys=True)


# ---------------- 1. code: page prep
def prep(pdf, out):
    doc = fitz.open(pdf)
    cand_path = out / "scan.json"
    pdfkit.scan(pdf, cand_path)
    cands = {p["page"]: p["figures"] for p in json.loads(cand_path.read_text(encoding="utf-8"))}
    pages = []
    for p in doc:
        n = p.number + 1
        png = out / "pages" / f"page_{n:03d}.png"; png.parent.mkdir(parents=True, exist_ok=True)
        p.get_pixmap(dpi=VIEW_DPI, colorspace=fitz.csGRAY).save(png)
        blocks = [f"[{','.join(str(round(v)) for v in b[:4])}] {' '.join(b[4].split())}"
                  for b in p.get_text("blocks") if b[4].strip()]
        pages.append(dict(page=n, png=str(png), size_pt=[round(p.rect.width, 1), round(p.rect.height, 1)],
                          text="\n".join(blocks), figure_candidates=[f[1:] for f in cands[n]]))
    return pages


def page_context(pg):
    return (f"Page {pg['page']}. Page size {pg['size_pt']} pt; the image is rendered at {VIEW_DPI} dpi "
            f"(1 pt = {VIEW_DPI / 72:.3f} px).\nVector-figure candidates (pt): {json.dumps(pg['figure_candidates'])}\n"
            f"Text-layer blocks [x0,y0,x1,y1 pt] text:\n{pg['text']}")


def image_block(pg):
    data = base64.standard_b64encode(Path(pg["png"]).read_bytes()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}


# ---------------- 2. DeepSeek draft (text only)
def deepseek_draft(pg):
    from openai import OpenAI  # DeepSeek's API is OpenAI-compatible
    ds = OpenAI(api_key=os.environ["DEEPSEEK_API_KEY"], base_url="https://api.deepseek.com")
    t = time.time()
    r = ds.chat.completions.create(
        model=DEEPSEEK, response_format={"type": "json_object"}, max_tokens=8000, temperature=0,
        messages=[{"role": "system", "content": SYSTEM + "\nYou only see the text layer, so set figures to [] and give your best json."},
                  {"role": "user", "content": page_context(pg)}])
    u = r.usage
    hit = getattr(u, "prompt_cache_hit_tokens", 0) or 0
    log = dict(stage="draft", page=pg["page"], model=DEEPSEEK, input=u.prompt_tokens - hit, cache_read=hit,
               cache_write=0, output=u.completion_tokens, batch=False, secs=round(time.time() - t, 1))
    try:
        draft = PageOut.model_validate_json(r.choices[0].message.content).model_dump()
    except Exception as e:  # a bad draft only means Sonnet works without one
        draft = dict(page_kind="other", questions=[], notes=f"draft_invalid: {e}"[:300])
    return draft, log


# ---------------- 3/5. Claude requests
def sonnet_params(pg, draft):
    return dict(
        model=SONNET, max_tokens=32000,
        system=[{"type": "text", "text": SYSTEM + "\nCall the advisor only for genuinely hard pages (dense math, ambiguous layout).",
                 "cache_control": {"type": "ephemeral"}}],
        tools=[{"type": "advisor_20260301", "name": "advisor", "model": OPUS, "max_uses": 1, "max_tokens": 4000}],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": [
            image_block(pg),
            {"type": "text", "text": page_context(pg) + "\n\nCheap first draft from a text-only model (may be wrong or "
                                     "incomplete; verify every field against the image):\n" + json.dumps(draft, ensure_ascii=False)}]}])


def opus_params(pg, sonnet_out, issues):
    return dict(
        model=OPUS, max_tokens=32000,
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": [
            image_block(pg),
            {"type": "text", "text": page_context(pg) + "\n\nYou are the auditor. A worker produced the JSON below; a cheaper "
                                     "draft disagreed with it or confidence was low on: " + "; ".join(issues) +
                                     "\nCheck those points against the image (and anything else wrong you see) and return "
                                     "the full corrected page JSON.\nWorker JSON:\n" + json.dumps(sonnet_out, ensure_ascii=False)}]}])


def usage_rows(msg, stage, page, batch, secs):
    """One row per model that ran; advisor sub-inference is itemized in usage.iterations."""
    u = msg.usage
    its = getattr(u, "iterations", None) or [u]
    rows = []
    for it in its:
        kind = getattr(it, "type", "message") or "message"
        model = getattr(it, "model", None) or (OPUS if "advisor" in kind else msg.model)
        rows.append(dict(stage=stage if "advisor" not in kind else stage + "_advisor", page=page, model=model,
                         input=it.input_tokens, output=it.output_tokens,
                         cache_write=getattr(it, "cache_creation_input_tokens", 0) or 0,
                         cache_read=getattr(it, "cache_read_input_tokens", 0) or 0, batch=batch, secs=secs))
    return rows


def parse_page(msg):
    if msg.stop_reason != "end_turn":
        raise RuntimeError(f"stop_reason={msg.stop_reason} {getattr(msg, 'stop_details', None)}")
    text = next(b.text for b in reversed(msg.content) if b.type == "text")
    return PageOut.model_validate_json(text).model_dump()


def run_claude(client, jobs, stage, sync):
    """jobs: {page: params}. Returns {page: (PageOut dict, usage rows)}. Batch API by default (50% off)."""
    betas = ["advisor-tool-2026-03-01"] if any("tools" in p for p in jobs.values()) else []
    if sync:
        def one(item):
            n, params = item
            t = time.time()
            with client.beta.messages.stream(betas=betas, **params) as s:
                msg = s.get_final_message()
            return n, (parse_page(msg), usage_rows(msg, stage, n, False, round(time.time() - t, 1)))
        with ThreadPoolExecutor(4) as ex:
            return dict(ex.map(one, jobs.items()))
    t = time.time()
    b = client.beta.messages.batches.create(betas=betas, requests=[{"custom_id": f"p{n}", "params": p} for n, p in jobs.items()])
    print(f"  {stage}: batch {b.id} with {len(jobs)} requests")
    while (b := client.beta.messages.batches.retrieve(b.id)).processing_status != "ended":
        time.sleep(20)
    secs, out = round(time.time() - t, 1), {}
    for r in client.beta.messages.batches.results(b.id):
        n = int(r.custom_id[1:])
        if r.result.type != "succeeded":
            raise RuntimeError(f"{stage} page {n}: {r.result.type} {getattr(r.result, 'error', '')}")
        out[n] = (parse_page(r.result.message), usage_rows(r.result.message, stage, n, True, secs))
    return out


# ---------------- disagreement check (code)
def issues_between(draft, son):
    d = {q["number"]: q for q in draft["questions"]}
    s = {q["number"]: q for q in son["questions"]}
    out = []
    if set(d) != set(s) and d:
        out.append(f"question numbers differ: only draft {sorted(set(d) - set(s))}, only worker {sorted(set(s) - set(d))}")
    diff = [n for n in set(d) & set(s) if d[n]["correct_option"] != s[n]["correct_option"]]
    if diff:
        out.append(f"correct_option differs for {sorted(diff)}")
    gone = [n for n in set(d) & set(s) if bool(d[n]["answer_text"]) != bool(s[n]["answer_text"])]
    if gone:
        out.append(f"answer_text present in only one of them for {sorted(gone)}")
    low = [q["number"] for q in son["questions"] if q["confidence"] < LOW_CONF]
    if low:
        out.append(f"worker confidence < {LOW_CONF} for {sorted(low)}")
    return out


# ---------------- 4. code: assemble DocumentOut + crops
def assemble(pdf, out, pages_out, doc_id):
    doc, s = fitz.open(pdf), CROP_DPI / 72
    merged = {}
    for n in sorted(pages_out):
        for q in pages_out[n]["questions"]:
            m = merged.setdefault(q["number"], dict(page=n, text="", options={}, correct=None, answer="", figs=[], conf=[], flags=[]))
            if q["answer_text"] or q["text"]:
                m["page"] = n  # the page holding the content, not just the grid answer
            m["text"] = m["text"] or q["text"]
            m["options"].update({o["label"]: o["text"] for o in q["options"]})
            if q["correct_option"]:
                if m["correct"] and m["correct"] != q["correct_option"]:
                    m["flags"].append(f"answer_conflict_p{n}:{m['correct']}vs{q['correct_option']}")
                m["correct"] = m["correct"] or q["correct_option"]
            m["answer"] = " ".join(x for x in (m["answer"], q["answer_text"]) if x)
            m["figs"] += [(n, f) for f in q["figures"]]
            m["conf"].append(q["confidence"])
    questions, variants = [], {"original": [], "regenerated": []}
    for num in sorted(merged):
        m = merged[num]
        qimgs, oimgs = {"original": [], "regenerated": []}, {}
        for i, (pg, f) in enumerate(m["figs"], 1):
            x0, y0, x1, y1 = f["box_pt"]
            box = [max(0, x0), max(0, y0), x1, y1]
            if x1 - x0 < 4 or y1 - y0 < 4:
                m["flags"].append(f"bad_figure_box_{i}"); continue
            name = f"{doc_id}_q{num:03d}_fig{i}.png"
            orig, regen = pdfkit.crop_pair(doc, pg, box, name, out, dpi=CROP_DPI)
            for v, a in (("original", orig), ("regenerated", regen)):
                a = AssetRef(**a)
                if f["belongs_to"].startswith("option_"):
                    oimgs.setdefault(v, {})[f["belongs_to"][-1]] = a
                else:
                    qimgs[v].append(a)
            if f["belongs_to"] == "solution":
                m["flags"].append("images_are_solution_figures")  # no solution_images field in this models.py
        for v in variants:
            opts = {L: OptionOut(text=m["options"].get(L, ""), image=oimgs.get(v, {}).get(L),
                                 image_only=not m["options"].get(L) and L in oimgs.get(v, {}))
                    for L in LETTERS if L in m["options"] or L in oimgs.get(v, {})}
            variants[v].append(QuestionOut(
                qid=f"{doc_id}_q{num:03d}", number=num, number_text=str(num), page=m["page"], text=m["text"],
                correct_option=m["correct"], answer_text=m["answer"], question_images=qimgs[v], options=opts,
                confidence=round(min(m["conf"]), 3), flags=sorted(set(m["flags"] + (["hint_solution"] if m["answer"] else [])))))
    docs = {}
    for v, qs in variants.items():
        d = DocumentOut(doc_id=doc_id, source_pdf=str(pdf), page_count=len(doc), pages=[], questions=qs,
                        page_issues={str(n): [p["notes"]] for n, p in pages_out.items() if p["notes"]},
                        run=dict(engine="claude_pipeline_pilot", images=v, dpi=CROP_DPI, models=[DEEPSEEK, SONNET, OPUS]))
        DocumentOut.model_validate(d.model_dump())  # schema gate
        (out / f"{doc_id}_{v}.json").write_text(d.model_dump_json(indent=1), encoding="utf-8")
        docs[v] = d
    return docs["original"]


# ---------------- scoring + report
def norm(t):
    return re.sub(r"[\s$\\{}]|\\[a-z]+", "", t or "").lower()


def iou(a, b):
    ix = max(0, min(a[2], b[2]) - max(a[0], b[0])); iy = max(0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    return inter / ((a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter or 1)


def score(pred, gt_path):
    gt = {q["number"]: q for q in json.loads(Path(gt_path).read_text(encoding="utf-8"))["questions"]}
    pr = {q["number"]: q for q in pred["questions"]}
    both = sorted(set(gt) & set(pr))
    s = gt_dpi = json.loads(Path(gt_path).read_text(encoding="utf-8"))["run"].get("dpi", 200) / 72
    pr_s = CROP_DPI / 72
    gfig = [(n, [v / s for v in a["bbox_px"]]) for n, q in gt.items() for a in q.get("solution_images", []) + q["question_images"]]
    pfig = [(n, [v / pr_s for v in a["bbox_px"]]) for n, q in pr.items() for a in q.get("solution_images", []) + q["question_images"]]
    hit = [max([iou(b, pb) for pn, pb in pfig if pn == n] or [0]) for n, b in gfig]
    phit = [max([iou(pb, b) for n, b in gfig if n == pn] or [0]) for pn, pb in pfig]
    hints = [n for n in both if gt[n]["answer_text"] and pr[n]["answer_text"]]
    sims = [difflib.SequenceMatcher(None, norm(gt[n]["answer_text"]), norm(pr[n]["answer_text"])).ratio() for n in hints]
    pct = lambda a, b: round(100 * a / b, 2) if b else None
    res = {
        "question_number_recall_%": pct(len(both), len(gt)),
        "question_number_precision_%": pct(len(both), len(pr)),
        "correct_option_acc_%": pct(sum(gt[n]["correct_option"] == pr[n]["correct_option"] for n in both), len(gt)),
        "page_acc_%": pct(sum(gt[n]["page"] == pr[n]["page"] for n in both), len(gt)),
        "hint_presence_acc_%": pct(sum(bool(gt[n]["answer_text"]) == bool(pr[n]["answer_text"]) for n in both), len(gt)),
        "hint_text_similarity_mean_%": round(100 * sum(sims) / len(sims), 2) if sims else None,
        "hint_text_similarity>=90%_share_%": pct(sum(x >= 0.9 for x in sims), len(sims)),
        "figure_recall_iou>=0.5_%": pct(sum(h >= 0.5 for h in hit), len(gfig)),
        "figure_precision_iou>=0.5_%": pct(sum(h >= 0.5 for h in phit), len(pfig)),
        "figure_mean_iou_%": round(100 * sum(hit) / len(hit), 1) if hit else None,
        "counts": dict(gt_questions=len(gt), pred_questions=len(pr), gt_figures=len(gfig), pred_figures=len(pfig), hints_compared=len(sims)),
        "wrong_answers": [(n, gt[n]["correct_option"], pr[n]["correct_option"]) for n in both if gt[n]["correct_option"] != pr[n]["correct_option"]],
        "missing_numbers": sorted(set(gt) - set(pr)), "extra_numbers": sorted(set(pr) - set(gt)),
        "low_hint_similarity": sorted((round(x, 2), n) for x, n in zip(sims, hints) if x < 0.9),
    }
    return res


def cost(r):
    p = PRICE[r["model"]]
    usd = (r["input"] * p[0] + r["output"] * p[1] + r["cache_write"] * p[2] + r["cache_read"] * p[3]) / 1e6
    return usd * (0.5 if r["batch"] else 1)


def report(out, log, n_pages, n_q, escalated, sc):
    by = {}
    for r in log:
        k = (r["stage"], r["model"])
        a = by.setdefault(k, dict(calls=0, input=0, cache_write=0, cache_read=0, output=0, usd=0.0))
        a["calls"] += 1; a["usd"] += cost(r)
        for f in ("input", "cache_write", "cache_read", "output"):
            a[f] += r[f]
    total = sum(a["usd"] for a in by.values())
    L = [f"# Claude pipeline pilot report\n", f"Pages: {n_pages} | questions: {n_q} | escalated to Opus: "
         f"{len(escalated)}/{n_pages} ({100 * len(escalated) / n_pages:.0f}%) {sorted(escalated)}\n",
         "| stage | model | calls | input | cache write | cache read | output | $ | $/page | $/question |",
         "|---|---|---|---|---|---|---|---|---|---|"]
    for (st, m), a in sorted(by.items()):
        L.append(f"| {st} | {m} | {a['calls']} | {a['input']} | {a['cache_write']} | {a['cache_read']} | {a['output']} | "
                 f"{a['usd']:.4f} | {a['usd'] / n_pages:.4f} | {a['usd'] / max(n_q, 1):.5f} |")
    L.append(f"| **total** | | | | | | | **{total:.4f}** | **{total / n_pages:.4f}** | **{total / max(n_q, 1):.5f}** |\n")
    L.append("Claude calls in batch mode are billed at 50%; the $ column already includes it. "
             f"Price table used ($/M in, out, cache write, cache read): {json.dumps(PRICE)}\n")
    if sc:
        L.append("## Accuracy vs ground truth\n\n| metric | value |\n|---|---|")
        L += [f"| {k} | {v} |" for k, v in sc.items()]
    (out / "report.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf", nargs="?"); ap.add_argument("out", nargs="?")
    ap.add_argument("--gt"); ap.add_argument("--sync", action="store_true", help="plain calls instead of Batch API")
    ap.add_argument("--rescore", help="OUT_DIR of an earlier run: rebuild report from saved state, no API calls")
    a = ap.parse_args()
    if a.rescore:
        out = Path(a.rescore); st = json.loads((out / "state.json").read_text(encoding="utf-8"))
        pred = json.loads((out / f"{st['doc_id']}_original.json").read_text(encoding="utf-8"))
        return report(out, st["log"], st["pages"], len(pred["questions"]), st["escalated"], score(pred, a.gt) if a.gt else None)
    for k in ("ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"):
        if not os.environ.get(k):
            sys.exit(f"set {k} in the environment first")
    import anthropic
    client = anthropic.Anthropic()
    out = Path(a.out); out.mkdir(parents=True, exist_ok=True)
    doc_id = re.sub(r"\W+", "_", Path(a.pdf).stem).strip("_")

    print("1. prep"); pages = prep(a.pdf, out)
    print("2. DeepSeek drafts")
    with ThreadPoolExecutor(4) as ex:
        drafts = dict(zip([p["page"] for p in pages], ex.map(deepseek_draft, pages)))
    log = [lg for _, lg in drafts.values()]
    print("3. Sonnet (parent, Opus advisor)")
    son = run_claude(client, {p["page"]: sonnet_params(p, drafts[p["page"]][0]) for p in pages}, "sonnet", a.sync)
    final, issues = {}, {}
    for p in pages:
        n = p["page"]; final[n] = son[n][0]; log += son[n][1]
        if iss := issues_between(drafts[n][0], son[n][0]):
            issues[n] = iss
    print(f"5. Opus audit on {sorted(issues)}")
    if issues:
        aud = run_claude(client, {p["page"]: opus_params(p, final[p["page"]], issues[p["page"]]) for p in pages if p["page"] in issues},
                         "opus_audit", a.sync)
        for n, (po, rows) in aud.items():
            final[n] = po; log += rows
    (out / "pages_out.json").write_text(json.dumps(dict(drafts={n: d for n, (d, _) in drafts.items()},
                                                         sonnet={n: s for n, (s, _) in son.items()}, final=final, issues=issues),
                                                    ensure_ascii=False, indent=1), encoding="utf-8")
    print("4. crops + DocumentOut")
    doc = assemble(a.pdf, out, final, doc_id)
    (out / "state.json").write_text(json.dumps(dict(doc_id=doc_id, pages=len(pages), escalated=sorted(issues), log=log), indent=1), encoding="utf-8")
    report(out, log, len(pages), len(doc.questions), sorted(issues), score(doc.model_dump(), a.gt) if a.gt else None)


if __name__ == "__main__":
    main()
