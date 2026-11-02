"""Reproduce an export using its validated parameters and seed."""
import argparse
import json
from pathlib import Path
from .parameters import SoundParams
from .dsp import write_wav


def main():
    parser = argparse.ArgumentParser(description="从导出的参数重建合成 WAV")
    parser.add_argument("parameters")
    parser.add_argument("output")
    args = parser.parse_args()
    output = Path(args.output)
    if output.exists():
        parser.error("输出文件已存在，请使用新文件名以避免覆盖原始录音")
    document = json.loads(Path(args.parameters).read_text(encoding="utf-8"))
    params = SoundParams.from_dict(document.get("params", document))
    write_wav(output, params)
    print(f"已重建合成听感示意：{output.resolve()}")


if __name__ == "__main__":
    main()
