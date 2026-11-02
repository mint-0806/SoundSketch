"""Local session persistence and immutable synthesis histories."""
from __future__ import annotations

import hashlib
import io
import json
import re
import threading
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import scipy

from . import __version__
from .analysis import analyze_wav
from .dsp import write_wav
from .parameters import SoundParams, apply_patch, default_params
from .planner import plan_initial, plan_revision


def now():
    return datetime.now(timezone.utc).isoformat()


def identifier():
    return uuid.uuid4().hex


def valid_id(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{32}", value):
        raise ValueError("无效的会话或文件标识")
    return value


def text_input(value, name, limit=4000, required=False):
    if not isinstance(value, str) or len(value) > limit:
        raise ValueError(f"{name}必须为不超过 {limit} 字的文本")
    value = value.strip()
    if required and not value:
        raise ValueError(f"请填写{name}")
    return value


def parameter_description(params):
    names = {"hum": "谐波嗡鸣", "whistle": "窄带尖啸", "knock": "间歇衰减敲击", "mix": "嗡鸣与敲击混合"}
    p = params
    parts = [names[p['kind']], f"基频/共振频率 {p['frequency_hz']:g} Hz"]
    if p['modulation_hz'] > 0 and p['modulation_depth'] > 0:
        parts.append(f"调幅速率 {p['modulation_hz']:g} Hz、深度 {p['modulation_depth']:g}")
    if p['kind'] in ('knock', 'mix'):
        parts.extend([f"敲击间隔 {p['pulse_interval_s']:g} s", f"衰减 {p['decay_s']:g} s"])
    parts.append(f"噪声混合 {p['noise_mix']:g}")
    return "；".join(parts) + "。这些是生成设定，不是现场测量。"


def plan_record(task, plan, params, before=None, locks=(), feedback=""):
    """Store proposals separately from measurements and synthesis output metrics."""
    values = params.to_dict() if params else None
    previous = before.to_dict() if before else None
    return {"task": task, "kind": params.kind if params else plan.get("kind"),
            "params": values, "before_params": previous,
            "patch": plan.get("patch", {}), "mode": plan["mode"],
            "notice": plan.get("notice", ""), "clarification": plan.get("clarification"),
            "model_call": plan.get("model_call"), "locked_fields": list(locks),
            "preserved_fields": plan.get("preserved_fields", []), "feedback": feedback,
            "changes": [{"field": k, "before": previous[k], "after": v}
                        for k, v in (values or {}).items() if previous and previous[k] != v],
            "parameter_role": "synthesis_settings_not_measurements"}


def inactive_changes(before, after):
    """Reject changes to disabled components, even alongside another valid edit."""
    changed = {k for k, v in after.to_dict().items() if before.to_dict()[k] != v}
    inactive = set()
    if after.kind not in ("knock", "mix"):
        inactive.update(("pulse_interval_s", "decay_s"))
    if after.kind not in ("hum", "mix"):
        inactive.add("harmonics")
    if after.noise_mix == 1:
        inactive.update(("frequency_hz", "harmonics"))
    if after.noise_mix == 0:
        inactive.update(("band_low_hz", "band_high_hz", "seed"))
    if before.modulation_depth == after.modulation_depth == 0:
        inactive.add("modulation_hz")
    if before.modulation_hz == after.modulation_hz == 0:
        inactive.add("modulation_depth")
    return sorted(changed & inactive)


class SessionStore:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()

    def directory(self, session_id):
        return self.root / valid_id(session_id)

    def load(self, session_id):
        path = self.directory(session_id) / "session.json"
        if not path.is_file():
            raise FileNotFoundError("未找到会话")
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, session):
        session["updated_at"] = now()
        directory = self.directory(session["id"])
        directory.mkdir(parents=True, exist_ok=True)
        temporary = directory / f".{identifier()}.tmp"
        temporary.write_text(json.dumps(session, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
        temporary.replace(directory / "session.json")

    def list_sessions(self):
        items = []
        for path in self.root.glob("*/session.json"):
            try:
                session = json.loads(path.read_text(encoding="utf-8"))
                items.append({k: session[k] for k in ("id", "description", "updated_at")})
            except (ValueError, KeyError, OSError):
                continue
        return sorted(items, key=lambda s: s["updated_at"], reverse=True)

    @staticmethod
    def current(session):
        return next(v for v in session["versions"] if v["id"] == session["current_version_id"])

    @classmethod
    def selected(cls, session):
        version = cls.current(session)
        candidate = next((c for c in version["candidates"] if c["id"] == version["selected_id"]), None)
        if candidate is None:
            raise ValueError("请先选择一个候选声音")
        return candidate

    @staticmethod
    def invalidate_confirmation(session):
        session["confirmed_version_id"] = None
        session["confirmed_candidate_id"] = None

    def candidate(self, session, params, label, evidence):
        candidate_id = identifier()
        directory = self.directory(session["id"]) / "generated"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{candidate_id}.wav"
        metrics = write_wav(path, params)
        return {
            "id": candidate_id, "label": label,
            "description": parameter_description(params.to_dict()), "params": params.to_dict(),
            "audio_url": f"/api/sessions/{session['id']}/audio/generated/{candidate_id}",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "metrics": metrics,
            "provenance": {"category": "synthetic", "generator": "local_dsp",
                           "evidence": evidence,
                           "uncertainty": "合成听感示意；未匹配真实案例，声源、地点和故障均未知。不能用此声音作为新观测。"},
        }

    def prepare_initial(self, description, kind="auto", scene="", timing="", use_model=False):
        description = text_input(description, "声音描述", required=True)
        scene = text_input(scene, "发生场景", 1000)
        timing = text_input(timing, "时间规律", 1000)
        if kind not in ("auto", "hum", "whistle", "knock"):
            raise ValueError("请选择嗡鸣、尖啸或间歇敲击")
        if type(use_model) is not bool:
            raise ValueError("use_model 必须为布尔值")
        plan = plan_initial(description, kind=kind, scene=scene, timing=timing, use_model=use_model)
        inputs = dict(description=description, kind=kind, scene=scene, timing=timing, use_model=use_model)
        if plan.get("clarification"):
            return inputs, plan, None
        params = apply_patch(default_params(plan["kind"]), plan.get("patch", {}))
        if params.gain == 0:
            raise ValueError("初始幅值为零，无法提供可听对照。请使用非零幅值重新生成。")
        return inputs, plan, params

    def create(self, description, kind="auto", scene="", timing="", use_model=False, *, _prepared=None):
        inputs, plan, params = (_prepared if _prepared is not None else
                                self.prepare_initial(description, kind, scene, timing, use_model))
        if plan.get("clarification"):
            raise ValueError(plan["clarification"])
        description, scene, timing, use_model = (inputs[k] for k in ("description", "scene", "timing", "use_model"))
        session = {"schema_version": 1, "id": identifier(), "description": description,
                   "scene": scene, "timing": timing, "created_at": now(), "updated_at": now(),
                   "mode": plan["mode"], "notice": plan.get("notice", ""), "use_model": use_model,
                   "model_call": plan.get("model_call"),
                   "versions": [], "references": [], "events": [], "locked_fields": [],
                   "confirmed_version_id": None, "confirmed_candidate_id": None,
                   "runtime": {"soundsketch": __version__, "numpy": np.__version__, "scipy": scipy.__version__}}
        evidence = [f"用户描述：{description}", "本地 DSP 参数探索；未使用合成音频进行来源推断"]
        candidates = [self.candidate(session, params, "A · 基准听感", evidence)]
        if candidates[0]["metrics"]["peak_amplitude"] == 0:
            raise ValueError("当前参数生成了静音（幅值过低或频带过窄），请调整幅值或频带后重试。")
        if params.kind == "knock" and params.pulse_interval_s < params.duration_s - 0.1:
            field = "pulse_interval_s"
        elif params.noise_mix < 1:
            field = "frequency_hz"
        else:
            field = "decay_s" if params.kind == "knock" else "band_high_hz"
        value = getattr(params, field)
        # Offer two feasible alternatives, even when the model chose a boundary value.
        alternatives = []
        for factor in (.75, 1.3, .5, 1.5):
            try:
                changed = apply_patch(params, {field: round(value * factor, 4)})
            except ValueError:
                continue
            if getattr(changed, field) != value:
                alternatives.append(changed)
            if len(alternatives) == 2:
                break
        for letter, variant in zip(("B", "C"), alternatives):
            names = {"pulse_interval_s": ("更短间隔", "更长间隔"), "frequency_hz": ("更低频率", "更高频率"),
                     "decay_s": ("更短尾音", "更长尾音"), "band_high_hz": ("噪声频带更窄", "噪声频带更宽")}
            direction = names[field][int(getattr(variant, field) > value)]
            candidates.append(self.candidate(session, variant, f"{letter} · {direction}", evidence))
        unique = {candidate["sha256"]: candidate for candidate in candidates}
        if len(unique) < 2:
            raise ValueError("当前参数边界内无法生成可区分的候选，请调整频带、间隔或幅值后重试。")
        candidates = list(unique.values())
        version = {"id": identifier(), "parent_id": None, "candidates": candidates, "selected_id": None,
                   "feedback": description, "changed_fields": [], "created_at": now(),
                   "plan": plan_record("initial", plan, params, feedback=description),
                   "mode": plan["mode"], "notice": plan.get("notice", ""), "model_call": plan.get("model_call")}
        session["versions"].append(version)
        session["current_version_id"] = version["id"]
        session["events"].append({"type": "create", "at": now(), "mode": plan["mode"], "model_call": plan.get("model_call")})
        self.save(session)
        return session

    def select(self, session, candidate_id):
        version = self.current(session)
        if candidate_id not in [c["id"] for c in version["candidates"]]:
            raise ValueError("候选不属于当前版本")
        version["selected_id"] = candidate_id
        self.invalidate_confirmation(session)
        session["events"].append({"type": "select", "candidate_id": candidate_id, "at": now()})
        self.save(session)
        return session

    def prepare_revision(self, session, feedback="", patch=None, locked_fields=None, use_model=None):
        feedback = text_input(feedback, "修改意见")
        previous = self.selected(session)
        params = SoundParams.from_dict(previous["params"])
        locks = session["locked_fields"] if locked_fields is None else locked_fields
        if not isinstance(locks, list) or any(not isinstance(f, str) or f not in params.to_dict() for f in locks):
            raise ValueError("锁定字段无效")
        if use_model is not None and type(use_model) is not bool:
            raise ValueError("use_model 必须为布尔值")
        if patch is None:
            if not feedback:
                raise ValueError("请填写修改意见，或修改一个参数")
            plan = plan_revision(feedback, params, locked_fields=locks,
                                 use_model=session["use_model"] if use_model is None else use_model)
        else:
            if not isinstance(patch, dict) or not patch:
                raise ValueError("参数修改必须是非空对象")
            plan = {"patch": patch, "mode": "local", "notice": "已应用表单参数修改。"}
        updated = params
        if not plan.get("clarification"):
            protected = set(locks) | set(plan.get("preserved_fields", []))
            updated = apply_patch(params, plan.get("patch", {}), locked_fields=protected)
            inert = inactive_changes(params, updated)
            if inert:
                plan = dict(plan, clarification="以下参数对当前声音成分不生效：" + "、".join(inert) + "。请改选有效特征，或明确启用该成分。")
            elif updated == params:
                plan = dict(plan, clarification="参数没有变化，请指定要改变的特征。")
        inputs = dict(feedback=feedback, patch=patch, locked_fields=list(locks), use_model=use_model)
        return inputs, plan, params, updated, list(locks)

    def revise(self, session, feedback="", patch=None, locked_fields=None, use_model=None, *, _prepared=None):
        inputs, plan, params, updated, locks = (_prepared if _prepared is not None else
                    self.prepare_revision(session, feedback, patch, locked_fields, use_model))
        feedback = inputs["feedback"]
        previous = self.selected(session)
        if plan.get("clarification"):
            session["mode"] = plan["mode"]
            session["notice"] = plan.get("notice", "")
            session["model_call"] = plan.get("model_call")
            session["events"].append({"type": "clarification", "feedback": feedback,
                                      "question": plan["clarification"], "at": now(),
                                      "mode": plan["mode"], "model_call": plan.get("model_call")})
            self.save(session)
            return {"session": session, "clarification": plan["clarification"], "status": plan["mode"]}
        changes = [f for f, value in updated.to_dict().items() if value != params.to_dict()[f]]
        if not changes:
            return {"session": session, "clarification": "参数没有变化，请指定要改变的特征。", "status": plan["mode"]}
        candidate = self.candidate(session, updated, f"版本 {len(session['versions']) + 1} · 修改后",
                                   [f"用户原描述：{session['description']}", f"用户修改：{feedback or '表单参数修改'}",
                                    "参数继承仅用于合成；生成历史不构成真实声源证据"])
        if candidate["sha256"] == previous["sha256"]:
            question = "所改参数未改变当前声音。该成分可能未启用，或变化低于 PCM 精度；请改选有效特征。"
            session["events"].append({"type": "clarification", "feedback": feedback, "question": question, "at": now()})
            self.save(session)
            return {"session": session, "clarification": question, "status": plan["mode"]}
        version = {"id": identifier(), "parent_id": session["current_version_id"], "candidates": [candidate],
                   "selected_id": candidate["id"], "feedback": feedback, "changed_fields": changes,
                   "patch": plan.get("patch", {}), "locked_fields": locks, "created_at": now(),
                   "plan": plan_record("revision", plan, updated, params, locks, feedback),
                   "mode": plan["mode"], "notice": plan.get("notice", ""), "model_call": plan.get("model_call")}
        session["versions"].append(version)
        session["current_version_id"] = version["id"]
        session["locked_fields"] = locks
        session["mode"] = plan["mode"]
        session["notice"] = plan.get("notice", "")
        session["model_call"] = plan.get("model_call")
        self.invalidate_confirmation(session)
        session["events"].append({"type": "revise", "version_id": version["id"], "at": now(),
                                  "mode": plan["mode"], "model_call": plan.get("model_call")})
        self.save(session)
        return {"session": session, "clarification": None, "status": plan["mode"]}

    def restore(self, session, version_id=None):
        target = version_id or self.current(session)["parent_id"]
        version = next((v for v in session["versions"] if v["id"] == target), None)
        if version is None:
            raise ValueError("没有可恢复的版本")
        session["current_version_id"] = target
        session["locked_fields"] = version.get("locked_fields", [])
        session["mode"] = version.get("mode", "local")
        session["notice"] = version.get("notice", "已恢复历史版本；旧版本可能没有模型调用记录。")
        session["model_call"] = version.get("model_call")
        self.invalidate_confirmation(session)
        session["events"].append({"type": "restore", "version_id": target, "at": now()})
        self.save(session)
        return session

    def confirm(self, session, description=""):
        candidate = self.selected(session)
        session["confirmed_version_id"] = session["current_version_id"]
        session["confirmed_candidate_id"] = candidate["id"]
        session["confirmed_description"] = text_input(description, "确认听感描述") or session["description"]
        session["events"].append({"type": "confirm", "candidate_id": candidate["id"], "at": now()})
        self.save(session)
        return session

    def add_reference(self, session, filename, content):
        if not content or len(content) > 20 * 1024 * 1024:
            raise ValueError("参考录音须为不超过 20 MiB 的 PCM WAV")
        digest = hashlib.sha256(content).hexdigest()
        # Exact copies of our generated sketches cannot be promoted to observations.
        for summary in self.list_sessions():
            known = self.load(summary["id"])
            if any(c.get("sha256") == digest for v in known["versions"] for c in v["candidates"]):
                raise ValueError("检测到本项目生成的合成示意，不能将其作为原始录音证据上传。")
        reference_id = identifier()
        path = self.directory(session["id"]) / "references" / f"{reference_id}.wav"
        path.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation: original bytes are never resampled or overwritten.
        with path.open("xb") as handle:
            handle.write(content)
        try:
            analysis = analyze_wav(path)
        except (ValueError, OSError, EOFError):
            path.unlink(missing_ok=True)
            raise
        reference = {"id": reference_id, "filename": str(filename).replace("\\", "/").rsplit("/", 1)[-1][:255],
                     "sha256": digest, "category": "observation", "origin": "user_supplied_unverified",
                     "notice": "用户提供的参考观测；录音来源真实性未独立验证。原始字节保留，仅本地分析。",
                     "audio_url": f"/api/sessions/{session['id']}/audio/references/{reference_id}",
                     "analysis": analysis, "created_at": now(), "external_analyses": []}
        session["references"].append(reference)
        session["events"].append({"type": "reference_local", "reference_id": reference_id, "at": now()})
        self.save(session)
        return session

    def export(self, session):
        candidate = self.selected(session)
        if (session["confirmed_version_id"] != session["current_version_id"] or
                session["confirmed_candidate_id"] != candidate["id"]):
            raise ValueError("请先确认当前候选及听感描述，再导出")
        buffer = io.BytesIO()
        description = ("SoundSketch · 已确认的合成听感示意\n\n"
                       f"用户听感描述：{session['confirmed_description']}\n"
                       f"场景（用户提供）：{session['scene'] or '未知'}\n时间规律：{session['timing'] or '未提供'}\n\n"
                       f"生成参数说明：{candidate['description']}\n\n"
                       "数字幅值不等于真实声压级。声源、地点、故障未验证。此音频用于沟通，不能作为现场观测证据。\n"
                       "原始参考录音未包含在导出包；session.json 包含用户描述、反馈和分析信息，分享前请检查。\n")
        metadata = {"params": candidate["params"], "provenance": candidate["provenance"],
                    "runtime": session["runtime"], "wav_sha256": candidate["sha256"],
                    "reproduce": "python -m soundsketch.render parameters.json reproduced.wav"}
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(self.directory(session["id"]) / "generated" / f"{candidate['id']}.wav", "confirmed.wav")
            archive.writestr("description.txt", description)
            archive.writestr("parameters.json", json.dumps(metadata, ensure_ascii=False, indent=2))
            archive.writestr("session.json", json.dumps(session, ensure_ascii=False, indent=2))
        buffer.seek(0)
        return buffer
