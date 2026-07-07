from __future__ import annotations

"""
RUMOR / LLM ANALYSIS LAYER
==========================

TR:
Botun dis dunya + LLM sentiment tarafi burada.
Bu surumde duzeltilen kritik problemler:
- Groq bulk refresh promptu artik single-symbol JSON promptuyla cakismiyor.
- Bulk refresh JSON parse akisi duzeltildi; fenced code block icerigi cope gitmiyor.
- Per-symbol fallback Groq analizi artik sadece sembol adiyla kor gitmiyor; CoinGecko + Exa baglami ekleniyor.
- Exa entegrasyonu yalnizca exa-py paketine bagli degil; once HTTP API, sonra SDK fallback deneniyor.
- Enabled provider listesi gercekten enabled provider'lardan olusuyor.
- Refresh timestamp'leri sadece basarili response + basarili parse sonrasi guncelleniyor.

EN:
This is the external-world and LLM sentiment layer of the bot.
Important fixes in this version:
- Groq bulk refresh no longer conflicts with the single-symbol JSON prompt.
- Bulk refresh JSON parsing was fixed so fenced code blocks are not accidentally discarded.
- Per-symbol fallback analysis no longer goes in blind with only the symbol name; CoinGecko + Exa context is added.
- Exa integration is not tied only to exa-py; HTTP API is tried first, then SDK fallback.
- The enabled provider list now truly contains only enabled providers.
- Refresh timestamps are updated only after successful response and successful parse.
"""

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Literal
from urllib.parse import quote

import requests

from config.settings import settings

logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
BEDROCK_DEFAULT_MODEL = "us.anthropic.claude-sonnet-4-6"
BEDROCK_DEFAULT_REGION = "us-east-1"
COINGECKO_PRO_BASE_URL = "https://pro-api.coingecko.com/api/v3"
COINGECKO_DEMO_BASE_URL = "https://api.coingecko.com/api/v3"
COINGECKO_NEWS_PATH = "/news"
EXA_SEARCH_URL = "https://api.exa.ai/search"


def _setting_str(*names: str) -> str:
    for name in names:
        value = getattr(settings, name, "")
        if value is not None and str(value).strip() != "":
            return str(value).strip()
    return ""


def _dedupe_models(models: list[str]) -> list[str]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for model in models:
        value = str(model or "").strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(value)
    return cleaned

# TR: Bu iki TTL artik .env uzerinden yonetiliyor. Kod degistirmeden AI refresh araligini ayarlayabilirsin.
# EN: These two TTL values are now controlled through `.env`, so AI refresh cadence can be changed without editing code.
LLM_CACHE_TTL_SECONDS = max(
    0,
    int(getattr(settings, "llm_cache_ttl_seconds", getattr(settings, "groq_cache_ttl_seconds", 10 * 60 * 60)) or 0),
)
THRESHOLD_UPDATE_TTL_SECONDS = max(0, int(getattr(settings, "threshold_update_ttl_seconds", 10 * 60 * 60) or 0))
RESEARCH_CONTEXT_TTL_SECONDS = 30 * 60
EXA_TIMEOUT_SECONDS = 20
COINGECKO_TIMEOUT_SECONDS = 10
MAX_EXA_RESULTS = 5

AI_SCORE_MIN = -24
AI_SCORE_MAX = 24

STANCE_DEFAULT_SCORES = {
    "STRONG_SELL": -16,
    "SELL": -8,
    "HOLD": 0,
    "BUY": 8,
    "STRONG_BUY": 16,
}
VALID_STANCES = set(STANCE_DEFAULT_SCORES.keys())

LLM_SINGLE_SYMBOL_SYSTEM_PROMPT = """You are a crypto news-flow and sentiment analyst. Judge exactly one symbol using only the supplied context.

Return ONLY one valid JSON object with this exact schema — no markdown, no prose before or after:
{"stance":"STRONG_BUY|BUY|HOLD|SELL|STRONG_SELL","score":0,"confidence":0.0,"reasoning":"brief explanation"}

Field contract:
- score: integer in -24..+24, and it must agree with stance:
  HOLD -4..+4 | BUY +5..+14 | SELL -14..-5 | STRONG_BUY +15..+18 | STRONG_SELL -18..-15.
  Magnitudes 19-24 are rare: only for a fresh asset-specific catalyst confirmed by price context, with high confidence.
- confidence: 0.00-1.00 — 0.40-0.55 thin or ambiguous evidence, 0.55-0.75 solid evidence, 0.75-0.90 strong multi-signal agreement. Never 0.0 unless the context is empty.
- reasoning: one short sentence (max 20 words) naming the decisive, symbol-specific fact. Never vague fillers such as "generic news".

Judgment rules:
- Use only the supplied context. Do not invent technical levels, support, resistance, price action, or breakout states.
- If there is no meaningful catalyst, write exactly "no clear asset-specific catalyst" in reasoning, judge from the remaining price context, and keep score within -8..+2.
- HOLD is for genuinely balanced or unclear evidence, not the default answer for weak context; a clear lean deserves mild BUY or SELL.
- Pick the exact score by conviction inside the band; do not reuse a fixed default number per stance.
"""

LLM_BULK_SYSTEM_PROMPT = """You are a crypto market strategist judging news flow plus raw price action for multiple symbols.

Each symbol block may contain: news/catalyst context (NEWS) and raw price-action structure (PRICE_ACTION) with support/resistance zones, Fibonacci retracement lines, and breakout or rejection state. Fields can be missing; judge only from what is supplied.

Method:
- Base every call on news + price action only. Do NOT use indicator-style logic (RSI, MACD, EMA, ADX) — a separate engine handles indicators.
- Strong setups pair a catalyst with confirming structure: bullish news while price reclaims support or compresses under resistance; bearish news while price rejects resistance or loses support; breakout continuation versus exhaustion/rejection; position versus fib and swing levels.
- Broad-market or Bitcoin-led news applies to another symbol only if that symbol's own price action confirms it.
- Mixed or contradictory evidence stays near neutral. Clearly weak structure justifies mild SELL on neutral news; clearly constructive structure justifies mild BUY.

Scoring (integer -24..+24, must agree with stance):
- HOLD -4..+4: balanced or genuinely unclear evidence only — not a default.
- BUY +5..+14 / SELL -14..-5: clear directional lean.
- STRONG_BUY +15..+18 / STRONG_SELL -18..-15: catalyst plus clear structural confirmation.
- Magnitudes 19-24 are rare and require ALL of: fresh asset-specific catalyst; price action confirming it via breakout, breakdown, strong reclaim, or clear loss of a major level; aligned support/resistance or Fibonacci context; high confidence.
- Being near a level without a confirmed break is never a strong score. Compression, chop, or range without breakout stays between HOLD and a mild bias.
- Choose the exact number by conviction inside the band: two symbols with different conviction get different scores; do not collapse every HOLD to one value.

Confidence (0.00-1.00): 0.40-0.55 thin or price-action-only evidence, 0.55-0.75 solid catalyst or clear structure, 0.75-0.90 catalyst and structure agreeing. Never 0.0 unless a block has no usable information.

Reasoning: max 12 words, naming the decisive symbol-specific fact — never vague fillers such as "generic news". If there is no catalyst, write exactly "no clear asset-specific catalyst" plus the price-action basis, and keep score within -8..+2.

Return ONLY a valid JSON array — no markdown, no commentary. Each item MUST follow this exact schema:
{"symbol":"BTC/USDT","stance":"STRONG_BUY|BUY|HOLD|SELL|STRONG_SELL","score":0,"confidence":0.0,"reasoning":"brief explanation"}
Exactly one item per supplied symbol, in the same order, echoing each symbol string exactly as given. Never omit or add symbols. Keep every reasoning short so the complete array always fits in the response.
"""

