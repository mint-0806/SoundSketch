"use strict";

const $ = (id) => document.getElementById(id);
let currentSession = null;
let serviceStatus = { planner_available: false, audio_available: false };
let busy = false;
let paramBaseline = {};
let locks = new Set();
let comparedVersionId = null;
let pendingPlan = null;
let previewEpoch = 0;
const kindNames = { hum: "嗡鸣", whistle: "尖啸", knock: "间歇敲击", mix: "混合" };
const fieldSpecs = [
  ["frequency_hz", "主要音调 / Hz", 1],
  ["modulation_hz", "起伏频率 / Hz", 0.05],
  ["modulation_depth", "起伏深度 / 0–1", 0.05],
  ["pulse_interval_s", "敲击间隔 / 秒", 0.05],
  ["decay_s", "衰减时长 / 秒", 0.01],
  ["noise_mix", "噪声混合比例 / 0–1", 0.05],
  ["harmonics", "谐波数量", 1],
  ["band_low_hz", "噪声低频截止 / Hz", 10],
  ["band_high_hz", "噪声高频截止 / Hz", 10],
  ["gain", "数字增益", 0.01],
  ["duration_s", "示意时长 / 秒", 0.5],
  ["seed", "随机种子", 1],
];

function node(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text !== undefined && text !== null) el.textContent = String(text);
  return el;
}

function showMessage(text, error = false) {
  $("message").textContent = text;
  $("message").className = error ? "message error" : "message";
  $("message").hidden = !text;
}

async function api(path, data, method = "POST") {
  const options = { method, headers: {} };
  if (method !== "GET") options.headers["X-SoundSketch"] = "1";
  if (data instanceof FormData) options.body = data;
  else if (data !== undefined) {
    options.headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(data);
  }
  const response = await fetch(path, options);
  let result;
  try { result = await response.json(); }
  catch (_) { throw new Error(`服务未返回有效结果（HTTP ${response.status}），请确认本地服务仍在运行。`); }
  if (!response.ok) {
    const reason = result.error || result.message || `请求失败（HTTP ${response.status}）`;
    throw new Error(typeof reason === "string" ? reason : JSON.stringify(reason));
  }
  return result;
}

async function runAction(work, pending = "正在处理，请稍候…") {
  if (busy) return;
  busy = true;
  document.body.classList.add("busy");
  document.body.setAttribute("aria-busy", "true");
  showMessage(pending);
  try { await work(); }
  catch (error) {
    showMessage(error.message || "操作失败，请检查输入后重试。", true);
    $("message").scrollIntoView({ behavior: "smooth", block: "nearest" });
  } finally {
    busy = false;
    document.body.classList.remove("busy");
    document.body.removeAttribute("aria-busy");
  }
}

function endpoint(suffix = "") {
  if (!currentSession) throw new Error("请先创建或打开一个会话。");
  return `/api/sessions/${encodeURIComponent(currentSession.id)}${suffix}`;
}

function currentVersion() {
  return currentSession?.versions.find((v) => v.id === currentSession.current_version_id);
}

function selectedCandidate() {
  const version = currentVersion();
  return version?.candidates.find((c) => c.id === version.selected_id);
}

function audioPlayer(url, label) {
  const audio = node("audio");
  audio.controls = true;
  audio.preload = "none";
  audio.volume = 0.15;
  audio.autoplay = false;
  audio.setAttribute("aria-label", label);
  audio.src = url;
  audio.addEventListener("play", () => {
    document.querySelectorAll("audio").forEach((other) => { if (other !== audio) other.pause(); });
  });
  audio.addEventListener("error", () => {
    showMessage("这段音频无法加载。请确认本地 WAV 文件仍在会话目录中，或重新生成候选。", true);
  });
  return audio;
}

function formatNumber(value, digits = 2) {
  return Number.isFinite(Number(value)) ? Number(value).toLocaleString("zh-CN", { maximumFractionDigits: digits }) : "未知";
}

