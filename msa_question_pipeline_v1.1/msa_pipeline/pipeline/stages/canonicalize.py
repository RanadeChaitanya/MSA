"""STAGE 0 - Canonicalization.

Turns every PDF page into ONE canonical RGB PNG. That PNG is the *only* image that is ever
(a) sent to Landing AI and (b) cropped for assets, so boxes and pixels always agree.

Order of operations per page:
  1. get raster: embedded full-page photo at native resolution, else render at RENDER_DPI
  2. EXIF/rotation baked into pixels (Landing AI would otherwise re-apply EXIF itself)
  3. optional perspective rectification (photos) -> homography stored
  4. optional illumination normalisation (shadows)  [never binarise: it destroys figures]
  5. optional small-angle deskew
  6. quality metrics + flags
"""
from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Optional

import cv2
import numpy as np
import pymupdf
from PIL import Image, ImageOps

from ..config import Settings
from ..models import PageRecord

log = logging.getLogger("pipeline.canon")

# Profiles are the knobs the retry ladder turns.
PROFILES: dict[str, dict] = {
    "default": {},
    "no_rectify": {"photo_rectify": "off", "deskew": False, "normalize_illumination": False},
    "force_rectify": {"photo_rectify": "force"},
    "hires": {"scale": 1.5, "photo_rectify": "off"},
}


def _decode(data: bytes) -> Image.Image:
    img = Image.open(__import__("io").BytesIO(data))
    img = ImageOps.exif_transpose(img)
    return img.convert("RGB")


def _embedded_raster(doc, page, cfg: Settings) -> Optional[Image.Image]:
    """Full-page photo/scan stored as one image -> take native pixels (no re-render blur)."""
    try:
        infos = page.get_images(full=True)
    except Exception:
        return None
    page_area = page.rect.width * page.rect.height
    best = None
    for info in infos:
        xref = info[0]
        try:
            rects = page.get_image_rects(xref)
        except Exception:
            continue
        area = sum(r.width * r.height for r in rects)
        if best is None or area > best[1]:
            best = (xref, area)
    if not best or page_area <= 0 or best[1] / page_area < 0.85:
        return None
    try:
        img = _decode(doc.extract_image(best[0])["image"])
    except Exception as e:
        log.debug("embedded decode failed: %s", e)
        return None
    if max(img.size) < cfg.embedded_min_long_side:
        return None
    if page.rotation:
        img = img.rotate(-page.rotation, expand=True)
    return img


def _render(page, dpi: float, max_long: int) -> Image.Image:
    zoom = dpi / 72.0
    w, h = page.rect.width * zoom, page.rect.height * zoom
    if max(w, h) > max_long:
        zoom *= max_long / max(w, h)
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


# ---------------- rectification ----------------
def _order_pts(pts: np.ndarray) -> np.ndarray:
    s, d = pts.sum(axis=1), np.diff(pts, axis=1).ravel()
    return np.array([pts[np.argmin(s)], pts[np.argmin(d)], pts[np.argmax(s)], pts[np.argmax(d)]], dtype="float32")


def _find_page_quad(bgr: np.ndarray, min_area_frac: float) -> Optional[np.ndarray]:
    h, w = bgr.shape[:2]
    sc = 1000.0 / max(h, w)
    small = cv2.resize(bgr, (int(w * sc), int(h * sc)))
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_BGR2GRAY), (5, 5), 0)
    edges = cv2.dilate(cv2.Canny(gray, 50, 150), np.ones((3, 3), np.uint8), iterations=2)
    cnts, _ = cv2.findContours(edges, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    area_img = small.shape[0] * small.shape[1]
    for c in sorted(cnts, key=cv2.contourArea, reverse=True)[:6]:
        approx = cv2.approxPolyDP(c, 0.02 * cv2.arcLength(c, True), True)
        if len(approx) == 4 and cv2.contourArea(approx) >= min_area_frac * area_img:
            return _order_pts(approx.reshape(4, 2).astype("float32")) / sc
    return None


def _quad_is_trivial(quad: np.ndarray, w: int, h: int) -> bool:
    """A flat, axis-aligned, edge-to-edge quad means 'nothing to rectify' (digital scan)."""
    tl, tr, br, bl = quad
    angs = [math.degrees(math.atan2(b[1] - a[1], b[0] - a[0])) for a, b in ((tl, tr), (bl, br))]
    angs += [math.degrees(math.atan2(b[0] - a[0], b[1] - a[1])) for a, b in ((tl, bl), (tr, br))]
    axis_aligned = max(abs(a) for a in angs) < 1.5
    top, bot = np.linalg.norm(tr - tl), np.linalg.norm(br - bl)
    lft, rgt = np.linalg.norm(bl - tl), np.linalg.norm(br - tr)
    keystone = max(top / max(bot, 1), bot / max(top, 1), lft / max(rgt, 1), rgt / max(lft, 1))
    return axis_aligned and keystone < 1.03


def _rectify(bgr: np.ndarray, mode: str):
    if mode == "off":
        return bgr, None
    quad = _find_page_quad(bgr, 0.40 if mode == "force" else 0.55)
    if quad is None:
        return bgr, None
    h, w = bgr.shape[:2]
    if mode == "auto" and _quad_is_trivial(quad, w, h):
        return bgr, None
    tl, tr, br, bl = quad
    W = int(max(np.linalg.norm(br - bl), np.linalg.norm(tr - tl)))
    H = int(max(np.linalg.norm(tr - br), np.linalg.norm(tl - bl)))
    if W < 200 or H < 200:
        return bgr, None
    dst = np.array([[0, 0], [W - 1, 0], [W - 1, H - 1], [0, H - 1]], dtype="float32")
    M = cv2.getPerspectiveTransform(quad, dst)
    return cv2.warpPerspective(bgr, M, (W, H), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255)), M


