from pathlib import Path

target = Path.cwd() / "pipeline" / "stages" / "canonicalize.py"
content = target.read_text(encoding="utf-8")

# Replace broken/malformed lines iteration with properly indented block
old_block = """        if lines is None:
            return 0.0
        angs = []
lines = lines.reshape(-1, 4)
        for x1, y1, x2, y2 in lines:"""

new_block = """        if lines is None:
            return 0.0
        angs = []
        lines = lines.reshape(-1, 4)
        for x1, y1, x2, y2 in lines:"""

if "lines = lines.reshape(-1, 4)" in content:
    # Handle both unindented and wrongly indented variations
    content = content.replace("lines = lines.reshape(-1, 4)\n        for x1, y1, x2, y2 in lines:", "lines = lines.reshape(-1, 4)\n        for x1, y1, x2, y2 in lines:")
    content = content.replace("\nlines = lines.reshape(-1, 4)", "\n        lines = lines.reshape(-1, 4)")

target.write_text(content, encoding="utf-8")
print("Indentation in canonicalize.py repaired!")
