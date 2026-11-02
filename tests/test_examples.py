"""Check the portable example metadata and preservation of existing outputs."""
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from soundsketch.dsp import write_wav
from soundsketch.parameters import SoundParams


spec = importlib.util.spec_from_file_location(
    "soundsketch_example", Path(__file__).resolve().parents[1] / "examples" / "synthesize.py")
example = importlib.util.module_from_spec(spec)
spec.loader.exec_module(example)


def test_example_metadata_reproduces_wav_without_real_source_claim(tmp_path):
    output = tmp_path / "demo.wav"
    example.generate(output)
    document = json.loads(output.with_suffix(".json").read_text(encoding="utf-8"))
    assert document["provenance"]["category"] == "synthetic"
    assert document["provenance"]["source_label"] == "unknown"
    assert document["provenance"]["source_recording"] is None
    assert document["metrics"]["source_type"] == "synthetic_illustration"
    assert document["wav_sha256"] == hashlib.sha256(output.read_bytes()).hexdigest()
    write_wav(tmp_path / "copy.wav", SoundParams.from_dict(document["params"]))
    assert output.read_bytes() == (tmp_path / "copy.wav").read_bytes()


@pytest.mark.parametrize("suffix", [".wav", ".json"])
def test_example_refuses_existing_wav_or_metadata(tmp_path, suffix):
    original = tmp_path / f"demo{suffix}"
    original.write_bytes(b"original bytes")
    with pytest.raises(FileExistsError):
        example.generate(tmp_path / "demo.wav")
    assert original.read_bytes() == b"original bytes"
    assert len(list(tmp_path.iterdir())) == 1