function fieldTitle(key) {
  return fieldSpecs.find((field) => field[0] === key)?.[1] || ({ kind: "声音原型", sample_rate: "采样率 / Hz" })[key] || key;
}

function fieldValue(key, value) {
  return key === "kind" ? (kindNames[value] || value) : formatNumber(value, 8);
}

function relevantField(key, params) {
  if (["pulse_interval_s", "decay_s"].includes(key)) return ["knock", "mix"].includes(params.kind);
  if (key === "harmonics") return ["hum", "mix"].includes(params.kind);
  return true;
}

function inactiveReason(key, params) {
  if (["frequency_hz", "harmonics"].includes(key) && params.noise_mix === 1) return "纯噪声时音调成分未启用";
  if (["band_low_hz", "band_high_hz"].includes(key) && params.noise_mix === 0) return "噪声比例为 0，当前不生效";
  if (key === "modulation_hz" && params.modulation_depth === 0) return "起伏深度为 0，当前不生效";
  if (key === "modulation_depth" && params.modulation_hz === 0) return "起伏频率为 0，当前不生效";
  return "";
}

function clearPlan(reason = "") {
  const existed = Boolean(pendingPlan);
  pendingPlan = null;
  previewEpoch += 1;
  $("plan-panel").hidden = true;
  if (!currentSession) $("empty-state").hidden = false;
  if (existed && reason) showMessage(reason);
}

function planTable(plan) {
  const wrap = node("div", "plan-table-wrap");
  const table = node("table", "plan-table");
  const head = node("thead"), header = node("tr");
  const revision = plan.task === "revision";
  for (const title of revision ? ["合成特征", "当前", "改为"] : ["合成特征", "基准设定", "依据"]) header.append(node("th", null, title));
  head.append(header);
  const body = node("tbody");
  if (revision) {
    for (const change of (plan.changes || [])) {
      const row = node("tr");
      row.append(node("th", null, fieldTitle(change.field)), node("td", "before-value", fieldValue(change.field, change.before)), node("td", "after-value", fieldValue(change.field, change.after)));
      body.append(row);
    }
  } else {
    const params = plan.params || {};
    for (const key of ["kind", ...fieldSpecs.map(([field]) => field), "sample_rate"]) {
      if (!(key in params) || !relevantField(key, params)) continue;
      const changed = Object.hasOwn(plan.patch || {}, key);
      const row = node("tr");
      const title = node("th", null, fieldTitle(key));
      const inactive = inactiveReason(key, params);
      if (inactive) title.append(node("small", "inactive-note", inactive));
      const source = key === "kind" ? "所选 / 解析的听感原型" : changed ? (plan.mode === "model" ? "模型建议 · 已校验" : "本地规则映射") : "原型默认值 · 尚待试听";
      row.append(title, node("td", changed ? "after-value" : "", fieldValue(key, params[key])), node("td", "plan-source", source));
      body.append(row);
    }
  }
  table.append(head, body);
  wrap.append(table);
  return wrap;
}

