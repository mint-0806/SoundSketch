"""Conservative local planning; model suggestions always pass the same validator."""
from __future__ import annotations

import re
from typing import Iterable

from .parameters import PARAMETER_BOUNDS, SoundParams, apply_patch, default_params

LOCAL_NOTICE = "本地降级模式：使用表单、明确的描述规则与 DSP，不推断具体设备或故障。"

_KIND_WORDS = {
    "knock": ("敲", "撞", "咚", "砰", "滴答", "哒哒", "脉冲"),
    "whistle": ("尖啸", "尖叫", "啸叫", "高频", "口哨", "吱", "嘶鸣"),
    "hum": ("嗡", "低沉", "低频", "轰", "蜂鸣", "持续低音"),
}


def _result(patch=None, mode="local", notice=LOCAL_NOTICE, clarification=None, **extra):
    return {"patch": patch or {}, "mode": mode, "notice": notice,
            "clarification": clarification, **extra}


def _validate_text(text, label):
    if not isinstance(text, str) or not text.strip() or len(text) > 4000:
        raise ValueError(f"{label}应为 1–4000 字符的文字。")
    return text.strip()


def _fallback_notice(error):
    return f"模型未成功返回可用参数（{error}）。已明确降级为本地规则；未采用模型结果。"


def model_contract():
    """Give the model the same numeric bounds enforced locally, plus JSON examples."""
    return {
        "editable_bounds": {k: {"minimum": v[0], "maximum": v[1],
                                "type": "integer" if k == "harmonics" else "number"}
                            for k, v in PARAMETER_BOUNDS.items()
                            if k not in {"duration_s", "sample_rate", "seed"}},
        "constraints": ["band_low_hz < band_high_hz < sample_rate / 2",
                        "frequency_hz < sample_rate / 2", "初始 gain 必须大于零",
                        "仅 knock/mix 有敲击间隔和衰减；whistle 的 harmonics 不生效",
                        "noise_mix=1 时音高不生效；modulation_depth=0 时起伏速率不生效"],
        "json_examples": [
            {"kind": "hum", "patch": {"frequency_hz": 80}, "clarification": None},
            {"kind": None, "patch": {}, "clarification": "你希望改变音高还是响度？"},
        ],
    }


def plan_initial(description, kind="auto", scene="", timing="", use_model=False):
    description = _validate_text(description, "听感描述")
    if kind not in ("auto", "hum", "whistle", "knock"):
        raise ValueError("请选择嗡鸣、尖啸或间歇敲击。")
    matches = [key for key, words in _KIND_WORDS.items() if any(w in description for w in words)]
    inferred = kind if kind != "auto" else (matches[0] if len(matches) == 1 else None)
    notice, mode = LOCAL_NOTICE, "local"
    if use_model:
        from .adapters import AdapterError, plan_parameters
        try:
            suggestion = plan_parameters({"task": "initial", "description": description,
                                          "scene": scene, "timing": timing, "chosen_kind": kind,
                                          "parameter_contract": model_contract(),
                                          "presets": {k: default_params(k).to_dict() for k in ("hum", "whistle", "knock")}})
            if suggestion["clarification"]:
                return _result(mode="model", notice="模型建议先澄清听感。", clarification=suggestion["clarification"], kind=inferred,
                               model_call=suggestion.get("_meta"))
            model_kind = suggestion.get("kind")
            if model_kind not in ("hum", "whistle", "knock"):
                raise AdapterError("模型没有提供有效原型")
            if kind != "auto" and model_kind != kind:
                raise AdapterError("模型试图改变已选原型")
            base = default_params(model_kind)
            patch = suggestion["patch"]
            if "kind" in patch or {"seed", "sample_rate", "duration_s"} & set(patch):
                raise AdapterError("模型修改了不允许自动调整的字段")
            apply_patch(base, patch)
            return _result(kind=model_kind, patch=patch, mode="model",
                           notice="模型建议已通过参数校验；候选仍由本地 DSP 合成，来源未知。",
                           model_call=suggestion.get("_meta"))
        except (AdapterError, ValueError, TypeError):
            # Never put provider response text or credentials in returned notices.
            notice = _fallback_notice("配置缺失、调用失败或参数校验未通过")
            mode = "fallback"
    if inferred is None:
        return _result(kind=None, mode=mode, notice=notice,
                       clarification="目前无法可靠选择原型。请在表单中选择嗡鸣、尖啸或间歇敲击；若兼有多种声音，可先选最想表达的一种。")
    base = default_params(inferred)
    patch = {}
    if "低沉" in description or "低频" in description:
        patch["frequency_hz"] = 80.0 if inferred == "hum" else base.frequency_hz * 0.8
    if "缓慢起伏" in description or "慢慢起伏" in description:
        patch.update(modulation_hz=0.4, modulation_depth=0.45)
    if "间歇" in description and inferred == "knock":
        patch["pulse_interval_s"] = 1.5
    apply_patch(base, patch)
    return _result(kind=inferred, patch=patch, mode=mode, notice=notice)


