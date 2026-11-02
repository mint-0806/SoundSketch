"""Local measurements on immutable reference PCM WAV recordings."""
from __future__ import annotations

from pathlib import Path
import wave
import numpy as np
from scipy import signal

MAX_FILE_BYTES = 20 * 1024 * 1024
MAX_DURATION_S = 30.0


def _read_pcm(path: Path) -> tuple[np.ndarray, int, int, int]:
    if path.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("参考录音不能超过 20 MiB")
    try:
        with wave.open(str(path), "rb") as wav:
            channels, width, rate, frames = (wav.getnchannels(), wav.getsampwidth(),
                                             wav.getframerate(), wav.getnframes())
            if wav.getcomptype() != "NONE":
                raise ValueError("首版仅支持未压缩 PCM WAV")
            if channels not in (1, 2) or width not in (1, 2, 3, 4):
                raise ValueError("参考录音须为单/双声道、8/16/24/32-bit PCM WAV")
            if not 8000 <= rate <= 96000:
                raise ValueError("参考录音采样率须在 8000 至 96000 Hz")
            if frames <= 0 or frames / rate > MAX_DURATION_S:
                raise ValueError("参考录音须非空且不超过 30 秒")
            raw = wav.readframes(frames)
    except (wave.Error, EOFError) as exc:
        raise ValueError("无法读取录音：首版仅支持未压缩整数 PCM WAV") from exc
    if len(raw) != frames * channels * width:
        raise ValueError("WAV 文件数据不完整")
    if width == 1:
        samples = (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128) / 128
    elif width == 3:
        byte_array = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        integers = byte_array[:, 0] | (byte_array[:, 1] << 8) | (byte_array[:, 2] << 16)
        integers = np.where(integers & 0x800000, integers - 0x1000000, integers)
        samples = integers.astype(np.float64) / 8388608
    else:
        samples = np.frombuffer(raw, dtype=f"<i{width}").astype(np.float64) / (2 ** (8 * width - 1))
    return samples.reshape(-1, channels), rate, channels, width


def analyze_wav(path: str | Path) -> dict:
    samples, rate, channels, width = _read_pcm(Path(path))
    mono = np.mean(samples, axis=1)
    fft_size = min(4096, len(mono))
    overlap = fft_size // 2
    freq, power = signal.welch(mono, fs=rate, window="hann", nperseg=fft_size,
                               noverlap=overlap, detrend="constant", scaling="spectrum")
    db = 10.0 * np.log10(np.maximum(power, 1e-12))
    # Peak-preserving aggregation for a compact plotting payload.
    bins = np.array_split(np.arange(len(freq)), min(512, len(freq)))
    positions = np.array([indices[np.argmax(power[indices])] for indices in bins])
    hop = max(1, round(rate * 0.02))
    starts = np.arange(0, len(mono), hop)
    envelope = np.array([np.sqrt(np.mean(mono[start:start + hop] ** 2)) for start in starts])
    selected = np.unique(np.linspace(0, len(starts) - 1, min(300, len(starts))).astype(int))
    rms = float(np.sqrt(np.mean(samples ** 2)))
    clip_threshold = 1.0 - 1.0 / (2 ** (width * 8 - 1))
    dominant = float(freq[np.argmax(power[1:]) + 1]) if len(power) > 1 and np.max(power[1:]) > 1e-12 else None
    return {
        "source_type": "reference_observation",
        "metrics": {
            "duration_s": len(mono) / rate, "sample_rate": rate, "channels": channels,
            "sample_width_bits": width * 8,
            "peak_amplitude": float(np.max(np.abs(samples))), "rms_amplitude": rms,
            "rms_dbfs": float(20 * np.log10(max(rms, 1e-6))),
            "dominant_frequency_hz": dominant,
            "clipped_sample_fraction": float(np.mean(np.abs(samples) >= clip_threshold)),
        },
        "spectrum": {"frequency_hz": freq[positions].tolist(), "magnitude_dbfs": db[positions].tolist()},
        "envelope": {"time_s": (starts[selected] / rate).tolist(), "rms": envelope[selected].tolist()},
        "method": {
            "spectrum": "Welch 功率谱（scipy.signal.welch，scaling=spectrum，去均值）",
            "fft_size": fft_size, "window": "hann", "overlap_samples": overlap,
            "frequency_resolution_hz": rate / fft_size,
            "plot_spectrum_reduction": "至多 512 个频率组，各组保留峰值频点",
            "envelope_window_ms": 20, "envelope_hop_samples": hop,
            "plot_envelope_reduction": "至多 300 个均匀选取的 RMS 窗",
            "downmix": "频谱和包络使用声道算术平均；总 RMS 和峰值使用原始所有声道",
            "db_reference": "1.0 数字满幅；dBFS 下限 -120，不是经过校准的声压级",
        },
        "uncertainty": [
            "这是参考录音的数字信号测量；不能据此确定现场声压级、地点、设备或故障。",
            "手机频响、自动增益、位置与背景噪声会影响记录；双声道相位抵消可能削弱下混频谱。",
            "主峰不一定是声源基频；合成示意不能作为新增观测证据。",
        ],
    }
