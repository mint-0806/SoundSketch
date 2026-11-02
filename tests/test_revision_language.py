"""Language constraints are verified independently of optional provider calls."""
import pytest

from soundsketch.parameters import apply_patch, default_params
from soundsketch.planner import plan_revision


@pytest.mark.parametrize("text", [
    "更低沉，音量不变", "请更低沉，但保持音量不变", "更低沉，并且不要改变音量",
    "保留音量，更低沉", "更低沉。音量不要变。", "更低沉，音量也不变",
])
def test_preservation_is_a_constraint_not_a_change(text):
    before = default_params()
    plan = plan_revision(text, before)
    assert not plan["clarification"]
    assert plan["patch"] == {"frequency_hz": before.frequency_hz * .8}
    assert plan["preserved_fields"] == ["gain"]
    assert apply_patch(before, plan["patch"]).gain == before.gain


def test_keep_everything_else_including_seed_and_duration():
    before = default_params()
    plan = plan_revision("起伏慢一点，其余不变", before)
    assert set(plan["patch"]) == {"modulation_hz"}
    assert set(plan["preserved_fields"]) == set(before.to_dict()) - {"modulation_hz"}
    after = apply_patch(before, plan["patch"]).to_dict()
    assert [f for f, value in before.to_dict().items() if after[f] != value] == ["modulation_hz"]


@pytest.mark.parametrize("text", ["音高低一点，起伏慢一点", "音高低一点并且起伏慢一点", "音高低一点，同时起伏慢一点，其余参数不变", "音高低一点，同时起伏慢一点，保持其他参数不变"])
def test_explicit_multiple_changes_form_one_patch(text):
    before = default_params()
    plan = plan_revision(text, before)
    assert not plan["clarification"]
    assert plan["patch"] == {"frequency_hz": 88, "modulation_hz": .7}
    after = apply_patch(before, plan["patch"])
    assert after.seed == before.seed
    assert after.gain == before.gain


@pytest.mark.parametrize("text", [
    "更低沉，音高不变", "起伏慢一点，起伏不变", "更低沉，更高一点",
    "更低沉，同时像窗外的那个", "更低沉，但不要太吓人", "不要更低沉",
    "更低沉还是音量小一点", "更低沉，音色不变", "其余不变", "保持音量不变",
])
def test_ambiguity_conflicts_and_unmapped_remainders_never_partially_apply(text, monkeypatch):
    def no_call(_):
        raise AssertionError("Known ambiguities should be clarified before an API call")
    monkeypatch.setattr("soundsketch.adapters.plan_parameters", no_call)
    for use_model in (False, True):
        plan = plan_revision(text, default_params(), use_model=use_model)
        assert plan["patch"] == {}
        assert plan["clarification"]


def test_one_locked_feature_blocks_entire_compound_change():
    plan = plan_revision("更低沉，起伏慢一点", default_params(), locked_fields=["modulation_hz"])
    assert plan["patch"] == {}
    assert plan["clarification"]


def test_generator_locks_remain_effective():
    plan = plan_revision("更低沉", default_params(), locked_fields=iter(["frequency_hz"]))
    assert plan["patch"] == {}
    assert plan["clarification"]


def model_patch(monkeypatch, patch, capture=None):
    def fake(context):
        if capture is not None:
            capture.append(context)
        return {"kind": "hum", "patch": patch, "clarification": None}
    monkeypatch.setattr("soundsketch.adapters.plan_parameters", fake)


def test_model_receives_change_and_preservation_separately(monkeypatch):
    contexts = []
    model_patch(monkeypatch, {"frequency_hz": 77}, contexts)
    plan = plan_revision("更低沉，音量不变", default_params(), use_model=True)
    assert plan["mode"] == "model"
    assert plan["patch"] == {"frequency_hz": 77}
    assert contexts[0]["allowed_fields"] == ["frequency_hz"]
    assert contexts[0]["required_fields"] == ["frequency_hz"]
    assert contexts[0]["preserved_fields"] == ["gain"]
    assert "gain" in contexts[0]["locked_fields"]


