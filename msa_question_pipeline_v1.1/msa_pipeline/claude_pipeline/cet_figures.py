"""Split a merged option-picture box (four pictures in one OCR image box) into A,B,C,D cells by whitespace gutters. Code only.
split_box(page_png, box_permille) -> dict(layout, cells{A..D: box}) or None. Row-major order: 2x2 = A B / C D; 1x4 = left to right; 4x1 = top to bottom.
"""
import fitz
import numpy as np
from PIL import Image


def _gaps(prof, minw):
    """interior runs of zeros in a 1-D ink profile: list of (start, end)"""
    n, out, i = len(prof), [], 0
    nz = np.nonzero(prof)[0]
    if len(nz) == 0:
        return out
    lo, hi = nz[0], nz[-1]
    i = lo
    while i <= hi:
        if prof[i] == 0:
            j = i
            while j <= hi and prof[j] == 0:
                j += 1
            if j - i >= minw:
                out.append((i, j))
            i = j
        else:
            i += 1
    return out


def split_box(page_png, box):
    im = Image.open(page_png).convert("L")
    W, H = im.size
    x0, y0, x1, y1 = [int(v) for v in (box[0] * W / 1000, box[1] * H / 1000, box[2] * W / 1000, box[3] * H / 1000)]
    a = np.array(im)[y0:y1, x0:x1] < 190
    if a.size == 0:
        return None
    cols, rows = a.any(axis=0).astype(int), a.any(axis=1).astype(int)
    vg = _gaps(cols, max(3, int(0.03 * a.shape[1])))
    hg = _gaps(rows, max(2, int(0.04 * a.shape[0])))
    vg.sort(key=lambda g: g[0] - g[1]); hg.sort(key=lambda g: g[0] - g[1])  # widest first
    if len(vg) >= 3 and len(hg) == 0:
        cuts = sorted((g[0] + g[1]) // 2 for g in vg[:3]); layout = "1x4"
        xs = [0] + cuts + [a.shape[1]]
        cells = [(xs[k], 0, xs[k + 1], a.shape[0]) for k in range(4)]
    elif len(hg) >= 3 and len(vg) == 0:
        cuts = sorted((g[0] + g[1]) // 2 for g in hg[:3]); layout = "4x1"
        ys = [0] + cuts + [a.shape[0]]
        cells = [(0, ys[k], a.shape[1], ys[k + 1]) for k in range(4)]
    elif vg and hg:
        cx = (vg[0][0] + vg[0][1]) // 2; cy = (hg[0][0] + hg[0][1]) // 2; layout = "2x2"
        cells = [(0, 0, cx, cy), (cx, 0, a.shape[1], cy), (0, cy, cx, a.shape[0]), (cx, cy, a.shape[1], a.shape[0])]
    else:
        return None
    to = lambda c: [round((x0 + c[0]) * 1000 / W), round((y0 + c[1]) * 1000 / H), round((x0 + c[2]) * 1000 / W), round((y0 + c[3]) * 1000 / H)]
    return dict(layout=layout, cells={L: to(c) for L, c in zip("ABCD", cells)})


def grow(page, box, dpi=150, max_pt=24):
    """OCR boxes are tight and clip edge labels. Grow each side outward while ink touches that edge, stopping at the first
    blank row/column (so neighbouring text lines, which sit behind a blank gap, are not pulled in) or after max_pt points."""
    import numpy as np
    s = dpi / 72
    ctx = (box + (-max_pt, -max_pt, max_pt, max_pt)) & page.rect
    a = np.array(page.get_pixmap(dpi=dpi, clip=ctx, colorspace=fitz.csGRAY).pil_image()) < 170
    x0, y0, x1, y1 = [int(round(v)) for v in ((box.x0 - ctx.x0) * s, (box.y0 - ctx.y0) * s, (box.x1 - ctx.x0) * s, (box.y1 - ctx.y0) * s)]
    H, W = a.shape
    x0, y0, x1, y1 = max(x0, 0), max(y0, 0), min(x1, W), min(y1, H)
    lim = int(max_pt * s)
    for _ in range(lim):
        moved = False
        if y1 < H and a[y1 - 1, x0:x1].any(): y1 += 1; moved = True
        if y0 > 0 and a[y0, x0:x1].any(): y0 -= 1; moved = True
        if x1 < W and a[y0:y1, x1 - 1].any(): x1 += 1; moved = True
        if x0 > 0 and a[y0:y1, x0].any(): x0 -= 1; moved = True
        if not moved: break
    m = 4
    return fitz.Rect(ctx.x0 + (x0 - m) / s, ctx.y0 + (y0 - m) / s, ctx.x0 + (x1 + m) / s, ctx.y0 + (y1 + m) / s) & page.rect