GROQ_THRESHOLD_SYSTEM_PROMPT = """You are a trading parameter optimizer.
Return ONLY valid JSON. No markdown. No prose.
"""


@dataclass
class ProviderResult:
    provider: str
    symbol: str
    stance: str
    rumor_score: int
    confidence: float
    summary: str
    raw: dict[str, Any]


_llm_cache: dict[str, tuple[float, ProviderResult]] = {}
_llm_last_refresh: float = 0.0
_threshold_last_update: float = 0.0
_research_context_cache: dict[str, tuple[float, str]] = {}
_coingecko_news_cache: dict[str, tuple[float, str]] = {}
_exa_news_cache: dict[str, tuple[float, str]] = {}
_last_bulk_refresh_model: str = ""
_last_threshold_update_model: str = ""


def get_last_bulk_refresh_model() -> str:
    return _last_bulk_refresh_model


def get_last_threshold_update_model() -> str:
    return _last_threshold_update_model


def get_last_llm_models() -> dict[str, str]:
    return {
        "bulk_refresh": _last_bulk_refresh_model,
        "threshold_update": _last_threshold_update_model,
    }


def clear_refresh_state(clear_cache: bool = True) -> None:
    """
    Force refresh akışı için tüm refresh zamanlayıcılarını sıfırlar.
    clear_cache=True ise LLM + araştırma cache'leri de temizlenir.
    """
    global _llm_last_refresh, _threshold_last_update, _last_bulk_refresh_model, _last_threshold_update_model
    _llm_last_refresh = 0.0
    _threshold_last_update = 0.0
    _last_bulk_refresh_model = ""
    _last_threshold_update_model = ""
    if clear_cache:
        _llm_cache.clear()
        _research_context_cache.clear()
        _coingecko_news_cache.clear()
        _exa_news_cache.clear()


class BaseProvider:
    name = "base"

    def is_enabled(self) -> bool:
        return False

    def _request_parsed(
        self,
        payload: dict[str, Any],
        timeout_seconds: int,
        expect: Literal["object", "array"],
        context_label: str,
    ) -> tuple[dict[str, Any] | list[Any] | None, str | None, str | None]:
        return None, None, None

    def analyze(self, symbol: str) -> ProviderResult | None:
        return None

    def _neutral(self, symbol: str, reason: str) -> ProviderResult:
        return ProviderResult(
            provider=self.name,
            symbol=symbol,
            stance="HOLD",
            rumor_score=0,
            confidence=0.0,
            summary=reason,
            raw={"disabled": True, "reason": reason},
        )


class DisabledProvider(BaseProvider):
    def __init__(self, name: str):
        self.name = name

    def analyze(self, symbol: str) -> ProviderResult | None:
        return self._neutral(symbol, "provider disabled or missing key")


