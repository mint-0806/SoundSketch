"""Acceptance checks for inspectable, single-use language-to-DSP plans."""
import json

import pytest

from soundsketch import adapters
from soundsketch.web import create_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("预览验收不得调用外部服务")

    monkeypatch.setattr(adapters, "_post_json", no_network)
    return create_app(tmp_path / "sessions")


@pytest.fixture
def client(app):
    return app.test_client()


def post(client, path, payload):
    return client.post(path, json=payload, headers={"X-SoundSketch": "1"})


def current(session):
    return next(v for v in session["versions"] if v["id"] == session["current_version_id"])


def create_selected(client):
    response = post(client, "/api/sessions", {"description": "持续的嗡鸣", "kind": "hum"})
    assert response.status_code == 201, response.json
    session = response.json
    prefix = f"/api/sessions/{session['id']}"
    selected = current(session)["candidates"][0]
    response = post(client, prefix + "/select", {"candidate_id": selected["id"]})
    assert response.status_code == 200, response.json
    return response.json, prefix, selected


def initial_plan(client, **extra):
    payload = {"description": "持续低沉的嗡鸣", "kind": "hum"}
    payload.update(extra)
    response = post(client, "/api/plans", payload)
    assert response.status_code in (200, 201), response.json
    assert response.json["id"]
    assert not response.json.get("clarification")
    return response.json


def revision_plan(client, prefix, **extra):
    payload = {"feedback": "更低沉"}
    payload.update(extra)
    response = post(client, prefix + "/preview", payload)
    assert response.status_code in (200, 201), response.json
    assert response.json["id"]
    assert not response.json.get("clarification")
    return response.json


def test_initial_preview_is_not_synthesis_and_apply_retains_plan(client, app):
    plan = initial_plan(client)
    root = app.extensions["soundsketch_store"].root
    assert plan["task"] == "initial"
    assert plan["params"]["frequency_hz"] == 80
    assert plan["patch"]["frequency_hz"] == 80
    assert plan["changes"] == []
    assert not list(root.rglob("*.wav"))
    assert client.get("/api/sessions").json["sessions"] == []

    response = post(client, f"/api/plans/{plan['id']}/apply", {})
    assert response.status_code in (200, 201), response.json
    session = response.json["session"]
    version = current(session)
    assert 2 <= len(version["candidates"]) <= 3
    assert version["candidates"][0]["params"] == plan["params"]
    assert version["plan"]["params"] == plan["params"]
    assert version["plan"]["patch"] == plan["patch"]
    assert version["plan"]["task"] == "initial"
    assert len(list(root.rglob("*.wav"))) == len(version["candidates"])
    assert all(c["provenance"]["category"] == "synthetic" for c in version["candidates"])


def test_revision_preview_leaves_saved_state_and_audio_untouched(client, app):
    session, prefix, previous = create_selected(client)
    root = app.extensions["soundsketch_store"].root
    session_path = root / session["id"] / "session.json"
    saved_before = session_path.read_bytes()
    files_before = {p: p.read_bytes() for p in root.rglob("*.wav")}
    plan = revision_plan(client, prefix)
    assert plan["task"] == "revision"
    assert plan["before_params"] == previous["params"]
    assert plan["changes"] == [{"field": "frequency_hz", "before": 110.0, "after": 88.0}]
    assert plan["params"]["frequency_hz"] == 88
    assert session_path.read_bytes() == saved_before
    assert {p: p.read_bytes() for p in root.rglob("*.wav")} == files_before

    result = post(client, f"/api/plans/{plan['id']}/apply", {}).json
    revised = result["session"]
    assert not result.get("clarification")
    version = current(revised)
    assert version["parent_id"] == session["current_version_id"]
    assert version["changed_fields"] == ["frequency_hz"]
    assert version["candidates"][0]["params"] == plan["params"]
    assert version["plan"]["changes"] == plan["changes"]
    for field, value in previous["params"].items():
        if field != "frequency_hz":
            assert version["candidates"][0]["params"][field] == value
    restored = post(client, prefix + "/undo", {}).json
    assert restored["current_version_id"] == session["current_version_id"]
    assert len(restored["versions"]) == 2


