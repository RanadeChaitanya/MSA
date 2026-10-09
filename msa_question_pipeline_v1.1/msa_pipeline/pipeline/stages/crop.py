"""STAGE 6 - Crop assets from the canonical page image and register them."""
from __future__ import annotations

from pathlib import Path

from PIL import Image

from ..models import AssetRef
from ..utils.io import sha256_file
from .visuals import VisualRef


def save_crop(img: Image.Image, ref: VisualRef, page: int, out_path: Path, asset_id: str, pad: int) -> AssetRef:
    W, H = img.size
    b = ref.box.pad(pad).clamp(W, H)
    x0, y0, x1, y1 = b.int_tuple()
    if x1 <= x0 or y1 <= y0:
        return AssetRef(asset_id=asset_id, path="", page=page, bbox_px=ref.box.to_list(), method=ref.method,
                        source_block_ids=ref.source_ids, width=0, height=0)
    crop = img.crop((x0, y0, x1, y1))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    crop.save(out_path, format="PNG")
    return AssetRef(asset_id=asset_id, path=str(out_path), page=page, bbox_px=ref.box.to_list(), method=ref.method,
                    source_block_ids=ref.source_ids, sha256=sha256_file(out_path), width=crop.width, height=crop.height)
