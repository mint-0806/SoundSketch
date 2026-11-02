from dataclasses import replace
import json
import wave

import numpy as np
import pytest
from scipy.io import wavfile
from scipy import signal

from soundsketch.parameters import SoundParams, default_params, apply_patch
from soundsketch.dsp import synthesize, write_wav
from soundsketch.analysis import analyze_wav


@pytest.mark.parametrize("kind", ["hum", "whistle", "knock", "mix"])
def test_deterministic_bounded_pcm(kind, tmp_path):
    params = default_params(kind)
    x = synthesize(params)
    assert np.array_equal(x, synthesize(SoundParams.from_dict(params.to_dict())))
    assert np.isfinite(x).all() and np.max(np.abs(x)) <= params.gain
    assert x[0] == x[-1] == 0
    path = tmp_path / f"{kind}.wav"
    metadata = write_wav(path, params)
    rate, pcm = wavfile.read(path)
    assert rate == params.sample_rate and len(pcm) == round(params.duration_s * rate)
    assert pcm.dtype == np.int16 and metadata["clipped_samples"] == 0
    assert metadata["peak_amplitude"] < 1
    json.dumps(metadata, allow_nan=False)


@pytest.mark.parametrize("patch", [
    {"frequency_hz": 80.0}, {"modulation_hz": 0.5}, {"pulse_interval_s": 2.0},
    {"gain": 0.1}, {"noise_mix": 0.4}, {"seed": 18},
])
def test_patch_preserves_every_other_field(patch):
    original = default_params()
    changed = apply_patch(original, patch)
    assert {k: v for k, v in changed.to_dict().items() if k not in patch} == {
        k: v for k, v in original.to_dict().items() if k not in patch}
    assert all(getattr(changed, k) == v for k, v in patch.items())
    assert original == default_params()


def test_gain_is_linear_and_seed_changes_noise():
    p = default_params()
    assert np.array_equal(synthesize(replace(p, gain=p.gain / 2)), synthesize(p) / 2)
    assert not np.array_equal(synthesize(p), synthesize(replace(p, seed=43)))


def test_measured_frequency_and_modulation():
    p = replace(default_params("whistle"), frequency_hz=1000, noise_mix=0,
                modulation_hz=2.0, modulation_depth=0.8)
    x = synthesize(p)
    frequencies = np.fft.rfftfreq(len(x), 1 / p.sample_rate)
    assert frequencies[np.argmax(np.abs(np.fft.rfft(x)))] == 1000
    envelope = np.abs(signal.hilbert(x))[p.sample_rate // 2:-p.sample_rate // 2]
    env_freq = np.fft.rfftfreq(len(envelope), 1 / p.sample_rate)
    spectrum = np.abs(np.fft.rfft(envelope - envelope.mean()))
    assert env_freq[np.argmax(spectrum)] == pytest.approx(2.0)


def test_knock_interval_is_measurable():
    p = replace(default_params("knock"), pulse_interval_s=0.8, noise_mix=0)
    x = synthesize(p)
    hop = p.sample_rate // 100
    energy = np.mean(x[:len(x) // hop * hop].reshape(-1, hop) ** 2, axis=1)
    peaks, _ = signal.find_peaks(energy, distance=50, prominence=energy.max() * 0.5)
    assert len(peaks) >= 4
    assert np.allclose(np.diff(peaks) / 100, 0.8, atol=0.02)


def test_noise_is_band_limited():
    p = replace(default_params(), noise_mix=1, modulation_depth=0,
                band_low_hz=1000, band_high_hz=2000)
    x = synthesize(p)
    freq = np.fft.rfftfreq(len(x), 1 / p.sample_rate)
    power = np.abs(np.fft.rfft(x)) ** 2
    # Edges use a 10 ms fade, so allow finite boundary leakage.
    assert power[(freq >= 900) & (freq <= 2100)].sum() / power.sum() > 0.99


@pytest.mark.parametrize("patch", [
    {"seed": True}, {"harmonics": 1.5}, {"sample_rate": 24000.0},
    {"gain": float("nan")}, {"frequency_hz": float("inf")},
    {"unknown": 1}, {"kind": "car"}, {"band_low_hz": 3000},
    {"band_high_hz": 12000}, {"duration_s": 100000},
    {"frequency_hz": 5000, "sample_rate": 8000},
])
def test_invalid_parameter_rejected(patch):
    with pytest.raises(ValueError):
        apply_patch(default_params(), patch)


def test_locks_are_enforced():
    p = default_params()
    with pytest.raises(ValueError, match="锁定"):
        apply_patch(p, {"frequency_hz": 80}, ["frequency_hz"])
    assert apply_patch(p, {"frequency_hz": p.frequency_hz}, ["frequency_hz"]) == p
    with pytest.raises(ValueError):
        apply_patch(p, {}, ["invented"])


def test_reference_analysis_is_observation_and_read_only(tmp_path):
    path = tmp_path / "original.wav"
    p = replace(default_params("whistle"), frequency_hz=1500, noise_mix=0)
    write_wav(path, p)
    original_bytes = path.read_bytes()
    result = analyze_wav(path)
    assert path.read_bytes() == original_bytes
    assert result["source_type"] == "reference_observation"
    assert result["metrics"]["dominant_frequency_hz"] == pytest.approx(1500, abs=6)
    assert result["method"]["fft_size"] == 4096
    assert len(result["spectrum"]["frequency_hz"]) <= 512
    assert len(result["envelope"]["rms"]) <= 300
    json.dumps(result, allow_nan=False)


def test_silence_analysis_has_no_nan(tmp_path):
    path = tmp_path / "silence.wav"
    wavfile.write(path, 8000, np.zeros(8000, dtype=np.int16))
    result = analyze_wav(path)
    assert result["metrics"]["dominant_frequency_hz"] is None
    json.dumps(result, allow_nan=False)


def test_recording_constraints(tmp_path):
    path = tmp_path / "long.wav"
    wavfile.write(path, 8000, np.zeros(31 * 8000, dtype=np.int16))
    with pytest.raises(ValueError, match="30 秒"):
        analyze_wav(path)
    path.write_bytes(b"not a WAV")
    with pytest.raises(ValueError, match="PCM WAV"):
        analyze_wav(path)
    path.write_bytes(b"0" * (20 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="20 MiB"):
        analyze_wav(path)


@pytest.mark.parametrize("width", [1, 2, 3, 4])
def test_integer_pcm_widths_and_stereo(width, tmp_path):
    path = tmp_path / f"pcm{width}.wav"
    silent_sample = b"\x80" if width == 1 else b"\x00" * width
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(2)
        wav.setsampwidth(width)
        wav.setframerate(8000)
        wav.writeframes(silent_sample * (2 * 8000))
    result = analyze_wav(path)
    assert result["metrics"]["channels"] == 2
    assert result["metrics"]["sample_width_bits"] == width * 8
    assert result["metrics"]["rms_amplitude"] == 0
