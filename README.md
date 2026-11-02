# SoundSketch

**自然语言驱动的可控噪声合成与听感表达 Agent**

*A natural-language agent for controllable noise synthesis and auditory expression.*

用文字描述难以表达的声音，通过试听、比较和逐项修改，生成可分享的声音示意。LLM 负责将描述转换为结构化参数，Python DSP 负责合成，会话管理保留参数、随机种子和反馈历史。

当前是轻量本地声学 Demo：覆盖 **嗡鸣、尖啸、间歇敲击** 三类听感。无 API 密钥时可使用本地规则与参数表单完成整个流程，无需 GPU、模型下载或训练。

```text
自然语言描述 → 本地规则 / 可选 LLM → 参数校验与预览 → Python DSP
      ↑                                                   ↓
      └──────────── 修改意见 ← 用户试听、对比、选择 ────────┘
                                      ↓
                          确认导出 WAV、描述和生成记录
```

## 快速启动

要求 Python 3.10+；本地验证环境为 Python 3.12。下载或克隆本仓库后，在项目根目录执行：

```bash
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m soundsketch
```

macOS / Linux：

```bash
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/python -m soundsketch
```

打开 [本地工作台](http://127.0.0.1:8765)。按 `Ctrl+C` 停止；端口占用时使用 `--port 8766`。Windows 也可运行 `./start.ps1`。更新代码或环境变量后，需要重启服务并刷新网页。

服务仅监听本机地址，适合单用户 Demo；不要直接部署到公网。依赖只有 NumPy、SciPy、Flask，前端无需构建或 CDN。下文的 `python` 命令均在已激活的虚拟环境中运行，或替换为上面的虚拟环境 Python 路径。

## 一分钟演示

1. 输入：**“持续低沉的嗡鸣，缓慢起伏，来源不确定。”**
2. 点击“解析听感，查看方案”，查看规则 / 模型建议与默认参数。
3. 点击“按方案生成候选”，手动试听 2–3 个候选，选择较接近的一个。
4. 输入：**“起伏慢一点，音高不变。”** 查看参数差异，再应用修改。
5. 用 A/B 比较新旧版本，或恢复上一版本；确认后导出 ZIP。

也可尝试“持续的尖啸”“间歇敲击”。候选不会自动播放，播放器初始音量为 15%。含糊、矛盾或当前原型不适用的修改会提示澄清。

## 当前能力

- 谐波嗡声、窄带音调、带限噪声、调幅起伏、衰减敲击及混合。
- 自然语言修改、精确参数表单、参数锁定与修改预览。
- 本地 JSON 会话、历史恢复、A/B 试听、确认导出。
- 同参数、同种子、同软件环境下可复现 WAV。
- 可选原始 PCM WAV 的频谱与能量包络分析。
- 可选文字模型适配器；调用失败明确降级，不采用无效参数。

文字模型、原录音理解与 DSP 是独立模块。原录音分析目前不自动拟合合成参数；原因推断、真实案例检索和压缩机项目集成均为后续扩展。

## 可选 LLM

配置示例见 [.env.example](.env.example)，详细步骤见 [模型与运行配置](docs/configuration.md)。**程序不会自动读取 `.env`**，请在启动进程的终端设置环境变量。

支持项目内的 `mimo`、`deepseek` 预设和兼容 Chat Completions JSON 输出的文字服务。具体模型可通过环境变量覆盖；预设不代表服务端当前可用性已经验证。

```bash
python -m soundsketch.check_llm
```

该命令默认仅检查配置、不联网。网页中勾选文字模型授权并执行解析时才会调用 API；不随文字请求发送录音。真实供应商效果尚待实测，现有接口验收使用模拟响应。

## Python 复用与压缩机项目

DSP 可以独立调用，便于后续作为压缩机项目的听感表达界面或受控对照声音工具：

```python
from soundsketch.parameters import apply_patch, default_params
from soundsketch.dsp import synthesize

params = apply_patch(default_params("hum"), {
    "frequency_hz": 80.0,
    "modulation_hz": 0.4,
    "modulation_depth": 0.45,
    "seed": 42,
})
samples = synthesize(params)  # 单声道浮点数组；采样率 params.sample_rate
```

在仓库根目录运行 WAV + JSON 示例：

```bash
python -m examples.synthesize --output artifacts/compressor-sketch.wav
```

这是合成听感演示，不是压缩机物理模型或真实设备故障样本。对接方法见 [压缩机项目集成说明](docs/compressor-integration.md)；当前仓库不依赖其他研究项目。

## 数据与复现

默认数据保存在当前工作目录的 `data/`；可通过 `--data-dir` 或 `SOUNDSKETCH_DATA_DIR` 更改。原始录音独立保存、不覆盖。数据、录音、密钥、日志和测试产物不应提交到 Git。

导出包包含 `confirmed.wav`、`description.txt`、`parameters.json`、`session.json`，不包含原始录音，但会话记录可能含个人描述和录音分析。解压后可重建声音：

```bash
python -m soundsketch.render parameters.json reproduced.wav
```

数字幅值与 dBFS 不等于现场声压级；参数保持不等于所有主观听感完全保持。生成音频是表达示意，不能作为新的现场观测或确定故障的证据。案例与参考录音的来源必须另外标注。

## 开发与测试

在已安装 `.[dev]` 的 Python 环境中执行：

```bash
python -m pytest -q
```

测试默认不访问付费模型。仓库包含 GitHub Actions 测试配置；运行结果会在上传后产生。最新本地记录见 [VERIFICATION.md](VERIFICATION.md)。

```text
soundsketch/       参数、DSP、规划器、会话、本地 Web UI
tests/            合成、修改约束、预览、导出与适配器测试
examples/         独立 Python 合成示例
docs/             配置、架构、压缩机对接和 GitHub 上传说明
data/             本地会话与录音（不提交）
```

[架构与边界](docs/architecture.md) · [GitHub 上传步骤](docs/github.md) · [相关工作](docs/related-work.md)

## 范围与后续方向

当前只有三个简化原型，无法覆盖完整城市声景或准确重建设备音色；本地语言规则覆盖有限。尚未进行用户听辨实验或真实故障诊断评估。

后续可按需要增加压缩机听感参数、带来源的参考案例及排查辅助，分别验证表达效果和排查效果。声音表达与反馈式合成已有相关研究，本项目不主张首次提出这类交互。

## 许可证

代码采用 [MIT License](LICENSE)。第三方依赖遵循各自许可证；引用论文及用户录音不因本项目许可而自动获得再分发授权。
