"""Independent acceptance checks for persistence, provenance and consent."""
import hashlib
import io
import json
import zipfile

import numpy as np
import pytest
from scipy.io import wavfile

import soundsketch.session as session_module
from soundsketch.session import SessionStore
from soundsketch.web import create_app
from soundsketch import adapters


def _reference_bytes(value=0):
    buffer = io.BytesIO()
    wavfile.write(buffer, 8000, np.full(8000, value, dtype=np.int16))
    return buffer.getvalue()


@pytest.fixture
def store(tmp_path, monkeypatch):
    def never_call(*args, **kwargs):
        raise AssertionError("本地操作不应发起外部请求")
    monkeypatch.setattr(adapters, "_post_json", never_call)
    return SessionStore(tmp_path / "sessions")


@pytest.mark.parametrize("kind,field,value", [
    ("hum", "frequency_hz", 25), ("hum", "frequency_hz", 6000),
    ("whistle", "frequency_hz", 25), ("whistle", "frequency_hz", 6000),
    ("knock", "pulse_interval_s", 0.1), ("knock", "pulse_interval_s", 8),
])
def test_boundary_parameters_still_offer_three_distinct_candidates(store, monkeypatch, kind, field, value):
    monkeypatch.setattr(session_module, "plan_initial", lambda *a, **k: {
        "kind": kind, "patch": {field: value}, "mode": "local", "notice": "test"})
    session = store.create("测试听感", kind=kind)
    candidates = store.current(session)["candidates"]
    assert len(candidates) == 3
    assert candidates[0]["params"][field] == value
    for candidate in candidates[1:]:
        changed = [key for key, value in candidate["params"].items()
                   if value != candidates[0]["params"][key]]
        assert len(changed) == 1
    assert len({c["sha256"] for c in candidates}) == 3


@pytest.mark.parametrize("kind,patch", [
    ("hum", {"noise_mix": 1.0}),
    ("whistle", {"noise_mix": 1.0}),
    ("knock", {"noise_mix": 1.0, "pulse_interval_s": 8}),
])
def test_candidate_contrasts_remain_audible_for_pure_noise(store, monkeypatch, kind, patch):
    monkeypatch.setattr(session_module, "plan_initial", lambda *a, **k: {
        "kind": kind, "patch": patch, "mode": "local", "notice": "test"})
    session = store.create("测试听感", kind=kind)
    candidates = store.current(session)["candidates"]
    assert 2 <= len(candidates) <= 3
    assert len({c["sha256"] for c in candidates}) == len(candidates)
    for candidate in candidates[1:]:
        assert sum(value != candidates[0]["params"][key]
                   for key, value in candidate["params"].items()) == 1


def test_initial_silence_does_not_claim_distinct_candidates(store, monkeypatch):
    monkeypatch.setattr(session_module, "plan_initial", lambda *a, **k: {
        "kind": "hum", "patch": {"gain": 0}, "mode": "local", "notice": "test"})
    with pytest.raises(ValueError):
        store.create("测试听感", kind="hum")


@pytest.mark.parametrize("patch", [
    {"gain": 1e-8},
    {"noise_mix": 1.0, "band_low_hz": 1000.01, "band_high_hz": 1000.02},
    {"noise_mix": 1.0, "band_low_hz": 11990.0, "band_high_hz": 11999.0},
])
def test_degenerate_audio_is_rejected_without_claiming_distinct_candidates(store, monkeypatch, patch):
    monkeypatch.setattr(session_module, "plan_initial", lambda *a, **k: {
        "kind": "hum", "patch": patch, "mode": "local", "notice": "test"})
    with pytest.raises(ValueError):
        store.create("测试听感", kind="hum")
    assert store.list_sessions() == []


def test_edit_undo_branch_confirmation_and_export(store):
    session = store.create("持续低沉的嗡鸣", kind="hum")
    initial = store.current(session)
    candidate = initial["candidates"][0]
    store.select(session, candidate["id"])
    with pytest.raises(ValueError, match="确认"):
        store.export(session)
    store.confirm(session, "低沉、持续，缓慢起伏")
    archive = zipfile.ZipFile(store.export(session))
    assert {"confirmed.wav", "parameters.json", "description.txt", "session.json"} <= set(archive.namelist())
    assert hashlib.sha256(archive.read("confirmed.wav")).hexdigest() == candidate["sha256"]
    assert json.loads(archive.read("parameters.json"))["params"] == candidate["params"]

    revised = store.revise(session, feedback="更低沉")["session"]
    current = store.current(revised)
    assert current["changed_fields"] == ["frequency_hz"]
    assert store.selected(revised)["params"]["frequency_hz"] < candidate["params"]["frequency_hz"]
    assert revised["confirmed_candidate_id"] is None
    changed_version_id = current["id"]
    store.restore(revised)
    assert store.current(revised)["id"] == initial["id"]
    assert store.selected(revised)["sha256"] == candidate["sha256"]
    store.revise(revised, patch={"gain": 0.1})
    assert len(revised["versions"]) == 3
    assert store.current(revised)["parent_id"] == initial["id"]
    store.restore(revised, changed_version_id)
    assert store.current(revised)["id"] == changed_version_id
    assert store.load(revised["id"])["current_version_id"] == changed_version_id


