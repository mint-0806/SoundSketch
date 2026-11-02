# 配置与本地数据

SoundSketch 不依赖模型 API 即可运行。未配置密钥时，网页使用本地描述规则和参数表单；WAV 始终由本地 Python DSP 生成。

配置模板见 [`.env.example`](../.env.example)。程序**不会自动加载 `.env` 文件**，请在启动服务的同一个终端设置环境变量。以下命令假设已进入仓库目录并安装依赖。

## 本地服务

```shell
python -m soundsketch
```

浏览器访问 <http://127.0.0.1:8765>。服务仅监听本机；端口和数据目录可指定：

```shell
python -m soundsketch --port 8766 --data-dir ./data-demo
```

数据目录按 `--data-dir`、`SOUNDSKETCH_DATA_DIR`、启动工作目录下 `data/` 的顺序选择。自定义数据目录也应加入自己的 Git 忽略规则。更改环境变量或 Python 代码后，停止原服务并从配置好的终端重新启动；刷新网页不会更新旧进程。

## 可选文字规划器

| 环境变量 | 用途 |
| --- | --- |
| `SOUNDSKETCH_PLANNER_PROVIDER` | `compatible`、`mimo` 或 `deepseek`；默认 `compatible` |
| `SOUNDSKETCH_PLANNER_API_KEY` | 所选服务的密钥 |
| `SOUNDSKETCH_PLANNER_BASE_URL` | 可覆盖内置地址；填写 API 根地址，不含 `/chat/completions` |
| `SOUNDSKETCH_PLANNER_MODEL` | 可覆盖内置模型名；`compatible` 必须填写 |

下面是代码内置的请求预设，**不代表最新官方推荐或线上可用性承诺**。模型权限、可用型号、价格和接口兼容性以所选服务为准；本项目未通过这些预设完成真实付费调用验收。

| provider | 内置 Base URL | 内置模型名 | 请求约定 |
| --- | --- | --- | --- |
| `compatible` | `https://api.openai.com/v1` | 无，必须配置 | `max_tokens`；保留供应商默认思考设置 |
| `mimo` | `https://api.xiaomimimo.com/v1` | `mimo-v2.6-flash` | `max_completion_tokens`；关闭思考 |
| `deepseek` | `https://api.deepseek.com` | `deepseek-flash` | `max_tokens`；关闭思考 |

三种模式均使用 Chat Completions 风格接口、`response_format=json_object`、非流式响应和 1024-token 输出上限。`compatible` 仍要求服务支持这些请求字段；名称兼容不保证所有供应商都能直接使用。外部地址须为 HTTPS，回环地址可用 HTTP；适配器拒绝重定向。

PowerShell 示例，通过交互输入密钥，避免将密钥字面量写入命令历史：

```powershell
$env:SOUNDSKETCH_PLANNER_PROVIDER = 'compatible'
$env:SOUNDSKETCH_PLANNER_BASE_URL = 'https://your-provider.example/v1'
$env:SOUNDSKETCH_PLANNER_MODEL = 'your-model-id'
$plannerSecret = Read-Host '文字规划 API Key' -AsSecureString
$env:SOUNDSKETCH_PLANNER_API_KEY = [System.Net.NetworkCredential]::new('', $plannerSecret).Password
python -m soundsketch.check_llm
python -m soundsketch
```

请先将示例地址和模型 ID 替换成自己的实际配置。Bash 中可使用：

```bash
export SOUNDSKETCH_PLANNER_PROVIDER=compatible
export SOUNDSKETCH_PLANNER_BASE_URL=https://your-provider.example/v1
export SOUNDSKETCH_PLANNER_MODEL=your-model-id
read -rsp '文字规划 API Key: ' SOUNDSKETCH_PLANNER_API_KEY
export SOUNDSKETCH_PLANNER_API_KEY
python -m soundsketch.check_llm
python -m soundsketch
```

