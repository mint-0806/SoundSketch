"""Opt-in model boundaries. No network requests happen on import or availability checks."""
from __future__ import annotations

import base64
import http.client
import json
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.parse
import urllib.request


class AdapterError(RuntimeError):
    """A deliberately sanitized, user-safe provider error."""


_PLANNER_PRESETS = {
    "compatible": {"base_url": "https://api.openai.com/v1", "model": "", "thinking": "provider_default"},
    "mimo": {"base_url": "https://api.xiaomimimo.com/v1", "model": "mimo-v2.6-flash", "thinking": "disabled"},
    "deepseek": {"base_url": "https://api.deepseek.com", "model": "deepseek-flash", "thinking": "disabled"},
}


def planner_config():
    """Return validated public configuration, never reading or including an API key.

    Provider presets describe the request protocol; explicit model/base overrides
    retain that protocol. Compatible mode requires an explicit model name.
    """
    provider = os.environ.get("SOUNDSKETCH_PLANNER_PROVIDER", "compatible").strip().lower()
    if provider not in _PLANNER_PRESETS:
        raise AdapterError("规划提供商配置无效，请选择 compatible、mimo 或 deepseek。")
    preset = _PLANNER_PRESETS[provider]
    model = os.environ.get("SOUNDSKETCH_PLANNER_MODEL", "").strip() or preset["model"]
    if not re.fullmatch(r"[A-Za-z0-9._:/-]{1,150}", model):
        raise AdapterError("请配置有效的规划模型名。")
    base = os.environ.get("SOUNDSKETCH_PLANNER_BASE_URL", "").strip() or preset["base_url"]
    return {"provider": provider, "model": model, "base_url": _safe_base_url(base),
            "max_output_tokens": 1024, "thinking": preset["thinking"]}


def planner_available():
    try:
        planner_config()
    except AdapterError:
        return False
    return bool(os.environ.get("SOUNDSKETCH_PLANNER_API_KEY", "").strip())


def audio_available():
    return bool(os.environ.get("GEMINI_API_KEY") and os.environ.get("GEMINI_MODEL"))


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise AdapterError("服务要求重定向，已停止发送以保护密钥。")


def _safe_base_url(base):
    if not isinstance(base, str) or any(ord(char) <= 32 for char in base):
        raise AdapterError("API 地址无效。")
    try:
        parsed = urllib.parse.urlsplit(base)
        # urlsplit delays invalid-port errors until .port is accessed.
        parsed.port
    except (ValueError, TypeError):
        raise AdapterError("API 地址无效。") from None
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise AdapterError("API 地址不能包含凭据、查询或片段。")
    if parsed.scheme != "https" and not (parsed.scheme == "http" and parsed.hostname in ("127.0.0.1", "localhost", "::1")):
        raise AdapterError("外部 API 必须使用 HTTPS。")
    if not parsed.hostname:
        raise AdapterError("API 地址无效。")
    return base.rstrip("/")


