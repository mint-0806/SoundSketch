import hashlib
import io
import json
import wave
import zipfile

import numpy as np
import pytest

from soundsketch.parameters import SoundParams
from soundsketch.dsp import write_wav
from soundsketch.web import create_app


@pytest.fixture
def app(tmp_path, monkeypatch):
    for name in ("SOUNDSKETCH_PLANNER_API_KEY", "SOUNDSKETCH_PLANNER_MODEL", "GEMINI_API_KEY", "GEMINI_MODEL"):
        monkeypatch.delenv(name, raising=False)
    return create_app(tmp_path / "sessions")


@pytest.fixture
def client(app):
    return app.test_client()


def post(client, url, data):
    return client.post(url, json=data, headers={"X-SoundSketch": "1"})


def create(client, kind="hum"):
    response = post(client, "/api/sessions", {"description": "持续的嗡鸣", "kind": kind})
    assert response.status_code == 201, response.json
    return response.json


def current(session):
    return next(v for v in session["versions"] if v["id"] == session["current_version_id"])


def test_offline_complete_workflow_and_exact_export_reproduction(client, app, tmp_path):
    assert client.get("/").status_code == 200
    assert client.get("/api/status").json["planner_available"] is False
    s = create(client)
    prefix = f"/api/sessions/{s['id']}"
    v = current(s)
    assert len(v["candidates"]) == 3
    assert len({c["params"]["frequency_hz"] for c in v["candidates"]}) == 3
    first = v["candidates"][0]
    assert client.get(prefix + "/export").status_code == 400
    post(client, prefix + "/select", {"candidate_id": first["id"]})
    response = post(client, prefix + "/revise", {"feedback": "更低沉"})
    assert response.status_code == 200, response.json
    revised = response.json["session"]
    second = current(revised)["candidates"][0]
    assert current(revised)["changed_fields"] == ["frequency_hz"]
    for name, value in first["params"].items():
        if name != "frequency_hz":
            assert second["params"][name] == value
    restored = post(client, prefix + "/undo", {}).json
    assert restored["current_version_id"] == v["id"]
    assert len(restored["versions"]) == 2
    assert post(client, prefix + "/restore", {"version_id": revised["current_version_id"]}).status_code == 200
    assert post(client, prefix + "/confirm", {"description": "确认这一版更像我的听感"}).status_code == 200
    exported = client.get(prefix + "/export")
    assert exported.status_code == 200
    with zipfile.ZipFile(io.BytesIO(exported.data)) as archive:
        assert set(archive.namelist()) == {"confirmed.wav", "parameters.json", "description.txt", "session.json"}
        document = json.loads(archive.read("parameters.json"))
        write_wav(tmp_path / "reproduced.wav", SoundParams.from_dict(document["params"]))
        assert archive.read("confirmed.wav") == (tmp_path / "reproduced.wav").read_bytes()
        assert hashlib.sha256(archive.read("confirmed.wav")).hexdigest() == document["wav_sha256"]
    # A new app instance reopens the same history.
    reopened = create_app(app.extensions["soundsketch_store"].root).test_client()
    assert reopened.get(prefix).json["confirmed_candidate_id"] == second["id"]
    post(client, prefix + "/revise", {"patch": {"modulation_hz": 0.3}})
    assert client.get(prefix + "/export").status_code == 400


@pytest.mark.parametrize("kind", ["hum", "whistle", "knock"])
def test_all_presets_have_valid_different_wavs(client, kind):
    session = create(client, kind)
    hashes = []
    for c in current(session)["candidates"]:
        response = client.get(c["audio_url"])
        assert response.status_code == 200
        hashes.append(hashlib.sha256(response.data).hexdigest())
        with wave.open(io.BytesIO(response.data)) as wav:
            samples = np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2")
            assert len(samples) == 96000
            assert np.max(np.abs(samples.astype(float))) < 32767
        assert c["provenance"]["category"] == "synthetic"
    assert len(set(hashes)) == 3


def test_locks_ambiguity_and_invalid_input_do_not_create_versions(client):
    s = create(client)
    prefix = f"/api/sessions/{s['id']}"
    post(client, prefix + "/select", {"candidate_id": current(s)["candidates"][0]["id"]})
    assert post(client, prefix + "/revise", {"patch": {"frequency_hz": 80}, "locked_fields": ["frequency_hz"]}).status_code == 400
    response = post(client, prefix + "/revise", {"feedback": "更像隔壁那个机器"})
    assert response.json["clarification"]
    assert len(response.json["session"]["versions"]) == 1
    assert post(client, prefix + "/revise", {"patch": {"gain": 100}}).status_code == 400
    assert post(client, prefix + "/revise", {"patch": {"invented": 1}}).status_code == 400
    assert len(client.get(prefix).json["versions"]) == 1