function renderPlan(plan, context) {
  pendingPlan = plan;
  const revision = plan.task === "revision";
  const panel = $("plan-panel");
  if (revision) $("parameter-details").before(panel);
  else document.querySelector(".work-area").prepend(panel);
  panel.hidden = false;
  if (!currentSession) $("empty-state").hidden = true;
  $("plan-title").textContent = revision ? "先看这次将改变什么" : "从文字得到的合成方案";
  $("plan-mode").textContent = plan.mode === "model" ? "文字模型 → 参数" : plan.mode === "fallback" ? "调用失败 · 本地回退" : "本地规则 · 降级模式";
  $("plan-note").textContent = plan.notice || "参数已通过本地校验，尚未生成声音。";
  if (plan.model_call) {
    const call = plan.model_call;
    $("plan-note").textContent += ` ${call.provider} / ${call.model} · ${formatNumber(call.latency_ms / 1000)} 秒` + (Number.isInteger(call.total_tokens) ? ` · ${call.total_tokens} tokens` : "");
  }
  $("plan-context").textContent = context;
  $("plan-content").replaceChildren();
  if (plan.clarification) $("plan-content").append(node("p", "clarification", plan.clarification));
  else $("plan-content").append(planTable(plan));
  const preserved = [...new Set([...(plan.locked_fields || []), ...(plan.preserved_fields || [])])];
  $("plan-preserved").textContent = plan.clarification ? "声音与版本保持不变。请补充描述后重新解析。" : revision ? `未列出的参数全部继承当前候选，包括随机种子。${preserved.length ? `明确保留 / 锁定：${preserved.map(fieldTitle).join("、")}。` : ""}` : "未指定的数值采用原型默认值，供试听比较；不会把描述当作精确测量。生成时只改变一个主要特征，提供 2–3 个对照候选。";
  $("apply-plan").hidden = Boolean(plan.clarification) || !plan.id;
  $("apply-plan").textContent = revision ? "应用改动并生成" : "按方案生成候选";
  panel.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function renderVersionPlan(version) {
  const plan = version.plan;
  $("version-plan").hidden = !plan;
  $("version-plan-content").replaceChildren();
  if (!plan) return;
  $("version-plan-content").append(node("p", "microcopy", "历史规划记录，仅说明合成设定的形成过程。不是现场观测或声源证据。"));
  if (plan.task === "initial") $("version-plan-content").append(node("p", "microcopy", "这里记录基准候选的设定，其他初始候选围绕一项特征作对照。选中声音的实际参数可在下方参数表单查看。"));
  if (plan.task && (plan.params || plan.changes)) $("version-plan-content").append(planTable(plan));
  else $("version-plan-content").append(node("pre", "plan-json", JSON.stringify(plan, null, 2)));
}

function renderCandidate(candidate, index, selectedId) {
  const card = node("article", `candidate${candidate.id === selectedId ? " selected" : ""}`);
  const top = node("div", "candidate-top");
  top.append(node("span", "candidate-letter", String.fromCharCode(65 + index)), node("span", "tag", "DSP · 合成示意"));
  card.append(top, node("h3", null, candidate.label || `候选 ${index + 1}`));
  card.append(node("p", "candidate-description", candidate.description || "可控 DSP 生成的听感示意。"));
  const p = candidate.params;
  const metric = p.kind === "knock" ? `间隔 ${formatNumber(p.pulse_interval_s)} s` : `音调 ${formatNumber(p.frequency_hz)} Hz`;
  card.append(node("div", "candidate-metric", metric));
  card.append(audioPlayer(candidate.audio_url, `试听${candidate.label || "合成候选"}`));
  const button = node("button", `full selection-button ${candidate.id === selectedId ? "primary" : "secondary"}`, candidate.id === selectedId ? "已选中，继续调整" : "选择这个听感");
  button.type = "button";
  button.addEventListener("click", () => runAction(async () => {
    adoptSession(await api(endpoint("/select"), { candidate_id: candidate.id }));
    showMessage("已选择候选。可以提出一项修改，或直接确认导出。");
  }));
  card.append(button);
  const details = node("details");
  details.append(node("summary", null, "来源、依据与不确定性"));
  details.append(node("p", null, "来源：本地 DSP 合成。不是现场观测或真实案例。"));
  const provenance = candidate.provenance || {};
  const list = node("ul");
  const evidence = Array.isArray(provenance.evidence) ? provenance.evidence : [provenance.evidence].filter(Boolean);
  evidence.forEach((item) => list.append(node("li", null, typeof item === "string" ? item : JSON.stringify(item))));
  if (evidence.length) details.append(list);
  details.append(node("p", null, provenance.uncertainty || "声源未知。试听匹配仅表达主观听感，不能确定设备、地点或故障。"));
  card.append(details);
  return card;
}

function renderParameters(candidate) {
  paramBaseline = candidate ? { ...candidate.params } : {};
  $("parameter-grid").replaceChildren();
  if (!candidate) return;
  fieldSpecs.forEach(([key, title, step]) => {
    if (!(key in candidate.params) || !relevantField(key, candidate.params)) return;
    const item = node("div", `parameter-field${locks.has(key) ? " locked" : ""}`);
    const label = node("label", null, title);
    label.htmlFor = `param-${key}`;
    const input = node("input");
    input.type = "number";
    input.id = `param-${key}`;
    input.step = String(step);
    input.value = candidate.params[key];
    input.required = true;
    input.disabled = locks.has(key);
    const lockLabel = node("label", "lock-line");
    const lock = node("input");
    lock.type = "checkbox";
    lock.checked = locks.has(key);
    lock.setAttribute("aria-label", `锁定${title}`);
    lock.addEventListener("change", () => {
      clearPlan("锁定状态已改变，请重新预览方案。");
      if (lock.checked) locks.add(key); else locks.delete(key);
      input.disabled = lock.checked;
      if (lock.checked) input.value = paramBaseline[key];
      item.classList.toggle("locked", lock.checked);
      try { localStorage.setItem(`soundsketch.locks.${currentSession.id}`, JSON.stringify([...locks])); } catch (_) { /* Optional local preference. */ }
    });
    lockLabel.append(lock, node("span", null, "锁定这项特征"));
    item.append(label, input, lockLabel);
    const inactive = inactiveReason(key, candidate.params);
    if (inactive) item.append(node("p", "inactive-note", inactive));
    $("parameter-grid").append(item);
  });
  $("parameter-grid").append(node("p", "microcopy", `原型：${kindNames[candidate.params.kind] || candidate.params.kind} · 采样率：${candidate.params.sample_rate} Hz。单项修改保持原型与采样率。`));
}

function allCandidates() {
  return (currentSession?.versions || []).flatMap((v, index) => v.candidates.map((c) => ({ ...c, versionIndex: index + 1, versionId: v.id })));
}

function renderComparison() {
  const candidates = allCandidates();
  const current = currentVersion();
  const versionChanged = comparedVersionId !== current.id;
  const parent = currentSession.versions.find((v) => v.id === current.parent_id);
  for (const side of ["a", "b"]) {
    const select = $(`compare-${side}`);
    const old = select.value;
    select.replaceChildren();
    candidates.forEach((c) => {
      const option = node("option", null, `v${c.versionIndex} · ${c.label || c.id}`);
      option.value = c.id;
      select.append(option);
    });
    if (!versionChanged && candidates.some((c) => c.id === old)) select.value = old;
    else if (side === "a") select.value = parent?.selected_id || current?.candidates[0]?.id || candidates[0]?.id || "";
    else select.value = current?.selected_id || current?.candidates.at(-1)?.id || candidates.at(-1)?.id || "";
    renderComparisonAudio(side);
  }
  $("history").replaceChildren();
  currentSession.versions.forEach((v, index) => {
    const changed = (v.changed_fields || []).map((key) => fieldSpecs.find((f) => f[0] === key)?.[1].split(" /")[0] || key).join("、");
    const option = node("option", null, `v${index + 1} · ${changed || v.feedback || "初始候选"}${v.id === currentSession.current_version_id ? "（当前）" : ""}`);
    option.value = v.id;
    $("history").append(option);
  });
  $("history").value = currentSession.current_version_id;
  comparedVersionId = current.id;
}

function renderComparisonAudio(side) {
  const candidate = allCandidates().find((c) => c.id === $(`compare-${side}`).value);
  $(`audio-${side}`).replaceChildren();
  if (candidate) $(`audio-${side}`).append(audioPlayer(candidate.audio_url, `${side.toUpperCase()} 对照试听`));
}

function createPlot(x, y, title, xCaption) {
  if (!Array.isArray(x) || !Array.isArray(y) || x.length < 2 || x.length !== y.length || !x.every(Number.isFinite) || !y.every(Number.isFinite)) return null;
  const wrap = node("div", "analysis-plot");
  wrap.append(node("p", null, title));
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 320 95");
  svg.setAttribute("role", "img");
  svg.setAttribute("aria-label", title);
  const path = document.createElementNS("http://www.w3.org/2000/svg", "polyline");
  const minX = Math.min(...x), spanX = Math.max(...x) - minX || 1;
  const minY = Math.min(...y), spanY = Math.max(...y) - minY || 1;
  path.setAttribute("points", x.map((v, i) => `${(8 + (v - minX) / spanX * 304).toFixed(2)},${(85 - (y[i] - minY) / spanY * 75).toFixed(2)}`).join(" "));
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "#447c8e");
  path.setAttribute("stroke-width", "1.4");
  svg.append(path);
  wrap.append(svg, node("p", null, `${xCaption} ${formatNumber(minX)}–${formatNumber(Math.max(...x))}；纵轴 ${formatNumber(minY)}–${formatNumber(Math.max(...y))}`));
  return wrap;
}

