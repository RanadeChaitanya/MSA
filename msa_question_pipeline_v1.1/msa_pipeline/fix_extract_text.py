import re
from pathlib import Path

# ── 1. Patch semantics.py: pass text= explicitly ──────────────────────
sem = Path("pipeline/stages/semantics.py")
sem_src = sem.read_text(encoding="utf-8")
bak_sem = Path(str(sem) + ".bak_extractfix")
if not bak_sem.exists():
    bak_sem.write_text(sem_src, encoding="utf-8")
    print(f"[backup] {bak_sem.name}")

old_call = "ext = client.extract(Path(br).read_bytes() if isinstance(br, (str, Path)) else br.markdown, QUESTION_SCHEMA)"
new_call = "ext = client.extract(text=br.markdown, schema=QUESTION_SCHEMA)"
if old_call in sem_src:
    sem_src = sem_src.replace(old_call, new_call, 1)
    sem.write_text(sem_src, encoding="utf-8")
    print("[patch] semantics.py: call now uses text= + schema=")
elif new_call in sem_src:
    print("[skip] semantics.py already patched")
else:
    # Fallback: regex-replace any client.extract(...) call in this file
    pat = re.compile(r"client\.extract\([^)]*QUESTION_SCHEMA\s*\)")
    sem_src2, n = pat.subn(new_call, sem_src, count=1)
    if n:
        sem.write_text(sem_src2, encoding="utf-8")
        print("[patch] semantics.py: matched via regex")
    else:
        raise SystemExit("FATAL: could not find client.extract(...) call in semantics.py")

# ── 2. Patch client.py: support text= kwarg ───────────────────────────
cli = Path("pipeline/lat/client.py")
cli_src = cli.read_text(encoding="utf-8")
bak_cli = Path(str(cli) + ".bak_extractfix")
if not bak_cli.exists():
    bak_cli.write_text(cli_src, encoding="utf-8")
    print(f"[backup] {bak_cli.name}")

# Add `text` handling at the top of the kwargs loop
anchor = '        doc_keys = {"document", "document_bytes", "image_bytes", "data", "content",'
if anchor not in cli_src:
    # Try alternative anchor used in the latest client.py
    anchor = '        doc_aliases = {'

if anchor not in cli_src:
    raise SystemExit("FATAL: could not find kwargs section in client.extract()")

# Add `text` to the recognized kwargs
cli_src = cli_src.replace(
    '        document_bytes = None\n        document_url = None',
    '        document_bytes = None\n        document_url = None\n        document_text = None',
    1,
)

# Recognize `text` kwarg
if '"text"' not in cli_src:
    cli_src = cli_src.replace(
        '        url_keys = {"document_url", "url", "image_url"}',
        '        url_keys = {"document_url", "url", "image_url"}\n        text_keys = {"text", "markdown", "content_text", "document_text", "input"}',
        1,
    )
    cli_src = cli_src.replace(
        '        doc_keys = {"document", "document_bytes", "image_bytes", "data", "content",',
        '        text_keys = {"text", "markdown", "content_text", "document_text", "input"}\n        doc_keys = {"document", "document_bytes", "image_bytes", "data", "content",',
        1,
    )

# Route the kwarg to document_text
if 'document_text = v' not in cli_src:
    # Insert text-handling as the first branch of the kwarg classifier
    cli_src = cli_src.replace(
        '            kl = k.lower()\n            if kl in doc_keys',
        '            kl = k.lower()\n            if kl in text_keys and document_text is None:\n                document_text = v\n            elif kl in doc_keys',
        1,
    )

# Also treat any string arg that looks like markdown (multi-line, no file extension) as text
if 'looks like markdown' not in cli_src:
    cli_src = cli_src.replace(
        '            # 6. default → filename (last resort)\n            filename = sl',
        '            # 6. markdown-like text → document_text (multi-line, no path)\n'
        '            if document_text is None and (len(sl) > 40 or "\\n" in sl) and not sl.endswith((".png",".pdf",".jpg",".jpeg",".tif",".tiff")):\n'
        '                document_text = sl\n'
        '                continue\n'
        '            # 7. default → filename (last resort)\n            filename = sl',
        1,
    )

# Allow the "no document" check to pass when text is provided
cli_src = cli_src.replace(
    'if document_bytes is None and document_url is None:',
    'if document_bytes is None and document_url is None and document_text is None:',
    1,
)

# When text is provided, send it as a JSON field, not a file
if 'JSON-with-text path' not in cli_src:
    # Replace the "try endpoints × fields" section to route text differently
    endpoints_anchor = '        # ---- try endpoints × fields -------------------------------'
    if endpoints_anchor in cli_src:
        cli_src = cli_src.replace(
            endpoints_anchor,
            '        # ---- JSON-with-text path (no file upload) ----------------\n'
            '        if document_text is not None and document_bytes is None:\n'
            '            for tkey in ("text", "markdown", "document_text", "input", "content"):\n'
            '                data_json = dict(data)\n'
            '                data_json[tkey] = document_text\n'
            '                try:\n'
            '                    resp_json = self._post("/v2/extract", json_data=data_json)\n'
            '                    logger.info("extract OK (text): field=%s", tkey)\n'
            '                    return resp_json\n'
            '                except Exception as e:\n'
            '                    msg = str(e)\n'
            '                    if "HTTP 404" in msg or "422" in msg or "400" in msg:\n'
            '                        continue\n'
            '                    raise\n'
            '            raise LatError("extract: all JSON text field names failed")\n\n'
            '        # ---- try endpoints × fields -------------------------------',
            1,
        )

cli.write_text(cli_src, encoding="utf-8")
print("[patch] client.py: text= support added")
