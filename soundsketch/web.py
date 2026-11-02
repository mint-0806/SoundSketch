"""Loopback-only Flask API. No automatic uploads or cloud synthesis."""
from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, jsonify, request, send_file
from werkzeug.exceptions import HTTPException

from . import adapters
from .session import SessionStore, now, valid_id
from .proposals import ProposalStore


def create_app(data_dir=None):
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.config.update(MAX_CONTENT_LENGTH=21 * 1024 * 1024, JSON_AS_ASCII=False)
    app.json.ensure_ascii = False
    store = SessionStore(data_dir or os.environ.get("SOUNDSKETCH_DATA_DIR", "data"))
    app.extensions["soundsketch_store"] = store
    proposals = ProposalStore(store)
    app.extensions["soundsketch_proposals"] = proposals

    @app.before_request
    def local_only():
        # Reject DNS rebinding and cross-site writes to the local service.
        if request.host.split(":")[0] not in ("127.0.0.1", "localhost"):
            return jsonify(error="本原型仅接受本机访问"), 403
        if request.method == "POST":
            origin = request.headers.get("Origin")
            if origin and urlsplit(origin).netloc != request.host:
                return jsonify(error="不接受跨站写入"), 403
            if request.headers.get("X-SoundSketch") != "1":
                return jsonify(error="缺少本地操作标识"), 403

    @app.after_request
    def headers(response):
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "img-src 'self' data:; media-src 'self'; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
        )
        response.headers["Cache-Control"] = "no-store"
        return response

    def body(allowed):
        data = request.get_json()
        if not isinstance(data, dict):
            raise ValueError("请求必须为 JSON 对象")
        if set(data) - set(allowed):
            raise ValueError("请求包含不支持的字段")
        return data

    @app.errorhandler(ValueError)
    def invalid(error):
        return jsonify(error=str(error)), 400

    @app.errorhandler(FileNotFoundError)
    def missing(error):
        return jsonify(error="文件或会话不存在"), 404

    @app.errorhandler(HTTPException)
    def http_error(error):
        messages = {413: "文件过大；请使用不超过 20 MiB 的 WAV", 415: "请提交 JSON 请求", 404: "未找到此资源"}
        return jsonify(error=messages.get(error.code, "请求格式或路径无效")), error.code

    @app.errorhandler(Exception)
    def unexpected(error):
        # Do not reflect exception bodies, URLs, local paths or environment values.
        app.logger.error("SoundSketch operation failed (%s)", type(error).__name__)
        return jsonify(error="操作失败，未生成或确认新结果。请检查输入或本地存储。"), 500

    @app.get("/")
    def index():
        return app.send_static_file("index.html")

    @app.get("/api/status")
    def status():
        planner_ready = adapters.planner_available()
        audio_ready = adapters.audio_available()
        try:
            planner_config = adapters.planner_config()
        except adapters.AdapterError:
            planner_config = None
        return jsonify(mode="optional_api" if planner_ready else "local", planner_available=planner_ready,
                       audio_available=audio_ready, planner=planner_config,
                       notice="默认本地表单 + DSP 降级模式；外部接口仅在勾选授权并执行操作时调用。",
                       synthesis="local_dsp", paid_generation_enabled=False,
                       reference_limits={"format": "PCM WAV", "max_mb": 20, "max_seconds": 30})

    @app.route("/api/sessions", methods=["GET", "POST"])
    def sessions():
        with store.lock:
            if request.method == "GET":
                return jsonify(sessions=store.list_sessions())
            data = body({"description", "kind", "scene", "timing", "use_model"})
            if "description" not in data:
                raise ValueError("请填写声音描述")
            return jsonify(store.create(**data)), 201

    @app.post("/api/plans")
    def preview_initial():
        with store.lock:
            data = body({"description", "kind", "scene", "timing", "use_model"})
            if "description" not in data:
                raise ValueError("请填写声音描述")
            return jsonify(proposals.initial(**data))

    @app.post("/api/plans/<plan_id>/apply")
    def apply_plan(plan_id):
        with store.lock:
            body(set())
            return jsonify(proposals.apply(plan_id))

    @app.get("/api/sessions/<session_id>")
    def get_session(session_id):
        with store.lock:
            return jsonify(store.load(session_id))

    @app.post("/api/sessions/<session_id>/<action>")
    def mutate(session_id, action):
        with store.lock:
            session = store.load(session_id)
            if action == "reference":
                upload = request.files.get("file")
                if upload is None:
                    raise ValueError("请选择原始 PCM WAV 参考录音")
                return jsonify(store.add_reference(session, upload.filename, upload.read(20 * 1024 * 1024 + 1)))
            if action == "select":
                data = body({"candidate_id"})
                return jsonify(store.select(session, data.get("candidate_id")))
            if action == "revise":
                return jsonify(store.revise(session, **body({"feedback", "patch", "locked_fields", "use_model"})))
            if action == "preview":
                return jsonify(proposals.revision(session, **body({"feedback", "patch", "locked_fields", "use_model"})))
            if action in ("restore", "undo"):
                data = body({"version_id"} if action == "restore" else set())
                if action == "restore" and "version_id" not in data:
                    raise ValueError("请选择历史版本")
                return jsonify(store.restore(session, data.get("version_id")))
            if action == "confirm":
                return jsonify(store.confirm(session, **body({"description"})))
            if action == "understand":
                data = body({"reference_id", "consent"})
                if data.get("consent") is not True:
                    raise ValueError("发送原始录音到 Gemini 前必须明确同意")
                reference = next((r for r in session["references"] if r["id"] == data.get("reference_id")), None)
                if reference is None or reference["category"] != "observation":
                    raise ValueError("仅允许分析用户提供的原始参考录音")
                if not adapters.audio_available():
                    return jsonify(session=session, error="Gemini 音频接口未配置；仅保留本地信号分析，未发送录音。"), 503
                path = store.directory(session_id) / "references" / f"{valid_id(reference['id'])}.wav"
                session["events"].append({"type": "external_audio_consent", "provider": "Gemini",
                                          "reference_id": reference["id"], "sha256": reference["sha256"], "at": now()})
                store.save(session)
                try:
                    result = adapters.understand_audio(path, consent=True)
                except Exception:
                    session["events"].append({"type": "external_audio_failure", "reference_id": reference["id"], "at": now()})
                    store.save(session)
                    return jsonify(session=session, error="Gemini 调用失败，未产生可用听觉描述；本地分析仍保留。"), 502
                reference["external_analyses"].append({"result": result, "at": now(), "provider": "Gemini"})
                store.save(session)
                return jsonify(session=session, result=result)
            return jsonify(error="未知操作"), 404

    @app.get("/api/sessions/<session_id>/audio/<category>/<audio_id>")
    def audio(session_id, category, audio_id):
        valid_id(audio_id)
        if category not in ("generated", "references"):
            raise ValueError("无效的音频类别")
        with store.lock:
            session = store.load(session_id)
            permitted = ([r["id"] for r in session["references"]] if category == "references" else
                         [c["id"] for v in session["versions"] for c in v["candidates"]])
            if audio_id not in permitted:
                raise FileNotFoundError()
            return send_file(store.directory(session_id) / category / f"{audio_id}.wav", mimetype="audio/wav", conditional=True)

    @app.get("/api/sessions/<session_id>/export")
    def export(session_id):
        with store.lock:
            session = store.load(session_id)
            return send_file(store.export(session), mimetype="application/zip", as_attachment=True,
                             download_name=f"soundsketch-{session_id[:8]}.zip")

    return app
