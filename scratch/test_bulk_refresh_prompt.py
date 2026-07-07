import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from analysis import rumor_analyzer


def test_bulk_refresh_prompt_is_capped_and_fallback_flow_still_called(monkeypatch):
    captured = {}

    class FakeGroqProvider:
        name = "groq"
        cache_namespace = "fake"

        def __init__(self, api_key: str, model: str):
            self.api_key = api_key
            self.model = model

        def _request_parsed(self, payload, timeout_seconds, expect, context_label):
            captured["payload"] = payload
            captured["timeout_seconds"] = timeout_seconds
            captured["expect"] = expect
            captured["context_label"] = context_label
            return (
                [
                    {"symbol": "BTC/USDT", "stance": "HOLD", "score": 0, "confidence": 0.5, "reasoning": "no clear asset-specific catalyst"},
                    {"symbol": "ETH/USDT", "stance": "BUY", "score": 5, "confidence": 0.7, "reasoning": "constructive price action"},
                ],
                "fake-model",
                "[]",
            )

    monkeypatch.setattr(
        rumor_analyzer,
        "settings",
        SimpleNamespace(
            groq_api_key="test-key",
            groq_model="fake-model",
            bulk_refresh_max_context_chars_per_symbol=260,
        ),
    )
    monkeypatch.setattr(rumor_analyzer, "GroqProvider", FakeGroqProvider)
    monkeypatch.setattr(rumor_analyzer, "build_symbol_research_context", lambda symbol: "x" * 2000)
    monkeypatch.setattr(rumor_analyzer, "_llm_last_refresh", 0.0)

    market_data = {
        "BTC/USDT": {"price": 100, "volume": 1, "change_24h": 2, "price_action": {"structure": "range", "breakout_state": "none"}},
        "ETH/USDT": {"price": 200, "volume": 2, "change_24h": 3, "price_action": {"structure": "trend", "breakout_state": "breakout"}},
    }

    assert rumor_analyzer.refresh_all_if_needed(["BTC/USDT", "ETH/USDT"], market_data=market_data, force=True)

    user_prompt = captured["payload"]["messages"][1]["content"]
    assert captured["expect"] == "array"
    assert captured["context_label"] == "bulk_refresh"
    assert len(user_prompt) < 1400
    assert "x" * 500 not in user_prompt
    assert rumor_analyzer.get_last_bulk_refresh_model() == "groq:fake-model"


def test_threshold_update_clamps_and_records_model(monkeypatch):
    class FakeGroqProvider:
        name = "groq"

        def __init__(self, api_key: str, model: str):
            pass

        def _request_parsed(self, payload, timeout_seconds, expect, context_label):
            assert expect == "object"
            assert context_label == "threshold_update"
            return (
                {
                    "buy_threshold": 99,
                    "strong_buy_threshold": 1,
                    "sell_threshold": -99,
                    "strong_sell_threshold": -1,
                    "buy_pct": 0.9,
                    "strong_buy_pct": 0.001,
                    "reasoning": "test",
                },
                "threshold-model",
                "{}",
            )

    monkeypatch.setattr(
        rumor_analyzer,
        "settings",
        SimpleNamespace(
            groq_api_key="test-key",
            groq_model="threshold-model",
            threshold_snapshot_max_symbols=4,
        ),
    )
    monkeypatch.setattr(rumor_analyzer, "GroqProvider", FakeGroqProvider)
    monkeypatch.setattr(rumor_analyzer, "_threshold_last_update", 0.0)

    result = rumor_analyzer.update_thresholds_if_needed(
        market_data={"BTC/USDT": {"price": 100, "volume": 1, "change_24h": 4, "indicators": {}}},
        regime_data={"BTC/USDT": {"regime": "TREND", "adx": 30, "trend_bias": "UP"}},
        current_settings={
            "buy_threshold": 4,
            "strong_buy_threshold": 8,
            "sell_threshold": -4,
            "strong_sell_threshold": -9,
            "buy_pct": 0.02,
            "strong_buy_pct": 0.04,
        },
        force=True,
    )

    assert result["buy_threshold"] == 12.0
    assert result["strong_buy_threshold"] == 12.0
    assert result["sell_threshold"] == -15.0
    assert result["strong_sell_threshold"] == -15.0
    assert result["buy_pct"] == 0.20
    assert result["strong_buy_pct"] == 0.20
    assert rumor_analyzer.get_last_threshold_update_model() == "groq:threshold-model"


