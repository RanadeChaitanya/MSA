"""Run the whole pipeline OFFLINE on a synthetic photo-PDF with a fake Landing AI client.
Proves the wiring and produces overlays/assets without an API key:   python scripts/demo_offline.py"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.config import Settings                      # noqa: E402
from pipeline.runner import process_document              # noqa: E402
from tests.fake_lat import FakeLat                        # noqa: E402
from tests.make_pdf import make_photo_pdf                 # noqa: E402

cfg = Settings()
cfg.data_dir = Path("data")
cfg.api_key = "offline"
pdf = make_photo_pdf(Path("data/demo_paper.pdf"))
out = process_document(pdf, cfg, client=FakeLat(), doc_id="demo_offline")
print("questions:", len(out.questions), "| needs_review:", out.run["needs_review"])
print("open:", Path(cfg.data_dir) / "demo_offline" / "debug" / "overlays")