function renderReferences() {
  $("references").replaceChildren();
  (currentSession.references || []).forEach((ref) => {
    const card = node("article", "reference-card");
    card.append(node("h3", null, `${ref.filename || "原始参考录音"} · 用户提供的观测`));
    card.append(node("p", "reference-meta", ref.notice || "录音来源真实性未经独立核实，保留原始文件字节。"));
    if (ref.audio_url) card.append(audioPlayer(ref.audio_url, "原始参考录音"));
    const analysis = ref.analysis || {};
    const metrics = analysis.metrics || {};
    card.append(node("p", "reference-meta", `时长 ${formatNumber(metrics.duration_s)} s · 采样率 ${formatNumber(metrics.sample_rate, 0)} Hz · 峰值数字幅度 ${formatNumber(metrics.peak_amplitude, 4)} · RMS ${formatNumber(metrics.rms_dbfs)} dBFS`));
    card.append(node("p", "reference-meta", "以上是录音文件的数字信号测量，不是现场声压级；设备频响、距离与录制增益会影响结果。"));
    const plots = node("div", "analysis-plots");
    const spectrum = createPlot(analysis.spectrum?.frequency_hz, analysis.spectrum?.magnitude_dbfs, "频谱 · 谱功率 dBFS", "频率 / Hz");
    const envelope = createPlot(analysis.envelope?.time_s, analysis.envelope?.rms, "能量包络 · RMS", "时间 / s");
    if (spectrum) plots.append(spectrum);
    if (envelope) plots.append(envelope);
    card.append(plots);
    const details = node("details");
    details.append(node("summary", null, "查看测量方法、参数与不确定性"));
    details.append(node("pre", null, JSON.stringify({ metrics, method: analysis.method, uncertainty: analysis.uncertainty }, null, 2)));
    card.append(details);
    const consentLabel = node("label", "check-line consent");
    const checkbox = node("input");
    checkbox.type = "checkbox";
    checkbox.disabled = !serviceStatus.audio_available;
    consentLabel.append(checkbox, node("span", null, "我同意将这一份原始参考录音发送到 Gemini 外部 API 进行音频理解，可能产生费用。默认仅本地保存，此授权不适用于其他录音。"));
    const understand = node("button", "secondary understand-button", "发送此录音到 Gemini 分析");
    understand.type = "button";
    understand.disabled = true;
    checkbox.addEventListener("change", () => { understand.disabled = !checkbox.checked || !serviceStatus.audio_available; });
    understand.addEventListener("click", () => runAction(async () => {
      if (!checkbox.checked) throw new Error("需要单独同意发送这份录音，才会调用外部 API。");
      const result = await api(endpoint("/understand"), { reference_id: ref.id, consent: true });
      if (result.session) adoptSession(result.session);
      if (result.error) showMessage(result.error, true);
      else showMessage("音频理解已返回。它是模型描述，不是本地测量结果或确定的声源结论。");
    }, "已获授权，正在发送这份原始录音到 Gemini…"));
    card.append(consentLabel, understand);
    for (const interpretation of (ref.external_analyses || [])) {
      const modelDetails = node("details");
      modelDetails.open = true;
      modelDetails.append(node("summary", null, `${interpretation.provider || "模型"} 音频描述 · 仅依据原始录音`), node("pre", null, typeof interpretation.result === "string" ? interpretation.result : JSON.stringify(interpretation.result, null, 2)));
      card.append(modelDetails);
    }
    $("references").append(card);
  });
  $("audio-mode-note").textContent = serviceStatus.audio_available ? "Gemini 已配置。每份原录音都需要单独勾选授权并点击发送，本地分析不触发发送。" : "音频理解降级模式：Gemini 未配置，参考录音仅进行本地信号分析。";
}

