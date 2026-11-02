"""Generate a local synthetic hum and portable metadata; no API or recordings."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import tempfile

import numpy as np
import scipy

from soundsketch import __version__
from soundsketch.dsp import write_wav
from soundsketch.parameters import apply_patch, default_params


def generate(output: Path, seed: int = 42) -> dict:
    """Write a WAV/JSON pair without replacing an existing destination.

    This is a listening-sketch example, not a compressor simulation or diagnosis.
    """
    output = Path(output)
    if output.suffix.lower() != ".wav":
        raise ValueError("输出文件必须使用 .wav 扩展名")
    metadata_path = output.with_suffix(".json")
    if output.exists() or metadata_path.exists():
        raise FileExistsError("WAV 或同名 JSON 已存在，请选择新的输出文件名")

    params = apply_patch(default_params("hum"), {
        "frequency_hz": 75.0, "harmonics": 5, "noise_mix": 0.06,
        "modulation_hz": 0.6, "modulation_depth": 0.25,
        "gain": 0.15, "seed": seed,
    })
    output.parent.mkdir(parents=True, exist_ok=True)
    # Render into an isolated temporary directory. Final paths use exclusive
    # creation, so neither originals nor a concurrently created file are replaced.
    with tempfile.TemporaryDirectory(prefix=".soundsketch-example-", dir=output.parent) as temporary:
        rendered = Path(temporary) / "synthetic.wav"
        metrics = write_wav(rendered, params)
        content = rendered.read_bytes()
    document = {
        "description": "低频谐波嗡声，带轻微噪声和缓慢起伏；用于听感沟通的合成演示。",
        "params": params.to_dict(),
        "provenance": {
            "category": "synthetic",
            "generator": "local_dsp",
            "purpose": "synthetic_demo",
            "source_label": "unknown",
            "source_recording": None,
            "note": "不是现场录音、压缩机物理模型或故障样本；参数不是实测值。",
        },
        "runtime": {"soundsketch": __version__, "python": platform.python_version(),
                    "numpy": np.__version__, "scipy": scipy.__version__},
        "metrics": metrics,
        "wav_sha256": hashlib.sha256(content).hexdigest(),
    }
    # Reserve metadata first; if reserving the WAV fails, remove only the JSON
    # created by this invocation. An existing file is never removed.
    with metadata_path.open("x", encoding="utf-8") as handle:
        try:
            with output.open("xb") as audio:
                audio.write(content)
            json.dump(document, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
        except Exception:
            handle.close()
            metadata_path.unlink()
            raise
    return document


def main():
    parser = argparse.ArgumentParser(description="生成嗡鸣听感示意及参数 JSON（完全本地，不调用 API）")
    parser.add_argument("--output", required=True, type=Path, help="新的 WAV 文件路径；同时生成同名 JSON")
    parser.add_argument("--seed", type=int, default=42, help="随机种子，默认 42")
    args = parser.parse_args()
    try:
        generate(args.output, args.seed)
    except (ValueError, OSError) as error:
        parser.error(str(error))
    print(f"Synthetic illustration: {args.output}")
    print(f"Parameters and provenance: {args.output.with_suffix('.json')}")


if __name__ == "__main__":
    main()