_CUES = {
    "frequency_hz": ("低沉", "高一点", "低一点", "音高", "频率", "尖锐", "更尖", "更低", "更高"),
    "modulation_hz": ("起伏", "调幅", "波动"),
    "modulation_depth": ("起伏幅度", "起伏深度", "波动幅度"),
    "pulse_interval_s": ("间隔", "敲击频率", "敲慢", "敲快", "敲击慢", "敲击快"),
    "decay_s": ("衰减", "尾音", "余音"),
    "gain": ("音量", "响一点", "轻一点", "响些", "小声", "大声", "响度"),
    "noise_mix": ("噪声比例", "沙沙", "噪声多", "噪声少"),
    "harmonics": ("谐波",),
    "band_low_hz": ("低截止",),
    "band_high_hz": ("高截止",),
}


def _local_revision(text, params):
    # One complete clause only; the caller accounts for every other clause.
    compact = re.sub(r"\s", "", text)
    compact = re.sub(r"^(?:(?:请|麻烦|帮我|让声音|把声音|再|只))+", "", compact)
    patterns = (
        (r"(更低沉|低沉一点|音高低一点|频率低一点|更低一点)", "frequency_hz", .8),
        (r"(更尖锐|尖锐一点|音高高一点|频率高一点|更高一点)", "frequency_hz", 1.25),
        (r"(起伏慢一点|起伏更慢|波动慢一点)", "modulation_hz", .7),
        (r"(起伏快一点|起伏更快|波动快一点)", "modulation_hz", 1.4),
        (r"(敲击间隔更长|敲击间隔长一点|间隔更长|敲慢一点)", "pulse_interval_s", 1.5),
        (r"(敲击间隔更短|敲击间隔短一点|间隔更短|敲快一点)", "pulse_interval_s", 2 / 3),
        (r"(尾音长一点|余音长一点|衰减慢一点)", "decay_s", 1.5),
        (r"(尾音短一点|余音短一点|衰减快一点)", "decay_s", 2 / 3),
        (r"(音量小一点|小声一点|轻一点)", "gain", .8),
        (r"(音量大一点|大声一点|响一点)", "gain", 1.2),
    )
    for pattern, field, ratio in patterns:
        if re.fullmatch(pattern, compact):
            return {field: round(getattr(params, field) * ratio, 8)}
    return None


_PRESERVE_NAMES = {
    "音高": ("frequency_hz",), "频率": ("frequency_hz",),
    "音量": ("gain",), "响度": ("gain",),
    "起伏速度": ("modulation_hz",), "起伏频率": ("modulation_hz",),
    "起伏幅度": ("modulation_depth",), "起伏深度": ("modulation_depth",),
    "起伏": ("modulation_hz", "modulation_depth"),
    "敲击间隔": ("pulse_interval_s",), "间隔": ("pulse_interval_s",),
    "尾音": ("decay_s",), "余音": ("decay_s",), "衰减": ("decay_s",),
    "噪声比例": ("noise_mix",), "噪声混合比例": ("noise_mix",),
    "谐波数量": ("harmonics",), "谐波": ("harmonics",),
    "噪声频带": ("band_low_hz", "band_high_hz"),
    "低截止": ("band_low_hz",), "高截止": ("band_high_hz",),
    "时长": ("duration_s",), "随机种子": ("seed",), "采样率": ("sample_rate",),
}


def _clause_fields(clause):
    fields = {field for field, cues in _CUES.items() if any(c in clause for c in cues)}
    if "modulation_depth" in fields:
        fields.discard("modulation_hz")
    if "pulse_interval_s" in fields:
        fields.discard("frequency_hz")
    return fields