function adoptSession(session, opening = false) {
  if (!session || !Array.isArray(session.versions)) throw new Error("服务返回的会话结构不完整，请刷新后重试。");
  clearPlan();
  const changed = currentSession?.id !== session.id;
  currentSession = session;
  if (changed) {
    locks = new Set(session.locked_fields || []);
  }
  try { localStorage.setItem("soundsketch.lastSession", session.id); } catch (_) { /* Storage is optional. */ }
  document.querySelectorAll("audio").forEach((audio) => audio.pause());
  $("empty-state").hidden = true;
  $("session-work").hidden = false;
  const version = currentVersion();
  if (!version) throw new Error("找不到当前版本，已保存的其他版本仍可保留，请检查会话文件。");
  $("version-badge").textContent = `版本 ${session.versions.indexOf(version) + 1} / ${session.versions.length}`;
  $("session-description").textContent = session.description;
  $("session-notice").textContent = session.notice || (session.mode === "local" ? "本地降级模式：使用规则与显式参数生成，不依赖模型 API。" : "参数经本地校验后，由 DSP 生成。模型不提供测量值或确定故障判断。");
  if (session.model_call) {
    const call = session.model_call;
    $("session-notice").textContent += ` 本轮：${call.provider} / ${call.model}，耗时 ${formatNumber(call.latency_ms / 1000)} 秒` + (Number.isInteger(call.total_tokens) ? `，${call.total_tokens} tokens。` : "。");
  }
  $("candidates").className = `candidate-grid${version.candidates.length === 1 ? " single" : ""}`;
  $("candidates").replaceChildren(...version.candidates.map((c, i) => renderCandidate(c, i, version.selected_id)));
  const selected = selectedCandidate();
  $("selection-hint").textContent = selected ? `当前选择：${selected.label || "候选"}。只修改你指定的特征；不明确的表达会先请求澄清。` : "先选择一个候选，再进行调整。";
  ["feedback", "feedback-button", "parameter-button", "confirm"].forEach((id) => { $(id).disabled = !selected; });
  document.querySelectorAll("[data-feedback]").forEach((button) => { button.disabled = !selected; });
  $("undo").disabled = !version.parent_id;
  $("clarification").hidden = true;
  const confirmed = session.confirmed_version_id === version.id && session.confirmed_candidate_id === selected?.id;
  $("download").disabled = !confirmed;
  $("confirmed-state").textContent = confirmed ? "当前版本已确认" : "选择并确认后可导出";
  if (changed || opening) {
    $("description").value = session.description || "";
    $("scene").value = session.scene || "";
    $("timing").value = session.timing || "";
    $("final-description").value = session.confirmed_description || "";
    if ("use_model" in session) $("use-model").checked = Boolean(session.use_model) && serviceStatus.planner_available;
  }
  renderParameters(selected);
  renderVersionPlan(version);
  renderComparison();
  renderReferences();
  document.querySelectorAll("[data-feedback]").forEach((button) => {
    button.hidden = button.dataset.feedback.includes("敲击") && selected && !["knock", "mix"].includes(selected.params.kind);
  });
}

