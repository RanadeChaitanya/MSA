"""Resumable local OCR queue for the whole CET PCB 2024 book (deepseek-ocr via Ollama, CPU). Run detached.
  python claude_pipeline/ocr_queue.py [FIRST LAST]
Writes D:\\MSA\\outputs\\cet_pcb_2024\\ocr\\page_NNN.txt, progress.json and flagged.txt. Skips pages already done (also reuses ocr_p1/).
Loop detection: output too long, or one 40-char chunk repeated many times, or the token cap was hit -> retry with a stronger repeat penalty.
Order: question pages first (17-219), then answer pages (220-427), then anything left.
"""
import base64, json, re, shutil, sys, time
from pathlib import Path
import httpx

R = Path(r"D:\MSA\outputs\cet_pcb_2024")
OUT = R / "ocr"; OUT.mkdir(exist_ok=True)
for f in (R / "ocr_p1").glob("page_0*.txt"):  # reuse paper 1 pages (page 6 is the repaired one)
    if f.name.startswith("page_0") and not (OUT / f.name).exists():
        shutil.copy(f, OUT / f.name)
first, last = (int(sys.argv[1]), int(sys.argv[2])) if len(sys.argv) > 2 else (1, 427)
order = [p for p in list(range(17, 220)) + list(range(220, 428)) + list(range(1, 17)) if first <= p <= last]
ATTEMPTS = [dict(temperature=0, repeat_penalty=1.1, num_predict=6000), dict(temperature=0, repeat_penalty=1.25, num_predict=4096),
            dict(temperature=0, repeat_penalty=1.4, num_predict=4096)]


def looped(txt, hit_cap):
    chunks = [txt[i:i + 40] for i in range(0, max(len(txt) - 40, 0), 20)]
    worst = max((chunks.count(c) for c in set(chunks)), default=0)
    return hit_cap or len(txt) > 9000 or worst >= 6


def run(page):
    img = base64.b64encode((R / "pages" / f"page_{page:03d}.png").read_bytes()).decode()
    for i, opts in enumerate(ATTEMPTS):
        r = httpx.post("http://localhost:11434/api/generate", timeout=3600, json=dict(
            model="deepseek-ocr", prompt="<|grounding|>Convert the document to markdown.", stream=False, images=[img], options=opts)).json()
        txt = r["response"]
        if not looped(txt, r.get("done_reason") == "length"):
            return txt, i + 1, False
    return txt, len(ATTEMPTS), True


prio_file = R / "ocr_priority.txt"  # optional: "220-237,250" = these pages first (read once at start)
if prio_file.exists():
    prio = []
    for part in prio_file.read_text().replace("\n", ",").split(","):
        part = part.strip()
        if part:
            a, _, b = part.partition("-"); prio += list(range(int(a), int(b or a) + 1))
    order = [p for p in prio if first <= p <= last] + [p for p in order if p not in prio]
todo = [p for p in order if not (OUT / f"page_{p:03d}.txt").exists()]
t0, done_now, busy = time.time(), 0, 0.0
prog = R / "ocr_progress.json"
for p in todo:
    t = time.time()
    txt = None
    for attempt in range(200):  # Ollama down (update, restart): wait and retry the SAME page instead of skipping it
        try:
            txt, tries, bad = run(p)
            break
        except Exception as e:
            (R / "ocr_errors.txt").open("a").write(f"{time.strftime('%H:%M:%S')} page {p}: {type(e).__name__} {str(e)[:80]}\n")
            time.sleep(30)
            t = time.time()  # waiting time is not counted in the page duration
    if txt is None:
        continue
    (OUT / f"page_{p:03d}.txt").write_text(txt, encoding="utf-8")
    if bad: (R / "flagged.txt").open("a").write(f"{p}: still looping after {tries} tries\n")
    done_now += 1; busy += time.time() - t
    total_done = sum(1 for _ in OUT.glob("page_*.txt"))
    rate = busy / done_now  # seconds of real OCR work per page (sleep and waiting excluded)
    prog.write_text(json.dumps(dict(done=total_done, total=427, last_page=p, secs_this=round(time.time() - t),
                                    tries=tries, avg_secs=round(rate), eta_hours=round(rate * (len(todo) - done_now) / 3600, 1),
                                    updated=time.strftime("%Y-%m-%d %H:%M:%S"))), encoding="utf-8")
    print(f"[{'#' * (40 * total_done // 427):<40}] {total_done}/427 page {p} {time.time() - t:.0f}s tries={tries}{' FLAGGED' if bad else ''}", flush=True)