def _revision_intent(feedback, params):
    """Separate change requests from preservation constraints without dropping text.

    Unknown single clauses may be interpreted by the optional model. In a compound
    request every unrecognised clause must at least name one explicit feature;
    otherwise no partial patch is permitted, including from the model.
    """
    clauses = [re.sub(r"^(?:(?:请|麻烦|帮我|且|也))+", "", clause.strip())
               for clause in re.split(r"[\n，,。;；、！？!?]+|同时|并且|而且|但是|不过|然后|但|并", feedback)
               if clause.strip()]
    known, allowed, required, preserved = {}, set(), set(), set()
    unknown, keep_others, problem = [], False, None
    for clause in clauses:
        compact = re.sub(r"\s", "", clause).rstrip(".")
        if re.fullmatch(r"(?:保持|保留|维持)?(?:其他|其余)(?:参数|特征|部分)?(?:都)?(?:不变|保持不变|别变|不要变)", compact):
            keep_others = True
            continue
        preserve = next((fields for name, fields in _PRESERVE_NAMES.items()
                         if re.fullmatch(rf"(?:{re.escape(name)}(?:也|都)?(?:不变|保持不变|别变|不要变)|"
                                         rf"(?:保持|保留|维持){re.escape(name)}(?:不变)?|"
                                         rf"(?:不要|别)(?:改变|调整|修改){re.escape(name)})", compact)), None)
        if preserve:
            preserved.update(preserve)
            continue
        # Negated, disjunctive and unrecognised preservation requests must not be
        # mistaken for positive change cues (e.g. “不要更低沉”).
        if re.search(r"不|别|保持|保留|维持|锁定|或者|还是|或", compact):
            problem = "这段意见包含尚不明确的否定、保留或二选一要求。请分开说明要改什么、要保留什么。"
            break
        patch = _local_revision(compact, params)
        if patch:
            for field, value in patch.items():
                if field in known and known[field] != value:
                    problem = "同一特征同时收到相反的修改要求。请先选择一个修改方向。"
                known[field] = value
                allowed.add(field)
                required.add(field)
        else:
            fields = _clause_fields(compact)
            unknown.append(compact)
            if len(fields) > 1:
                problem = "这一句涉及多种特征，无法可靠区分修改目标。请用逗号分开描述每项修改。"
            if not fields and len(clauses) > 1:
                problem = "部分意见尚不能映射到参数，未执行其他修改。请先明确这部分对应音高、起伏、间隔、尾音还是音量。"
            allowed.update(fields)
            required.update(fields)
    if keep_others:
        if not allowed:
            problem = "请先明确要改变的特征，再保留其余参数。"
        else:
            preserved.update(set(params.to_dict()) - allowed)
    if required & preserved:
        problem = "同一特征既要求修改又要求保留。请先确认这项特征是否需要改变。"
    if not known and not unknown and problem is None:
        problem = "已了解要保留的特征。请再指定一项要改变的听感，或直接确认当前声音。"
    return {"patch": None if unknown else known, "known_patch": known,
            "allowed": allowed, "required": required, "preserved": preserved,
            "problem": problem}


def _inactive_change(params, patch):
    """Explain an inactive DSP control instead of silently enabling other fields."""
    updated = apply_patch(params, patch)
    fields = {f for f, value in patch.items() if value != getattr(params, f)}
    if fields & {"pulse_interval_s", "decay_s"} and updated.kind not in ("knock", "mix"):
        return "当前候选没有敲击成分，敲击间隔和尾音衰减不会生效。请先建立间歇敲击原型。"
    if "harmonics" in fields and updated.kind not in ("hum", "mix"):
        return "当前原型没有可调谐波数量。请修改音高、噪声比例或选择嗡鸣原型。"
    if fields & {"frequency_hz", "harmonics"} and updated.noise_mix == 1:
        return "当前声音为纯噪声，音调成分未启用。请先明确是否降低噪声比例，再调整音高或谐波。"
    if fields & {"band_low_hz", "band_high_hz"} and updated.noise_mix == 0:
        return "当前噪声成分未启用，改变噪声频带不会生效。请先明确是否增加噪声比例。"
    if "modulation_hz" in fields and updated.modulation_depth == 0:
        return "当前起伏深度为零，改变起伏速度不会生效。请先明确是否启用起伏。"
    if "modulation_depth" in fields and updated.modulation_hz == 0:
        return "当前起伏速度为零，改变起伏深度不会生效。请先明确是否启用起伏。"
    return None