def reference_bytes():
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        samples = (1000 * np.sin(2 * np.pi * 320 * np.arange(16000) / 16000)).astype("<i2")
        wav.writeframes(samples.tobytes())
    return output.getvalue()


def test_reference_kept_byte_exact_consent_and_no_synthetic_evidence(client, app, monkeypatch):
    s = create(client)
    prefix = f"/api/sessions/{s['id']}"
    generated = client.get(current(s)["candidates"][0]["audio_url"]).data
    rejected = client.post(prefix + "/reference", data={"file": (io.BytesIO(generated), "pretend.wav")},
                           headers={"X-SoundSketch": "1"})
    assert rejected.status_code == 400
    original = reference_bytes()
    response = client.post(prefix + "/reference", data={"file": (io.BytesIO(original), "参考.wav")},
                           headers={"X-SoundSketch": "1"})
    assert response.status_code == 200, response.json
    ref = response.json["references"][0]
    assert client.get(ref["audio_url"]).data == original
    assert ref["analysis"]["metrics"]["dominant_frequency_hz"] == pytest.approx(320, abs=5)
    calls = []
    monkeypatch.setattr("soundsketch.adapters.audio_available", lambda: True)
    monkeypatch.setattr("soundsketch.adapters.understand_audio", lambda *a, **k: calls.append(a) or {"description": "听见持续音"})
    assert post(client, prefix + "/understand", {"reference_id": ref["id"]}).status_code == 400
    assert calls == []
    assert post(client, prefix + "/understand", {"reference_id": ref["id"], "consent": True}).status_code == 200
    assert len(calls) == 1
    assert client.get(ref["audio_url"]).data == original
    # Export never bundles original recording bytes.
    post(client, prefix + "/select", {"candidate_id": current(s)["candidates"][0]["id"]})
    post(client, prefix + "/confirm", {})
    with zipfile.ZipFile(io.BytesIO(client.get(prefix + "/export").data)) as archive:
        assert not any("references/" in name for name in archive.namelist())


def test_external_audio_failure_explicit_and_original_preserved(client, monkeypatch):
    s = create(client)
    prefix = f"/api/sessions/{s['id']}"
    response = client.post(prefix + "/reference", data={"file": (io.BytesIO(reference_bytes()), "r.wav")}, headers={"X-SoundSketch": "1"})
    ref = response.json["references"][0]
    monkeypatch.setattr("soundsketch.adapters.audio_available", lambda: True)
    def fail(*args, **kwargs):
        raise RuntimeError("secret-key-must-not-leak")
    monkeypatch.setattr("soundsketch.adapters.understand_audio", fail)
    response = post(client, prefix + "/understand", {"reference_id": ref["id"], "consent": True})
    assert response.status_code == 502
    assert b"secret-key" not in response.data
    assert response.json["session"]["references"][0]["external_analyses"] == []


def test_request_boundaries(client):
    assert client.post("/api/sessions", json={"description": "hum"}).status_code == 403
    assert client.get("/api/status", headers={"Host": "attacker.example"}).status_code == 403
    assert client.post("/api/sessions", json={"description": "hum"}, headers={"X-SoundSketch": "1", "Origin": "https://evil.example"}).status_code == 403
    assert post(client, "/api/sessions", {"description": ""}).status_code == 400
    assert post(client, "/api/sessions", {"description": "嗡鸣", "use_model": "false"}).status_code == 400
    assert client.get("/api/sessions/not-a-session").status_code == 400


def test_inaudible_parameter_edit_returns_clarification(client):
    s = create(client)
    prefix = f"/api/sessions/{s['id']}"
    post(client, prefix + "/select", {"candidate_id": current(s)["candidates"][0]["id"]})
    # Hum has no decay envelope; a valid numerical change must not claim new audio.
    response = post(client, prefix + "/revise", {"patch": {"decay_s": 0.9}})
    assert response.status_code == 200
    assert response.json["clarification"]
    assert len(response.json["session"]["versions"]) == 1
