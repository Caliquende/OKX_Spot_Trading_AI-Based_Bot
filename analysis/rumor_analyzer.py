from __future__ import annotations

"""
RUMOR / LLM ANALYSIS KATMANI
===========================

Botun dış dünya + LLM sentiment tarafı burada.
Bu sürümde düzeltilen kritik problemler:
- Groq bulk refresh promptu artık single-symbol JSON promptuyla çakışmıyor.
- Bulk refresh JSON parse akışı düzeltildi; fenced code block içeriği artık yanlışlıkla çöpe atılmıyor.
- Per-symbol fallback Groq analizi artık sadece sembol adıyla kör gitmiyor; CoinGecko + Exa bağlamı ekleniyor.
- Exa entegrasyonu artık yalnızca exa-py paketine bağlı değil; önce HTTP API, sonra SDK fallback deneniyor.
- Enabled provider listesi artık gerçekten enabled provider'lardan oluşuyor. Disabled provider çöplüğü logu kirletmiyor.
- Refresh timestamp'leri yalnızca başarılı response + başarılı parse sonrası güncelleniyor.
"""

import json
import logging
import re
import time
from dataclasses import dataclass
from typing import Any, Literal

import requests

from config.settings import settings

logger = logging.getLogger(__name__)

GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"
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

GROQ_CACHE_TTL_SECONDS = 10 * 60 * 60
THRESHOLD_UPDATE_TTL_SECONDS = 10 * 60 * 60
RESEARCH_CONTEXT_TTL_SECONDS = 30 * 60
EXA_TIMEOUT_SECONDS = 20
COINGECKO_TIMEOUT_SECONDS = 10
MAX_EXA_RESULTS = 5

STANCE_DEFAULT_SCORES = {
    "STRONG_SELL": -8,
    "SELL": -5,
    "HOLD": 0,
    "BUY": 5,
    "STRONG_BUY": 8,
}
VALID_STANCES = set(STANCE_DEFAULT_SCORES.keys())

GROQ_SINGLE_SYMBOL_SYSTEM_PROMPT = """You are a crypto trading assistant.
Return ONLY valid JSON with this exact schema:
{"stance":"STRONG_BUY|BUY|HOLD|SELL|STRONG_SELL","score":-10,"confidence":0.0,"reasoning":"brief explanation"}
Do not wrap JSON in markdown. Do not add any prose before or after the JSON.
"""

GROQ_BULK_SYSTEM_PROMPT = """You are a crypto trading assistant.
You will receive multiple symbols plus technical/news context.
Return ONLY a valid JSON array. Each item MUST follow this exact schema:
{"symbol":"BTC/USDT","stance":"STRONG_BUY|BUY|HOLD|SELL|STRONG_SELL","score":-10,"confidence":0.0,"reasoning":"brief explanation"}
Do not wrap JSON in markdown. Do not add commentary. Do not omit symbols.
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


_groq_cache: dict[str, tuple[float, ProviderResult]] = {}
_groq_last_refresh: float = 0.0
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
    clear_cache=True ise Groq + araştırma cache'leri de temizlenir.
    """
    global _groq_last_refresh, _threshold_last_update, _last_bulk_refresh_model, _last_threshold_update_model
    _groq_last_refresh = 0.0
    _threshold_last_update = 0.0
    _last_bulk_refresh_model = ""
    _last_threshold_update_model = ""
    if clear_cache:
        _groq_cache.clear()
        _research_context_cache.clear()
        _coingecko_news_cache.clear()
        _exa_news_cache.clear()


