import re
from pathlib import Path

target = Path.cwd() / "pipeline" / "stages" / "canonicalize.py"
content = target.read_text(encoding="utf-8")

# Completely clean implementation of _estimate_skew
clean_skew_func = """def _estimate_skew(gray: np.ndarray) -> float:
    small = cv2.resize(gray, None, fx=0.5, fy=0.5) if max(gray.shape) > 2500 else gray
    edges = cv2.Canny(small, 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 720, threshold=150,
                            minLineLength=int(small.shape[1] * 0.15), maxLineGap=20)
    if lines is None:
        return 0.0
    angs = []
    lines = lines.reshape(-1, 4)
    for x1, y1, x2, y2 in lines:
        dx, dy = x2 - x1, y2 - y1
        if dx == 0:
            continue
        ang = np.degrees(np.arctan2(dy, dx))
        if -15.0 < ang < 15.0:
            angs.append(ang)
    return float(np.median(angs)) if angs else 0.0"""

# Replace the entire _estimate_skew function body using regex pattern
pattern = r"def _estimate_skew\(gray: np\.ndarray\).*?return float\(np\.median\(angs\)\) if angs else 0\.0"
updated_content = re.sub(pattern, clean_skew_func, content, flags=re.DOTALL)

target.write_text(updated_content, encoding="utf-8")
print("Successfully clean-rewrote _estimate_skew in canonicalize.py!")