使用 `mimo` 或 `deepseek` 预设时，可改用对应 provider 并显式设置当前可用的 URL、模型和密钥。切换服务时检查这三项：显式 URL 和模型配置优先于预设，不会因为切换 provider 自动清除。

网页需要勾选文字模型选项，再执行解析操作，才会发送相应的描述、场景、时间规律或当前参数与修改意见。**文字规划请求不附带参考录音。** 查看已有会话或播放声音不会调用模型。

### 配置检查与失败处理

`python -m soundsketch.check_llm` 默认只检查配置，不发请求，也不打印密钥。只有明确运行下面的命令，才会向所选 API 发送内置的公开演示文本及参数，最多两次调用，可能产生费用：

```shell
python -m soundsketch.check_llm --live
```

实时检查失败、降级或需要澄清时退出码为 `2`。运行成功也只说明这两个演示请求通过，不代表对所有听感描述都准确。

适配器超时为 25 秒，不自动重试。调用失败、响应不完整、JSON 无效、数值越界或违反锁定约束时，明确显示失败/降级；本地能够识别的描述可以继续使用规则，无法可靠映射的内容要求澄清。没有伪造模型结果。

已采用的结果记录请求的 provider、模型别名、调用耗时及实际返回的 token 用量。没有返回的用量保持缺失；不保存原始模型响应或推理文本，不记录密钥。模型别名不是对供应商实际运行版本的独立验证。

## 可选原录音理解

本地录音分析无需 API。当前接受未压缩整数 PCM WAV：不超过 20 MiB、30 秒，单/双声道、8–96 kHz、8/16/24/32 位。分析保存 Welch 频谱、RMS 包络、数字信号指标及方法参数。

Gemini 是独立的可选音频理解适配器，需要同时设置 `GEMINI_MODEL` 和 `GEMINI_API_KEY`。模型应由使用者选择为支持音频输入的当前可用型号。PowerShell 中可以沿用上面的 `Read-Host -AsSecureString` 方式设置密钥。

每次外传都要在对应原录音旁勾选同意并点击发送。项目对该内联上传限制为 10 MiB；更大的有效录音仍可在本地分析。服务器记录授权事件和文件哈希，调用失败保留原始录音和本地分析，不添加假结果。模型听觉描述与仪器测量分别标注；合成候选不会作为原录音发送。

ElevenLabs Sound Effects 与素材库目前仅为扩展点，未启用付费音效生成。

## 数据保存与分享

每个会话独立保存 `session.json`、`generated/*.wav` 和 `references/*.wav`。原始录音按随机 ID 原字节保存，并计算 SHA-256；同名上传也不覆盖旧文件。生成声音、用户提供的观测和经验案例保持不同来源类别。

确认后的 ZIP 包包含 `confirmed.wav`、`description.txt`、`parameters.json`、`session.json`，不包含原始录音文件。会话记录仍可能包含个人描述、反馈、录音文件名和分析结果，分享前应检查。

默认没有遥测、自动上传或云同步。此原型不加密本地文件，也没有多用户权限、跨进程写入协调或磁盘配额；使用本机单进程服务，避免两个服务同时写同一数据目录。密钥、录音、导出包和个人会话不应提交到版本库。

## 常见问题

- **未配置模型仍能生成吗？** 可以，使用本地规则或参数表单；界面显示降级模式。
- **复制 `.env.example` 后不生效？** 程序不读取 `.env`，需设置启动进程的环境变量。
- **密钥配置后仍显示不可用？** 检查 URL、模型 ID、provider，并从设置变量的同一终端重启服务。
- **方案失效？** 未应用方案只在内存保存，30 分钟有效、最多 100 份；重启或会话变化后重新解析。
- **参数改了却没有声音差异？** 未启用成分、片段太短或变化低于 PCM 精度时会要求澄清；数字差异也不保证人耳可辨。

返回 [README](../README.md) · 查看 [架构与接口](architecture.md)
