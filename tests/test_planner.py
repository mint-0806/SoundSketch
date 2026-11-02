import pytest

from soundsketch.parameters import default_params
from soundsketch.planner import plan_initial, plan_revision


@pytest.mark.parametrize("kind,phrase,field", [
    ("hum", "更低沉", "frequency_hz"),
    ("hum", "起伏慢一点", "modulation_hz"),
    ("knock", "敲击间隔更长", "pulse_interval_s"),
])
def test_single_feature_intent(kind, phrase, field):
    result = plan_revision(phrase, default_params(kind))
    assert set(result["patch"]) == {field}
    assert result["clarification"] is None


def test_unknown_intent_requires_clarification():
    result = plan_revision("更像隔壁那台设备的感觉", default_params("hum"))
    assert result["patch"] == {}
    assert result["clarification"]


def test_combined_extra_instruction_is_not_silently_dropped():
    result = plan_revision("更低沉，同时像窗外的那个", default_params("hum"))
    assert result["patch"] == {}
    assert result["clarification"]


def test_lock_blocks_change():
    result = plan_revision("更低沉", default_params("hum"), ["frequency_hz"])
    assert result["patch"] == {}
    assert result["clarification"]


def test_no_knock_in_hum():
    result = plan_revision("敲击间隔更长", default_params("hum"))
    assert result["patch"] == {}
    assert result["clarification"]


def test_unknown_initial_auto_requires_choice():
    result = plan_initial("说不清楚的声音")
    assert result["clarification"]
    assert result["kind"] is None
    assert plan_initial("说不清楚的声音", kind="hum")["kind"] == "hum"


def test_initial_keywords_without_source_inference():
    result = plan_initial("晚上持续低沉的嗡鸣")
    assert result["kind"] == "hum"
    assert result["patch"]["frequency_hz"] == 80
    assert result["mode"] == "local"


def test_missing_model_config_is_explicit_fallback(monkeypatch):
    monkeypatch.delenv("SOUNDSKETCH_PLANNER_API_KEY", raising=False)
    result = plan_initial("嗡嗡响", use_model=True)
    assert result["mode"] == "fallback"
    assert "降级" in result["notice"]
    result = plan_revision("更低沉", default_params("hum"), use_model=True)
    assert result["mode"] == "fallback"
    assert set(result["patch"]) == {"frequency_hz"}


def test_model_cannot_change_unrelated_fields(monkeypatch):
    monkeypatch.setattr("soundsketch.adapters.plan_parameters", lambda _: {
        "kind": "hum", "patch": {"frequency_hz": 75, "gain": .3}, "clarification": None})
    result = plan_revision("更低沉", default_params("hum"), use_model=True)
    assert result["mode"] == "fallback"
    assert set(result["patch"]) == {"frequency_hz"}


def test_model_result_validated(monkeypatch):
    monkeypatch.setattr("soundsketch.adapters.plan_parameters", lambda _: {
        "kind": "hum", "patch": {"frequency_hz": -20}, "clarification": None})
    result = plan_initial("嗡嗡响", use_model=True)
    assert result["mode"] == "fallback"
    assert result["patch"] == {}


def test_valid_model_result(monkeypatch):
    monkeypatch.setattr("soundsketch.adapters.plan_parameters", lambda _: {
        "kind": "whistle", "patch": {"frequency_hz": 2400}, "clarification": None})
    result = plan_initial("像细细的尖啸", use_model=True)
    assert result["mode"] == "model"
    assert result["patch"] == {"frequency_hz": 2400}


def test_explicit_kind_is_preserved(monkeypatch):
    monkeypatch.setattr("soundsketch.adapters.plan_parameters", lambda _: {
        "kind": "whistle", "patch": {}, "clarification": None})
    result = plan_initial("声音很难形容", kind="hum", use_model=True)
    assert result["kind"] == "hum"
    assert result["mode"] == "fallback"


@pytest.mark.parametrize("phrase,field", [("更低沉", "frequency_hz"), ("起伏慢一点", "modulation_hz")])
def test_model_cannot_reverse_requested_direction(monkeypatch, phrase, field):
    params = default_params("hum")
    monkeypatch.setattr("soundsketch.adapters.plan_parameters", lambda _: {
        "kind": "hum", "patch": {field: getattr(params, field) * 1.25}, "clarification": None})
    result = plan_revision(phrase, params, use_model=True)
    assert result["mode"] == "fallback"
    assert result["patch"][field] < getattr(params, field)


def test_model_can_map_unfamiliar_phrase_to_validated_single_feature(monkeypatch):
    monkeypatch.setattr("soundsketch.adapters.plan_parameters", lambda _: {
        "kind": "hum", "patch": {"frequency_hz": 75}, "clarification": None})
    result = plan_revision("再往低音区走一点", default_params("hum"), use_model=True)
    assert result["mode"] == "model"
    assert result["patch"] == {"frequency_hz": 75}


def test_model_unknown_phrase_must_not_change_multiple_features(monkeypatch):
    monkeypatch.setattr("soundsketch.adapters.plan_parameters", lambda _: {
        "kind": "hum", "patch": {"frequency_hz": 75, "gain": .1}, "clarification": None})
    result = plan_revision("像另外一种感觉", default_params("hum"), use_model=True)
    assert result["mode"] == "fallback"
    assert result["patch"] == {}
    assert result["clarification"]