@pytest.mark.parametrize("patch", [
    {"frequency_hz": 75, "gain": .1},
    {"frequency_hz": 160},
    {"frequency_hz": 110},
])
def test_model_wrong_direction_or_preserved_change_falls_back(monkeypatch, patch):
    model_patch(monkeypatch, patch)
    plan = plan_revision("更低沉，音量不变", default_params(), use_model=True)
    assert plan["mode"] == "fallback"
    assert plan["patch"] == {"frequency_hz": 88}
    assert plan["preserved_fields"] == ["gain"]


@pytest.mark.parametrize("patch", [
    {"frequency_hz": 75},
    {"frequency_hz": 75, "modulation_hz": 1},
    {"frequency_hz": 75, "modulation_hz": 1.5},
    {"frequency_hz": 75, "modulation_hz": .5, "noise_mix": .2},
])
def test_model_cannot_omit_or_reverse_any_explicit_change(monkeypatch, patch):
    model_patch(monkeypatch, patch)
    plan = plan_revision("更低沉，起伏慢一点", default_params(), use_model=True)
    assert plan["mode"] == "fallback"
    assert plan["patch"] == {"frequency_hz": 88, "modulation_hz": .7}


def test_valid_model_compound_change(monkeypatch):
    model_patch(monkeypatch, {"frequency_hz": 75, "modulation_hz": .5})
    plan = plan_revision("更低沉，起伏慢一点，音量不变", default_params(), use_model=True)
    assert plan["mode"] == "model"
    assert plan["patch"] == {"frequency_hz": 75, "modulation_hz": .5}


def test_unfamiliar_but_named_clause_requires_full_model_patch(monkeypatch):
    model_patch(monkeypatch, {"frequency_hz": 75})
    plan = plan_revision("更低沉，音量降低百分之十", default_params(), use_model=True)
    assert plan["mode"] == "fallback"
    assert plan["patch"] == {}
    assert plan["clarification"]
    model_patch(monkeypatch, {"frequency_hz": 75, "gain": .18})
    plan = plan_revision("更低沉，音量降低百分之十", default_params(), use_model=True)
    assert plan["mode"] == "model"
    assert set(plan["patch"]) == {"frequency_hz", "gain"}


@pytest.mark.parametrize("kind,params_patch,phrase", [
    ("hum", {}, "尾音长一点"),
    ("whistle", {}, "敲击间隔更长"),
    ("hum", {"noise_mix": 1}, "更低沉"),
    ("hum", {"modulation_depth": 0}, "起伏慢一点"),
])
def test_inactive_component_does_not_silently_enable_other_controls(kind, params_patch, phrase):
    before = apply_patch(default_params(kind), params_patch)
    plan = plan_revision(phrase, before)
    assert plan["patch"] == {}
    assert plan["clarification"]


@pytest.mark.parametrize("kind,base_patch,feedback,patch", [
    ("whistle", {}, "谐波增加一些", {"harmonics": 4}),
    ("knock", {}, "谐波增加一些", {"harmonics": 4}),
    ("hum", {"noise_mix": 0}, "高截止降低一些", {"band_high_hz": 1000}),
    ("knock", {}, "起伏深度增加一些", {"modulation_depth": .5}),
])
def test_model_proposals_for_inactive_controls_need_clarification(monkeypatch, kind, base_patch, feedback, patch):
    model_patch(monkeypatch, patch)
    plan = plan_revision(feedback, apply_patch(default_params(kind), base_patch), use_model=True)
    assert plan["patch"] == {}
    assert plan["clarification"]


def test_no_implicit_boundary_clamping():
    plan = plan_revision("更低沉，音量不变", apply_patch(default_params(), {"frequency_hz": 25}))
    assert plan["patch"] == {}
    assert plan["clarification"]
