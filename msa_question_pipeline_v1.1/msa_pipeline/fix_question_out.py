import re, shutil, importlib
from pathlib import Path

p = Path("pipeline/models.py")
if not p.exists():
    raise SystemExit(f"FATAL: {p} does not exist")

src = p.read_text(encoding="utf-8")
bak = Path(str(p) + ".bak_v1_8")
if not bak.exists():
    shutil.copy2(p, bak)
    print(f"[backup] {bak.name}")

# 1. Show the QuestionOut class block
m = re.search(r"^class\s+QuestionOut\b[\s\S]*?(?=^\S|\Z)", src, re.MULTILINE)
if not m:
    raise SystemExit("FATAL: class QuestionOut not found in models.py")

block = m.group(0)
print("------ QuestionOut block ------")
for line in block.splitlines():
    print("  " + line)

# 2. Replace `profile: <anything>` with `profile: Any = None`
new_block, n = re.subn(
    r"^(\s*)profile\s*:.*$",
    r"\1profile: Any = None",
    block, count=1, flags=re.MULTILINE,
)
if n == 0:
    # No profile field: insert it right after the class line
    new_block = re.sub(
        r"(^class\s+QuestionOut[^\n]*\n)",
        r"\1    profile: Any = None\n",
        block, count=1, flags=re.MULTILINE,
    )
    n = 1
print(f"[patched] profile field ({n} change)")

src = src[:m.start()] + new_block + src[m.end():]

# 3. Ensure Any is imported from typing
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

# 4. Verify
importlib.invalidate_caches()
import pipeline.models as models
importlib.reload(models)
ann = models.QuestionOut.model_fields["profile"].annotation
print(f"[verify] QuestionOut.profile -> {ann}")
assert "any" in str(ann).lower(), "still not permissive"
print("[OK] models.py patched & verified")
