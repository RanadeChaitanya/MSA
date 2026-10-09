"""Debug overlays: draw every box the pipeline decided on top of each canonical page.
blue=question region  green=option box  red=question image  orange=option image  gray=watermark/ignored."""
from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


def draw_overlays(ctx, drafts_by_page: dict[int, list], issues: dict[int, list[str]], out_dir: Path) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for p, rec in sorted(ctx.records.items()):
        img = Image.open(rec.canonical_path).convert("RGB")
        sc = min(1.0, 1600 / img.width)
        if sc < 1:
            img = img.resize((int(img.width * sc), int(img.height * sc)))
        dr = ImageDraw.Draw(img)

        def rect(b, color, w=3):
            dr.rectangle([b.x0 * sc, b.y0 * sc, b.x1 * sc, b.y1 * sc], outline=color, width=w)

        for d in drafts_by_page.get(p, []):
            rect(d.region.box, (30, 90, 220), 3)
            dr.text((d.region.box.x0 * sc + 4, d.region.box.y0 * sc + 2), f"Q{d.number}", fill=(30, 90, 220))
            for L, o in d.layout.options.items():
                rect(o.box, (20, 160, 60), 2)
                dr.text((o.box.x0 * sc + 3, o.box.y0 * sc + 2), L, fill=(20, 160, 60))
            for r in d.q_visuals:
                rect(r.box, (220, 30, 30), 4)
            for L, o in d.options.items():
                if o.ref is not None:
                    rect(o.ref.box, (255, 140, 0), 3)
            if d.flags:
                dr.text((d.region.box.x0 * sc + 40, d.region.box.y0 * sc + 2), ",".join(d.flags)[:70], fill=(200, 0, 0))
        if issues.get(p):
            dr.text((10, 10), "PAGE ISSUES: " + ", ".join(issues[p])[:150], fill=(200, 0, 0))
        path = out_dir / f"overlay_page_{p:04d}.png"
        img.save(path)
        paths.append(str(path))
    return paths