def plan_revision(feedback, params, locked_fields: Iterable[str] = (), use_model=False):
    feedback = _validate_text(feedback, "修改意见")
    if isinstance(params, dict):
        params = SoundParams.from_dict(params)
    # Validate lock names even when the parser will only return a clarification.
    if isinstance(locked_fields, (str, bytes)):
        raise ValueError("锁定字段必须为字段名列表")
    locked_fields = tuple(locked_fields)
    apply_patch(params, {}, locked_fields)
    intent = _revision_intent(feedback, params)
    preserved = sorted(intent["preserved"])
    effective_locks = sorted(set(locked_fields) | set(preserved))
    if intent["problem"]:
        return _result(clarification=intent["problem"], preserved_fields=preserved)
    if intent["required"] & set(effective_locks):
        return _result(clarification="修改触及已锁定或明确保留的特征。请先解除对应锁定，或明确新的修改要求。",
                       preserved_fields=preserved)
    patch = intent["patch"]
    notice, mode = LOCAL_NOTICE, "local"
    model_call = None
    allowed = intent["allowed"]
    if use_model:
        from .adapters import AdapterError, plan_parameters
        try:
            suggestion = plan_parameters({"task": "revision", "feedback": feedback,
                                          "current_params": params.to_dict(),
                                          "locked_fields": effective_locks,
                                          "preserved_fields": preserved,
                                          "required_fields": sorted(intent["required"]),
                                          "allowed_fields": sorted((allowed or set(_CUES)) - set(effective_locks)),
                                          "parameter_contract": model_contract()})
            if suggestion["clarification"]:
                return _result(mode="model", notice="模型要求先澄清。", clarification=suggestion["clarification"],
                               model_call=suggestion.get("_meta"), preserved_fields=preserved)
            suggested_patch = suggestion["patch"]
            expected = set(patch) if patch else (allowed or set(_CUES))
            if not suggested_patch or not set(suggested_patch) <= expected:
                raise AdapterError("模型试图修改无关特征")
            changed = {f for f, value in suggested_patch.items() if value != getattr(params, f)}
            if not intent["required"] <= changed:
                raise AdapterError("模型遗漏明确要求修改的特征")
            if not allowed and len(suggested_patch) > 1:
                raise AdapterError("没有明确特征词时需要一次澄清一个修改")
            apply_patch(params, suggested_patch, effective_locks)
            if intent["known_patch"]:
                for field, local_value in intent["known_patch"].items():
                    current = getattr(params, field)
                    if (suggested_patch.get(field, current) - current) * (local_value - current) <= 0:
                        raise AdapterError("模型修改方向与明确意见不一致")
            patch = suggested_patch
            model_call = suggestion.get("_meta")
            notice, mode = "模型修改建议已通过字段、锁定状态和数值范围校验。", "model"
        except (AdapterError, ValueError, TypeError):
            mode, notice = "fallback", _fallback_notice("配置缺失、调用失败或修改不符合约束")
    if patch is None:
        return _result(mode=mode, notice=notice,
                       clarification="这段描述暂不能可靠映射，未执行部分修改。请指定音高、起伏速度、敲击间隔、尾音或音量，也可用参数表单比较。例如“更低沉”只改变音高。",
                       preserved_fields=preserved)
    try:
        updated = apply_patch(params, patch, effective_locks)
        inactive = _inactive_change(params, patch)
    except ValueError:
        return _result(mode=mode, notice=notice, clarification="修改触及已锁定特征或参数边界。请解锁对应特征，或通过表单选择有效数值。",
                       preserved_fields=preserved)
    if inactive:
        return _result(mode=mode, notice=notice, clarification=inactive, preserved_fields=preserved,
                       model_call=model_call)
    if updated == params:
        return _result(mode=mode, notice=notice, clarification="该特征已处于当前值；请使用参数表单指定期望值。",
                       preserved_fields=preserved)
    return _result(patch=patch, mode=mode, notice=notice, model_call=model_call, preserved_fields=preserved)
