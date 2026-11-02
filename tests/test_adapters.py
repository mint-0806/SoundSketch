import json
import base64
import http.client
import urllib.error
import wave

import pytest

from soundsketch import adapters
from soundsketch.cases import CaseLibrary, CaseRecord


@pytest.fixture(autouse=True)
def isolated_adapter_environment(monkeypatch):
    # Test only synthetic configuration: do not inspect real credentials.
    monkeypatch.setattr(adapters.os, "environ", {})


def test_consent_precedes_file_read_and_network(monkeypatch):
    monkeypatch.setattr(adapters, "_post_json", lambda *args: pytest.fail("must not send"))
    with pytest.raises(adapters.AdapterError, match="同意"):
        adapters.understand_audio("does-not-exist.wav", consent=False)
    with pytest.raises(adapters.AdapterError, match="同意"):
        adapters.understand_audio("does-not-exist.wav", consent="true")


def test_missing_audio_key_is_explicit(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    with pytest.raises(adapters.AdapterError, match="不可用"):
        adapters.understand_audio("does-not-exist.wav", consent=True)


@pytest.mark.parametrize("url", ["http://example.com/v1", "https://u:secret@example.com", "https://example.com?key=secret"])
def test_reject_unsafe_api_url(url):
    with pytest.raises(adapters.AdapterError):
        adapters._safe_base_url(url)


def test_loopback_http_allowed():
    assert adapters._safe_base_url("http://127.0.0.1:8000/v1/") == "http://127.0.0.1:8000/v1"


def test_no_secret_in_provider_failure(monkeypatch):
    class BrokenOpener:
        def open(self, *args, **kwargs):
            raise urllib.error.URLError("https://service.invalid?key=TOP_SECRET")
    monkeypatch.setattr(adapters.urllib.request, "build_opener", lambda *args: BrokenOpener())
    with pytest.raises(adapters.AdapterError) as error:
        adapters._post_json("https://service.invalid", {}, {})
    assert "TOP_SECRET" not in str(error.value)


def test_redirect_does_not_forward_key():
    with pytest.raises(adapters.AdapterError):
        adapters._NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.invalid")


def test_api_protocol_and_schema(monkeypatch):
    monkeypatch.setenv("SOUNDSKETCH_PLANNER_API_KEY", "secret-test-key")
    monkeypatch.setenv("SOUNDSKETCH_PLANNER_MODEL", "test-model")
    monkeypatch.setenv("SOUNDSKETCH_PLANNER_BASE_URL", "https://example.com/v1")
    calls = []
    def fake_post(url, payload, headers):
        calls.append((url, payload, headers))
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
            "kind": "hum", "patch": {"frequency_hz": 80}, "clarification": None})}}]}
    monkeypatch.setattr(adapters, "_post_json", fake_post)
    result = adapters.plan_parameters({"description": "嗡鸣"})
    assert result["patch"] == {"frequency_hz": 80}
    assert calls[0][0].endswith("/chat/completions")
    assert calls[0][1]["response_format"] == {"type": "json_object"}
    assert calls[0][1]["stream"] is False
    assert calls[0][1]["max_tokens"] == 1024
    assert "thinking" not in calls[0][1]
    assert "secret-test-key" not in json.dumps(calls[0][1])


@pytest.mark.parametrize("suggestion", [
    {"kind": "hum", "patch": {"seed": 123}, "clarification": None},
    {"kind": "hum", "patch": {}, "clarification": None, "diagnosis": "motor broken"},
    {"kind": "hum", "patch": {"gain": .1}, "clarification": "Which sound?"},
])
def test_reject_schema_violations(suggestion):
    with pytest.raises(adapters.AdapterError):
        adapters._parse_suggestion(suggestion)


def test_synthetic_cases_are_not_source_evidence():
    record = CaseRecord("demo", "DSP 演示", "synthetic_demo", "demo.wav", generator="local_dsp")
    assert CaseLibrary([record]).source_evidence() == []
    with pytest.raises(ValueError):
        CaseRecord("fake", "伪观测", "observation", "demo.wav", evidence=("generated",), generator="dsp")
    with pytest.raises(ValueError):
        CaseRecord("fake", "伪声源", "synthetic_demo", "demo.wav", generator="dsp", verified_source=True)


