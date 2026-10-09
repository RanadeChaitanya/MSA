import re
from pathlib import Path

root = Path.cwd()
target = root / "pipeline" / "stages" / "canonicalize.py"

if not target.exists():
    import sys
    sys.exit("Run this command from the folder containing the 'pipeline' directory.")

content = target.read_text(encoding="utf-8")

# Safe reshape logic for HoughLinesP output
old_pattern = r"for x1, y1, x2, y2 in lines\[:, 0\]:"
new_pattern = "lines = lines.reshape(-1, 4)\n        for x1, y1, x2, y2 in lines:"

if "lines = lines.reshape(-1, 4)" not in content:
    content = re.sub(old_pattern, new_pattern, content)
    target.write_text(content, encoding="utf-8")
    print("Fixed _estimate_skew in canonicalize.py!")
else:
    print("Fix already applied!")