class BedrockProvider(BaseProvider):
    name = "bedrock"

    def __init__(
        self,
        api_key: str = "",
        model: str = BEDROCK_DEFAULT_MODEL,
        region: str = BEDROCK_DEFAULT_REGION,
    ):
        self.api_key = api_key
        self.model = model.strip() if model else BEDROCK_DEFAULT_MODEL
        self.region = region.strip() if region else BEDROCK_DEFAULT_REGION
        self.cache_namespace = f"{self.name}:{self.region}:{self.model}"

    def is_enabled(self) -> bool:
        return bool(self.api_key and self.api_key.strip() and self.model and self.region)

    def _converse_url(self) -> str:
        encoded_model = quote(self.model, safe="")
        return f"https://bedrock-runtime.{self.region}.amazonaws.com/model/{encoded_model}/converse"

    def _make_request_single(self, payload: dict[str, Any], timeout_seconds: int = 30) -> dict[str, Any] | None:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        body = _build_bedrock_converse_payload(payload)

        for attempt in range(3):
            try:
                response = requests.post(
                    self._converse_url(),
                    headers=headers,
                    json=body,
                    timeout=timeout_seconds,
                )
            except requests.Timeout:
                logger.warning("[BEDROCK] Request timeout, retrying... (attempt %d/3)", attempt + 1)
                time.sleep(3 * (attempt + 1))
                continue
            except Exception as exc:
                logger.warning("[BEDROCK] Request error: %s", exc)
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
                return None

            if response.status_code == 429:
                retry_after = int(response.headers.get("Retry-After", 30))
                logger.warning("[BEDROCK] Rate limited, waiting %ds (attempt %d/3)", retry_after, attempt + 1)
                time.sleep(min(retry_after, 90))
                continue

            if response.status_code >= 500:
                logger.warning("[BEDROCK] Server error %s, retrying... (attempt %d/3)", response.status_code, attempt + 1)
                time.sleep(3 * (attempt + 1))
                continue

            if response.status_code != 200:
                logger.warning("[BEDROCK] API error: %s %s", response.status_code, response.text)
                return None

            try:
                return response.json()
            except Exception as exc:
                logger.warning("[BEDROCK] Response JSON decode failed: %s", exc)
                return None

        return None

    def _request_parsed(
        self,
        payload: dict[str, Any],
        timeout_seconds: int,
        expect: Literal["object", "array"],
        context_label: str,
    ) -> tuple[dict[str, Any] | list[Any] | None, str | None, str | None]:
        logger.info("[BEDROCK MODEL] %s model=%s region=%s", context_label, self.model, self.region)
        data = self._make_request_single(payload, timeout_seconds=timeout_seconds)
        if data is None:
            return None, None, None

        content = _extract_bedrock_converse_text(data)
        try:
            parsed = _parse_json_response(content, expect=expect)
            return parsed, self.model, content
        except ValueError as exc:
            logger.warning(
                "[BEDROCK PARSE FAIL] %s model=%s expect=%s err=%s content_preview=%s",
                context_label,
                self.model,
                expect,
                exc,
                (content or "")[:300],
            )
            return None, None, content

    def analyze(self, symbol: str) -> ProviderResult | None:
        if not self.is_enabled():
            return self._neutral(symbol, "bedrock bearer token not configured")

        cache_key = _llm_cache_key(self.cache_namespace, symbol)
        now = time.time()

        cached = _llm_cache.get(cache_key)
        if cached and now - cached[0] < LLM_CACHE_TTL_SECONDS:
            logger.debug("[BEDROCK CACHE HIT] %s", symbol)
            return cached[1]

        context_text = build_symbol_research_context(symbol)
        user_prompt = f"""Analyze market sentiment for {symbol}.
Use only the RESEARCH CONTEXT below. Do not invent prices, levels, support, resistance, price action, or breakout details that are not explicitly in it.
If the context contains no meaningful asset-specific catalyst, write exactly "no clear asset-specific catalyst" in reasoning and keep the score small.
Return only the JSON object defined by the schema — no markdown, no extra text.

RESEARCH CONTEXT:
{context_text or 'No external research context available.'}
"""

        payload = {
            "messages": [
                {"role": "system", "content": LLM_SINGLE_SYMBOL_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 220,
        }

        logger.info("[BEDROCK API CALL] %s", symbol)
        parsed, used_model, _ = self._request_parsed(
            payload,
            timeout_seconds=30,
            expect="object",
            context_label=f"analyze:{symbol}",
        )
        if parsed is None or used_model is None or not isinstance(parsed, dict):
            return self._neutral(symbol, "bedrock api error")

        result = _provider_result_from_payload(self.name, symbol, parsed, used_model)
        _llm_cache[cache_key] = (now, result)
        logger.info("[BEDROCK CACHED] %s for %d seconds model=%s", symbol, LLM_CACHE_TTL_SECONDS, used_model)
        return result


class GroqProvider(BaseProvider):
    name = "groq"

    def __init__(self, api_key: str = "", model: str = "llama-3.3-70b-versatile"):
        self.api_key = api_key
        primary_model = model.strip() if model else "llama-3.3-70b-versatile"
        self.models = _dedupe_models(
            [
                primary_model,
                _setting_str("groq_fallback_model"),
                _setting_str("groq_fallback_fallback_model"),
                _setting_str("groq_fallback_fallback_fallback_model"),
                _setting_str("groq_fallback_fallback_fallback_fallback_model"),
            ]
        )
        self.model = self.models[0] if self.models else "llama-3.3-70b-versatile"
        self.cache_namespace = "||".join(self.models) if self.models else self.model
        self._refresh_done = False
        self._last_request_time = 0.0
        self._min_request_interval = 1.0

    def is_enabled(self) -> bool:
        return bool(self.api_key and self.api_key.strip())

    def set_refresh_done(self) -> None:
        self._refresh_done = True

    def _rate_limit(self) -> None:
        elapsed = time.time() - self._last_request_time
        if elapsed < self._min_request_interval:
            sleep_time = self._min_request_interval - elapsed
            logger.debug("[GROQ RATE LIMIT] sleeping %.2fs", sleep_time)
            time.sleep(sleep_time)
        self._last_request_time = time.time()

    def _make_request_single(self, payload: dict[str, Any], timeout_seconds: int = 30) -> dict[str, Any] | None:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        for attempt in range(3):
            self._rate_limit()
            try:
                response = requests.post(
                    GROQ_API_URL,
                    headers=headers,
                    json=payload,
                    timeout=timeout_seconds,
                )
            except requests.Timeout:
                logger.warning("[GROQ] Request timeout, retrying... (attempt %d/3)", attempt + 1)
                time.sleep(5 * (attempt + 1))
                continue
            except Exception as exc:
                logger.warning("[GROQ] Request error: %s", exc)
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
                return None

            if response.status_code == 429:
                try:
                    error_data = response.json() if response.text else {}
                except Exception:
                    error_data = {}
                error_msg = str(error_data.get("error", {}).get("message", ""))
                if "tokens per day" in error_msg.lower() or "rate limit exceeded" in error_msg.lower():
                    logger.warning("[GROQ] Daily token limit reached. %s", error_msg)
                    return None
                retry_after = int(response.headers.get("Retry-After", 60))
                logger.warning("[GROQ] Rate limited, waiting %ds (attempt %d/3)", retry_after, attempt + 1)
                time.sleep(min(retry_after, 120))
                continue

            if response.status_code >= 500:
                logger.warning("[GROQ] Server error %s, retrying... (attempt %d/3)", response.status_code, attempt + 1)
                time.sleep(5 * (attempt + 1))
                continue

            if response.status_code != 200:
                logger.warning("[GROQ] API error: %s %s", response.status_code, response.text)
                return None

            try:
                return response.json()
            except Exception as exc:
                logger.warning("[GROQ] Response JSON decode failed: %s", exc)
                return None

        return None

    def _request_parsed(
        self,
        payload: dict[str, Any],
        timeout_seconds: int,
        expect: Literal["object", "array"],
        context_label: str,
    ) -> tuple[dict[str, Any] | list[Any] | None, str | None, str | None]:
        last_content: str | None = None
        for idx, candidate_model in enumerate(self.models or [self.model]):
            request_payload = dict(payload)
            request_payload["model"] = candidate_model
            if idx == 0:
                logger.info("[GROQ MODEL] %s using primary model=%s", context_label, candidate_model)
            else:
                logger.warning("[GROQ FALLBACK] %s fallback_index=%d model=%s", context_label, idx, candidate_model)

            data = self._make_request_single(request_payload, timeout_seconds=timeout_seconds)
            if data is None:
                continue

            content = _extract_choice_content(data)
            last_content = content
            try:
                parsed = _parse_json_response(content, expect=expect)
                return parsed, candidate_model, content
            except ValueError as exc:
                logger.warning(
                    "[GROQ PARSE FAIL] %s model=%s expect=%s err=%s content_preview=%s",
                    context_label,
                    candidate_model,
                    expect,
                    exc,
                    (content or "")[:300],
                )
                continue

        return None, None, last_content

    def analyze(self, symbol: str) -> ProviderResult | None:
        if not self.is_enabled():
            return self._neutral(symbol, "groq api key not configured")

        cache_key = _llm_cache_key(self.cache_namespace, symbol)
        now = time.time()

        cached = _llm_cache.get(cache_key)
        if cached and now - cached[0] < LLM_CACHE_TTL_SECONDS:
            logger.debug("[GROQ CACHE HIT] %s", symbol)
            return cached[1]

        if self._refresh_done:
            return self._neutral(symbol, "groq cache miss after refresh")

        context_text = build_symbol_research_context(symbol)
        user_prompt = f"""Analyze market sentiment for {symbol}.
Use only the RESEARCH CONTEXT below. Do not invent prices, levels, support, resistance, price action, or breakout details that are not explicitly in it.
If the context contains no meaningful asset-specific catalyst, write exactly "no clear asset-specific catalyst" in reasoning and keep the score small.
Return only the JSON object defined by the schema — no markdown, no extra text.

RESEARCH CONTEXT:
{context_text or 'No external research context available.'}
"""

        payload = {
            "messages": [
                {"role": "system", "content": LLM_SINGLE_SYMBOL_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.2,
            "max_tokens": 220,
        }

        logger.info("[GROQ API CALL] %s", symbol)
        parsed, used_model, _ = self._request_parsed(
            payload,
            timeout_seconds=30,
            expect="object",
            context_label=f"analyze:{symbol}",
        )
        if parsed is None or used_model is None or not isinstance(parsed, dict):
            return self._neutral(symbol, "groq api error")

        result = _provider_result_from_payload(self.name, symbol, parsed, used_model)
        _llm_cache[cache_key] = (now, result)
        logger.info("[GROQ CACHED] %s for %d seconds model=%s", symbol, LLM_CACHE_TTL_SECONDS, used_model)
        return result


def _llm_cache_key(model: str, symbol: str) -> str:
    return f"{model}:{symbol.upper()}"


def _extract_choice_content(data: dict[str, Any]) -> str:
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    return str(content or "").strip()


def _build_bedrock_converse_payload(payload: dict[str, Any]) -> dict[str, Any]:
    system_parts: list[str] = []
    converse_messages: list[dict[str, Any]] = []

    for message in payload.get("messages", []) or []:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "user").strip().lower()
        content = str(message.get("content") or "").strip()
        if not content:
            continue
        if role == "system":
            system_parts.append(content)
            continue
        if role not in {"user", "assistant"}:
            role = "user"
        converse_messages.append({
            "role": role,
            "content": [{"text": content}],
        })

    if not converse_messages:
        converse_messages.append({
            "role": "user",
            "content": [{"text": "Return valid JSON."}],
        })

    body: dict[str, Any] = {
        "messages": converse_messages,
        "inferenceConfig": {
            "maxTokens": int(payload.get("max_tokens") or 1024),
            "temperature": float(payload.get("temperature") or 0.0),
        },
    }
    if system_parts:
        body["system"] = [{"text": "\n\n".join(system_parts)}]
    return body


def _extract_bedrock_converse_text(data: dict[str, Any]) -> str:
    output_message = (data.get("output") or {}).get("message")
    if isinstance(output_message, dict):
        content = output_message.get("content")
        if isinstance(content, list):
            parts: list[str] = []
            for item in content:
                if isinstance(item, dict) and item.get("text") is not None:
                    parts.append(str(item.get("text") or ""))
            return "".join(parts).strip()

    content = data.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            elif isinstance(item, str):
                parts.append(item)
        return "".join(parts).strip()
    if isinstance(data.get("completion"), str):
        return str(data.get("completion") or "").strip()
    if isinstance(data.get("output_text"), str):
        return str(data.get("output_text") or "").strip()
    return ""


def _provider_display_name(provider: Any) -> str:
    return str(getattr(provider, "name", provider.__class__.__name__.lower()) or "llm")


def _provider_model_label(provider: Any, model: str) -> str:
    provider_name = _provider_display_name(provider)
    if str(model).startswith(f"{provider_name}:"):
        return str(model)
    return f"{provider_name}:{model}"


def _strip_code_fences(content: str) -> str:
    text = (content or "").strip()
    if not text:
        return ""

    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z0-9_\-]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        return text.strip()

    return text


def _find_balanced_json_segment(text: str, opening: str, closing: str) -> str | None:
    start = text.find(opening)
    if start < 0:
        return None

    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(text)):
        ch = text[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue

        if ch == '"':
            in_string = True
            continue
        if ch == opening:
            depth += 1
        elif ch == closing:
            depth -= 1
            if depth == 0:
                return text[start:idx + 1]
    return None


def _parse_json_response(content: str, expect: Literal["object", "array"]) -> dict[str, Any] | list[Any]:
    text = _strip_code_fences(content)
    if not text:
        raise ValueError("empty content")

    candidates: list[str] = [text]
    array_candidate = _find_balanced_json_segment(text, "[", "]")
    object_candidate = _find_balanced_json_segment(text, "{", "}")

    if expect == "array":
        if array_candidate and array_candidate not in candidates:
            candidates.append(array_candidate)
        if object_candidate and object_candidate not in candidates:
            candidates.append(object_candidate)
    else:
        if object_candidate and object_candidate not in candidates:
            candidates.append(object_candidate)
        if array_candidate and array_candidate not in candidates:
            candidates.append(array_candidate)

    last_error: Exception | None = None
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except Exception as exc:
            last_error = exc
            continue

        if expect == "array":
            if isinstance(parsed, list):
                return parsed
            if isinstance(parsed, dict):
                for key in ("results", "analyses", "items", "symbols", "data"):
                    value = parsed.get(key)
                    if isinstance(value, list):
                        return value
        else:
            if isinstance(parsed, dict):
                return parsed
            if isinstance(parsed, list) and parsed and isinstance(parsed[0], dict):
                return parsed[0]

    raise ValueError(f"json parse failed: {last_error}")


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except Exception:
        return float(default)


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(round(float(value)))
    except Exception:
        return int(default)


def _normalize_stance(value: Any) -> str:
    stance = str(value or "HOLD").strip().upper()
    return stance if stance in VALID_STANCES else "HOLD"


def _normalize_score(value: Any, stance: str) -> int:
    score = _safe_int(value, STANCE_DEFAULT_SCORES.get(stance, 0))
    return max(AI_SCORE_MIN, min(AI_SCORE_MAX, score))


def _normalize_confidence(value: Any) -> float:
    conf = _safe_float(value, 0.0)
    return max(0.0, min(1.0, conf))


def _provider_result_from_payload(provider_name: str, symbol: str, payload: dict[str, Any], model: str) -> ProviderResult:
    stance = _normalize_stance(payload.get("stance"))
    score = _normalize_score(payload.get("score"), stance)
    confidence = _normalize_confidence(payload.get("confidence"))
    reasoning = str(payload.get("reasoning") or payload.get("summary") or "").strip()
    reasoning_lower = reasoning.lower()
    weak_catalyst = (
        "no clear asset-specific catalyst" in reasoning_lower
        or "no meaningful catalyst" in reasoning_lower
    )
    if weak_catalyst and score > 0:
        score = min(score, 2)
    elif weak_catalyst and score < 0:
        score = max(score, -8)
    if confidence < 0.55 and score > 0:
        score = min(score, 1)
    return ProviderResult(
        provider=provider_name,
        symbol=symbol,
        stance=stance,
        rumor_score=score,
        confidence=confidence,
        summary=reasoning,
        raw={"model": model, "raw_response": payload},
    )


def _canonical_symbol(symbol: str) -> str:
    return str(symbol or "").strip().upper()


def _symbol_aliases(symbol: str) -> set[str]:
    canonical = _canonical_symbol(symbol)
    base = canonical.split("/")[0]
    aliases = {
        canonical,
        base,
        canonical.replace("/", ""),
        canonical.replace("/USDT", ""),
        base.lower(),
    }
    return {a for a in aliases if a}


def _resolve_symbol_from_payload(raw_symbol: Any, allowed_symbols: list[str]) -> str | None:
    raw = _canonical_symbol(str(raw_symbol or ""))
    if not raw:
        return None
    alias_map: dict[str, str] = {}
    for sym in allowed_symbols:
        canonical = _canonical_symbol(sym)
        for alias in _symbol_aliases(canonical):
            alias_map[_canonical_symbol(alias)] = canonical
    if raw in alias_map:
        return alias_map[raw]
    cleaned = raw.replace("-", "/")
    if cleaned in alias_map:
        return alias_map[cleaned]
    if "/" not in raw and raw.endswith("USDT"):
        raw2 = raw[:-4] + "/USDT"
        if raw2 in alias_map:
            return alias_map[raw2]
    return None



COINGECKO_ID_MAP = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "XRP": "ripple",
    "AVAX": "avalanche-2",
    "ADA": "cardano",
    "BNB": "binancecoin",
}


def _coingecko_coin_id_from_symbol(symbol: str) -> str | None:
    canonical = _canonical_symbol(symbol)
    base = canonical.split("/")[0].strip().upper()
    if not base:
        return None
    return COINGECKO_ID_MAP.get(base)


def _coingecko_request_candidates() -> list[tuple[str, str, dict[str, str]]]:
    """
    CoinGecko key isimleri proje içinde tutarsız olabildiği için hem
    coingecko_api_key hem coin_gecko_api_key varyantlarını destekler.

    Dönüş formatı:
    [(label, url, headers), ...]
    """
    pro_key = _setting_str("coingecko_pro_api_key", "coin_gecko_pro_api_key")
    demo_key = _setting_str("coingecko_demo_api_key", "coin_gecko_demo_api_key")
    generic_key = _setting_str("coingecko_api_key", "coin_gecko_api_key")

    candidates: list[tuple[str, str, dict[str, str]]] = []

    if pro_key:
        candidates.append(
            (
                "pro",
                COINGECKO_PRO_BASE_URL + COINGECKO_NEWS_PATH,
                {"x-cg-pro-api-key": pro_key},
            )
        )

    if demo_key:
        candidates.append(
            (
                "demo",
                COINGECKO_DEMO_BASE_URL + COINGECKO_NEWS_PATH,
                {"x-cg-demo-api-key": demo_key},
            )
        )

    if generic_key:
        # Generic key için önce demo dene, sonra pro dene.
        # Çünkü kullanıcı çoğu zaman elindeki key'in demo mu pro mu olduğunu
        # settings isimlendirmesine doğru yansıtmayabiliyor.
        candidates.append(
            (
                "generic-demo",
                COINGECKO_DEMO_BASE_URL + COINGECKO_NEWS_PATH,
                {"x-cg-demo-api-key": generic_key},
            )
        )
        candidates.append(
            (
                "generic-pro",
                COINGECKO_PRO_BASE_URL + COINGECKO_NEWS_PATH,
                {"x-cg-pro-api-key": generic_key},
            )
        )

    deduped: list[tuple[str, str, dict[str, str]]] = []
    seen: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    for label, url, headers in candidates:
        key = (url, tuple(sorted(headers.items())))
        if key in seen:
            continue
        seen.add(key)
        deduped.append((label, url, headers))

    return deduped


def fetch_coingecko_news(symbol: str) -> str:
    """
    CoinGecko /news endpointinden ilgili coin haberlerini çeker ve kısa özet döndürür.
    Endpoint pair formatı değil coin_id ister. Ayrıca response şeması liste döner.
    """
    canonical = _canonical_symbol(symbol)
    cached = _coingecko_news_cache.get(canonical)
    now = time.time()
    if cached and now - cached[0] < RESEARCH_CONTEXT_TTL_SECONDS:
        return cached[1]

    coin_id = _coingecko_coin_id_from_symbol(canonical)
    if not coin_id:
        logger.warning("[COINGECKO NEWS] skipped for %s: no coin_id mapping", canonical)
        _coingecko_news_cache[canonical] = (now, "")
        return ""

    candidates = _coingecko_request_candidates()
    if not candidates:
        logger.info("[COINGECKO NEWS] skipped for %s: api key missing", canonical)
        _coingecko_news_cache[canonical] = (now, "")
        return ""

    params = {
        "coin_id": coin_id,
        "page": 1,
        "per_page": 10,
        "type": "news",
        "language": "en",
    }

    last_error_log = ""

    for label, url, headers in candidates:
        try:
            response = requests.get(
                url,
                headers=headers,
                params=params,
                timeout=COINGECKO_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            last_error_log = f"transport_error via={label} err={exc}"
            logger.warning("[COINGECKO NEWS] transport error for %s coin_id=%s via=%s: %s", canonical, coin_id, label, exc)
            continue

        if response.status_code != 200:
            body_preview = (response.text or "")[:240].replace("\n", " ")
            last_error_log = f"status={response.status_code} via={label} body={body_preview}"
            logger.warning(
                "[COINGECKO NEWS] API error for %s coin_id=%s via=%s status=%s body=%s",
                canonical,
                coin_id,
                label,
                response.status_code,
                body_preview,
            )
            # Auth / permission / wrong key type durumlarında diğer adayları dene.
            if response.status_code in {400, 401, 403, 404, 422, 429}:
                continue
            _coingecko_news_cache[canonical] = (now, "")
            return ""

        try:
            payload = response.json()
        except Exception as exc:
            logger.warning("[COINGECKO NEWS] JSON decode error for %s via=%s: %s", canonical, label, exc)
            last_error_log = f"json_decode_error via={label} err={exc}"
            continue

        news_items = payload if isinstance(payload, list) else payload.get("data", [])
        if not isinstance(news_items, list):
            logger.warning(
                "[COINGECKO NEWS] unexpected payload type for %s via=%s: %s",
                canonical,
                label,
                type(payload).__name__,
            )
            last_error_log = f"unexpected_payload via={label} type={type(payload).__name__}"
            continue

        relevant_news: list[str] = []
        for item in news_items[:10]:
            if not isinstance(item, dict):
                continue
            related_ids = item.get("related_coin_ids") or []
            if related_ids and coin_id not in [str(x).strip().lower() for x in related_ids]:
                continue
            title = str(item.get("title", "") or "").strip()
            source_name = str(item.get("source_name", "") or "").strip()
            if not title:
                continue
            snippet = f"{source_name}: {title}" if source_name else title
            relevant_news.append(snippet[:180])

        result = " | ".join(relevant_news[:5]) if relevant_news else ""
        _coingecko_news_cache[canonical] = (now, result)
        if result:
            logger.info(
                "[COINGECKO NEWS] %s coin_id=%s via=%s matched=%d",
                canonical,
                coin_id,
                label,
                len(relevant_news[:5]),
            )
        else:
            logger.info("[COINGECKO NEWS] %s coin_id=%s via=%s no matches", canonical, coin_id, label)
        return result

    if last_error_log:
        logger.warning("[COINGECKO NEWS] all candidates failed for %s coin_id=%s last=%s", canonical, coin_id, last_error_log)
    _coingecko_news_cache[canonical] = (now, "")
    return ""


def _parse_exa_results(payload: dict[str, Any]) -> str:
    results = payload.get("results")
    if not isinstance(results, list):
        results = payload.get("data") if isinstance(payload.get("data"), list) else []

    snippets: list[str] = []
    for item in results[:MAX_EXA_RESULTS]:
        title = str(item.get("title", "") or "").strip()
        highlights = item.get("highlights") or []
        highlight = ""
        if isinstance(highlights, list) and highlights:
            highlight = str(highlights[0] or "").strip()
        elif isinstance(item.get("summary"), str):
            highlight = str(item.get("summary") or "").strip()
        elif isinstance(item.get("text"), str):
            highlight = str(item.get("text") or "").strip()[:160]

        if title:
            if highlight:
                snippets.append(f"{title}: {highlight[:180]}")
            else:
                snippets.append(title[:180])

    return " || ".join(snippets)


def _fetch_exa_news_http(symbol: str, api_key: str) -> str:
    query = f"{symbol} crypto news market sentiment recent developments"
    headers = {
        "x-api-key": api_key,
        "Content-Type": "application/json",
    }
    payload = {
        "query": query,
        "type": "auto",
        "numResults": MAX_EXA_RESULTS,
        "contents": {
            "highlights": {"query": symbol, "maxCharacters": 240}
        },
    }
    logger.info("[EXA SEARCH] %s via HTTP", symbol)
    response = requests.post(EXA_SEARCH_URL, headers=headers, json=payload, timeout=EXA_TIMEOUT_SECONDS)
    if response.status_code != 200:
        raise RuntimeError(f"exa http status={response.status_code} body={response.text[:200]}")
    return _parse_exa_results(response.json())


def _fetch_exa_news_sdk(symbol: str, api_key: str) -> str:
    from exa_py import Exa

    logger.info("[EXA SEARCH] %s via SDK", symbol)
    exa = Exa(api_key=api_key)
    results = exa.search_and_contents(
        f"{symbol} crypto news market sentiment recent developments",
        type="auto",
        num_results=MAX_EXA_RESULTS,
        contents={"highlights": {"max_characters": 240}},
    )
    snippets: list[str] = []
    for item in getattr(results, "results", [])[:MAX_EXA_RESULTS]:
        title = str(getattr(item, "title", "") or "").strip()
        highlights = getattr(item, "highlights", None) or []
        highlight = str(highlights[0] or "").strip() if highlights else ""
        if title:
            snippets.append(f"{title}: {highlight[:180]}" if highlight else title[:180])
    return " || ".join(snippets)


def fetch_exa_news(symbol: str, api_key: str) -> str:
    """
    Exa web araştırmasını çeker.
    Önce raw HTTP denenir; olmazsa exa-py SDK fallback yapılır.
    """
    canonical = _canonical_symbol(symbol)
    cached = _exa_news_cache.get(canonical)
    now = time.time()
    if cached and now - cached[0] < RESEARCH_CONTEXT_TTL_SECONDS:
        return cached[1]

    if not api_key:
        logger.info("[EXA SEARCH] skipped for %s: api key missing", canonical)
        return ""

    try:
        result = _fetch_exa_news_http(canonical, api_key)
        _exa_news_cache[canonical] = (now, result)
        if result:
            logger.info("[EXA SEARCH] %s ok via HTTP", canonical)
        else:
            logger.info("[EXA SEARCH] %s empty via HTTP", canonical)
        return result
    except Exception as http_exc:
        logger.warning("[EXA SEARCH] HTTP failed for %s: %s", canonical, http_exc)

    try:
        result = _fetch_exa_news_sdk(canonical, api_key)
        _exa_news_cache[canonical] = (now, result)
        if result:
            logger.info("[EXA SEARCH] %s ok via SDK", canonical)
        else:
            logger.info("[EXA SEARCH] %s empty via SDK", canonical)
        return result
    except ImportError:
        logger.warning("[EXA SEARCH] SDK fallback unavailable for %s: exa-py not installed", canonical)
    except Exception as sdk_exc:
        logger.warning("[EXA SEARCH] SDK failed for %s: %s", canonical, sdk_exc)

    _exa_news_cache[canonical] = (now, "")
    return ""


def _compact_text(text: str, limit: int) -> str:
    raw = " ".join(str(text or "").split())
    if limit <= 0 or len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 3)].rstrip() + "..."


