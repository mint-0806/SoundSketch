"""Deterministic bounded audio sketches; no source identification or SPL claims."""
from __future__ import annotations

from pathlib import Path
import numpy as np
from scipy.io import wavfile

from .parameters import SoundParams

ALGORITHM_VERSION = "soundsketch-dsp-v1"


def _band_noise(params: SoundParams, n: int) -> np.ndarray:
    # Component normalization precedes mixing/gain. It never cancels output gain.
    rng = np.random.default_rng(params.seed)
    source = rng.standard_normal(n)
    coefficients = np.fft.rfft(source)
    frequencies = np.fft.rfftfreq(n, 1 / params.sample_rate)
    coefficients[(frequencies < params.band_low_hz) |
                 (frequencies > params.band_high_hz)] = 0.0
    result = np.fft.irfft(coefficients, n=n)
    peak = float(np.max(np.abs(result)))
    return result / peak if peak > 0 else np.zeros(n)


def _hum(params: SoundParams, t: np.ndarray) -> np.ndarray:
    result = np.zeros_like(t)
    weight_sum = 0.0
    for harmonic in range(1, params.harmonics + 1):
        frequency = params.frequency_hz * harmonic
        if frequency >= params.sample_rate / 2:
            break
        weight = 1.0 / harmonic
        result += weight * np.sin(2 * np.pi * frequency * t)
        weight_sum += weight
    return result / weight_sum


def _knock(params: SoundParams, t: np.ndarray, noise: np.ndarray) -> np.ndarray:
    # Delay the first event so the session fade-in does not truncate its attack.
    shifted = t - 0.05
    age = np.remainder(np.maximum(shifted, 0), params.pulse_interval_s)
    envelope = np.exp(-age / params.decay_s)
    envelope *= 1.0 - np.exp(-age / 0.0015)
    # A short release before retriggering avoids discontinuities for long decays.
    envelope *= np.minimum(1.0, (params.pulse_interval_s - age) / 0.005)
    envelope[shifted < 0] = 0.0
    carrier = np.sin(2 * np.pi * params.frequency_hz * age)
    return ((1.0 - params.noise_mix) * carrier + params.noise_mix * noise) * envelope


def synthesize(params: SoundParams) -> np.ndarray:
    """Return mono float64 samples in [-gain, gain], reproducible for a fixed build.

    Gain is a linear digital amplitude. Band-noise normalization only bounds that
    component; the final signal is never loudness/peak normalized or clipped.
    """
    if not isinstance(params, SoundParams):
        raise ValueError("合成需要经过校验的 SoundParams")
    n = int(round(params.duration_s * params.sample_rate))
    t = np.arange(n, dtype=np.float64) / params.sample_rate
    noise = _band_noise(params, n)
    if params.kind == "knock":
        signal = _knock(params, t, noise)
    else:
        tonal = (np.sin(2 * np.pi * params.frequency_hz * t)
                 if params.kind == "whistle" else _hum(params, t))
        signal = (1.0 - params.noise_mix) * tonal + params.noise_mix * noise
        if params.kind == "mix":
            signal = 0.5 * signal + 0.5 * _knock(params, t, noise)
    if params.modulation_hz > 0:
        envelope = (1.0 - params.modulation_depth / 2.0 +
                    params.modulation_depth / 2.0 *
                    np.cos(2 * np.pi * params.modulation_hz * t))
        signal *= envelope
    fade_n = min(int(0.01 * params.sample_rate), n // 2)
    fade = np.sin(np.linspace(0, np.pi / 2, fade_n)) ** 2
    signal[:fade_n] *= fade
    signal[-fade_n:] *= fade[::-1]
    signal *= params.gain
    if not np.isfinite(signal).all() or np.max(np.abs(signal)) >= 1:
        raise RuntimeError("合成安全检查失败：存在非有限值或满幅样本")
    return signal


def write_wav(path: str | Path, params: SoundParams) -> dict:
    """Write 16-bit PCM WAV and return measurable digital signal information."""
    path = Path(path)
    samples = synthesize(params)
    pcm = np.rint(samples * 32767).astype(np.int16)
    path.parent.mkdir(parents=True, exist_ok=True)
    wavfile.write(str(path), params.sample_rate, pcm)
    decoded = pcm.astype(np.float64) / 32768.0
    rms = float(np.sqrt(np.mean(decoded ** 2)))
    return {
        "algorithm_version": ALGORITHM_VERSION,
        "source_type": "synthetic_illustration",
        "sample_rate": params.sample_rate, "channels": 1,
        "sample_width_bits": 16, "sample_count": len(pcm),
        "duration_s": len(pcm) / params.sample_rate,
        "peak_amplitude": float(np.max(np.abs(decoded))),
        "rms_amplitude": rms,
        "rms_dbfs": float(20 * np.log10(max(rms, 1e-6))),
        "clipped_samples": int(np.sum((pcm == 32767) | (pcm == -32768))),
        "amplitude_note": "数字幅值与 dBFS 不等于现场声压级；合成音仅用于表达听感。",
    }