def test_real_case_requires_evidence():
    with pytest.raises(ValueError):
        CaseRecord("unverified", "无出处", "observation", "recording.wav")


def test_paid_adapter_is_disabled():
    with pytest.raises(adapters.AdapterError, match="未启用"):
        adapters.ElevenLabsSoundEffectsAdapter().generate("noise", consent=True)


@pytest.fixture
def original_wav(tmp_path):
    path = tmp_path / "reference.wav"
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(24000)
        handle.writeframes(b"\0\0" * 24000)
    return path


def test_gemini_opt_in_sends_original_bytes_and_no_description_inference(monkeypatch, original_wav):
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret")
    monkeypatch.setenv("GEMINI_MODEL", "test-audio-model")
    captured = {}
    def fake_post(url, payload, headers):
        captured.update(url=url, payload=payload, headers=headers)
        return {"candidates": [{"content": {"parts": [{"text": json.dumps({
            "description": "未听到清晰事件", "events": [], "uncertainty": "信号过弱，声源未知"})}]}}]}
    monkeypatch.setattr(adapters, "_post_json", fake_post)
    result = adapters.understand_audio(original_wav, consent=True)
    assert result["measurement"] is False
    assert result["source"] == "original_reference_recording"
    assert captured["headers"]["x-goog-api-key"] == "test-secret"
    assert "test-secret" not in captured["url"]
    inline = captured["payload"]["contents"][0]["parts"][1]["inline_data"]
    assert base64.b64decode(inline["data"]) == original_wav.read_bytes()


def test_gemini_malformed_response_is_not_fake_success(monkeypatch, original_wav):
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret")
    monkeypatch.setenv("GEMINI_MODEL", "test-audio-model")
    monkeypatch.setattr(adapters, "_post_json", lambda *args: {"candidates": []})
    with pytest.raises(adapters.AdapterError, match="没有采用"):
        adapters.understand_audio(original_wav, consent=True)


def test_gemini_invalid_wav_is_not_sent(monkeypatch, tmp_path):
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret")
    monkeypatch.setenv("GEMINI_MODEL", "test-audio-model")
    path = tmp_path / "bad.wav"
    path.write_bytes(b"not a wav")
    monkeypatch.setattr(adapters, "_post_json", lambda *args: pytest.fail("invalid wav must not be sent"))
    with pytest.raises(adapters.AdapterError, match="WAV 校验"):
        adapters.understand_audio(path, consent=True)


@pytest.mark.parametrize("provider,base,model,limit_name", [
    ("mimo", "https://api.xiaomimimo.com/v1", "mimo-v2.6-flash", "max_completion_tokens"),
    ("deepseek", "https://api.deepseek.com", "deepseek-flash", "max_tokens"),
])
def test_provider_presets_and_request_contract(monkeypatch, provider, base, model, limit_name):
    monkeypatch.setenv("SOUNDSKETCH_PLANNER_PROVIDER", provider)
    monkeypatch.setenv("SOUNDSKETCH_PLANNER_API_KEY", "synthetic-key")
    captured = {}
    def fake_post(url, payload, headers):
        captured.update(url=url, payload=payload, headers=headers)
        return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
            "kind": "hum", "patch": {}, "clarification": None})}}]}
    monkeypatch.setattr(adapters, "_post_json", fake_post)
    assert adapters.planner_available()
    config = adapters.planner_config()
    assert config == {"provider": provider, "model": model, "base_url": base,
                      "thinking": "disabled", "max_output_tokens": 1024}
    result = adapters.plan_parameters({"description": "嗡鸣"})
    assert captured["url"] == base + "/chat/completions"
    assert captured["headers"] == {"Authorization": "Bearer synthetic-key"}
    assert captured["payload"]["thinking"] == {"type": "disabled"}
    assert captured["payload"][limit_name] == 1024
    other_limit = "max_tokens" if limit_name == "max_completion_tokens" else "max_completion_tokens"
    assert other_limit not in captured["payload"]
    assert result["_meta"]["provider"] == provider
    assert result["_meta"]["model"] == model
    assert "synthetic-key" not in json.dumps(result)