def _compact_context_lines(lines: list[str], per_line_limit: int, total_limit: int) -> str:
    compacted = [_compact_text(line, per_line_limit) for line in lines if str(line or "").strip()]
    merged = "\n".join(compacted)
    return _compact_text(merged, total_limit)


def _format_price_action_context(data: dict[str, Any]) -> str:
    price_action = data.get("price_action", {}) if isinstance(data, dict) else {}
    if not isinstance(price_action, dict) or not price_action:
        return _compact_text(
            f"price={data.get('price', 'N/A')}, volume={data.get('volume', 'N/A')}, change_24h={data.get('change_24h', 'N/A')}",
            220,
        )

    return _compact_text(
        (
            f"price={data.get('price', 'N/A')}, change_24h={data.get('change_24h', 'N/A')}, volume={data.get('volume', 'N/A')}, "
            f"structure={price_action.get('structure', 'N/A')}, breakout={price_action.get('breakout_state', 'N/A')}, "
            f"support={_safe_float(price_action.get('recent_support'), 0.0):.6f}, resistance={_safe_float(price_action.get('recent_resistance'), 0.0):.6f}, "
            f"swing_low={_safe_float(price_action.get('swing_low'), 0.0):.6f}, swing_high={_safe_float(price_action.get('swing_high'), 0.0):.6f}, "
            f"nearest_support={price_action.get('nearest_support_label', 'N/A')}@{_safe_float(price_action.get('nearest_support_value'), 0.0):.6f}, "
            f"nearest_resistance={price_action.get('nearest_resistance_label', 'N/A')}@{_safe_float(price_action.get('nearest_resistance_value'), 0.0):.6f}, "
            f"fib_0.382={_safe_float(price_action.get('fib_0_382'), 0.0):.6f}, fib_0.500={_safe_float(price_action.get('fib_0_500'), 0.0):.6f}, "
            f"fib_0.618={_safe_float(price_action.get('fib_0_618'), 0.0):.6f}, range_position={_safe_float(price_action.get('range_position'), 0.0):.3f}, "
            f"last_close_change_pct={_safe_float(price_action.get('last_close_change_pct'), 0.0):.3f}"
        ),
        360,
    )


