import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import google.api_core.exceptions as google_api_exceptions
import google.generativeai as genai
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from starlette.responses import PlainTextResponse
from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Always load backend/.env from this file's directory (works no matter what cwd uvicorn uses).
# override=True: values in this file win over a stale GEMINI_API_KEY in the OS environment.
_BACKEND_DIR = Path(__file__).resolve().parent
_ENV_PATH = _BACKEND_DIR / ".env"
load_dotenv(_ENV_PATH, override=True)


def _strip_env_value(raw: str) -> str:
    """Strip BOM, newlines, ASCII and curly quotes often pasted from docs or .env examples."""
    k = (raw or "").replace("\ufeff", "").strip()
    for old, new in (
        ("\u201c", '"'),
        ("\u201d", '"'),
        ("\u2018", "'"),
        ("\u2019", "'"),
    ):
        k = k.replace(old, new)
    if "\n" in k or "\r" in k:
        k = k.splitlines()[0].strip()
    if len(k) >= 2 and k[0] in "'\"" and k[0] == k[-1]:
        k = k[1:-1].strip()
    return k


# Treat obvious placeholders as "not configured" (avoids silent bad config).
_PLACEHOLDER_KEYS = frozenset(
    {
        "",
        "your_gemini_api_key_here",
        "paste_your_key_here",
        "sk-placeholder",
    }
)


def _normalize_api_key(key: str) -> str:
    k = _strip_env_value(key)
    # Google API keys are a single token — remove accidental spaces/tabs from paste errors.
    k = re.sub(r"\s+", "", k)
    return k


def _key_is_non_placeholder(k: str) -> bool:
    return bool(k) and k.lower() not in {p.lower() for p in _PLACEHOLDER_KEYS}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_ENV_PATH,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gemini_api_key: str = Field(default="", validation_alias="GEMINI_API_KEY")
    google_api_key: str = Field(default="", validation_alias="GOOGLE_API_KEY")
    gemini_model: str = Field(default="gemini-2.5-flash", validation_alias="GEMINI_MODEL")
    cors_origins: str = Field(
        default="http://localhost:3000,http://127.0.0.1:3000",
        validation_alias="CORS_ORIGINS",
    )
    whatsapp_verify_token: str = Field(default="", validation_alias="WHATSAPP_VERIFY_TOKEN")

    @field_validator("gemini_api_key", "google_api_key", "gemini_model", mode="before")
    @classmethod
    def _strip_env_wrappers(cls, v: object) -> object:
        if isinstance(v, str):
            return _strip_env_value(v)
        return v


settings = Settings()


def _resolved_api_key() -> str:
    """Prefer GEMINI_API_KEY; use GOOGLE_API_KEY if primary is missing or a placeholder."""
    a = _normalize_api_key(settings.gemini_api_key)
    b = _normalize_api_key(settings.google_api_key)
    if _key_is_non_placeholder(a):
        return a
    if _key_is_non_placeholder(b):
        return b
    return ""


def _api_key_is_configured() -> bool:
    return bool(_resolved_api_key())


def _api_key_source_label() -> str:
    a = _normalize_api_key(settings.gemini_api_key)
    b = _normalize_api_key(settings.google_api_key)
    if _key_is_non_placeholder(a):
        return "GEMINI_API_KEY"
    if _key_is_non_placeholder(b):
        return "GOOGLE_API_KEY"
    return "none"


class AnalyzeRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=200_000)
    language: str | None = Field(default=None, max_length=64)


class BugItem(BaseModel):
    severity: str = "info"
    title: str = ""
    description: str = ""
    suggestion: str | None = None
    line_hint: int | None = None


class AnalyzeResponse(BaseModel):
    summary: str = ""
    logic_explanation: str = ""
    bugs: list[BugItem] = Field(default_factory=list)
    time_complexity: str = ""
    space_complexity: str = ""
    complexity_notes: str = ""
    raw_model_error: str | None = None


SYSTEM_INSTRUCTION = """You are an expert software engineer and algorithms reviewer.
You MUST respond with a single valid JSON object only — no markdown fences, no prose before or after.
Use this exact schema (fill every string/array; use empty string or [] if nothing applies):
{
  "summary": "one short paragraph",
  "logic_explanation": "clear step-by-step explanation of what the code does",
  "bugs": [{"severity": "critical|high|medium|low|info", "title": "short", "description": "details", "suggestion": "fix or null", "line_hint": null or integer}],
  "time_complexity": "Big-O with notation, e.g. O(n log n)",
  "space_complexity": "Big-O for auxiliary space",
  "complexity_notes": "brief justification referencing loops/recursion/data structures"
}
Be precise about bugs (real issues, edge cases, security). If unsure, lower severity."""

