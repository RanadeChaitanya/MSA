"""Step 3b: screenshot KaTeX pages (Edge headless) -> OCR (local DeepSeek-OCR) -> compare each question with the OCR of the book page.
Compared tokens: signed numbers, Greek/maths commands, chemical-looking tokens. A difference = our LaTeX does not say what the book says.
python render_back.py [shoot|ocr|compare|all]
"""
import base64, json, re, subprocess, sys, time
from collections import Counter
from pathlib import Path
import httpx
from PIL import Image
import numpy as np

T = Path(r"D:\MSA\outputs\cet_pcb_2024\latex_tools"); RD = T / "render"; R = T.parent
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
mode = sys.argv[1] if len(sys.argv) > 1 else "all"
pages = json.load(open(RD / "map.json"))

if mode in ("shoot", "all"):
    for p in pages:
        png = RD / f"render_{p['page']}.png"
        if png.exists():
            continue
        subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--screenshot={png}", "--window-size=1000,3600",
                        (RD / f"render_{p['page']}.html").as_uri()], capture_output=True, timeout=120)
        im = Image.open(png).convert("L"); a = np.array(im)
        ys = np.where(a.min(1) < 200)[0]
        im.crop((0, 0, im.width, int(ys.max()) + 30)).save(png)
    print("screenshots done", flush=True)

if mode in ("ocr", "all"):
    for p in pages:
        out = RD / f"render_{p['page']}.txt"
        if out.exists():
            continue
        img = base64.b64encode((RD / f"render_{p['page']}.png").read_bytes()).decode()
        t = time.time()
        for opts in (dict(temperature=0, repeat_penalty=1.1, num_predict=6000), dict(temperature=0, repeat_penalty=1.25, num_predict=4096)):
            r = httpx.post("http://localhost:11434/api/generate", timeout=3600, json=dict(
                model="deepseek-ocr", prompt="<|grounding|>Convert the document to markdown.", stream=False, images=[img], options=opts)).json()
            if r.get("done_reason") != "length":
                break
        out.write_text(r["response"], encoding="utf-8")
        print(f"ocr render_{p['page']}: {time.time() - t:.0f}s", flush=True)

GREEK = r"\\(?:alpha|beta|gamma|delta|Delta|theta|lambda|mu|nu|pi|rho|sigma|omega|Omega|phi|epsilon|varepsilon|eta|tau|circ|times|sqrt|frac|hat|vec|infty|propto|leq|geq|rightarrow|rightleftharpoons)\b"


def clean(s):
    s = re.sub(r"<\|ref\|>.*?<\|/ref\|><\|det\|>.*?<\|/det\|>", " ", s, flags=re.S)
    return s.replace("−", "-").replace("–", "-")


def tokens(s):
    s = clean(s)
    s = re.sub(r"^\s*\(?[A-D]\)\s*", " ", s, flags=re.M)  # option labels
    nums = re.findall(r"(?<![A-Za-z])-?\d+(?:\.\d+)?", s)
    return Counter(nums) + Counter(re.findall(GREEK, s))


if mode in ("compare", "all"):
    g = {q["qid"]: q for q in json.load(open(R / "final" / "paper_01_v10.json", encoding="utf-8"))["questions"]}
    rows = []
    for p in pages:
        f = RD / f"render_{p['page']}.txt"
        if not f.exists():
            continue
        txt = clean(f.read_text(encoding="utf-8"))
        parts = re.split(r"QID\s*\**\s*((?:Phy|Che|Bio)_q\d{3})", txt)
        seg = {f"CET_PCB_2024_P01_{parts[i]}": parts[i + 1] for i in range(1, len(parts) - 1, 2)}
        for qid in p["qids"]:
            q = g[qid]; n = int(q["number"]); pg = int(q["page"])
            book = "".join((R / "ocr" / f"page_{x:03d}.txt").read_text(encoding="utf-8") for x in (pg, pg + 1) if (R / "ocr" / f"page_{x:03d}.txt").exists())
            m = re.search(rf"(?m)^\W*{n}\s*\.(.*?)(?=^\W*{n + 1}\s*\.|\Z)", clean(book), flags=re.S)
            if qid not in seg or not m:
                rows.append(dict(qid=qid, status="not_compared", why="render segment missing" if qid not in seg else "book segment not found")); continue
            a, b = tokens(seg[qid]), tokens(m.group(1))
            only_ours, only_book = dict(a - b), dict(b - a)
            rows.append(dict(qid=qid, status="match" if not only_ours and not only_book else "MISMATCH", only_in_ours=only_ours, only_in_book=only_book))
    (T / "render_back_report.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    c = Counter(r["status"] for r in rows); print(dict(c))
    for r in rows:
        if r["status"] == "MISMATCH": print(" ", r["qid"].split("_P01_")[1], "ours+", r["only_in_ours"], "book+", r["only_in_book"])
