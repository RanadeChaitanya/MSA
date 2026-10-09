import json
import logging
import time
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, Union

import requests

logger = logging.getLogger(__name__)


class LatError(Exception):
    pass


class LatClient:
    """Landing AI ADE client.

    Accepts either a raw API key string OR a Settings object:
        LatClient("land-xxxx")
        LatClient(cfg)
    """

    def __init__(
        self,
        api_key: Union[str, Any],
        base_url: str = "https://api.ade.landing.ai",
        parse_model: str = "dpt-3-pro-latest",
        verity_model: str = "dpt-3-verity-latest",
    ):
        if not isinstance(api_key, str):
            cfg = api_key
            api_key = (
                getattr(cfg, "api_key", None)
                or getattr(cfg, "landing_api_key", None)
                or ""
            )
            base_url = getattr(cfg, "base_url", None) or getattr(cfg, "landing_base_url", base_url)
            parse_model = getattr(cfg, "parse_model", parse_model)
            verity_model = (
                getattr(cfg, "verify_model", None)
                or getattr(cfg, "verity_model", verity_model)
            )

        self.api_key = (api_key or "").strip()
        if not self.api_key:
            raise LatError("Empty API key. Set LANDING_API_KEY in .env")

        self.base_url = base_url.rstrip("/")
        self.parse_model = parse_model
        self.verity_model = verity_model
        self.extract_model = parse_model
        self.credits = 0.0
        self.calls: Dict[str, int] = {}
        self.cache_hits = 0

        self.session = requests.Session()
        self._auth_header = self._pick_auth_header(self.api_key)
        self.session.headers.update({
            "Authorization": self._auth_header,
            "User-Agent": "msa-pipeline/1.1",
        })
        logger.info("LatClient ready: %s @ %s (key_len=%d)",
                    self._auth_header.split(" ", 1)[0], self.base_url, len(self.api_key))

    # ------------------------------------------------------------------ #
    @staticmethod
    def _pick_auth_header(api_key: str) -> str:
        k = api_key.strip()
        if k.startswith("Basic ") or k.startswith("Bearer "):
            return k
        if k.startswith("land_") or k.startswith("APIKEY") or len(k) < 80:
            return f"Bearer {k}"
        return f"Basic {k}"

    @staticmethod
    def _mime_for(filename: str) -> str:
        low = (filename or "").lower()
        if low.endswith(".pdf"):            return "application/pdf"
        if low.endswith(".png"):            return "image/png"
        if low.endswith((".jpg", ".jpeg")): return "image/jpeg"
        if low.endswith((".tif", ".tiff")): return "image/tiff"
        return "application/octet-stream"

    # ------------------------------------------------------------------ #
    def _post(
        self,
        path: str,
        data: Optional[Dict[str, Any]] = None,
        files: Optional[Dict[str, Any]] = None,
        json_data: Optional[Dict[str, Any]] = None,
        timeout: Tuple[int, int] = (15, 120),
        max_attempts: int = 3,
    ) -> Dict[str, Any]:
        url = f"{self.base_url}{path}"
        kw: Dict[str, Any] = {"timeout": timeout}
        if data:      kw["data"] = data
        if files:     kw["files"] = files
        if json_data: kw["json"] = json_data

        last_err: Optional[Exception] = None
        for attempt in range(1, max_attempts + 1):
            try:
                resp = self.session.post(url, **kw)
                if resp.status_code == 200:
                    return resp.json()

                body = resp.text[:800]

                if resp.status_code in (401, 402, 403):
                    raise LatError(f"{path} -> HTTP {resp.status_code}: {body}")
                if 400 <= resp.status_code < 500:
                    raise LatError(f"{path} -> HTTP {resp.status_code}: {body}")

                last_err = LatError(f"{path} -> HTTP {resp.status_code}: {body}")
            except requests.RequestException as e:
                last_err = e
            except LatError:
                raise

            if attempt < max_attempts:
                sleep_s = 2 ** attempt
                logger.warning("retry %d/%d on %s in %ds: %s",
                               attempt, max_attempts, path, sleep_s, last_err)
                time.sleep(sleep_s)

        raise LatError(f"Failed after {max_attempts} attempts on {path}: {last_err}")

    # ------------------------------------------------------------------ #
    def parse(
        self,
        image_bytes: bytes,
        filename: str = "page.png",
        options: Optional[Dict[str, Any]] = None,
        model: Optional[str] = None,
    ) -> Dict[str, Any]:
        target_model = model or self.parse_model
        data: Dict[str, Any] = {"model": target_model}
        if options:
            data["options"] = json.dumps(options)
        mime = self._mime_for(filename)
        files = {"document": (filename, image_bytes, mime)}
        return self._post("/v2/parse", data=data, files=files)

    # ------------------------------------------------------------------ #
    # extract() — accepts ANY call style used by pipeline/stages/semantics.py
    # ------------------------------------------------------------------ #
    def extract(self, *args, **kwargs) -> Dict[str, Any]:
        """Permissive extract(). Understands:

            client.extract(text=markdown, schema=dict)      <- semantics.py uses this
            client.extract(markdown_str, schema_dict)
            client.extract(path, schema_dict)
            client.extract(bytes, schema_dict)
            client.extract(bytes, schema=schema)
            client.extract(schema=schema, text=markdown)
        """
        # ── Named kwargs, alias-matched ──────────────────────────────
        TEXT_KEYS   = {"text", "markdown", "content", "content_text",
                       "document_text", "input", "body"}
        DOC_KEYS    = {"document", "document_bytes", "image_bytes", "data",
                       "payload", "bytes", "buf", "pdf", "file", "file_bytes"}
        URL_KEYS    = {"document_url", "url", "image_url"}
        NAME_KEYS   = {"filename", "name", "file_name"}
        SCHEMA_KEYS = {"schema", "output_schema", "json_schema", "structure",
                       "extraction_schema", "question_schema"}
        MODEL_KEYS  = {"model", "model_id"}
        OPT_KEYS    = {"options", "opts"}
        PROMPT_KEYS = {"prompt", "instruction", "instructions"}

        document_bytes: Optional[bytes] = None
        document_url: Optional[str]     = None
        document_text: Optional[str]    = None
        filename = "document.pdf"
        schema   = None
        model    = None
        options  = None
        prompt   = None
        rest: Dict[str, Any] = {}

        for k, v in kwargs.items():
            kl = k.lower()
            if   kl in TEXT_KEYS   and document_text  is None: document_text  = v
            elif kl in DOC_KEYS    and document_bytes is None: document_bytes = v
            elif kl in URL_KEYS    and document_url   is None: document_url   = v
            elif kl in NAME_KEYS   and isinstance(v, str):     filename       = v
            elif kl in SCHEMA_KEYS and schema         is None: schema         = v
            elif kl in MODEL_KEYS:                             model          = v
            elif kl in OPT_KEYS:                               options        = v
            elif kl in PROMPT_KEYS:                            prompt         = v
            else:                                              rest[k]        = v

        # ── Positional arg sniffing ──────────────────────────────────
        str_candidates: list[str] = []
        for arg in args:
            if arg is None:
                continue
            # bytes → document
            if isinstance(arg, (bytes, bytearray, memoryview)):
                if document_bytes is None:
                    document_bytes = bytes(arg)
                continue
            # Path → read
            if isinstance(arg, Path):
                if arg.exists() and document_bytes is None:
                    document_bytes = arg.read_bytes()
                    filename = arg.name
                continue
            # dict / list → schema
            if isinstance(arg, (dict, list)):
                if schema is None:
                    schema = arg
                continue
            # str → defer
            if isinstance(arg, str):
                str_candidates.append(arg)
                continue

        # ── Resolve deferred strings ─────────────────────────────────
        for s in str_candidates:
            sl = s.strip()
            if not sl:
                continue
            # 1. Existing file path
            try:
                p = Path(sl)
                if p.exists() and p.is_file() and document_bytes is None:
                    document_bytes = p.read_bytes()
                    filename = p.name
                    continue
            except Exception:
                pass
            # 2. URL
            if sl.startswith(("http://", "https://")) and document_url is None:
                document_url = sl
                continue
            # 3. JSON string → schema
            if sl.startswith(("{", "[")) and schema is None:
                try:
                    schema = json.loads(sl)
                    continue
                except Exception:
                    pass
            # 4. Short token with no spaces and no extension → model id
            if (model is None and 3 < len(sl) < 60 and " " not in sl
                    and not sl.endswith((".png", ".pdf", ".jpg", ".jpeg", ".tif", ".tiff"))
                    and "\n" not in sl and "/" not in sl):
                model = sl
                continue
            # 5. Everything else → markdown text
            if document_text is None:
                document_text = s

        # ── Validation ───────────────────────────────────────────────
        if document_bytes is None and document_url is None and document_text is None:
            raise LatError(
                "extract(): no document. "
                f"pos_args={[type(a).__name__ for a in args]}, "
                f"kwargs={list(kwargs.keys())}"
            )

        # ── Build request ────────────────────────────────────────────
        target_model = model or self.extract_model or self.parse_model
        data: Dict[str, Any] = {"model": target_model}
        if schema is not None:
            data["schema"] = schema if isinstance(schema, str) else json.dumps(schema)
        if prompt:
            data["prompt"] = prompt
        if options:
            data["options"] = options if isinstance(options, str) else json.dumps(options)
        for k, v in rest.items():
            if v is None:
                continue
            data[k] = v if isinstance(v, str) else json.dumps(v)

        # ═══════════════════════════════════════════════════════════════
        # PATH A: we have markdown TEXT → send as JSON body
        # ═══════════════════════════════════════════════════════════════
        if document_text is not None and document_bytes is None and document_url is None:
            for text_field in ("text", "markdown", "content", "document", "input"):
                body = dict(data)
                body[text_field] = document_text
                for endpoint in ("/v2/extract", "/v1/extract", "/v2/parse/extract"):
                    try:
                        resp = self._post(endpoint, json_data=body)
                        self._tally(endpoint, resp)
                        logger.info("extract OK (text): %s field=%s", endpoint, text_field)
                        return resp
                    except Exception as e:
                        msg = str(e)
                        if "HTTP 404" in msg or "HTTP 422" in msg or "HTTP 400" in msg:
                            continue  # try next endpoint / field
                        raise  # auth/quota/5xx → bubble up
            raise LatError("extract: all JSON text shapes failed on /v2/extract")

        # ═══════════════════════════════════════════════════════════════
        # PATH B: we have bytes or URL → multipart upload
        # ═══════════════════════════════════════════════════════════════
        if document_bytes is not None and not isinstance(document_bytes, (bytes, bytearray)):
            document_bytes = bytes(document_bytes)

        if document_url and not document_bytes:
            data["document_url"] = document_url

        mime = self._mime_for(filename)
        for field in ("document", "file"):
            files = {field: (filename, document_bytes, mime)} if document_bytes else None
            for endpoint in ("/v2/extract", "/v1/extract", "/v2/parse/extract"):
                try:
                    resp = self._post(endpoint, data=dict(data), files=files)
                    self._tally(endpoint, resp)
                    logger.info("extract OK (file): %s field=%s", endpoint, field)
                    return resp
                except Exception as e:
                    msg = str(e)
                    if "HTTP 404" in msg:
                        continue
                    if "HTTP 422" in msg and "document" in msg and field == "document":
                        break
                    raise
        raise LatError("extract: all multipart shapes failed")

    def _tally(self, endpoint: str, resp: Any) -> None:
        self.calls[endpoint] = self.calls.get(endpoint, 0) + 1
        if isinstance(resp, dict):
            for k in ("credits_used", "credits", "credit_cost", "cost"):
                v = resp.get(k)
                if isinstance(v, (int, float)):
                    self.credits += float(v)
                    break