# Code review can trip default safety filters; relax for API string form (works across SDK versions).
_CODE_REVIEW_SAFETY: list[dict[str, str]] = [
    {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
    {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
]


def _gemini_response_text(response: Any) -> str:
    """Read model text; raise clear errors for blocks / empty candidates."""
    fb = getattr(response, "prompt_feedback", None)
    if fb is not None:
        br = getattr(fb, "block_reason", None)
        if br is not None and str(br) not in ("BlockReason.BLOCK_REASON_UNSPECIFIED", "BLOCK_REASON_UNSPECIFIED"):
            raise ValueError(f"Prompt blocked: {br}")
    if not getattr(response, "candidates", None):
        raise ValueError("Model returned no candidates (blocked or empty).")
    try:
        t = response.text
    except ValueError as e:
        c0 = response.candidates[0] if response.candidates else None
        fr = getattr(c0, "finish_reason", None) if c0 else None
        raise ValueError(f"No text in response (finish_reason={fr!s}).") from e
    return (t or "").strip()


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*\n?(.*?)\n?```\s*$", text, re.DOTALL | re.IGNORECASE)
    if fence:
        text = fence.group(1).strip()
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("No JSON object in model output")
    return json.loads(text[start : end + 1])


def _normalize_response(data: dict[str, Any]) -> AnalyzeResponse:
    bugs_raw = data.get("bugs") or []
    bugs: list[BugItem] = []
    for b in bugs_raw if isinstance(bugs_raw, list) else []:
        if not isinstance(b, dict):
            continue
        bugs.append(
            BugItem(
                severity=str(b.get("severity", "info")),
                title=str(b.get("title", "")),
                description=str(b.get("description", "")),
                suggestion=b.get("suggestion"),
                line_hint=b.get("line_hint"),
            )
        )
    return AnalyzeResponse(
        summary=str(data.get("summary", "")),
        logic_explanation=str(data.get("logic_explanation", "")),
        bugs=bugs,
        time_complexity=str(data.get("time_complexity", "")),
        space_complexity=str(data.get("space_complexity", "")),
        complexity_notes=str(data.get("complexity_notes", "")),
    )


app = FastAPI(title="Explain Code API", version="1.0.0")


def _setup_cors(application: FastAPI) -> None:
    origins = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
    application.add_middleware(
        CORSMiddleware,
        allow_origins=origins or ["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )


_setup_cors(app)

_log = logging.getLogger("explain_code")
if not logging.root.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [explain_code] %(message)s",
        datefmt="%H:%M:%S",
    )


@app.middleware("http")
async def log_requests(request: Request, call_next):
    """Log every HTTP request so you can see hits in the uvicorn terminal."""
    start = time.perf_counter()
    path = request.url.path
    client = request.client.host if request.client else "?"
    _log.info("→ %s %s (client %s)", request.method, path, client)
    response = await call_next(request)
    ms = (time.perf_counter() - start) * 1000
    _log.info("← %s %s %s (%.0f ms)", request.method, path, response.status_code, ms)
    return response


if _api_key_is_configured():
    genai.configure(api_key=_resolved_api_key())


@app.get("/health")
def health():
    return {
        "ok": True,
        "model_configured": _api_key_is_configured(),
    }


@app.get("/api/config-status")
def config_status():
    """Safe diagnostics: no full key, helps debug loading issues."""
    key = _resolved_api_key()
    exists = _ENV_PATH.is_file()
    prefix_ok = key.startswith("AIza") and len(key) >= 35
    mid = (settings.gemini_model or "").strip().lower()
    uses_gemini_family = mid.startswith("gemini-")
    return {
        "env_file": str(_ENV_PATH),
        "env_file_exists": exists,
        "gemini_key_length": len(key),
        "gemini_key_prefix_ok": prefix_ok,
        "gemini_model": settings.gemini_model,
        "api_key_env_used": _api_key_source_label(),
        "model_id_looks_like_gemini_api": uses_gemini_family,
        "model_hint": (
            None
            if uses_gemini_family
            else (
                "This app calls the Gemini API (google.generativeai). "
                "Set GEMINI_MODEL to a Gemini id from AI Studio, e.g. gemini-2.5-flash or gemini-1.5-flash — "
                "not Gemma-only ids like gemma-* unless your account exposes them for this endpoint."
            )
        ),
        "note": "Google AI Studio keys usually start with AIza and are ~39 characters. "
        "backend/.env is loaded with priority over OS env; restart uvicorn after editing .env.",
        "traffic_flow": (
            "Browser → http://localhost:3000 (Next.js) → fetch → http://127.0.0.1:8000 (FastAPI) → "
            "HTTPS → generativelanguage.googleapis.com (Gemini). Google never sees port 3000; only your server calls Google."
        ),
    }


@app.get("/api/gemini-live-check")
def gemini_live_check():
    """
    Proves the **backend** can reach Google with your API key (calls `list_models`).
    If this works but AI Studio “activity” is empty, that is normal — usage is often shown in Google Cloud Console metrics, not the Studio chat UI.
    """
    if not _api_key_is_configured():
        raise HTTPException(
            status_code=503,
            detail="No API key configured. Set GEMINI_API_KEY in backend/.env.",
        )
    try:
        genai.configure(api_key=_resolved_api_key())
        names: list[str] = []
        for m in genai.list_models():
            methods = getattr(m, "supported_generation_methods", None) or []
            if "generateContent" in methods:
                names.append(m.name)
        return {
            "ok": True,
            "message": "Backend successfully called Google Generative Language API (list_models).",
            "models_with_generate_content": len(names),
            "sample_model_ids": names[:12],
            "hint": (
                "Programmatic API calls do not always appear in the Google AI Studio chat/activity view. "
                "Check Google Cloud Console → APIs & Services → Generative Language API → Metrics."
            ),
        }
    except Exception as e:
        _log.exception("gemini_live_check failed")
        raise HTTPException(
            status_code=502,
            detail=f"Could not reach Gemini API with this key: {e!s}",
        ) from e


def _gemini_auth_help() -> str:
    return (
        "Create a key at https://aistudio.google.com/apikey and set GEMINI_API_KEY=... in backend/.env "
        "(or GOOGLE_API_KEY=...). One line, no quotes. If API_KEY_INVALID persists, open Google Cloud Console → "
        "APIs & Services → Credentials → your key: set Application restrictions to None for testing; under API restrictions "
        "choose Don't restrict key or enable Generative Language API."
    )


def _api_key_invalid_detail() -> str:
    return (
        "Google rejected this API key (API_KEY_INVALID). "
        "1) Create a new key at https://aistudio.google.com/apikey and paste into backend/.env as GEMINI_API_KEY= (no space after =). "
        "2) In Google Cloud Console → APIs & Services → Credentials → that key: Application restrictions = None (HTTP referrer "
        "blocks server-side Python). API restrictions = none or include Generative Language API. "
        "3) Restart uvicorn. Check GET /api/config-status (gemini_key_length ~39, api_key_env_used shows which variable loaded)."
    )


def _extract_whatsapp_inbound_text(data: dict[str, Any]) -> str | None:
    """First text message body from WhatsApp Cloud API webhook JSON."""
    try:
        for entry in data.get("entry") or []:
            for change in entry.get("changes") or []:
                val = change.get("value") or {}
                for msg in val.get("messages") or []:
                    if msg.get("type") == "text":
                        body = (msg.get("text") or {}).get("body")
                        if isinstance(body, str) and body.strip():
                            return body.strip()
    except (TypeError, AttributeError):
        pass
    return None


def _analyze_code_with_gemini(code: str, language: str | None) -> AnalyzeResponse:
    """Core Gemini call; raises HTTPException on transport errors."""
    lang = (language or "unspecified").strip()
    combined_prompt = f"""{SYSTEM_INSTRUCTION}

---

Language/context hint: {lang}

Analyze the following code and produce the JSON object as specified.

--- CODE START ---
{code}
--- CODE END ---
"""

    try:
        api_key = _resolved_api_key()
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(settings.gemini_model)
        response = model.generate_content(
            combined_prompt,
            generation_config=genai.types.GenerationConfig(
                temperature=0.2,
                max_output_tokens=8192,
            ),
            safety_settings=_CODE_REVIEW_SAFETY,
        )
        text = _gemini_response_text(response)
        if not text:
            raise ValueError("Empty response from model")
        data = _extract_json(text)
        return _normalize_response(data)
    except json.JSONDecodeError as e:
        return AnalyzeResponse(
            summary="The model returned text that could not be parsed as JSON.",
            logic_explanation="",
            bugs=[],
            time_complexity="",
            space_complexity="",
            complexity_notes="",
            raw_model_error=str(e),
        )
    except google_api_exceptions.NotFound as e:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Model `{settings.gemini_model}` was not found for this API. "
                "Set GEMINI_MODEL=gemini-1.5-flash or gemini-2.0-flash in backend/.env and restart."
            ),
        ) from e
    except google_api_exceptions.ResourceExhausted as e:
        raise HTTPException(
            status_code=429,
            detail="Gemini quota or rate limit exceeded. Wait and retry, or check billing/quotas in Google Cloud.",
        ) from e
    except google_api_exceptions.InvalidArgument as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid request to Gemini: {e!s}. Check GEMINI_MODEL matches a model id from AI Studio.",
        ) from e
    except google_api_exceptions.PermissionDenied as e:
        raise HTTPException(
            status_code=403,
            detail=f"Permission denied by Gemini API: {e!s}",
        ) from e
    except Exception as e:
        err = str(e)
        if "API_KEY_INVALID" in err or "API key not valid" in err:
            raise HTTPException(status_code=401, detail=_api_key_invalid_detail()) from e
        if "not found" in err.lower() or "404" in err or "invalid model" in err.lower():
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Model `{settings.gemini_model}` is not available for this API. "
                    "Set GEMINI_MODEL to a Gemini model id (e.g. gemini-2.5-flash, gemini-1.5-flash) from "
                    "https://aistudio.google.com/ — Gemma ids (gemma-*) often use a different surface."
                ),
            ) from e
        _log.exception("Gemini generate_content failed")
        raise HTTPException(
            status_code=502,
            detail=(
                f"Gemini request failed: {e!s}. "
                "Try GEMINI_MODEL=gemini-1.5-flash in backend/.env, restart uvicorn, and try again."
            ),
        ) from e


@app.post("/api/analyze", response_model=AnalyzeResponse)
def analyze(body: AnalyzeRequest):
    if not _api_key_is_configured():
        raise HTTPException(
            status_code=503,
            detail=f"Missing or placeholder API key. Set GEMINI_API_KEY or GOOGLE_API_KEY in backend/.env. {_gemini_auth_help()}",
        )
    code = body.code.strip()
    if not code:
        raise HTTPException(status_code=400, detail="Code is empty")
    return _analyze_code_with_gemini(code, body.language)


@app.get("/api/webhooks/whatsapp")
async def whatsapp_verify(request: Request):
    """Meta webhook verification (subscribe). Set WHATSAPP_VERIFY_TOKEN in backend/.env to match Meta."""
    q = request.query_params
    mode = q.get("hub.mode")
    token = q.get("hub.verify_token")
    challenge = q.get("hub.challenge")
    expected = (settings.whatsapp_verify_token or "").strip()
    if mode == "subscribe" and expected and token == expected and challenge:
        return PlainTextResponse(content=str(challenge))
    if mode == "subscribe" and not expected:
        raise HTTPException(
            status_code=503,
            detail="Set WHATSAPP_VERIFY_TOKEN in backend/.env to the same verify token you configure in Meta.",
        )
    raise HTTPException(status_code=403, detail="Verification failed")


@app.post("/api/webhooks/whatsapp", response_model=AnalyzeResponse)
async def whatsapp_webhook(request: Request):
    """
    WhatsApp Cloud API webhook: accepts the JSON Meta sends (not the same JSON as /api/analyze).
    Extracts the first inbound text message and runs the same analysis as /api/analyze.
    """
    if not _api_key_is_configured():
        raise HTTPException(
            status_code=503,
            detail=f"Missing or placeholder API key. {_gemini_auth_help()}",
        )
    try:
        data = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Body must be JSON") from None
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Body must be a JSON object")
    text = _extract_whatsapp_inbound_text(data)
    if not text:
        raise HTTPException(
            status_code=400,
            detail=(
                "No WhatsApp text message found. For manual testing, POST JSON with entry[].changes[].value.messages[].text.body. "
                "To analyze source code from the app or Postman, use POST /api/analyze with body "
                '{"code": "your code here", "language": "python"} — not a WhatsApp webhook payload.'
            ),
        )
    return _analyze_code_with_gemini(text, "plaintext")