@pytest.mark.parametrize("task", ["initial", "revision"])
def test_model_called_once_during_preview_never_again_during_apply(client, monkeypatch, task):
    calls = []

    def fake_model(context):
        calls.append(context)
        return {"kind": "hum", "patch": {"frequency_hz": 70}, "clarification": None,
                "_meta": {"provider": "test-model", "model": "mock-only", "latency_ms": 2}}

    monkeypatch.setattr(adapters, "plan_parameters", fake_model)
    if task == "initial":
        plan = initial_plan(client, use_model=True)
    else:
        _, prefix, _ = create_selected(client)
        plan = revision_plan(client, prefix, use_model=True)
    assert len(calls) == 1
    assert plan["mode"] == "model"
    assert plan["model_call"]["model"] == "mock-only"
    applied = post(client, f"/api/plans/{plan['id']}/apply", {})
    assert applied.status_code in (200, 201), applied.json
    assert len(calls) == 1
    assert current(applied.json["session"])["candidates"][0]["params"]["frequency_hz"] == 70
    assert current(applied.json["session"])["plan"]["mode"] == "model"


@pytest.mark.parametrize("payload", [
    {"patch": {"gain": 0.5}}, {"locked_fields": []}, {"use_model": True},
])
def test_apply_cannot_replace_previously_validated_plan(client, payload):
    plan = initial_plan(client)
    path = f"/api/plans/{plan['id']}/apply"
    response = post(client, path, payload)
    assert response.status_code == 400, response.json
    assert not client.get("/api/sessions").json["sessions"]
    # A rejected tampering request does not consume the original valid plan.
    response = post(client, path, {})
    assert response.status_code in (200, 201), response.json
    assert current(response.json["session"])["candidates"][0]["params"] == plan["params"]


def test_applied_plan_is_single_use(client):
    plan = initial_plan(client)
    path = f"/api/plans/{plan['id']}/apply"
    assert post(client, path, {}).status_code in (200, 201)
    assert post(client, path, {}).status_code in (400, 404, 409)
    assert len(client.get("/api/sessions").json["sessions"]) == 1


@pytest.mark.parametrize("operation", ["select", "revise", "confirm", "restore"])
def test_stale_revision_plan_cannot_overwrite_later_session_actions(client, operation):
    session, prefix, previous = create_selected(client)
    if operation == "restore":
        updated = post(client, prefix + "/revise", {"patch": {"gain": 0.1}})
        assert updated.status_code == 200
    plan = revision_plan(client, prefix)
    if operation == "select":
        other = current(session)["candidates"][1]
        changed = post(client, prefix + "/select", {"candidate_id": other["id"]})
    elif operation == "revise":
        changed = post(client, prefix + "/revise", {"patch": {"gain": 0.1}})
    elif operation == "confirm":
        changed = post(client, prefix + "/confirm", {"description": "确认此前听感"})
    else:
        changed = post(client, prefix + "/undo", {})
    assert changed.status_code == 200, changed.json
    expected = client.get(prefix).json
    response = post(client, f"/api/plans/{plan['id']}/apply", {})
    assert response.status_code in (400, 409), response.json
    assert client.get(prefix).json == expected


def test_initial_ambiguity_returns_question_without_plan_token_or_audio(client, app):
    response = post(client, "/api/plans", {"description": "有一点奇怪的声音"})
    assert response.status_code == 200, response.json
    assert response.json["clarification"]
    assert response.json.get("id") is None
    assert not list(app.extensions["soundsketch_store"].root.rglob("*.wav"))
    assert not client.get("/api/sessions").json["sessions"]


@pytest.mark.parametrize("payload", [
    {"feedback": "更像楼上那个东西"},
    {"feedback": "更低沉", "locked_fields": ["frequency_hz"]},
    {"patch": {"frequency_hz": 110.0}},
    {"patch": {"decay_s": 0.9}},
])
def test_revision_without_effect_or_reliable_mapping_needs_clarification(client, app, payload):
    session, prefix, _ = create_selected(client)
    root = app.extensions["soundsketch_store"].root
    saved = (root / session["id"] / "session.json").read_bytes()
    files = set(root.rglob("*.wav"))
    response = post(client, prefix + "/preview", payload)
    assert response.status_code == 200, response.json
    assert response.json["clarification"]
    assert response.json.get("id") is None
    assert (root / session["id"] / "session.json").read_bytes() == saved
    assert set(root.rglob("*.wav")) == files


