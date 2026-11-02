"""Evidence-aware case library schema; v0.1 intentionally ships no real cases."""
from dataclasses import asdict, dataclass, field
import json
from pathlib import Path


@dataclass(frozen=True)
class CaseRecord:
    id: str
    description: str
    category: str  # observation or synthetic_demo
    recording_path: str
    source_labels: tuple[str, ...] = ()
    evidence: tuple[str, ...] = ()
    generator: str | None = None
    verified_source: bool = False

    def __post_init__(self):
        if self.category not in ("observation", "synthetic_demo"):
            raise ValueError("案例必须区分原始观测和合成演示。")
        if not self.id or not self.description or not self.recording_path:
            raise ValueError("案例缺少标识、描述或录音路径。")
        if self.category == "synthetic_demo":
            if not self.generator or self.verified_source:
                raise ValueError("合成演示须标注生成器，且不能认证为真实声源。")
        elif self.generator:
            raise ValueError("带生成器来源的样本不能标为真实观测。")
        if self.category == "observation" and not self.evidence:
            raise ValueError("原始观测须提供录音出处、采集信息或其他可检查证据。")
        if self.verified_source and not self.source_labels:
            raise ValueError("验证声源需要来源标签及证据。")

    def to_dict(self):
        return asdict(self)


class CaseLibrary:
    def __init__(self, records=()):
        self.records = tuple(records)
        if len({r.id for r in self.records}) != len(self.records):
            raise ValueError("案例标识重复。")

    @classmethod
    def load(cls, path):
        path = Path(path)
        if not path.exists():
            return cls()
        values = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(values, list):
            raise ValueError("案例库应为 JSON 数组。")
        return cls(CaseRecord(**value) for value in values)

    def source_evidence(self):
        """Generated samples must never become evidence for source identification."""
        return [r.to_dict() for r in self.records if r.category == "observation"]