def build_symbol_research_context(symbol: str) -> str:
    canonical = _canonical_symbol(symbol)
    cached = _research_context_cache.get(canonical)
    now = time.time()
    if cached and now - cached[0] < RESEARCH_CONTEXT_TTL_SECONDS:
        return cached[1]

    parts: list[str] = []
    coingecko_news = fetch_coingecko_news(canonical)
    if coingecko_news:
        parts.append(f"CoinGecko news: {_compact_text(coingecko_news, 280)}")

    exa_key = getattr(settings, "exa_api_key", "") or ""
    exa_news = fetch_exa_news(canonical, exa_key) if exa_key else ""
    if exa_news:
        parts.append(f"Exa web research: {_compact_text(exa_news, 320)}")

    context_limit = int(getattr(settings, "research_context_max_chars", 700) or 700)
    context = _compact_context_lines(parts, per_line_limit=360, total_limit=context_limit)
    _research_context_cache[canonical] = (now, context)
    return context


def _provider_order() -> list[str]:
    raw = _setting_str("llm_provider_order")
    if not raw:
        raw = "groq"
    names = [item.strip().lower() for item in raw.split(",")]
    return _dedupe_models(names)


def _provider_is_enabled(provider: Any) -> bool:
    is_enabled = getattr(provider, "is_enabled", None)
    if callable(is_enabled):
        try:
            return bool(is_enabled())
        except Exception as exc:
            logger.warning("[LLM PROVIDER] enabled check failed provider=%s err=%s", _provider_display_name(provider), exc)
            return False
    return True


