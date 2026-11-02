import json

from soundsketch import adapters, check_llm
from soundsketch.parameters import PARAMETER_BOUNDS, default_params
from soundsketch.planner import model_contract, plan_initial, plan_revision
from soundsketch.web import create_app


def mock_mimo(monkeypatch):
    monkeypatch.setenv("SOUNDSKETCH_PLANNER_PROVIDER", "mimo")
    monkeypatch.setenv("SOUNDSKETCH_PLANNER_API_KEY", "test-only-not-a-real-key")
    monkeypatch.delenv("SOUNDSKETCH_PLANNER_BASE_URL", raising=False)
    monkeypatch.delenv("SOUNDSKETCH_PLANNER_MODEL", raising=False)


def fake_response(payload):
    context = json.loads(payload["messages"][1]["content"])
    value = 80 if context["task"] == "initial" else context["current_params"]["frequency_hz"] * .8
    return {"choices": [{"finish_reason": "stop", "message": {"content": json.dumps({
        "kind": "hum", "patch": {"frequency_hz": value}, "clarification": None})}}],
        "usage": {"prompt_tokens": 123, "completion_tokens": 45, "total_tokens": 168}}


def test_contract_matches_validator_and_is_sent_to_model(monkeypatch):
    bounds = model_contract()["editable_bounds"]
    assert "seed" not in bounds
    for field, rule in bounds.items():
        assert (rule["minimum"], rule["maximum"]) == PARAMETER_BOUNDS[field]
    contexts = []
    def fake_plan(context):
        contexts.append(context)
        return {"kind": "hum", "patch": {"frequency_hz": 70}, "clarification": None}
    monkeypatch.setattr(adapters, "plan_parameters", fake_plan)
    assert plan_initial("嗡鸣", use_model=True)["mode"] == "model"
    assert plan_revision("更低沉", default_params(), use_model=True)["mode"] == "model"
    assert all(c["parameter_contract"] == model_contract() for c in contexts)
    assert set(contexts[0]["presets"]) == {"hum", "whistle", "knock"}


def test_mimo_model_metadata_persists_without_key_and_restores(tmp_path, monkeypatch):
    mock_mimo(monkeypatch)
    calls = []
    def fake_post(url, payload, headers):
        calls.append(payload)
        return fake_response(payload)
    monkeypatch.setattr(adapters, "_post_json", fake_post)
    app = create_app(tmp_path / "sessions")
    client = app.test_client()
    headers = {"X-SoundSketch": "1"}
    status = client.get("/api/status").json
    assert status["planner"]["model"] == "mimo-v2.6-flash"
    assert status["planner_available"]
    response = client.post("/api/sessions", json={"description": "嗡鸣", "kind": "hum", "use_model": True}, headers=headers)
    assert response.status_code == 201, response.json
    session = response.json
    assert session["model_call"]["total_tokens"] == 168
    prefix = f"/api/sessions/{session['id']}"
    client.post(prefix + "/select", json={"candidate_id": session["versions"][0]["candidates"][0]["id"]}, headers=headers)
    response = client.post(prefix + "/revise", json={"feedback": "更低沉", "use_model": True}, headers=headers)
    assert response.json["status"] == "model"
    version = response.json["session"]["versions"][-1]
    assert version["changed_fields"] == ["frequency_hz"]
    assert version["model_call"]["provider"] == "mimo"
    assert len(calls) == 2
    response = client.post(prefix + "/undo", json={}, headers=headers)
    assert response.json["model_call"] == session["model_call"]
    assert "test-only-not-a-real-key" not in json.dumps(response.json)
    saved = (tmp_path / "sessions" / session["id"] / "session.json").read_text(encoding="utf-8")
    assert "test-only-not-a-real-key" not in saved


def test_check_cli_is_offline_unless_explicitly_requested(monkeypatch, capsys):
    mock_mimo(monkeypatch)
    monkeypatch.setattr("sys.argv", ["check_llm"])
    def no_network(*args, **kwargs):
        raise AssertionError("offline configuration check must never call the API")
    monkeypatch.setattr(adapters, "_post_json", no_network)
    assert check_llm.main() == 0
    output = capsys.readouterr().out
    assert "test-only-not-a-real-key" not in output
    assert "mimo-v2.6-flash" in output


def test_check_cli_live_only_sends_builtin_examples(monkeypatch, capsys):
    mock_mimo(monkeypatch)
    monkeypatch.setattr("sys.argv", ["check_llm", "--live"])
    calls = []
    def fake_post(url, payload, headers):
        calls.append(payload)
        return fake_response(payload)
    monkeypatch.setattr(adapters, "_post_json", fake_post)
    assert check_llm.main() == 0
    assert len(calls) == 2
    assert "test-only-not-a-real-key" not in capsys.readouterr().out