@pytest.mark.parametrize("payload", [
    {"patch": {"frequency_hz": 80}, "locked_fields": ["frequency_hz"]},
    {"patch": {"gain": 0.8}},
    {"patch": {"invented_field": 10}},
    {"feedback": "更低沉", "locked_fields": "frequency_hz"},
])
def test_invalid_parameter_preview_does_not_mutate_session(client, payload):
    _, prefix, _ = create_selected(client)
    before = client.get(prefix).json
    response = post(client, prefix + "/preview", payload)
    assert response.status_code == 400, response.json
    assert client.get(prefix).json == before


def test_restart_discards_unapplied_plans_but_preserves_sessions(client, app):
    session, prefix, _ = create_selected(client)
    plan = revision_plan(client, prefix)
    reopened = create_app(app.extensions["soundsketch_store"].root).test_client()
    assert reopened.get(prefix).json == session
    assert post(reopened, f"/api/plans/{plan['id']}/apply", {}).status_code in (400, 404, 409)
    assert reopened.get(prefix).json == session


def test_plan_cache_is_bounded_without_creating_sessions(client):
    first = initial_plan(client)
    for _ in range(100):
        newest = initial_plan(client)
    assert post(client, f"/api/plans/{first['id']}/apply", {}).status_code in (400, 404, 409)
    assert not client.get("/api/sessions").json["sessions"]
    assert post(client, f"/api/plans/{newest['id']}/apply", {}).status_code in (200, 201)


def test_plan_endpoints_keep_local_write_protection(client):
    assert client.post("/api/plans", json={"description": "嗡鸣"}).status_code == 403
    response = client.post("/api/plans", json={"description": "嗡鸣"}, headers={
        "X-SoundSketch": "1", "Origin": "https://external.example"})
    assert response.status_code == 403
    assert post(client, "/api/plans", {"description": "嗡鸣", "unexpected": 1}).status_code == 400


def test_preview_model_failure_falls_back_explicitly_without_provider_error_leak(client, monkeypatch):
    def fail(context):
        raise adapters.AdapterError("SECRET-PROVIDER-DETAIL-MUST-NOT-LEAK")

    monkeypatch.setattr(adapters, "plan_parameters", fail)
    plan = initial_plan(client, use_model=True)
    assert plan["mode"] == "fallback"
    assert plan["notice"]
    assert "SECRET-PROVIDER" not in json.dumps(plan)
    response = post(client, f"/api/plans/{plan['id']}/apply", {})
    assert response.status_code in (200, 201), response.json
    assert response.json["status"] == "fallback"
    assert current(response.json["session"])["plan"]["mode"] == "fallback"


@pytest.mark.parametrize("elapsed,expired", [(1799.9, False), (1800.0, True)])
def test_unapplied_plan_expires_after_thirty_minutes(client, monkeypatch, elapsed, expired):
    clock = [100.0]
    monkeypatch.setattr("soundsketch.proposals.time.monotonic", lambda: clock[0])
    plan = initial_plan(client)
    clock[0] += elapsed
    response = post(client, f"/api/plans/{plan['id']}/apply", {})
    if expired:
        assert response.status_code in (400, 404, 409), response.json
        assert not client.get("/api/sessions").json["sessions"]
    else:
        assert response.status_code in (200, 201), response.json
        assert len(client.get("/api/sessions").json["sessions"]) == 1


def test_failed_synthesis_can_retry_valid_plan_without_second_model_call(client, monkeypatch):
    from soundsketch import session as session_module

    calls = []

    def fake_model(context):
        calls.append(context)
        return {"kind": "hum", "patch": {"frequency_hz": 70}, "clarification": None}

    monkeypatch.setattr(adapters, "plan_parameters", fake_model)
    plan = initial_plan(client, use_model=True)
    original_writer = session_module.write_wav

    def fail_write(*args, **kwargs):
        raise OSError("PRIVATE-DISK-DETAIL")

    monkeypatch.setattr(session_module, "write_wav", fail_write)
    path = f"/api/plans/{plan['id']}/apply"
    failed = post(client, path, {})
    assert failed.status_code == 500
    assert b"PRIVATE-DISK-DETAIL" not in failed.data
    assert not client.get("/api/sessions").json["sessions"]
    monkeypatch.setattr(session_module, "write_wav", original_writer)
    retried = post(client, path, {})
    assert retried.status_code in (200, 201), retried.json
    assert len(calls) == 1
    assert len(client.get("/api/sessions").json["sessions"]) == 1