def _build_provider(provider_name: str) -> BaseProvider | None:
    if provider_name == "bedrock":
        api_key = _setting_str("aws_bearer_token_bedrock", "bedrock_api_key")
        if not api_key:
            return None
        return BedrockProvider(
            api_key=api_key,
            model=_setting_str("bedrock_model") or BEDROCK_DEFAULT_MODEL,
            region=_setting_str("bedrock_region", "aws_region", "aws_default_region") or BEDROCK_DEFAULT_REGION,
        )

    if provider_name == "groq":
        groq_api_key = _setting_str("groq_api_key")
        if not groq_api_key:
            return None
        return GroqProvider(
            api_key=groq_api_key,
            model=_setting_str("groq_model") or "llama-3.3-70b-versatile",
        )

    logger.warning("[LLM PROVIDER] unknown provider in LLM_PROVIDER_ORDER: %s", provider_name)
    return None


def build_provider_list() -> list[BaseProvider]:
    providers: list[BaseProvider] = []

    for provider_name in _provider_order():
        provider = _build_provider(provider_name)
        if provider and _provider_is_enabled(provider):
            providers.append(provider)

    if not providers:
        providers.append(DisabledProvider("no_provider"))

    return providers


def has_configured_llm_provider() -> bool:
    return any(provider.name != "no_provider" and _provider_is_enabled(provider) for provider in build_provider_list())