async function refreshSessions() {
  const data = await api("/api/sessions", undefined, "GET");
  $("saved-sessions").replaceChildren(node("option", null, "选择已保存的会话"));
  $("saved-sessions").firstElementChild.value = "";
  (data.sessions || []).forEach((session) => {
    const option = node("option", null, `${session.description.slice(0, 37)}${session.description.length > 37 ? "…" : ""}`);
    option.value = session.id;
    $("saved-sessions").append(option);
  });
  if (currentSession) $("saved-sessions").value = currentSession.id;
}

$("create-form").addEventListener("submit", (event) => {
  event.preventDefault();
  runAction(async () => {
    const description = $("description").value.trim();
    if (!description) throw new Error("请先描述你听到的声音，声源可以未知。");
    clearPlan();
    const epoch = previewEpoch;
    const plan = await api("/api/plans", { description, kind: $("kind").value, scene: $("scene").value.trim(), timing: $("timing").value.trim(), use_model: $("use-model").checked });
    if (epoch !== previewEpoch) { showMessage("输入已改变，请按当前描述重新解析。"); return; }
    renderPlan(plan, description);
    showMessage(plan.clarification ? "需要补充听感描述，尚未生成声音。" : "参数方案已准备好。查看设定后，可生成候选试听。");
  }, "正在整理听感并校验参数方案…");
});

