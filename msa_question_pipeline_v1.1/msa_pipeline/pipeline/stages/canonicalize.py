from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import cv2
import fitz  # PyMuPDF
import numpy as np
from pydantic import BaseModel, ConfigDict, Field

logger = logging.getLogger(__name__)


class CanonicalRecord(BaseModel):
    """Canonical page record returned by canonicalize_pdf.

    Includes all fields expected by runner.py and the test suite:
    page_no, page_num, canonical_path, image_path, source, width,
    height, deskew_deg, skew_angle, flags, profile.
    """

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="allow",  # tolerate extra attrs without raising AttributeError
    )

    page_no: int
    page_num: int
    canonical_path: str
    image_path: str
    source: str = "rendered"
    width: int
    height: int
    deskew_deg: float = 0.0
    skew_angle: float = 0.0
    flags: List[str] = Field(default_factory=list)
    profile: Dict[str, Any] = Field(default_factory=dict)


def _estimate_skew(gray: np.ndarray) -> float:
    small = cv2.resize(gray, None, fx=0.5, fy=0.5) if max(gray.shape) > 2500 else gray
    edges = cv2.Canny(small, 50, 150)
    lines = cv2.HoughLinesP(
        edges,
        1,
        np.pi / 720,
        threshold=150,
        minLineLength=int(small.shape[1] * 0.15),
        maxLineGap=20,
    )
    if lines is None:
        return 0.0
    angs: List[float] = []
    # Normalize shape to (-1, 4) regardless of OpenCV/NumPy version.
    lines = np.asarray(lines).reshape(-1, 4)
    for x1, y1, x2, y2 in lines:
        dx, dy = float(x2) - float(x1), float(y2) - float(y1)
        if dx == 0:
            continue
        ang = float(np.degrees(np.arctan2(dy, dx)))
        if -15.0 < ang < 15.0:
            angs.append(ang)
    return float(np.median(angs)) if angs else 0.0


def _rotate_image(image: np.ndarray, angle: float) -> np.ndarray:
    if abs(angle) < 0.1:
        return image
    h, w = image.shape[:2]
    center = (w // 2, h // 2)
    M = cv2.getRotationMatrix2D(center, angle, 1.0)
    return cv2.warpAffine(
        image,
        M,
        (w, h),
        flags=cv2.INTER_CUBIC,
        borderMode=cv2.BORDER_REPLICATE,
    )


def canonicalize_page(
    doc: fitz.Document,
    page_idx: int,
    out_dir: Path,
    cfg: Any,
    profile: Optional[Dict[str, Any]] = None,
) -> CanonicalRecord:
    page = doc[page_idx]
    pix = page.get_pixmap(dpi=200)
    out_dir.mkdir(parents=True, exist_ok=True)
    img_path = out_dir / f"page_{page_idx + 1:03d}.png"
    pix.save(str(img_path))

    bgr = cv2.imread(str(img_path))
    if bgr is None:
        raise RuntimeError(f"Failed to load rendered image: {img_path}")

    angle = _estimate_skew(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))
    if abs(angle) >= 0.1:
        bgr = _rotate_image(bgr, angle)
        cv2.imwrite(str(img_path), bgr)

    # Detect if page consists of embedded photos/images (photo PDFs)
    images = page.get_images()
    source_type = "embedded" if images else "rendered"

    h, w = bgr.shape[:2]
    p_num = page_idx + 1
    path_str = str(img_path)

    prof = dict(profile) if profile else {}
    prof.update({"width": w, "height": h, "page_no": p_num, "skew_angle": angle})

    return CanonicalRecord(
        page_no=p_num,
        page_num=p_num,
        canonical_path=path_str,
        image_path=path_str,
        source=source_type,
        width=w,
        height=h,
        deskew_deg=angle,
        skew_angle=angle,
        flags=[],
        profile=prof,
    )


def canonicalize_pdf(
    pdf_path: Path,
    out_dir: Path,
    cfg: Any,
    pages: Optional[List[int]] = None,
    profile: Optional[Dict[str, Any]] = None,
) -> List[CanonicalRecord]:
    doc = fitz.open(str(pdf_path))
    target_pages = pages if pages is not None else list(range(1, len(doc) + 1))
    records: List[CanonicalRecord] = []
    for p in target_pages:
        records.append(canonicalize_page(doc, p - 1, out_dir, cfg, profile))
    doc.close()
    return records