def _provider_result_is_failure(result: ProviderResult) -> bool:
    raw = result.raw or {}
    if raw.get("disabled") or raw.get("error"):
        return True
    summary = str(result.summary or "").lower()
    failure_markers = (
        "api error",
        "not configured",
        "missing key",
        "provider disabled",
        "cache miss after refresh",
        "request error",
    )
    return any(marker in summary for marker in failure_markers)


def _request_parsed_with_provider_fallback(
    payload: dict[str, Any],
    timeout_seconds: int,
    expect: Literal["object", "array"],
    context_label: str,
) -> tuple[BaseProvider | None, dict[str, Any] | list[Any] | None, str | None, str | None]:
    providers = [provider for provider in build_provider_list() if provider.name != "no_provider"]
    if not providers:
        logger.info("[LLM PROVIDER] %s skipped: no provider configured", context_label)
        return None, None, None, None

    last_content: str | None = None
    for idx, provider in enumerate(providers):
        provider_name = _provider_display_name(provider)
        if idx == 0:
            logger.info("[LLM PROVIDER] %s using provider=%s", context_label, provider_name)
        else:
            logger.warning("[LLM PROVIDER FALLBACK] %s trying provider=%s", context_label, provider_name)

        parsed, used_model, content = provider._request_parsed(
            payload,
            timeout_seconds=timeout_seconds,
            expect=expect,
            context_label=context_label,
        )
        last_content = content or last_content
        if parsed is not None and used_model is not None:
            return provider, parsed, used_model, content

        logger.warning(
            "[LLM PROVIDER FAIL] %s provider=%s content_preview=%s",
            context_label,
            provider_name,
            (content or "")[:300] if content else "empty",
        )

    return None, None, None, last_content


def aggregate_rumor(symbol: str) -> dict[str, Any]:
    providers = build_provider_list()
    items: list[ProviderResult] = []
    failed_items: list[ProviderResult] = []

    for provider in providers:
        try:
            result = provider.analyze(symbol)
            if result and not _provider_result_is_failure(result):
                items.append(result)
                break
            if result:
                failed_items.append(result)
        except Exception as exc:
            logger.exception("[RUMOR] provider=%s symbol=%s error=%s", _provider_display_name(provider), symbol, exc)
            failed_items.append(
                ProviderResult(
                    provider=_provider_display_name(provider),
                    symbol=symbol,
                    stance="HOLD",
                    rumor_score=0,
                    confidence=0.0,
                    summary=f"provider error: {exc}",
                    raw={"error": str(exc)},
                )
            )
            continue

    if not items and failed_items:
        items.append(failed_items[-1])

    # TR: AI tarafi provider sayisi arttikca sismesin diye toplam degil ortalama kullaniyoruz.
    # EN: We use the average instead of the sum so the AI side does not inflate when provider count grows.
    # TR: Boylece rumor_total_score tek bir birlesik AI gorusu gibi davranir ve -24..24 bandinda kalir.
    # EN: This keeps rumor_total_score behaving like one combined AI opinion inside the -24..24 range.
    total_score = round(sum(x.rumor_score for x in items) / len(items), 2) if items else 0.0
    avg_conf = round(sum(x.confidence for x in items) / len(items), 4) if items else 0.0
    return {
        "symbol": symbol,
        "rumor_total_score": total_score,
        "rumor_avg_confidence": avg_conf,
        "providers": [
            {
                "provider": x.provider,
                "stance": x.stance,
                "rumor_score": x.rumor_score,
                "confidence": x.confidence,
                "summary": x.summary,
            }
            for x in items
        ],
    }


