from pathlib import Path
import re

target = Path("pipeline/stages/semantics.py")
if not target.exists():
    import sys
    sys.exit("Could not find pipeline/stages/semantics.py")

content = target.read_text(encoding="utf-8")

# Convert string/path parameters passed to client.extract or client.parse into raw bytes
old_call = r"client\.(extract|parse)\((\w+)"
new_call = r"client.\1(Path(\2).read_bytes() if isinstance(\2, (str, Path)) else \2"

updated = re.sub(old_call, new_call, content)

if updated != content:
    target.write_text(updated, encoding="utf-8")
    print("Successfully patched pipeline/stages/semantics.py to pass document bytes!")
else:
    print("Checking semantics.py directly...")
