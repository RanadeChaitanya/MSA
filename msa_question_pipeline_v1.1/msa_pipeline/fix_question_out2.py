import re, shutil, importlib
from pathlib import Path

p = Path("pipeline/stages/assemble.py")
src = p.read_text(encoding="utf-8")
bak = Path(str(p) + ".bak_v1_6")
if not bak.exists():
    shutil.copy2(p, bak)
    print(f"[backup] {bak.name}")

# 1. Find every class definition and print its name + line
print("[classes]")
for m in re.finditer(r"^class\s+(\w+)", src, re.MULTILINE):
    print(f"  line {src[:m.start()].count(chr(10))+1}: {m.group(1)}")

# 2. Slice out the QuestionOut class block (from `class QuestionOut` to next top-level `class`/`def`)
m = re.search(r"^class\s+QuestionOut\b[\s\S]*?(?=^\S|\Z)", src, re.MULTILINE)
if not m:
    raise SystemExit("FATAL: class QuestionOut not found")
block = m.group(0)
print(f"[found] QuestionOut block, {block.count(chr(10))+1} lines")
print("------ QuestionOut.profile BEFORE ------")
for line in block.splitlines():
    if "profile" in line:
        print("  " + line)

# 3. Patch ONLY the `profile:` line inside QuestionOut
new_block, n = re.subn(
    r"^(\s*)profile\s*:.*$",
    r"\1profile: Any = None",
    block, count=1, flags=re.MULTILINE,
)
if n == 0:
    raise SystemExit("FATAL: no `profile:` field inside QuestionOut")
print(f"[patched] {n} profile field -> Any = None")

# 4. Splice it back
src = src[:m.start()] + new_block + src[m.end():]

# 5. Ensure `Any` is imported (never before `from __future__`)
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

# 6. Verify
importlib.invalidate_caches()
import pipeline.stages.assemble as asm
importlib.reload(asm)
ann = asm.QuestionOut.model_fields["profile"].annotation
print(f"[verify] QuestionOut.profile -> {ann}")
assert "any" in str(ann).lower(), "still not permissive"
print("[OK] QuestionOut.profile is now Any")
