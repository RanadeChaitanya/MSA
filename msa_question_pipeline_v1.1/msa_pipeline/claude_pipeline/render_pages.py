"""Render every page of a PDF to PNG (grayscale) with a progress bar, then make a backup copy.
  python claude_pipeline/render_pages.py PDF OUT_DIR [DPI]
"""
import shutil, sys, time
from pathlib import Path
import fitz

pdf, out, dpi = sys.argv[1], Path(sys.argv[2]), int(sys.argv[3]) if len(sys.argv) > 3 else 120
d = fitz.open(pdf)
(out / "pages").mkdir(parents=True, exist_ok=True)
t0 = time.time()
for p in d:
    f = out / "pages" / f"page_{p.number + 1:03d}.png"
    if not f.exists():  # resumable
        p.get_pixmap(dpi=dpi, colorspace=fitz.csGRAY).save(f)
    n, w = p.number + 1, 40
    print(f"\r[{'#' * (w * n // len(d)):<{w}}] {n}/{len(d)} pages  {time.time() - t0:.0f}s", end="", flush=True)
print()
shutil.copytree(out / "pages", out / "backup" / "pages_v1", dirs_exist_ok=True)
print("rendered + backed up", len(list((out / "pages").glob("*.png"))))