def test_bedrock_converse_payload_extracts_system_and_uses_text_blocks():
    payload = {
        "messages": [
            {"role": "system", "content": "system prompt"},
            {"role": "user", "content": "user prompt"},
        ],
        "temperature": 0.2,
        "max_tokens": 123,
    }

    body = rumor_analyzer._build_bedrock_converse_payload(payload)

    assert body["system"] == [{"text": "system prompt"}]
    assert body["inferenceConfig"]["temperature"] == 0.2
    assert body["inferenceConfig"]["maxTokens"] == 123
    assert body["messages"] == [
        {"role": "user", "content": [{"text": "user prompt"}]},
    ]


def test_bedrock_converse_text_extraction_from_output_message():
    data = {
        "output": {
            "message": {
                "content": [
                    {"text": "{\"stance\":\"HOLD\""},
                    {"text": ",\"score\":0}"},
                ]
            }
        }
    }

    assert rumor_analyzer._extract_bedrock_converse_text(data) == "{\"stance\":\"HOLD\",\"score\":0}"


def test_provider_fallback_uses_groq_after_bedrock_failure(monkeypatch):
    calls = []

    class FakeBedrockProvider:
        name = "bedrock"
        cache_namespace = "bedrock:test"

        def __init__(self, api_key: str, model: str, region: str):
            self.api_key = api_key
            self.model = model
            self.region = region

        def is_enabled(self):
            return True

        def _request_parsed(self, payload, timeout_seconds, expect, context_label):
            calls.append("bedrock")
            return None, None, "not json"

    class FakeGroqProvider:
        name = "groq"
        cache_namespace = "groq:test"

        def __init__(self, api_key: str, model: str):
            self.api_key = api_key
            self.model = model

        def is_enabled(self):
            return True

        def _request_parsed(self, payload, timeout_seconds, expect, context_label):
            calls.append("groq")
            return {"stance": "HOLD", "score": 0, "confidence": 0.5, "reasoning": "test"}, self.model, "{}"

    monkeypatch.setattr(
        rumor_analyzer,
        "settings",
        SimpleNamespace(
            llm_provider_order="bedrock,groq",
            aws_bearer_token_bedrock="bedrock-key",
            bedrock_model="bedrock-model",
            bedrock_region="us-east-1",
            groq_api_key="test-key",
            groq_model="groq-model",
        ),
    )
    monkeypatch.setattr(rumor_analyzer, "BedrockProvider", FakeBedrockProvider)
    monkeypatch.setattr(rumor_analyzer, "GroqProvider", FakeGroqProvider)

    provider, parsed, used_model, _ = rumor_analyzer._request_parsed_with_provider_fallback(
        {"messages": [{"role": "user", "content": "x"}]},
        timeout_seconds=5,
        expect="object",
        context_label="test",
    )

    assert calls == ["bedrock", "groq"]
    assert provider.name == "groq"
    assert parsed["stance"] == "HOLD"
    assert used_model == "groq-model"


def test_missing_bedrock_key_still_builds_groq_provider(monkeypatch):
    class FakeGroqProvider:
        name = "groq"
        cache_namespace = "groq:test"

        def __init__(self, api_key: str, model: str):
            self.api_key = api_key
            self.model = model

        def is_enabled(self):
            return True

    monkeypatch.setattr(
        rumor_analyzer,
        "settings",
        SimpleNamespace(
            llm_provider_order="bedrock,groq",
            groq_api_key="test-key",
            groq_model="groq-model",
        ),
    )
    monkeypatch.setattr(rumor_analyzer, "GroqProvider", FakeGroqProvider)

    providers = rumor_analyzer.build_provider_list()

    assert [provider.name for provider in providers] == ["groq"]