def refresh_all_if_needed(
    symbols: list[str],
    market_data: dict[str, dict] | None = None,
    force: bool = False,
) -> bool:
    """
    TTL dolduysa veya force=True ise tüm semboller için seçili LLM provider'a toplu istek atar.
    Exa + CoinGecko + market data bağlamı tek promptta verilir.
    """
    global _llm_last_refresh, _last_bulk_refresh_model
    now = time.time()

    if not force and now - _llm_last_refresh < LLM_CACHE_TTL_SECONDS:
        return False

    if not has_configured_llm_provider():
        logger.info("[LLM REFRESH] skipped: provider not configured")
        return False

    if not symbols:
        logger.info("[LLM REFRESH] skipped: empty symbol list")
        return False

    refresh_reason = "forced" if force else "ttl_expired"
    logger.info("[LLM REFRESH] starting (%s) for %d symbols", refresh_reason, len(symbols))

    context_lines: list[str] = []
    per_symbol_context_limit = int(getattr(settings, "bulk_refresh_max_context_chars_per_symbol", 260) or 260)
    per_symbol_context_limit = max(120, min(320, per_symbol_context_limit))
    news_context_limit = max(60, min(120, per_symbol_context_limit // 2))
    for sym in symbols:
        data = (market_data or {}).get(sym, {})
        symbol_lines = [f"SYMBOL={sym}"]
        if data:
            symbol_lines.append(
                "PRICE_ACTION=" + _format_price_action_context(data)
            )
        research_context = build_symbol_research_context(sym)
        symbol_lines.append(f"NEWS={_compact_text(research_context or 'none', news_context_limit)}")
        context_lines.append(_compact_context_lines(symbol_lines, per_line_limit=160, total_limit=per_symbol_context_limit))

    user_prompt = """Score every symbol block below and return one JSON array item per block.
Use only the supplied SYMBOL / PRICE_ACTION / NEWS lines. Keep each reasoning under 12 words and symbol-specific.
If NEWS contains no real catalyst, judge from PRICE_ACTION alone: state "no clear asset-specific catalyst", keep score within -8..+2, and set confidence 0.40-0.65.
Only return score=0 with HOLD when price action is genuinely ambiguous — absence of news alone is not HOLD.
Confidence must always be meaningful; never 0.0 unless a block has no usable information.
Echo each symbol exactly as written, keep the input order, and include every symbol exactly once.

""" + "\n\n".join(context_lines)

    logger.info(
        "[LLM REFRESH] prompt_budget symbols=%d chars=%d per_symbol_limit=%d news_limit=%d",
        len(symbols),
        len(user_prompt),
        per_symbol_context_limit,
        news_context_limit,
    )

    payload = {
        "messages": [
            {"role": "system", "content": LLM_BULK_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.2,
        "max_tokens": 1200,
    }

    provider, parsed, used_model, content = _request_parsed_with_provider_fallback(
        payload,
        timeout_seconds=35,
        expect="array",
        context_label="bulk_refresh",
    )
    if provider is None or parsed is None or used_model is None:
        logger.warning("[LLM REFRESH] all providers failed. Content preview: %s", (content or "")[:500] if content else "empty")
        return False

    if not isinstance(parsed, list):
        logger.warning("[LLM REFRESH] parsed payload is not a list")
        return False

    provider_name = _provider_display_name(provider)
    provider_namespace = str(getattr(provider, "cache_namespace", f"{provider_name}:{used_model}"))
    cached_count = 0
    refreshed_at = time.time()
    for item in parsed:
        if not isinstance(item, dict):
            continue
        resolved_symbol = _resolve_symbol_from_payload(item.get("symbol"), symbols)
        if not resolved_symbol:
            continue
        cache_key = _llm_cache_key(provider_namespace, resolved_symbol)
        result = _provider_result_from_payload(provider_name, resolved_symbol, item, used_model)
        _llm_cache[cache_key] = (refreshed_at, result)
        cached_count += 1

    if cached_count == 0:
        logger.warning("[LLM REFRESH] parsed response but cached 0 symbols")
        return False

    _llm_last_refresh = refreshed_at
    _last_bulk_refresh_model = _provider_model_label(provider, used_model)
    logger.info("[LLM REFRESH] Cached %d/%d symbols model=%s", cached_count, len(symbols), _last_bulk_refresh_model)
    return True


def get_cached_rumors(symbols: list[str]) -> dict[str, dict[str, Any]]:
    results: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        results[symbol] = aggregate_rumor(symbol)
    return results


def update_thresholds_if_needed(
    market_data: dict[str, dict],
    regime_data: dict[str, dict],
    current_settings: dict[str, Any],
    force: bool = False,
) -> dict[str, Any] | None:
    """
    TTL dolduysa veya force=True ise AI'dan dinamik threshold güncellemesi ister.
    Timestamp sadece başarılı parse sonrası güncellenir.
    """
    global _threshold_last_update, _last_threshold_update_model
    now = time.time()

    if not force and now - _threshold_last_update < THRESHOLD_UPDATE_TTL_SECONDS:
        return None

    if not has_configured_llm_provider():
        logger.info("[AI THRESHOLD] skipped: llm provider not configured")
        return None

    refresh_reason = "forced" if force else "ttl_expired"
    logger.info("[AI THRESHOLD] starting (%s)", refresh_reason)

    threshold_symbol_cap = max(1, int(getattr(settings, "threshold_snapshot_max_symbols", 5) or 5))
    ranked_symbols = sorted(
        market_data.items(),
        key=lambda item: abs(_safe_float((item[1] or {}).get("change_24h"), 0.0)),
        reverse=True,
    )[:threshold_symbol_cap]

    market_lines: list[str] = []
    for sym, data in ranked_symbols:
        indicators = data.get("indicators", {}) if isinstance(data, dict) else {}
        market_lines.append(
            _compact_text(
                f"{sym}: price={data.get('price', 'N/A')}, volume={data.get('volume', 'N/A')}, change_24h={data.get('change_24h', 'N/A')}, "
                f"rsi={indicators.get('rsi', 'N/A')}, macd_cross={indicators.get('macd_cross', 'N/A')}, ema_trend={indicators.get('ema_trend', 'N/A')}",
                180,
            )
        )

    regime_lines: list[str] = []
    for sym, data in list(regime_data.items())[:threshold_symbol_cap]:
        adx = data.get('adx')
        try:
            adx_text = f"{float(adx):.1f}" if adx is not None else "N/A"
        except (TypeError, ValueError):
            adx_text = "N/A"
        regime_lines.append(
            _compact_text(
                f"{sym}: regime={data.get('regime', 'UNKNOWN')}, adx={adx_text}, trend_bias={data.get('trend_bias', 'N/A')}",
                100,
            )
        )

    current_thresholds = {
        "buy_threshold": float(current_settings.get("buy_threshold", 5)),
        "strong_buy_threshold": float(current_settings.get("strong_buy_threshold", 8)),
        "sell_threshold": float(current_settings.get("sell_threshold", -5)),
        "strong_sell_threshold": float(current_settings.get("strong_sell_threshold", -8)),
        "buy_pct": float(current_settings.get("buy_pct", 0.04)),
        "strong_buy_pct": float(current_settings.get("strong_buy_pct", 0.08)),
    }

    market_snapshot = "\n".join(market_lines) if market_lines else "No market snapshot"
    regime_snapshot = "\n".join(regime_lines) if regime_lines else "No regime snapshot"
    current_settings_json = json.dumps(current_thresholds, ensure_ascii=False)

    user_prompt = f"""Optimize the trading thresholds below using the supplied market/regime snapshot.
Be conservative.

MARKET SNAPSHOT:
{market_snapshot}

REGIME SNAPSHOT:
{regime_snapshot}

CURRENT SETTINGS:
{current_settings_json}

Return ONLY JSON with these keys:
{{
  "buy_threshold": number,
  "strong_buy_threshold": number,
  "sell_threshold": number,
  "strong_sell_threshold": number,
  "buy_pct": number,
  "strong_buy_pct": number,
  "reasoning": "brief explanation"
}}
"""

    payload = {
        "messages": [
            {"role": "system", "content": GROQ_THRESHOLD_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 400,
    }

    provider, parsed, used_model, content = _request_parsed_with_provider_fallback(
        payload,
        timeout_seconds=20,
        expect="object",
        context_label="threshold_update",
    )
    if provider is None or parsed is None or used_model is None or not isinstance(parsed, dict):
        logger.warning("[AI THRESHOLD] all providers failed. Content: %s", content[:500] if content else "empty")
        return None

    updated_at = time.time()
    result = {
        "buy_threshold": max(1.0, min(12.0, _safe_float(parsed.get("buy_threshold"), current_thresholds["buy_threshold"]))),
        "strong_buy_threshold": max(1.0, min(15.0, _safe_float(parsed.get("strong_buy_threshold"), current_thresholds["strong_buy_threshold"]))),
        "sell_threshold": max(-15.0, min(-1.0, _safe_float(parsed.get("sell_threshold"), current_thresholds["sell_threshold"]))),
        "strong_sell_threshold": max(-18.0, min(-1.0, _safe_float(parsed.get("strong_sell_threshold"), current_thresholds["strong_sell_threshold"]))),
        "buy_pct": max(0.001, min(0.20, _safe_float(parsed.get("buy_pct"), current_thresholds["buy_pct"]))),
        "strong_buy_pct": max(0.001, min(0.25, _safe_float(parsed.get("strong_buy_pct"), current_thresholds["strong_buy_pct"]))),
        "reasoning": str(parsed.get("reasoning", "")).strip(),
        "updated_at": updated_at,
    }

    # Mantıksal koruma: strong eşik normal eşikten zayıf olamaz.
    result["strong_buy_threshold"] = max(result["strong_buy_threshold"], result["buy_threshold"])
    result["strong_sell_threshold"] = min(result["strong_sell_threshold"], result["sell_threshold"])
    result["strong_buy_pct"] = max(result["strong_buy_pct"], result["buy_pct"])

    _threshold_last_update = updated_at
    _last_threshold_update_model = _provider_model_label(provider, used_model)
    logger.info("[AI THRESHOLD] Updated model=%s: %s", _last_threshold_update_model, {k: v for k, v in result.items() if k != "reasoning"})
    return result
