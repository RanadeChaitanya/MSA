import re, shutil, importlib
from pathlib import Path

p = Path("pipeline/models.py")
if not p.exists():
    raise SystemExit("FATAL: pipeline/models.py not found")

src = p.read_text(encoding="utf-8")
bak = Path(str(p) + ".bak_v1_9")
if not bak.exists():
    shutil.copy2(p, bak)
    print(f"[backup] {bak.name}")

# Show every `pages:` annotation anywhere in the file
print("[scan] all `pages:` field lines in models.py:")
for i, line in enumerate(src.splitlines(), 1):
    if re.match(r"^\s*pages\s*:", line):
        print(f"  {i}: {line}")

# Slice out DocumentOut class
m = re.search(r"^class\s+DocumentOut\b[\s\S]*?(?=^\S|\Z)", src, re.MULTILINE)
if not m:
    raise SystemExit("FATAL: class DocumentOut not found in models.py")
block = m.group(0)
print("------ DocumentOut.pages BEFORE ------")
for line in block.splitlines():
    if re.match(r"^\s*pages\s*:", line):
        print("  " + line)

# Replace `pages: list[PageRecord]` (any capitalization) -> `pages: list[Any]`
new_block, n = re.subn(
    r"^(\s*)pages\s*:\s*(?:list|List)\s*\[\s*PageRecord\s*\]",
    r"\1pages: list[Any]",
    block, count=1, flags=re.MULTILINE,
)
if n == 0:
    # Fallback: any `pages:` line inside DocumentOut
    new_block, n = re.subn(
        r"^(\s*)pages\s*:.*$",
        r"\1pages: list[Any] = []",
        block, count=1, flags=re.MULTILINE,
    )
if n == 0:
    raise SystemExit("FATAL: no `pages:` field inside DocumentOut")

print(f"[patched] DocumentOut.pages -> list[Any] ({n} site)")
src = src[:m.start()] + new_block + src[m.end():]

# Ensure `Any` is imported
if not re.search(r"^from typing import .*\bAny\b", src, re.MULTILINE):
    if re.search(r"^from typing import ", src, re.MULTILINE):
        src = re.sub(
            r"^from typing import (.+)$",
            lambda mm: "from typing import Any, " + mm.group(1),
            src, count=1, flags=re.MULTILINE,
        )
    elif "from __future__ import annotations" in src:
        src = src.replace(
            "from __future__ import annotations",
            "from __future__ import annotations\nfrom typing import Any",
            1,
        )
    else:
        src = "from typing import Any\n" + src
    print("[patch] ensured `from typing import Any`")

p.write_text(src, encoding="utf-8")

# Verify
importlib.invalidate_caches()
import pipeline.models as models
importlib.reload(models)
ann = models.DocumentOut.model_fields["pages"].annotation
print(f"[verify] DocumentOut.pages -> {ann}")
print("[OK] models.py is clean")
