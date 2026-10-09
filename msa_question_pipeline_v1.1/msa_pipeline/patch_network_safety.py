from pathlib import Path

# 1. Update client.py to set a hard socket timeout (15s connection, 30s read)
client_path = Path("pipeline/lat/client.py")
if client_path.exists():
    text = client_path.read_text(encoding="utf-8")
    if "timeout=" not in text:
        text = text.replace("resp = self.session.post(url, **kw)", "kw.setdefault('timeout', (15, 30))\n        resp = self.session.post(url, **kw)")
        client_path.write_text(text, encoding="utf-8")
        print("Updated client.py with 30s HTTP timeouts.")

# 2. Update visuals.py to safely catch repass_region errors without halting pipeline
visuals_path = Path("pipeline/stages/visuals.py")
if visuals_path.exists():
    text = visuals_path.read_text(encoding="utf-8")
    old_code = 'out = client.parse(buf.getvalue(), "region.png", options={"inline_markdown": False, "atomic_grounding": False})'
    new_code = '''try:
        out = client.parse(buf.getvalue(), "region.png", options={"inline_markdown": False, "atomic_grounding": False})
    except Exception as e:
        import logging
        logging.warning(f"repass_region failed cleanly: {e}")
        return None'''
    if old_code in text and "repass_region failed cleanly" not in text:
        text = text.replace(old_code, new_code)
        visuals_path.write_text(text, encoding="utf-8")
        print("Updated visuals.py with safe repass_region error catching.")
