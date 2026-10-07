"""Create a synthetic 'photo PDF': one full-page raster (slightly skewed + shadow) embedded in a PDF."""
from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pymupdf

from .fake_lat import FakeLat, H, W


def make_photo_pdf(path: Path, skew_deg: float = 0.0) -> Path:
    fk = FakeLat()
    img = np.full((H, W, 3), 255, np.uint8)
    for b in fk.blocks:
        x0, y0, x1, y1 = [int(v) for v in b["box"]]
        if b["type"] == "text" and b["text"] != "Matrix Science Academy":
            cv2.rectangle(img, (x0, y0 + 4), (x1 - 4, y1 - 4), (0, 0, 0), -1)      # fake 'ink' for text
        elif b["type"] == "figure":
            cv2.circle(img, ((x0 + x1) // 2, (y0 + y1) // 2), max(10, min(x1 - x0, y1 - y0) // 2 - 4), (0, 0, 0), 5)
    # the figure the parser MISSES (option C of Q3): draw ink only
    q3 = fk.questions[2]
    cb = next(b for b in fk.blocks if b["id"] == q3["opts"]["C"][0])["box"]   # label of C
    cx, cy = int(cb[0]) + 80, int(cb[1]) + 50
    cv2.circle(img, (cx, cy), 36, (0, 0, 0), 6)
    cv2.line(img, (cx - 36, cy), (cx + 36, cy), (0, 0, 0), 5)
    cv2.line(img, (cx, cy - 36), (cx, cy + 36), (0, 0, 0), 5)
    # table-like ruling lines under each question (must be ignored by ink check)
    for y in (400, 880, 1320):
        cv2.line(img, (60, y), (1140, y), (0, 0, 0), 3)
    if skew_deg:
        M = cv2.getRotationMatrix2D((W / 2, H / 2), skew_deg, 1.0)
        img = cv2.warpAffine(img, M, (W, H), borderValue=(255, 255, 255))
    png = path.with_suffix(".png")
    cv2.imwrite(str(png), img)
    doc = pymupdf.open()
    page = doc.new_page(width=595, height=842)
    page.insert_image(page.rect, filename=str(png))
    doc.save(str(path))
    return path
