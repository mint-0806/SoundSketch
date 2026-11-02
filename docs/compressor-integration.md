# 与压缩机项目复用

SoundSketch 可以作为压缩机项目的听感表达工具：使用者描述或试听调整嗡鸣、起伏和敲击特征，然后向研究人员或维修人员分享声音示意及参数。当前尚未与任何压缩机项目连接，也没有引入该项目的数据、物理模型或故障标签。

## 先运行一个本地合成示例

在 SoundSketch 仓库根目录中，使用已安装项目依赖的 Python 环境：

```console
python -m pip install -e .
python -m examples.synthesize --output artifacts/synthetic-hum.wav
python -m soundsketch.render artifacts/synthetic-hum.json artifacts/synthetic-hum-reproduced.wav
```

示例生成低频谐波嗡声，带少量带限噪声和缓慢起伏，不调用模型 API。输出包含：

- `synthetic-hum.wav`：4 秒、24 kHz、单声道、16 位 PCM 的合成示意。
- `synthetic-hum.json`：参数、种子、软件版本、生成音频的数字信号指标、SHA-256，以及明确的合成来源标签。

WAV 或同名 JSON 已存在时，示例拒绝覆盖。`render` 重建命令也需要新的输出文件名。相同依赖版本和平台下，固定参数与种子可以复现相同 WAV 字节。`artifacts/` 中的输出不进入版本库。

这个示例可用于压缩机听感沟通演示，但不能当作实际压缩机声样本、转速/负载的物理预测或某种故障的证据。数字增益不表示现场声压级；从低播放音量开始试听。

## 两种简单复用方式

**文件交换。** 先让两个项目分别运行，使用 SoundSketch 导出的 WAV 和 `parameters.json` 作为交接材料。压缩机项目保留自己的原始数据、试验编号和工况记录；SoundSketch 保存表达方案与试听反馈。这样可以在尚未确定对方接口时开展演示。

**Python 导入。** 后续在压缩机项目自己的虚拟环境中安装 SoundSketch 的本地路径或固定版本，再复用已有函数：

```python
from soundsketch.parameters import default_params, apply_patch
from soundsketch.dsp import synthesize

params = apply_patch(default_params("hum"), {
    "frequency_hz": 75.0,
    "modulation_hz": 0.6,
    "seed": 42,
})
samples = synthesize(params)  # 单声道 NumPy 数组；此处不写文件

# 在另一轮表达中仅调整起伏，保留已确认的音高。
updated = apply_patch(params, {"modulation_hz": 0.4},
                      locked_fields=["frequency_hz"])
```

`SoundParams` 与 `apply_patch` 检查参数范围和锁定约束。调用方应自行管理输出路径、样本身份和原始记录；`dsp.write_wav` 是底层写入函数，不能直接用于覆盖原始录音。

## 数据如何区分

| 数据 | 标记与用途 |
| --- | --- |
| 实际压缩机录音 | 原始观测；由来源项目提供试验编号、设备及工况等真实记录 |
| 已核实故障案例 | 经验案例；故障标签需要检查或维修结果等证据支持 |
| SoundSketch 生成音频 | `category=synthetic`；仅用于听感表达、交互演示或明确标记的合成试验 |

可以将项目自己的 `case_id` 与 SoundSketch 会话 ID 建立映射，但这只是关联记录，不会把合成声音变成真实观测。合成参数不能自动成为原设备的实测频率、声压级或故障判断。

正式接入时再确认压缩机项目的数据接口、采样率和元数据格式，并明确选取哪个版本的 SoundSketch。原录音与设备记录仍由来源项目管理；本仓库保持独立，默认不上传这些文件。
