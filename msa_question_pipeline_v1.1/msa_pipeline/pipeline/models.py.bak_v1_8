"""Pydantic data contracts that cross stage boundaries and the final JSON output."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class PageRecord(BaseModel):
    """One canonical page image. THE coordinate space for everything downstream."""
    page_no: int                       # 1-indexed, global within the document
    canonical_path: str
    width: int
    height: int
    source: str                        # "embedded" (raster pulled from PDF) | "render"
    profile: str = "default"
    rectified: bool = False
    deskew_deg: float = 0.0
    homography: Optional[list[list[float]]] = None   # original raster -> canonical (if rectified)
    blur: float = 0.0
    flags: list[str] = Field(default_factory=list)


class AssetRef(BaseModel):
    asset_id: str
    path: str
    page: int
    bbox_px: list[float]               # box on the canonical page image (before padding)
    method: str                        # parse_figure | repass_figure | ink_fallback | math_text_crop | option_region
    source_block_ids: list[str] = Field(default_factory=list)
    sha256: str = ""
    width: int = 0
    height: int = 0


class OptionOut(BaseModel):
    text: str = ""
    image: Optional[AssetRef] = None
    image_only: bool = False


class QuestionOut(BaseModel):
    qid: str
    number: Optional[int] = None
    number_text: str = ""
    page: int
    column: int = 0
    bbox_px: Optional[list[float]] = None      # None when the question could not be located on the page
    text: str = ""
    correct_option: Optional[str] = None
    answer_text: str = ""
    question_images: list[AssetRef] = Field(default_factory=list)
    options: dict[str, OptionOut] = Field(default_factory=dict)
    confidence: float = 1.0
    ocr_confidence: Optional[float] = None
    profile: str = "default"
    flags: list[str] = Field(default_factory=list)


class DocumentOut(BaseModel):
    doc_id: str
    source_pdf: str
    page_count: int
    pages: list[PageRecord]
    questions: list[QuestionOut]
    page_issues: dict[str, list[str]] = Field(default_factory=dict)
    run: dict = Field(default_factory=dict)