def _normalize_illum(bgr: np.ndarray) -> np.ndarray:
    lab = cv2.cvtColor(bgr, cv2.COLOR_BGR2LAB)
    L = lab[..., 0]
    k = max(15, (min(L.shape) // 60) | 1)
    bg = cv2.dilate(L, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    bg = cv2.GaussianBlur(bg, (0, 0), sigmaX=max(L.shape) / 40)
    lab[..., 0] = cv2.divide(L, bg, scale=245)
    return cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)


def _estimate_skew(gray: np.ndarray) -> float:
    small = cv2.resize(gray, None, fx=0.5, fy=0.5) if max(gray.shape) > 2500 else gray
    edges = cv2.Canny(small, 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 720, threshold=150,
                            minLineLength=int(small.shape[1] * 0.15), maxLineGap=20)
    if lines is None:
        return 0.0
    angs = []
    # OpenCV <5 returns (N,1,4), OpenCV 5 returns (N,4): normalise both
    for x1, y1, x2, y2 in np.asarray(lines).reshape(-1, 4).astype(float):
        a = math.degrees(math.atan2(y2 - y1, x2 - x1))
        if abs(a) < 15:
            angs.append(a)
    return float(np.median(angs)) if len(angs) >= 5 else 0.0


def _rotate(bgr: np.ndarray, deg: float) -> np.ndarray:
    h, w = bgr.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), deg, 1.0)
    return cv2.warpAffine(bgr, M, (w, h), flags=cv2.INTER_CUBIC, borderValue=(255, 255, 255))


def _blur_score(bgr: np.ndarray) -> float:
    g = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    if max(g.shape) > 2000:
        g = cv2.resize(g, None, fx=2000 / max(g.shape), fy=2000 / max(g.shape))
    return float(cv2.Laplacian(g, cv2.CV_64F).var())


# ---------------- public ----------------
def canonicalize_page(doc, page_idx: int, out_dir: Path, cfg: Settings, profile: str = "default") -> PageRecord:
    ov = PROFILES.get(profile, {})
    rectify = ov.get("photo_rectify", cfg.photo_rectify)
    do_illum = ov.get("normalize_illumination", cfg.normalize_illumination)
    do_deskew = ov.get("deskew", cfg.deskew)
    scale = ov.get("scale", 1.0)

    page = doc[page_idx]
    img = _embedded_raster(doc, page, cfg)
    source = "embedded"
    if img is None:
        source = "render"
        img = _render(page, cfg.render_dpi * scale, int(cfg.max_long_side * max(scale, 1.0)))
    elif scale != 1.0:
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)

    bgr = cv2.cvtColor(np.array(img), cv2.COLOR_RGB2BGR)
    H = None
    deskew_deg = 0.0
    if source == "embedded":                       # only photos/scans need geometric repair
        bgr, H = _rectify(bgr, rectify)
        if do_illum:
            bgr = _normalize_illum(bgr)
        if do_deskew and H is None:
            a = _estimate_skew(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))
            if 0.3 < abs(a) < 8:
                bgr = _rotate(bgr, a)
                deskew_deg = a
    h, w = bgr.shape[:2]
    if max(h, w) > cfg.max_long_side * max(scale, 1.0):
        f = cfg.max_long_side * max(scale, 1.0) / max(h, w)
        bgr = cv2.resize(bgr, (int(w * f), int(h * f)), interpolation=cv2.INTER_AREA)
        h, w = bgr.shape[:2]

    blur = _blur_score(bgr)
    flags: list[str] = []
    if blur < cfg.blur_threshold:
        flags.append("low_sharpness")
    if min(h, w) < cfg.min_short_side_px:
        flags.append("low_resolution")

    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = "" if profile == "default" else f"__{profile}"
    path = out_dir / f"page_{page_idx + 1:04d}{suffix}.png"
    cv2.imwrite(str(path), bgr)
    return PageRecord(page_no=page_idx + 1, canonical_path=str(path), width=w, height=h, source=source,
                      profile=profile, rectified=H is not None, deskew_deg=round(deskew_deg, 3),
                      homography=None if H is None else H.tolist(), blur=round(blur, 2), flags=flags)


def canonicalize_pdf(pdf_path: Path, out_dir: Path, cfg: Settings, pages: Optional[list[int]] = None,
                     profile: str = "default") -> list[PageRecord]:
    doc = pymupdf.open(str(pdf_path))
    try:
        wanted = pages or list(range(1, doc.page_count + 1))
        recs = []
        for p in wanted:
            if not 1 <= p <= doc.page_count:
                raise ValueError(f"page {p} out of range 1..{doc.page_count}")
            rec = canonicalize_page(doc, p - 1, out_dir, cfg, profile)
            log.info("page %d: %s %dx%d blur=%.0f rect=%s skew=%.2f %s", p, rec.source, rec.width, rec.height,
                     rec.blur, rec.rectified, rec.deskew_deg, rec.flags or "")
            recs.append(rec)
        return recs
    finally:
        doc.close()
