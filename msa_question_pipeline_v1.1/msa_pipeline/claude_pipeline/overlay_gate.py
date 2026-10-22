"""Overlay gate: score a redrawn figure (PNG or SVG) against the original crop by ink overlap. Code only, no model.
  python claude_pipeline/overlay_gate.py            # scores the existing Haiku / Sonnet SVG redraws vs the original crops
Score = F1 of ink pixels after both are rendered at the same size and ink is dilated by TOL px (so thin-line wobble is tolerated,
missing or added strokes are not). Also reports ink-extent ratio. PASS threshold is a guess to be tuned: F1 >= 0.80 and extent 0.8-1.25.
"""
import sys
from pathlib import Path
import fitz
import numpy as np
from PIL import Image
from scipy.ndimage import binary_dilation

R = Path(r"D:\MSA\outputs\cet_pcb_2024")
TOL, W = 4, 600


def ink(img, size):
    a = np.array(img.convert("L").resize(size, Image.LANCZOS))
    return a < 150  # dark = ink; light scan haze and watermark are ignored


def load(path, size_hint):
    p = Path(path)
    if p.suffix == ".svg":
        pg = fitz.open(str(p))[0]
        z = W / pg.rect.width
        return pg.get_pixmap(matrix=fitz.Matrix(z, z), alpha=False).pil_image()
    return Image.open(p)


def tight(img, scale=2):
    """Render ink at a working size, then crop to the ink bounding box so framing/margins differences do not count."""
    big = np.array(img.convert("L").resize((W * scale // 2, round(W * scale // 2 * img.height / img.width)), Image.LANCZOS)) < 150
    ys, xs = np.where(big)
    return big[ys.min():ys.max() + 1, xs.min():xs.max() + 1] if len(ys) else big


def skel(m):
    from skimage.morphology import skeletonize
    return skeletonize(m)


def score_skel(orig_path, cand_path, tol=5):
    """Same alignment as score(), but compares 1-px skeletons so stroke thickness / font weight do not matter."""
    try:
        a, c = tight(Image.open(orig_path)), tight(load(cand_path, None))
    except Exception:
        return None
    if not a.any() or not c.any():
        return dict(f1=0.0, extent=0.0)
    aspect = (c.shape[1] / c.shape[0]) / (a.shape[1] / a.shape[0])
    c = np.array(Image.fromarray((c * 255).astype(np.uint8)).resize((a.shape[1], a.shape[0]), Image.LANCZOS)) > 100
    sa, sc = skel(a), skel(c)
    prec = (sc & binary_dilation(sa, iterations=tol)).sum() / max(sc.sum(), 1)
    rec = (sa & binary_dilation(sc, iterations=tol)).sum() / max(sa.sum(), 1)
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return dict(f1=round(f1, 3), prec=round(prec, 3), rec=round(rec, 3), extent=round(aspect, 2))


def score(orig_path, cand_path):
    try:
        a, c = tight(Image.open(orig_path)), tight(load(cand_path, None))
    except Exception:
        return None
    if not a.any() or not c.any():
        return dict(f1=0.0, extent=0.0)
    aspect = (c.shape[1] / c.shape[0]) / (a.shape[1] / a.shape[0])  # 1.0 = same shape of ink area
    # bring the candidate's ink box to the original's box size, then compare strokes
    c = np.array(Image.fromarray((c * 255).astype(np.uint8)).resize((a.shape[1], a.shape[0]), Image.LANCZOS)) > 100
    prec = (c & binary_dilation(a, iterations=TOL)).sum() / max(c.sum(), 1)
    rec = (a & binary_dilation(c, iterations=TOL)).sum() / a.sum()
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return dict(f1=round(f1, 3), prec=round(prec, 3), rec=round(rec, 3), extent=round(aspect, 2))


def verdict(s):
    return "n/a" if s is None else ("PASS" if s["f1"] >= 0.80 and 0.8 <= s["extent"] <= 1.25 else "FAIL")


if __name__ == "__main__":
    names = [p.stem for p in sorted((R / "haiku_svg").glob("*.svg"))]
    print(f"{'figure':<34}{'cand':<9}{'F1':>6}{'prec':>7}{'rec':>7}{'extent':>8}  verdict")
    for n in names:
        o = R / "images_original" / f"{n}.png"
        for label, p in (("cleaned", R / "images_regenerated" / f"{n}.png"), ("haiku", R / "haiku_svg" / f"{n}.svg"), ("sonnet", R / "sonnet_svg" / f"{n}.svg")):
            s, k = score(o, p), score_skel(o, p)
            print(f"{n[12:]:<34}{label:<9}" + (f"{s['f1']:>6}{s['prec']:>7}{s['rec']:>7}{s['extent']:>8}  {verdict(s)}   skeleton F1 {k['f1']} {verdict(k)}" if s else "  render failed"))