class BaseProvider:
    name = "base"

    def is_enabled(self) -> bool:
        return False

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

        cache_key = _groq_cache_key(self.cache_namespace, symbol)
        now = time.time()

        cached = _groq_cache.get(cache_key)
        if cached and now - cached[0] < GROQ_CACHE_TTL_SECONDS:
            logger.debug("[GROQ CACHE HIT] %s", symbol)
            return cached[1]

        if self._refresh_done:
            return self._neutral(symbol, "groq cache miss after refresh")

        context_text = build_symbol_research_context(symbol)
        user_prompt = f"""Analyze market sentiment for {symbol}.
Consider technical state, recent price action, volume, and any supplied news or web research.

RESEARCH CONTEXT:
{context_text or 'No external research context available.'}
"""

        payload = {
            "messages": [
                {"role": "system", "content": GROQ_SINGLE_SYMBOL_SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.1,
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
        _groq_cache[cache_key] = (now, result)
        logger.info("[GROQ CACHED] %s for %d seconds model=%s", symbol, GROQ_CACHE_TTL_SECONDS, used_model)
        return result


def _groq_cache_key(model: str, symbol: str) -> str:
    return f"{model}:{symbol.upper()}"


def _extract_choice_content(data: dict[str, Any]) -> str:
    content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
    return str(content or "").strip()


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
    return max(-10, min(10, score))


def _normalize_confidence(value: Any) -> float:
    conf = _safe_float(value, 0.0)
    return max(0.0, min(1.0, conf))


def _provider_result_from_payload(provider_name: str, symbol: str, payload: dict[str, Any], model: str) -> ProviderResult:
    stance = _normalize_stance(payload.get("stance"))
    score = _normalize_score(payload.get("score"), stance)
    confidence = _normalize_confidence(payload.get("confidence"))
    reasoning = str(payload.get("reasoning") or payload.get("summary") or "").strip()
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


def build_provider_list() -> list[BaseProvider]:
    providers: list[BaseProvider] = []

    if settings.groq_api_key:
        providers.append(GroqProvider(
            api_key=settings.groq_api_key,
            model=settings.groq_model or "llama-3.3-70b-versatile",
        ))

    if not providers:
        providers.append(DisabledProvider("no_provider"))

    return providers


def aggregate_rumor(symbol: str) -> dict[str, Any]:
    providers = build_provider_list()
    items: list[ProviderResult] = []

    for provider in providers:
        try:
            result = provider.analyze(symbol)
            if result:
                items.append(result)
        except Exception as exc:
            logger.exception("[RUMOR] provider=%s symbol=%s error=%s", provider.name, symbol, exc)
            items.append(
                ProviderResult(
                    provider=provider.name,
                    symbol=symbol,
                    stance="HOLD",
                    rumor_score=0,
                    confidence=0.0,
                    summary=f"provider error: {exc}",
                    raw={"error": str(exc)},
                )
            )

    total_score = sum(x.rumor_score for x in items)
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
    TTL dolduysa veya force=True ise tüm semboller için Groq'a toplu istek atar.
    Exa + CoinGecko + market data bağlamı tek promptta verilir.
    """
    global _groq_last_refresh, _last_bulk_refresh_model
    now = time.time()

    if not force and now - _groq_last_refresh < GROQ_CACHE_TTL_SECONDS:
        return False

    provider = GroqProvider(
        api_key=settings.groq_api_key,
        model=settings.groq_model or "llama-3.3-70b-versatile",
    ) if settings.groq_api_key else None

    if not provider:
        logger.info("[GROQ REFRESH] skipped: provider not configured")
        return False

    if not symbols:
        logger.info("[GROQ REFRESH] skipped: empty symbol list")
        return False

    refresh_reason = "forced" if force else "ttl_expired"
    logger.info("[GROQ REFRESH] starting (%s) for %d symbols", refresh_reason, len(symbols))

    context_lines: list[str] = []
    per_symbol_context_limit = int(getattr(settings, "bulk_refresh_max_context_chars_per_symbol", 420) or 420)
    for sym in symbols:
        data = (market_data or {}).get(sym, {})
        indicators = data.get("indicators", {}) if isinstance(data, dict) else {}
        symbol_lines = [f"SYMBOL={sym}"]
        if data:
            symbol_lines.append(
                "TECH="
                + _compact_text(
                    f"price={data.get('price', 'N/A')}, volume={data.get('volume', 'N/A')}, change_24h={data.get('change_24h', 'N/A')}, "
                    f"rsi={indicators.get('rsi', 'N/A')}, macd_cross={indicators.get('macd_cross', 'N/A')}, "
                    f"ema_trend={indicators.get('ema_trend', 'N/A')}, adx={indicators.get('adx', 'N/A')}",
                    220,
                )
            )
        research_context = build_symbol_research_context(sym)
        symbol_lines.append(f"NEWS={_compact_text(research_context or 'none', 180)}")
        context_lines.append(_compact_context_lines(symbol_lines, per_line_limit=220, total_limit=per_symbol_context_limit))

    user_prompt = """Analyze each symbol below and return a JSON array with one item per symbol.
Use only the supplied context. Keep reasoning very short.

""" + "\n\n".join(context_lines)

    payload = {
        "messages": [
            {"role": "system", "content": GROQ_BULK_SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ],
        "temperature": 0.1,
        "max_tokens": 1200,
    }

    parsed, used_model, content = provider._request_parsed(
        payload,
        timeout_seconds=35,
        expect="array",
        context_label="bulk_refresh",
    )
    if parsed is None or used_model is None:
        logger.warning("[GROQ REFRESH] all models failed. Content preview: %s", (content or "")[:500] if content else "empty")
        return False

    if not isinstance(parsed, list):
        logger.warning("[GROQ REFRESH] parsed payload is not a list")
        return False

    cached_count = 0
    refreshed_at = time.time()
    for item in parsed:
        if not isinstance(item, dict):
            continue
        resolved_symbol = _resolve_symbol_from_payload(item.get("symbol"), symbols)
        if not resolved_symbol:
            continue
        cache_key = _groq_cache_key(provider.cache_namespace, resolved_symbol)
        result = _provider_result_from_payload("groq", resolved_symbol, item, used_model)
        _groq_cache[cache_key] = (refreshed_at, result)
        cached_count += 1

    if cached_count == 0:
        logger.warning("[GROQ REFRESH] parsed response but cached 0 symbols")
        return False

    _groq_last_refresh = refreshed_at
    _last_bulk_refresh_model = used_model
    logger.info("[GROQ REFRESH] Cached %d/%d symbols model=%s", cached_count, len(symbols), used_model)
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

    if not settings.groq_api_key:
        logger.info("[AI THRESHOLD] skipped: groq api key not configured")
        return None

    refresh_reason = "forced" if force else "ttl_expired"
    logger.info("[AI THRESHOLD] starting (%s)", refresh_reason)

    provider = GroqProvider(
        api_key=settings.groq_api_key,
        model=settings.groq_model or "llama-3.3-70b-versatile",
    )

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

    parsed, used_model, content = provider._request_parsed(
        payload,
        timeout_seconds=20,
        expect="object",
        context_label="threshold_update",
    )
    if parsed is None or used_model is None or not isinstance(parsed, dict):
        logger.warning("[AI THRESHOLD] all models failed. Content: %s", content[:500] if content else "empty")
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
    _last_threshold_update_model = used_model
    logger.info("[AI THRESHOLD] Updated model=%s: %s", used_model, {k: v for k, v in result.items() if k != "reasoning"})
    return result