def test_compatible_requires_explicit_model(monkeypatch):
    monkeypatch.setenv("SOUNDSKETCH_PLANNER_API_KEY", "synthetic-key")
    assert not adapters.planner_available()
    with pytest.raises(adapters.AdapterError, match="模型名"):
        adapters.planner_config()
    monkeypatch.setenv("SOUNDSKETCH_PLANNER_MODEL", "org/model:version")
    assert adapters.planner_available()
    assert adapters.planner_config()["thinking"] == "provider_default"


def test_preset_overrides_and_public_configuration_do_not_read_keys(monkeypatch):
    class PublicOnlyEnvironment(dict):
        def get(self, key, default=None):
            if key.endswith("API_KEY"):
                pytest.fail("public config must not read a key")
            return super().get(key, default)
    environment = PublicOnlyEnvironment({
        "SOUNDSKETCH_PLANNER_PROVIDER": "mimo",
        "SOUNDSKETCH_PLANNER_MODEL": "org/model:custom",
        "SOUNDSKETCH_PLANNER_BASE_URL": "http://127.0.0.1:8800/v1/",
    })
    monkeypatch.setattr(adapters.os, "environ", environment)
    config = adapters.planner_config()
    assert config["model"] == "org/model:custom"
    assert config["base_url"] == "http://127.0.0.1:8800/v1"
    assert set(config) == {"provider", "model", "base_url", "max_output_tokens", "thinking"}


@pytest.mark.parametrize("field,value", [
    ("PROVIDER", "UNKNOWN_SECRET"),
    ("MODEL", "bad model SECRET"),
    ("MODEL", "x" * 151),
    ("BASE_URL", "https://user:SECRET@example.com"),
    ("BASE_URL", "https://example.com?key=SECRET"),
    ("BASE_URL", "https://example.com#SECRET"),
    ("BASE_URL", "https://example.com:SECRET"),
    ("BASE_URL", "https://[SECRET"),
    ("BASE_URL", "https://exam\nple.com"),
])
def test_invalid_configuration_is_unavailable_and_sanitized(monkeypatch, field, value):
    monkeypatch.setenv("SOUNDSKETCH_PLANNER_PROVIDER", "mimo")
    monkeypatch.setenv("SOUNDSKETCH_PLANNER_API_KEY", "key-SECRET")
    monkeypatch.setenv("SOUNDSKETCH_PLANNER_" + field, value)
    assert adapters.planner_available() is False
    with pytest.raises(adapters.AdapterError) as error:
        adapters.planner_config()
    assert "SECRET" not in str(error.value)


def test_only_unified_planner_key_is_accepted(monkeypatch):
    monkeypatch.setenv("SOUNDSKETCH_PLANNER_PROVIDER", "mimo")
    monkeypatch.setenv("MIMO_API_KEY", "other-service-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "other-service-key")
    assert not adapters.planner_available()
    monkeypatch.setattr(adapters, "_post_json", lambda *args: pytest.fail("missing key must not send"))
    with pytest.raises(adapters.AdapterError, match="密钥"):
        adapters.plan_parameters({})


def good_planner_response():
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
        "kind": "hum", "patch": {"frequency_hz": 80}, "clarification": None})}}]}


@pytest.mark.parametrize("reason", ["length", "content_filter", "tool_calls", None, "unexpected"])
def test_incomplete_or_nonstop_completion_never_accepted(monkeypatch, reason):
    monkeypatch.setenv("SOUNDSKETCH_PLANNER_PROVIDER", "mimo")
    monkeypatch.setenv("SOUNDSKETCH_PLANNER_API_KEY", "synthetic-key")
    response = good_planner_response()
    response["choices"][0]["finish_reason"] = reason
    monkeypatch.setattr(adapters, "_post_json", lambda *args: response)
    with pytest.raises(adapters.AdapterError, match="未采用"):
        adapters.plan_parameters({})


