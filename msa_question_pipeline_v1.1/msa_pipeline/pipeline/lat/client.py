"""Thin, cached, retrying REST client for Landing AI ADE Gen2 (DPT-3).

Endpoints used (verified against the live API, see data/cache/lat/ for real responses):
  POST /v2/parse    multipart: document, model, options(JSON string)
  POST /v2/extract  multipart: markdown (FILE part), schema (JSON string), model, options{strict}
                    -> extraction, extraction_metadata (per leaf {value, ranges:[{start,end}]})
  POST /v2/ground   json: extraction_metadata, structure   (OPTIONAL - unused by default; grounding is
                    computed client-side from block ranges, see stages/semantics.py)
Responses: 200 ok, 206 partial (metadata.failed_pages), 422 validation, 429 rate limit.

Extract does NOT accept a JSON body with the markdown as a string field; it must be a multipart file part
named `markdown` and the model must be an extract model (`extract-latest`), not a parse model.
"""
from __future__ import annotations

import json
import logging
import random
import time
from collections import defaultdict
from pathlib import Path
from typing import Optional

import requests

from ..config import Settings
from ..utils.io import read_json, sha256_bytes, write_json

log = logging.getLogger("pipeline.lat")

RETRY_STATUS = {429, 500, 502, 503, 504}
PLACEHOLDER_KEYS = {"", "APIKEYHERE"}


class LatError(RuntimeError):
    pass


class LatClient:
    def __init__(self, cfg: Settings, cache_dir: Optional[Path] = None):
        if not isinstance(cfg, Settings):
            raise TypeError("LatClient(cfg) expects a Settings object (it needs models, timeouts and data_dir)")
        if (cfg.api_key or "").strip() in PLACEHOLDER_KEYS:
            raise LatError("LANDING_API_KEY is not set. Put your key in .env (currently a placeholder).")
        self.cfg = cfg
        self.api_key = cfg.api_key.strip()
        self.cache_dir = Path(cache_dir) if cache_dir else Path(cfg.data_dir) / "cache" / "lat"
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.session = requests.Session()
        self.credits = 0.0
        self.calls: dict[str, int] = defaultdict(int)
        self.cache_hits = 0

    # ---------------- internals ----------------
    def _sleep(self, attempt: int, retry_after: Optional[str] = None) -> None:
        if retry_after:
            try:
                time.sleep(min(float(retry_after), 120))
                return
            except ValueError:
                pass
        time.sleep(min(60.0, 2 ** attempt + random.random()))

    def _post(self, path: str, *, form=None, files_factory=None, json_body=None) -> dict:
        url = self.cfg.base_url.rstrip("/") + path
        headers = {"Authorization": f"Bearer {self.api_key}", "User-Agent": "msa-pipeline/1.1"}
        # (connect, read): a dead connection fails fast, a long parse may still take http_timeout seconds
        timeout = (15, self.cfg.http_timeout)
        last: Exception | str = ""
        for attempt in range(self.cfg.max_retries + 1):
            try:
                kw: dict = {"headers": headers, "timeout": timeout}
                if files_factory is not None:
                    kw["files"] = files_factory()
                    kw["data"] = form or {}
                elif json_body is not None:
                    kw["json"] = json_body
                resp = self.session.post(url, **kw)
            except (requests.ConnectionError, requests.Timeout) as e:
                last = e
                log.warning("%s network error (attempt %d/%d): %s", path, attempt + 1, self.cfg.max_retries + 1, e)
                if attempt < self.cfg.max_retries:
                    self._sleep(attempt)
                continue
            if resp.status_code in (200, 206):
                try:
                    return resp.json()
                except ValueError as e:
                    raise LatError(f"{path} -> HTTP {resp.status_code} but body is not JSON: {resp.text[:200]}") from e
            if resp.status_code in RETRY_STATUS:
                last = f"HTTP {resp.status_code}: {resp.text[:200]}"
                log.warning("%s %s (attempt %d/%d) - retrying", path, last, attempt + 1, self.cfg.max_retries + 1)
                if attempt < self.cfg.max_retries:
                    self._sleep(attempt, resp.headers.get("Retry-After"))
                continue
            raise LatError(f"{path} -> HTTP {resp.status_code}: {resp.text[:800]}")
        raise LatError(f"{path} failed after {self.cfg.max_retries + 1} attempts: {last}")

    def _note(self, kind: str, out: dict) -> None:
        self.calls[kind] += 1
        try:
            c = (out.get("metadata") or {}).get("billing", {}) or {}
            self.credits += float(c.get("total_credits") or 0)
        except Exception:  # pragma: no cover
            pass

    def _cache_get(self, key: str) -> Optional[dict]:
        p = self.cache_dir / f"{key}.json"
        if p.exists():
            self.cache_hits += 1
            return read_json(p)
        return None

    def _cache_put(self, key: str, obj: dict) -> None:
        write_json(self.cache_dir / f"{key}.json", obj)

    # ---------------- public API ----------------
    def parse(self, data: bytes, filename: str, model: Optional[str] = None,
              options: Optional[dict] = None) -> dict:
        model = model or self.cfg.parse_model
        options = options if options is not None else {"inline_markdown": True, "atomic_grounding": True}
        key = "parse_" + sha256_bytes(data + json.dumps([model, options], sort_keys=True).encode())
        hit = self._cache_get(key)
        if hit is not None:
            return hit
        mime = "application/pdf" if filename.lower().endswith(".pdf") else "image/png"
        out = self._post(
            "/v2/parse",
            form={"model": model, "options": json.dumps(options)},
            files_factory=lambda: {"document": (filename, data, mime)},
        )
        self._note("parse", out)
        failed = (out.get("metadata") or {}).get("failed_pages") or []
        if not failed:                      # never cache partial results
            self._cache_put(key, out)
        return out

    def extract(self, markdown: str, schema: dict) -> dict:
        key = "extract_" + sha256_bytes((markdown + json.dumps(schema, sort_keys=True) + self.cfg.extract_model).encode())
        hit = self._cache_get(key)
        if hit is not None:
            return hit
        out = self._post(
            "/v2/extract",
            form={"schema": json.dumps(schema), "model": self.cfg.extract_model,
                  "options": json.dumps({"strict": False})},
            files_factory=lambda: {"markdown": ("doc.md", markdown.encode("utf-8"), "text/markdown")},
        )
        self._note("extract", out)
        self._cache_put(key, out)
        return out

    def ground(self, extraction_metadata: dict, structure: dict) -> dict:
        key = "ground_" + sha256_bytes(json.dumps([extraction_metadata, structure], sort_keys=True).encode())
        hit = self._cache_get(key)
        if hit is not None:
            return hit
        out = self._post("/v2/ground", json_body={"extraction_metadata": extraction_metadata,
                                                  "structure": structure})
        self._note("ground", out)
        self._cache_put(key, out)
        return out
