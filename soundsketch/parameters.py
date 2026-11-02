"""Validated, immutable DSP parameters. All amplitudes are digital, not SPL."""
from __future__ import annotations

from dataclasses import asdict, dataclass, fields
import math
from typing import Mapping, Iterable

PARAMETER_BOUNDS = {
    "frequency_hz": (25.0, 6000.0), "harmonics": (1, 16),
    "noise_mix": (0.0, 1.0), "band_low_hz": (10.0, 12000.0),
    "band_high_hz": (20.0, 20000.0), "modulation_hz": (0.0, 20.0),
    "modulation_depth": (0.0, 1.0), "pulse_interval_s": (0.1, 8.0),
    "decay_s": (0.005, 3.0), "duration_s": (0.5, 12.0),
    "sample_rate": (8000, 96000), "gain": (0.0, 0.5),
    "seed": (0, 4294967295),
}


@dataclass(frozen=True)
class SoundParams:
    kind: str = "hum"
    frequency_hz: float = 110.0
    harmonics: int = 4
    noise_mix: float = 0.08
    band_low_hz: float = 80.0
    band_high_hz: float = 2000.0
    modulation_hz: float = 1.0
    modulation_depth: float = 0.2
    pulse_interval_s: float = 1.0
    decay_s: float = 0.12
    duration_s: float = 4.0
    sample_rate: int = 24000
    gain: float = 0.2
    seed: int = 42

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or self.kind not in {"hum", "whistle", "knock", "mix"}:
            raise ValueError("kind 必须为 hum、whistle、knock 或 mix")
        for name, (lower, upper) in PARAMETER_BOUNDS.items():
            value = getattr(self, name)
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError(f"{name} 必须是有限数值（不能是布尔值）")
            if not lower <= value <= upper:
                raise ValueError(f"{name} 必须在 {lower} 至 {upper} 之间")
        for name in ("harmonics", "sample_rate", "seed"):
            if type(getattr(self, name)) is not int:
                raise ValueError(f"{name} 必须是整数")
        if not self.band_low_hz < self.band_high_hz < self.sample_rate / 2:
            raise ValueError("带限噪声频带须满足 0 < 下限 < 上限 < 采样率 / 2")
        if self.frequency_hz >= self.sample_rate / 2:
            raise ValueError("基频必须低于 Nyquist 频率（采样率 / 2）")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, values: Mapping) -> "SoundParams":
        if not isinstance(values, Mapping):
            raise ValueError("声音参数必须为对象")
        unknown = set(values) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError("未知声音参数：" + ", ".join(sorted(map(str, unknown))))
        return cls(**dict(values))


def default_params(kind: str = "hum") -> SoundParams:
    presets = {
        "hum": {},
        "whistle": {"frequency_hz": 2400.0, "harmonics": 1, "noise_mix": 0.03,
                    "band_low_hz": 1500.0, "band_high_hz": 4000.0,
                    "modulation_hz": 0.5, "modulation_depth": 0.1},
        "knock": {"frequency_hz": 420.0, "harmonics": 1, "noise_mix": 0.2,
                  "band_low_hz": 100.0, "band_high_hz": 5000.0,
                  "modulation_hz": 0.0, "modulation_depth": 0.0,
                  "pulse_interval_s": 0.9, "decay_s": 0.09},
        "mix": {"frequency_hz": 120.0, "noise_mix": 0.12},
    }
    if not isinstance(kind, str) or kind not in presets:
        raise ValueError("未知听感原型")
    return SoundParams(kind=kind, **presets[kind])


def apply_patch(params: SoundParams, patch: Mapping,
                locked_fields: Iterable[str] = ()) -> SoundParams:
    """Change only explicitly named fields; never silently clamp or reset a preset."""
    if not isinstance(patch, Mapping):
        raise ValueError("参数修改必须为对象")
    names = {f.name for f in fields(SoundParams)}
    if isinstance(locked_fields, (str, bytes)):
        raise ValueError("锁定字段必须为字段名列表")
    try:
        locks = set(locked_fields)
    except TypeError as exc:
        raise ValueError("锁定字段必须为字段名列表") from exc
    if not locks <= names:
        raise ValueError("包含未知锁定字段")
    values = params.to_dict()
    blocked = [name for name in locks & set(patch) if patch[name] != values[name]]
    if blocked:
        raise ValueError("已确认并锁定的特征不能修改：" + ", ".join(sorted(blocked)))
    values.update(patch)
    return SoundParams.from_dict(values)