def test_refusal_even_with_valid_json_is_rejected(monkeypatch):
    monkeypatch.setenv("SOUNDSKETCH_PLANNER_PROVIDER", "deepseek")
    monkeypatch.setenv("SOUNDSKETCH_PLANNER_API_KEY", "synthetic-key")
    response = good_planner_response()
    response["choices"][0]["message"]["refusal"] = "provider content must not leak"
    monkeypatch.setattr(adapters, "_post_json", lambda *args: response)
    with pytest.raises(adapters.AdapterError) as error:
        adapters.plan_parameters({})
    assert "provider content" not in str(error.value)


@pytest.mark.parametrize("response", [None, [], True, {"choices": []},
    {"choices": [{"finish_reason": "stop", "message": None}]},
    {"choices": [{"finish_reason": "stop", "message": {"content": None}}]},
    {"choices": [{"finish_reason": "stop", "message": {"content": "null"}}]},
])
def test_invalid_response_shapes_are_safe(monkeypatch, response):
    monkeypatch.setenv("SOUNDSKETCH_PLANNER_PROVIDER", "mimo")
    monkeypatch.setenv("SOUNDSKETCH_PLANNER_API_KEY", "synthetic-key")
    monkeypatch.setattr(adapters, "_post_json", lambda *args: response)
    with pytest.raises(adapters.AdapterError):
        adapters.plan_parameters({})


def test_metadata_is_whitelisted_and_uses_requested_model(monkeypatch):
    monkeypatch.setenv("SOUNDSKETCH_PLANNER_PROVIDER", "deepseek")
    monkeypatch.setenv("SOUNDSKETCH_PLANNER_API_KEY", "synthetic-key")
    response = good_planner_response()
    response.update(model="provider-value-SECRET", secret="SECRET", usage={
        "prompt_tokens": 42, "completion_tokens": 19, "total_tokens": 61,
        "raw_request": "SECRET", "prompt_tokens_details": {"secret": "SECRET"}})
    response["choices"][0]["message"]["reasoning_content"] = "SECRET"
    clock_values = iter([100.0, 100.125])
    monkeypatch.setattr(adapters.time, "perf_counter", lambda: next(clock_values))
    monkeypatch.setattr(adapters, "_post_json", lambda *args: response)
    result = adapters.plan_parameters({})
    assert result["_meta"] == {"provider": "deepseek", "model": "deepseek-flash", "latency_ms": 125.0,
                               "input_tokens": 42, "output_tokens": 19, "total_tokens": 61}
    assert "SECRET" not in json.dumps(result)
    assert "synthetic-key" not in json.dumps(result)


@pytest.mark.parametrize("usage", [None, "SECRET", {"prompt_tokens": True, "completion_tokens": -1,
    "total_tokens": "SECRET"}, {"prompt_tokens": float("nan"), "completion_tokens": 1.5,
    "total_tokens": 10 ** 15}])
def test_malformed_usage_omitted_without_faking_measurements(monkeypatch, usage):
    monkeypatch.setenv("SOUNDSKETCH_PLANNER_PROVIDER", "mimo")
    monkeypatch.setenv("SOUNDSKETCH_PLANNER_API_KEY", "synthetic-key")
    response = good_planner_response()
    response["usage"] = usage
    monkeypatch.setattr(adapters, "_post_json", lambda *args: response)
    result = adapters.plan_parameters({})
    assert set(result["_meta"]) == {"provider", "model", "latency_ms"}


def test_incomplete_http_body_becomes_sanitized_adapter_error(monkeypatch):
    class IncompleteResponse:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def read(self, *args):
            raise http.client.IncompleteRead(b"SECRET partial body", 100)
    class Opener:
        def open(self, *args, **kwargs):
            return IncompleteResponse()
    monkeypatch.setattr(adapters.urllib.request, "build_opener", lambda *args: Opener())
    with pytest.raises(adapters.AdapterError) as error:
        adapters._post_json("https://example.invalid", {}, {})
    assert "SECRET" not in str(error.value)