def test_unknown_feedback_does_not_create_synthetic_evidence(store):
    session = store.create("嗡鸣", kind="hum")
    store.select(session, store.current(session)["candidates"][0]["id"])
    before_id = session["current_version_id"]
    result = store.revise(session, feedback="像一种遥远但又近的金属感觉")
    assert result["clarification"]
    assert session["current_version_id"] == before_id
    assert len(session["versions"]) == 1
    assert not session["references"]


def test_locks_reject_changes_and_preserve_history(store):
    session = store.create("嗡鸣", kind="hum")
    store.select(session, store.current(session)["candidates"][0]["id"])
    before = store.selected(session)["params"]
    with pytest.raises(ValueError, match="锁定"):
        store.revise(session, patch={"frequency_hz": 90}, locked_fields=["frequency_hz"])
    assert len(session["versions"]) == 1
    assert store.selected(session)["params"] == before


def test_reference_bytes_preserved_same_names_do_not_overwrite(store):
    session = store.create("敲击声", kind="knock")
    first_bytes, second_bytes = _reference_bytes(0), _reference_bytes(15)
    store.add_reference(session, "same.wav", first_bytes)
    first = session["references"][0]
    path = store.directory(session["id"]) / "references" / f"{first['id']}.wav"
    store.add_reference(session, "same.wav", second_bytes)
    second = session["references"][1]
    assert first["id"] != second["id"]
    assert path.read_bytes() == first_bytes
    assert first["sha256"] == hashlib.sha256(first_bytes).hexdigest()
    assert first["category"] == "observation"
    assert first["origin"] == "user_supplied_unverified"
    assert first["external_analyses"] == []


def test_synthetic_reupload_blocked_across_sessions(store):
    first = store.create("尖啸", kind="whistle")
    candidate = store.current(first)["candidates"][0]
    generated = (store.directory(first["id"]) / "generated" / f"{candidate['id']}.wav").read_bytes()
    second = store.create("间歇敲击", kind="knock")
    with pytest.raises(ValueError, match="合成示意"):
        store.add_reference(second, "alleged_recording.wav", generated)
    assert not second["references"]


def test_invalid_reference_is_not_persisted(store):
    session = store.create("嗡鸣", kind="hum")
    with pytest.raises(ValueError):
        store.add_reference(session, "invalid.wav", b"this is not wav")
    assert not session["references"]
    assert not list((store.directory(session["id"]) / "references").glob("*.wav"))


def test_api_requires_per_call_audio_consent(tmp_path, monkeypatch):
    app = create_app(tmp_path)
    client = app.test_client()
    headers = {"X-SoundSketch": "1"}
    response = client.post("/api/sessions", json={"description": "嗡鸣", "kind": "hum"}, headers=headers)
    assert response.status_code == 201
    session_id = response.json["id"]
    response = client.post(f"/api/sessions/{session_id}/reference",
                           data={"file": (io.BytesIO(_reference_bytes()), "original.wav")}, headers=headers)
    assert response.status_code == 200
    reference_id = response.json["references"][0]["id"]
    calls = []
    monkeypatch.setattr(adapters, "audio_available", lambda: True)
    monkeypatch.setattr(adapters, "understand_audio", lambda path, consent=False: calls.append(consent) or {
        "description": "测试接口观察", "category": "observation", "measurement": False})
    for consent in (None, False, 1, "true"):
        response = client.post(f"/api/sessions/{session_id}/understand",
                               json={"reference_id": reference_id, "consent": consent}, headers=headers)
        assert response.status_code == 400
    assert calls == []
    response = client.post(f"/api/sessions/{session_id}/understand",
                           json={"reference_id": reference_id, "consent": True}, headers=headers)
    assert response.status_code == 200 and calls == [True]
    response = client.post(f"/api/sessions/{session_id}/understand",
                           json={"reference_id": reference_id, "consent": False}, headers=headers)
    assert response.status_code == 400 and calls == [True]


def test_api_rejects_cross_site_mutation_and_host_rebinding(tmp_path):
    client = create_app(tmp_path).test_client()
    assert client.get("/api/status", headers={"Host": "attacker.example"}).status_code == 403
    assert client.post("/api/sessions", json={"description": "嗡鸣"}).status_code == 403
    assert client.post("/api/sessions", json={"description": "嗡鸣"},
                       headers={"X-SoundSketch": "1", "Origin": "https://attacker.example"}).status_code == 403