$("open-session").addEventListener("click", () => runAction(async () => {
  const id = $("saved-sessions").value;
  if (!id) throw new Error("请先选择一个已保存的会话。");
  adoptSession(await api(`/api/sessions/${encodeURIComponent(id)}`, undefined, "GET"), true);
  showMessage("已恢复本地会话及历史版本。音频不会自动播放。");
}));

async function revise(feedback, patch) {
  clearPlan();
  const epoch = previewEpoch;
  const payload = { feedback, locked_fields: [...locks], use_model: $("use-model").checked };
  if (patch !== undefined) payload.patch = patch;
  const plan = await api(endpoint("/preview"), payload);
  if (epoch !== previewEpoch) { showMessage("输入已改变，请重新预览参数改动。"); return; }
  renderPlan(plan, feedback);
  showMessage(plan.clarification ? "需要澄清这项修改。当前声音与参数保持不变。" : "修改方案已准备好，当前声音尚未改变。");
}

$("feedback-form").addEventListener("submit", (event) => {
  event.preventDefault();
  runAction(async () => {
    const feedback = $("feedback").value.trim();
    if (!feedback) throw new Error("请写下这次希望改变或保留的听感特征。");
    await revise(feedback);
  }, "正在解析修改意见并检查保留的特征…");
});

document.querySelectorAll("[data-feedback]").forEach((button) => {
  button.addEventListener("click", () => {
    clearPlan("修改意见已改变，请重新预览方案。");
    $("feedback").value = button.dataset.feedback;
    $("feedback").focus();
  });
});

for (const id of ["create-form", "feedback-form", "parameter-form"]) {
  $(id).addEventListener("input", () => clearPlan("输入已改变，请重新预览方案。"));
  $(id).addEventListener("change", () => clearPlan("输入已改变，请重新预览方案。"));
}

$("cancel-plan").addEventListener("click", () => {
  clearPlan();
  showMessage("已取消方案，没有生成声音或改变历史版本。");
});

$("apply-plan").addEventListener("click", () => runAction(async () => {
  const plan = pendingPlan;
  if (!plan?.id || plan.clarification) throw new Error("请先解析一份可应用的参数方案。");
  let result;
  try { result = await api(`/api/plans/${encodeURIComponent(plan.id)}/apply`, {}); }
  catch (error) { clearPlan(); throw error; }
  if (result.session) adoptSession(result.session, plan.task === "initial");
  if (result.clarification) {
    clearPlan();
    $("clarification").hidden = false;
    $("clarification").textContent = result.clarification;
    showMessage("这项方案没有产生可用的新声音，请根据提示调整。");
    return;
  }
  if (!result.session) throw new Error("服务未返回生成结果，请重新打开会话核对。");
  $("feedback").value = "";
  await refreshSessions();
  showMessage(plan.task === "initial" ? `${currentVersion().candidates.length} 个候选已由 Python DSP 生成并保存。请从低音量开始试听。` : "已按方案生成新版本。未修改的参数与种子保持不变，可 A/B 试听或恢复上一版本。");
  $("session-work").scrollIntoView({ behavior: "smooth", block: "start" });
}, "正在按已校验的参数，用本地 Python DSP 合成…"));

