import re, shutil, sys
from pathlib import Path

target = Path.cwd() / "pipeline" / "stages" / "canonicalize.py"
backup = Path.cwd() / "pipeline" / "stages" / "canonicalize.py.bak_v1_2"

# 1. Restore clean original file from backup if available
if backup.exists():
    shutil.copy2(backup, target)
    print("Restored canonicalize.py from backup.")

content = target.read_text(encoding="utf-8")

# 2. Fix the OpenCV / NumPy HoughLinesP unpacking issue safely
old_line = "for x1, y1, x2, y2 in lines[:, 0]:"
new_lines = "lines = lines.reshape(-1, 4)\n        for x1, y1, x2, y2 in lines:"

if old_line in content:
    content = content.replace(old_line, new_lines)
    target.write_text(content, encoding="utf-8")
    print("Successfully applied OpenCV/NumPy version fix to canonicalize.py!")
elif "lines = lines.reshape(-1, 4)" in content:
    print("Fix is already present in canonicalize.py!")
else:
    print("Warning: Could not locate target line. Checking file manually...")
