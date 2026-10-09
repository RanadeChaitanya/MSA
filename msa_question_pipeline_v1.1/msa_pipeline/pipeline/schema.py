"""Canonical output schema (v2): `data/<doc_id>/document.json`.

Design rules
  * Everything needed to rebuild a question deterministically, nothing else (no raw Landing AI fields,
    no unstable block ids, no machine paths, no pixel sizes of a particular render).
  * ORDER is data: `stem` and every option's `content` are ordered component lists.
  * Images are referenced by stable asset id from exactly one place; the asset records its owner
    (question + stem/option), so an image cannot silently move to another question or option.
  * Geometry is normalised to the page (0..1, origin top-left), so it is independent of render DPI.
  * Uncertainty is recorded, never hidden: `certain=false` on assets/positions + question `flags`.

Text format of `text` components ("md_latex"): plain text with inline LaTeX in `$...$` and HTML <sub>/<sup>.
Display equations ($$...$$) and tables (<table>...</table>) are their own components.
"""
from __future__ import annotations

from typing import Annotated, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = "2.0"
OptionLabel = Literal["A", "B", "C", "D", "E"]
NormBox = Annotated[list[float], Field(min_length=4, max_length=4, description="[x0,y0,x1,y1] as page fractions")]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------- ordered components ----------------
class TextComponent(_Strict):
    type: Literal["text"] = "text"
    text: str = Field(min_length=1)


class EquationComponent(_Strict):
    type: Literal["equation"] = "equation"
    latex: str = Field(min_length=1, description="display equation, without the $$ delimiters")


class TableComponent(_Strict):
    type: Literal["table"] = "table"
    html: str = Field(min_length=1, description="<table>...</table> exactly as extracted")


class ImageComponent(_Strict):
    type: Literal["image"] = "image"
    asset_id: str


Component = Annotated[Union[TextComponent, EquationComponent, TableComponent, ImageComponent],
                      Field(discriminator="type")]


# ---------------- questions ----------------
class SourceRef(_Strict):
    pages: list[int] = Field(min_length=1, description="1-based PDF page numbers the question occupies")
    bbox: Optional[NormBox] = Field(None, description="question region on pages[0]; null if not located")


class Option(_Strict):
    label: OptionLabel
    content: list[Component] = Field(description="ordered; may hold 0..n image components")


class Answer(_Strict):
    label: OptionLabel
    source: Literal["printed_inline", "answer_key"]


class Question(_Strict):
    qid: str = Field(pattern=r"^p\d{4}-[qx]\d{3}(-\d+)?$", description="stable within the document")
    number: Optional[int] = Field(None, description="printed question number")
    section: Optional[str] = Field(None, description="chapter/section title printed in the PDF, if detected")
    source: SourceRef
    stem: list[Component]
    options: list[Option] = Field(description="in label order A, B, C, D")
    answer: Optional[Answer] = None
    confidence: float = Field(ge=0, le=1)
    flags: list[str] = Field(default_factory=list, description="warnings/errors; see README section 8")


# ---------------- assets ----------------
class Owner(_Strict):
    qid: str
    role: Literal["stem", "option"]
    option: Optional[OptionLabel] = None


class Asset(_Strict):
    path: str = Field(pattern=r"^assets/[^\\:]+\.png$", description="relative to the document directory, '/' only")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    page: int = Field(ge=1)
    bbox: NormBox
    owner: Owner
    method: Literal["pdf_figure", "parse_figure", "repass_figure", "table_crop", "ink_fallback", "math_text_crop",
                    "option_region"]
    certain: bool = Field(description="false: fallback crop (whole option cell / ink bbox) or estimated association")
    description: str = Field("", description="Landing AI figure description; '' if none was produced")


class SourceDoc(_Strict):
    filename: str
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    page_count: int = Field(ge=1)
    pages_processed: list[int]


class Document(_Strict):
    schema_version: Literal["2.0"] = SCHEMA_VERSION
    doc_id: str
    source: SourceDoc
    text_format: Literal["md_latex"] = "md_latex"
    questions: list[Question]
    assets: dict[str, Asset] = Field(description="asset_id -> asset; every asset is referenced exactly once")


def json_schema() -> dict:
    return Document.model_json_schema()