$("parameter-form").addEventListener("submit", (event) => {
  event.preventDefault();
  runAction(async () => {
    const patch = {};
    for (const [key] of fieldSpecs) {
      const input = $(`param-${key}`);
      if (!input || input.disabled) continue;
      if (input.value.trim() === "" || !Number.isFinite(Number(input.value))) throw new Error("参数必须是有限数值。");
      const value = Number(input.value);
      if (value !== paramBaseline[key]) patch[key] = value;
    }
    if (!Object.keys(patch).length) throw new Error("尚未修改参数。可先调整一项数值，或使用听感描述修改。");
    await revise("通过参数表单精确修改", patch);
  });
});

$("undo").addEventListener("click", () => runAction(async () => {
  const restored = await api(endpoint("/undo"), {});
  locks = new Set(restored.locked_fields || []);
  try { localStorage.setItem(`soundsketch.locks.${restored.id}`, JSON.stringify([...locks])); } catch (_) { /* Optional preference. */ }
  adoptSession(restored);
  showMessage("已恢复上一版本。后续版本仍保存在历史中。");
}));

$("restore").addEventListener("click", () => runAction(async () => {
  const restored = await api(endpoint("/restore"), { version_id: $("history").value });
  locks = new Set(restored.locked_fields || []);
  try { localStorage.setItem(`soundsketch.locks.${restored.id}`, JSON.stringify([...locks])); } catch (_) { /* Optional preference. */ }
  adoptSession(restored);
  showMessage("已恢复选定的历史版本。");
}));

["a", "b"].forEach((side) => $(`compare-${side}`).addEventListener("change", () => renderComparisonAudio(side)));

$("reference-form").addEventListener("submit", (event) => {
  event.preventDefault();
  runAction(async () => {
    const file = $("reference-file").files[0];
    if (!file) throw new Error("请选择一个原始 WAV 文件。");
    if (file.size > 20 * 1024 * 1024) throw new Error("文件超过 20 MB。请另存一段最长 30 秒的 WAV，保留原文件。");
    const data = new FormData();
    data.append("file", file);
    adoptSession(await api(endpoint("/reference"), data));
    $("reference-file").value = "";
    showMessage("原始录音已独立保存，本地分析完成；没有发送到外部 API。");
  }, "正在本地保存与分析参考录音…");
});

$("confirm").addEventListener("click", () => runAction(async () => {
  adoptSession(await api(endpoint("/confirm"), { description: $("final-description").value.trim() }));
  showMessage("已确认当前声音。现在可下载 WAV、听感描述和完整生成参数。");
}));

$("download").addEventListener("click", () => {
  if (!currentSession || $("download").disabled) return;
  window.location.href = endpoint("/export");
});

async function initialize() {
  try {
    serviceStatus = await api("/api/status", undefined, "GET");
    $("mode-badge").textContent = serviceStatus.planner_available ? "本地 DSP · 模型可选" : "本地降级模式 · 无需 API";
    $("use-model").disabled = !serviceStatus.planner_available;
    $("planner-note").textContent = serviceStatus.planner_available ? `已配置 ${serviceStatus.planner?.provider || "兼容 API"} / ${serviceStatus.planner?.model || "文字模型"}。勾选后会将描述、场景、时间规律和参数发送到该规划 API，可能产生费用；不发送录音。` : "未配置有效规划 API，当前使用本地规则与参数表单；无需密钥即可生成、修改和导出。";
    await refreshSessions();
    // A saved session is offered explicitly, never played or uploaded on page load.
    let last;
    try { last = localStorage.getItem("soundsketch.lastSession"); } catch (_) { /* Optional storage. */ }
    if (last && [...$("saved-sessions").options].some((option) => option.value === last)) $("saved-sessions").value = last;
  } catch (error) {
    $("mode-badge").textContent = "本地服务连接失败";
    showMessage(error.message, true);
  }
}

initialize();
