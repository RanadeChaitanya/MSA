"""Central configuration. Every tunable lives here and is overridable via .env."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv


def _env(name: str, default=None):
    v = os.getenv(name)
    return default if v is None or v == "" else v


def _bool(name: str, default: bool) -> bool:
    v = _env(name)
    return default if v is None else v.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    v = _env(name)
    return default if v is None else int(v)


def _float(name: str, default: float) -> float:
    v = _env(name)
    return default if v is None else float(v)


def _list(name: str, default: list[str]) -> list[str]:
    v = _env(name)
    return default if v is None else [s for s in v.split("||") if s]


@dataclass
class Settings:
    # Landing AI
    api_key: str = "APIKEYHERE"
    base_url: str = "https://api.ade.landing.ai"
    parse_model: str = "dpt-3-pro-latest"
    verity_model: str = "dpt-3-verity-latest"
    extract_model: str = "extract-latest"
    # storage
    data_dir: Path = Path("data")
    # canonicalization
    render_dpi: int = 300
    max_long_side: int = 6000
    embedded_min_long_side: int = 1500
    photo_rectify: str = "auto"  # auto | off | force
    normalize_illumination: bool = True
    deskew: bool = True
    min_short_side_px: int = 1000
    blur_threshold: float = 60.0
    # parsing
    batch_pages: int = 10
    max_upload_mb: float = 45.0
    http_timeout: int = 300
    max_retries: int = 4
    # policy / geometry
    option_image_mode: str = "visual_only"  # visual_only | visual_or_math | always
    asset_pad_px: int = 8
    min_asset_px: int = 24
    repass_min_side: int = 1000
    repass_max_per_page: int = 4         # photo pages only; all candidates of a page go in ONE mosaic call
    pdf_text: bool = True                # born-digital pages: text from the PDF layer, no Parse call
    docling: bool = False                # picture-question pages: text from local Docling OCR first, Parse fallback
    extract_mode: str = "auto"           # auto: local split, Extract only for unverified pages | extract | local
    describe_images: bool = True         # one mosaic call per page whose images Landing AI has not described yet
    parse_source: str = "auto"           # auto: digital pages are uploaded as original PDF pages | canonical
    ink_threshold: int = 120
    ink_min_bbox_frac: float = 0.002
    gutter_cluster_tol: float = 0.025
    min_anchor_gap_frac: float = 0.04
    min_cluster_size: int = 2
    last_region_pad_frac: float = 0.03
    # extras
    qa_verity: bool = False
    ladder_profiles: list[str] = field(default_factory=lambda: ["no_rectify", "force_rectify", "hires"])
    ladder_max_pages: int = 20
    debug_overlays: bool = True
    overlays_all: bool = False           # False: overlays only for pages with errors (rendering every page is slow)
    log_level: str = "INFO"
    watermark_patterns: list[str] = field(default_factory=lambda: [r"matrix\s*science\s*academy", r"^\s*MSA\s*$"])
    answer_markers: list[str] = field(default_factory=lambda: [r"correct\s*option", r"^\s*answer\s*[:.]", r"^\s*ans\s*[:.]"])

    @classmethod
    def load(cls, env_file: str | None = ".env") -> "Settings":
        if env_file and Path(env_file).exists():
            load_dotenv(env_file, override=False)
        d = cls()
        return cls(
            api_key=_env("LANDING_API_KEY", d.api_key),
            base_url=_env("LANDING_BASE_URL", d.base_url),
            parse_model=_env("PARSE_MODEL", d.parse_model),
            verity_model=_env("VERITY_MODEL", d.verity_model),
            extract_model=_env("EXTRACT_MODEL", d.extract_model),
            data_dir=Path(_env("DATA_DIR", str(d.data_dir))),
            render_dpi=_int("RENDER_DPI", d.render_dpi),
            max_long_side=_int("MAX_LONG_SIDE", d.max_long_side),
            embedded_min_long_side=_int("EMBEDDED_MIN_LONG_SIDE", d.embedded_min_long_side),
            photo_rectify=_env("PHOTO_RECTIFY", d.photo_rectify),
            normalize_illumination=_bool("NORMALIZE_ILLUMINATION", d.normalize_illumination),
            deskew=_bool("DESKEW", d.deskew),
            min_short_side_px=_int("MIN_SHORT_SIDE_PX", d.min_short_side_px),
            blur_threshold=_float("BLUR_THRESHOLD", d.blur_threshold),
            batch_pages=_int("BATCH_PAGES", d.batch_pages),
            max_upload_mb=_float("MAX_UPLOAD_MB", d.max_upload_mb),
            http_timeout=_int("HTTP_TIMEOUT", d.http_timeout),
            max_retries=_int("MAX_RETRIES", d.max_retries),
            option_image_mode=_env("OPTION_IMAGE_MODE", d.option_image_mode),
            asset_pad_px=_int("ASSET_PAD_PX", d.asset_pad_px),
            min_asset_px=_int("MIN_ASSET_PX", d.min_asset_px),
            repass_min_side=_int("REPASS_MIN_SIDE", d.repass_min_side),
            repass_max_per_page=_int("REPASS_MAX_PER_PAGE", d.repass_max_per_page),
            describe_images=_bool("DESCRIBE_IMAGES", d.describe_images),
            extract_mode=_env("EXTRACT_MODE", d.extract_mode),
            pdf_text=_bool("PDF_TEXT", d.pdf_text),
            docling=_bool("DOCLING", d.docling),
            parse_source=_env("PARSE_SOURCE", d.parse_source),
            ink_threshold=_int("INK_THRESHOLD", d.ink_threshold),
            ink_min_bbox_frac=_float("INK_MIN_BBOX_FRAC", d.ink_min_bbox_frac),
            gutter_cluster_tol=_float("GUTTER_CLUSTER_TOL", d.gutter_cluster_tol),
            min_anchor_gap_frac=_float("MIN_ANCHOR_GAP_FRAC", d.min_anchor_gap_frac),
            min_cluster_size=_int("MIN_CLUSTER_SIZE", d.min_cluster_size),
            last_region_pad_frac=_float("LAST_REGION_PAD_FRAC", d.last_region_pad_frac),
            qa_verity=_bool("QA_VERITY", d.qa_verity),
            ladder_profiles=[x.strip() for x in _env("LADDER_PROFILES", ",".join(d.ladder_profiles)).split(",") if x.strip()],
            ladder_max_pages=_int("LADDER_MAX_PAGES", d.ladder_max_pages),
            debug_overlays=_bool("DEBUG_OVERLAYS", d.debug_overlays),
            overlays_all=_bool("OVERLAYS_ALL", d.overlays_all),
            log_level=_env("LOG_LEVEL", d.log_level),
            watermark_patterns=_list("WATERMARK_PATTERNS", d.watermark_patterns),
            answer_markers=_list("ANSWER_MARKERS", d.answer_markers),
        )