def _post_json(url, payload, headers):
    data = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    request = urllib.request.Request(url, data=data,
                                     headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.build_opener(_NoRedirect()).open(request, timeout=25) as response:
            body = response.read(1024 * 1024 + 1)
            if len(body) > 1024 * 1024:
                raise AdapterError("服务响应过大。")
            return json.loads(body.decode("utf-8"))
    except AdapterError:
        raise
    except (urllib.error.URLError, http.client.HTTPException, TimeoutError, OSError, UnicodeError, ValueError):
        # HTTPError strings often contain provider URLs; never return/log them.
        raise AdapterError("外部服务调用失败或响应无效。") from None


_PLANNER_SYSTEM = """你是听感参数规划器，只描述用户听到的声音，不判断真实设备、地点或故障。
只返回一个 JSON 对象，键为 kind、patch、clarification；kind 为 hum/whistle/knock 或 null；
patch 为需要修改的字段到数值的对象，clarification 为必要的中文追问字符串或 null。
初始原型：hum 嗡鸣，whistle 尖啸，knock 间歇敲击。声源可以未知；不要凭空增加用户未提到的特征。
可调字段：frequency_hz 音高, harmonics 整数谐波数量, noise_mix 噪声比例,
band_low_hz/band_high_hz 噪声截止频率, modulation_hz 起伏频率,
modulation_depth 起伏深度, pulse_interval_s 敲击间隔, decay_s 衰减, gain 数字幅值。
不要修改 kind/seed/sample_rate/duration_s。遵守 chosen_kind、allowed_fields、locked_fields。
preserved_fields 是用户在本轮明确要求保持不变的字段；required_fields 中的每项都必须实际改变，不能遗漏。
“其余不变”意味着只改变明确要求的字段；同一特征既要求修改又要求保留，或方向互相矛盾时追问。
修改阶段 patch 仅含用户明确要求修改的字段，其余维持不变。
更低沉仅降低 frequency_hz；起伏慢一点仅降低 modulation_hz；敲击间隔更长仅增加 pulse_interval_s。
不能可靠映射、含糊、没有对应成分、或需要修改锁定特征时返回空 patch 并追问。
任何测量数值必须来自程序，不得声称听感参数是实测声压级。"""


def _parse_suggestion(value):
    if not isinstance(value, dict) or set(value) != {"kind", "patch", "clarification"}:
        raise AdapterError("模型响应结构不符合要求。")
    if value["kind"] not in (None, "hum", "whistle", "knock") or not isinstance(value["patch"], dict):
        raise AdapterError("模型响应字段类型无效。")
    clarification = value["clarification"]
    if clarification is not None and (not isinstance(clarification, str) or not clarification.strip() or len(clarification) > 1000):
        raise AdapterError("模型追问格式无效。")
    if clarification and value["patch"]:
        raise AdapterError("模型不能同时要求澄清和应用修改。")
    allowed = {"frequency_hz", "harmonics", "noise_mix", "band_low_hz", "band_high_hz",
               "modulation_hz", "modulation_depth", "pulse_interval_s", "decay_s", "gain"}
    if not set(value["patch"]) <= allowed:
        raise AdapterError("模型返回了不允许修改的字段。")
    return value


def plan_parameters(context):
    """Send description/parameter JSON only. Never attaches recordings."""
    config = planner_config()
    key = os.environ.get("SOUNDSKETCH_PLANNER_API_KEY", "").strip()
    if not key:
        raise AdapterError("未配置规划模型 API 密钥。")
    payload = {"model": config["model"], "temperature": 0, "stream": False,
               "response_format": {"type": "json_object"},
               "messages": [{"role": "system", "content": _PLANNER_SYSTEM},
                            {"role": "user", "content": json.dumps(context, ensure_ascii=False)}]}
    if config["provider"] in ("mimo", "deepseek"):
        payload["thinking"] = {"type": "disabled"}
    limit_field = "max_completion_tokens" if config["provider"] == "mimo" else "max_tokens"
    payload[limit_field] = config["max_output_tokens"]
    started = time.perf_counter()
    result = _post_json(config["base_url"] + "/chat/completions", payload,
                        {"Authorization": "Bearer " + key})
    elapsed_ms = round((time.perf_counter() - started) * 1000, 2)
    try:
        choice = result["choices"][0]
        if choice["finish_reason"] != "stop":
            raise AdapterError("规划响应未完整结束或被拒绝，未采用模型结果。")
        message = choice["message"]
        if message.get("refusal"):
            raise AdapterError("规划请求被模型拒绝，未采用模型结果。")
        content = message["content"]
        if not isinstance(content, str) or len(content) > 16000:
            raise ValueError
        suggestion = json.loads(content)
    except (KeyError, TypeError, IndexError, ValueError, AttributeError):
        raise AdapterError("规划模型没有返回有效 JSON。") from None
    validated = _parse_suggestion(suggestion)
    # Model identifies the requested alias, not a verified provider model version.
    metadata = {"provider": config["provider"], "model": config["model"], "latency_ms": elapsed_ms}
    usage = result.get("usage")
    if isinstance(usage, dict):
        for output_name, provider_name in (("input_tokens", "prompt_tokens"),
                                           ("output_tokens", "completion_tokens"),
                                           ("total_tokens", "total_tokens")):
            value = usage.get(provider_name)
            if type(value) is int and 0 <= value <= 10 ** 12:
                metadata[output_name] = value
    return {**validated, "_meta": metadata}


_AUDIO_PROMPT = """仅描述这份原始参考录音中实际可听到的事件、音色和时间规律。声源不明确时明确写未知。
不要给出确定设备、具体地点、故障诊断；不要虚构分贝值、频率测量值或其他测量。
返回 JSON 对象：description 为中文听觉观察文字，events 为中文事件字符串数组，
uncertainty 为中文不确定性文字。不能清晰听到时直接说明。用户的合成音频不是证据。"""


def understand_audio(path, consent=False):
    """Caller must select an original observation. Explicit per-call consent is mandatory."""
    if consent is not True:
        raise AdapterError("发送录音前必须明确同意将所选原始录音上传至 Gemini。")
    if not audio_available():
        raise AdapterError("Gemini 音频理解不可用：未配置密钥和模型名；保留本地信号分析。")
    model = os.environ["GEMINI_MODEL"].removeprefix("models/")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,100}", model):
        raise AdapterError("Gemini 模型名无效。")
    path = Path(path)
    if path.suffix.lower() != ".wav":
        raise AdapterError("首版只向 Gemini 发送已验证的 WAV 原始录音。")
    try:
        if path.stat().st_size > 10 * 1024 * 1024:
            raise AdapterError("录音超过 Gemini 内联上传的本项目 10 MiB 上限；请仅使用本地分析。")
        data = path.read_bytes()
    except OSError:
        raise AdapterError("无法读取所选原始录音。") from None
    # Format validation only. The session layer must enforce original-observation provenance.
    from .analysis import analyze_wav
    try:
        analyze_wav(path)
    except (ValueError, OSError):
        raise AdapterError("所选录音未通过 WAV 校验。") from None
    payload = {"contents": [{"parts": [{"text": _AUDIO_PROMPT},
                   {"inline_data": {"mime_type": "audio/wav", "data": base64.b64encode(data).decode("ascii")}}]}],
               "generationConfig": {"responseMimeType": "application/json", "temperature": 0}}
    result = _post_json(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                        payload, {"x-goog-api-key": os.environ["GEMINI_API_KEY"]})
    try:
        parts = result["candidates"][0]["content"]["parts"]
        content = "".join(part.get("text", "") for part in parts)
        parsed = json.loads(content)
        if not isinstance(parsed, dict) or set(parsed) != {"description", "events", "uncertainty"}:
            raise ValueError
        if not isinstance(parsed["description"], str) or not parsed["description"].strip() or len(parsed["description"]) > 6000:
            raise ValueError
        if not isinstance(parsed["uncertainty"], str) or not parsed["uncertainty"].strip() or len(parsed["uncertainty"]) > 4000:
            raise ValueError
        if not isinstance(parsed["events"], list) or len(parsed["events"]) > 30 or not all(isinstance(v, str) and len(v) <= 1000 for v in parsed["events"]):
            raise ValueError
    except (KeyError, TypeError, IndexError, ValueError):
        raise AdapterError("Gemini 未返回有效听觉观察；没有采用其结果。") from None
    return {**parsed, "category": "observation", "provider": "gemini", "model": model,
            "method": "可选模型对原始参考录音的听觉描述，非仪器测量，须人工核对。",
            "source": "original_reference_recording", "measurement": False}


class ElevenLabsSoundEffectsAdapter:
    """Extension contract only; deliberately cannot make a paid request in v0.1."""
    def generate(self, description, *, consent=False):
        raise AdapterError("ElevenLabs Sound Effects 仅预留接口，首版未启用任何付费生成。")


class MaterialLibraryAdapter:
    def find(self, description):
        return []  # Empty means no evidence-backed matching material exists yet.
