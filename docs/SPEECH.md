# ASR、TTS 与主模型下载 / Speech models and downloads

Luna-Aster 的语音能力通过可选 Adapter 接入。默认启动仍只初始化四个 Mock；API 不导入模型框架、不加载权重，只有单个 Runtime Worker 管理真实引擎。当前本地引擎使用 CPU，GPU 指标仍明确为模拟。

| Adapter | 能力 | 依赖 / 来源 |
|---|---|---|
| `local.sensevoice.asr` | 中、英、日、韩、粤语完整音频转写 | FunASR / SenseVoiceSmall |
| `local.paraformer.asr` | 中文分块解码，对外提交完整 WAV | FunASR / Paraformer streaming |
| `local.faster_whisper.asr` | Whisper 转写 | faster-whisper / CTranslate2 权重 |
| `local.kokoro.tts` | 语音合成、WAV、拼接全部生成片段 | Kokoro，默认中文 1.1、zf_001 |
| `zipvoice.tts` | HTTP 参考音色合成，WAV / base64 JSON | 另行运行的 ZipVoice 服务 |
| `edge.tts` | 在线语音，WAV / MP3 | Microsoft Edge，无本地权重 |

源项目的付费 Whisper API、Google SpeechRecognition、gTTS、系统 pyttsx3 本次未新增为 Adapter。ZipVoice 源项目只有 HTTP 客户端，未包含其服务及权重，因此不提供 ZipVoice 主模型下载。Edge 会将合成文本发送给 Microsoft，需要网络。

## 后台下载与启用

1. 管理员进入 **模型管理 → 主模型下载**，选择 SenseVoice、Paraformer、Faster-Whisper base 或 Kokoro 中文版。只有点击下载才联网，启动和加载不会自动下载。
2. Worker 串行下载到 `MODELDock_MODELS_DIR/.downloads/<id>`，获取官方仓库 commit，按固定清单下载，并检查文件大小及 LFS SHA-256 / Git blob SHA-1。进度、当前文件、失败代码和取消状态持久化到数据库。
3. 完整快照原子移动到最终目录，并写入 `.modeldock-manifest.json`。失败或取消清理临时文件；已有同名目录保留并报 `model_path_exists`。重试重新下载，不支持断点续传。Worker 重启将中断下载标为失败，排队下载继续执行。
4. 点击 **创建模型配置**，生成停用的 CPU 模型。安装对应可选依赖后启用，再通过在线测试或 `/v1` 调用。下载不安装 Python 库，也不验证真实推理质量。

只允许预设，不接受请求自带 URL、仓库、路径或代码。下载管理需要管理员 Web 会话及 CSRF，API Key 无权使用；记录、取消与注册按创建者所有权查询。创建的模型沿用现有授权规则。

默认源为 `https://huggingface.co`。Worker 可通过 `MODELDock_MODEL_HUB_URL` 配置受信任且兼容 Hugging Face API 的 HTTPS 源；不会自动修改代理。默认单次上限 10 GiB、超时 7200 秒，分别通过 `MODELDock_MODEL_DOWNLOAD_MAX_GB`、`MODELDock_MODEL_DOWNLOAD_TIMEOUT_SECONDS` 调整。只接受模型源自身与 Hugging Face 官方域名的 HTTPS 下载重定向。网络、磁盘与校验失败会记录明确代码。

## 可选依赖与部署

```bash
# 需要几类能力就合并几项 --extra，防止 uv sync 移除其他可选依赖。
uv sync --frozen --extra dev --extra speech-asr --extra speech-tts --extra speech-edge
# 本次新增下载记录表；升级现有本地部署后重启 API 和 Worker。
uv run alembic upgrade head
```

这些命令安装依赖，不下载模型。Edge WAV 转换需要 `ffmpeg`，镜像已包含。Kokoro 默认中文 G2P；其他语言可能还需要自行准备语言资源。本地模型子进程关闭网络并设置 Hugging Face 离线模式；权重或词典缺失时明确失败。FunASR 禁止远端代码和自动更新。

Docker 在 `.env` 设置 `MODELDock_SPEECH=true` 后运行 `docker compose build`、`docker compose up -d`，显式安装三类语音依赖。Compose 只有 Worker 挂载可写 `models:/models` 命名卷，API 不挂载权重。卷初始权限属于镜像 UID 10001。宿主开发目录默认 `./models`，与容器命名卷分别管理。改用宿主目录绑定时须确保 Worker UID 10001 可读写；不要将模型复制进镜像。

## 手动放置权重

路径相对 `MODELDock_MODELS_DIR`，下载目录与默认配置一致：

```text
models/
  asr/SenseVoiceSmall/           # model.pt, config.yaml, configuration.json, tokenizer, am.mvn
  asr/paraformer-zh-streaming/   # model.pt, config.yaml, configuration.json, tokens.json, am.mvn, seg_dict
  asr/faster-whisper-base/       # model.bin, config.json, tokenizer.json, vocabulary.txt
  tts/Kokoro-82M-v1.1-zh/        # kokoro-v1_1-zh.pth, config.json, voices/zf_001.pt
  vad/silero-vad/               # 从 Luna-Aster 复制的辅助 VAD
```

`model_path` 必须为模型目录内的相对路径，不能越界或用符号链接跳出目录。Faster-Whisper 不接受普通 OpenAI Whisper `.pt`。Kokoro 的 `voices` 映射公开音色名到本地 voice 文件；`alloy` 表示第一个配置音色。ZipVoice 的 `prompt_text` 和 `prompt_wav_path` 来自管理员配置，参考音频路径属于上游机器。

ASR 接受单/双声道 PCM WAV，转换为单声道 16 kHz，支持 8/16/24/32 位 PCM。Paraformer 缓存按请求隔离，只有最后一块标记 final；未新增实时音频 WebSocket。Kokoro / ZipVoice 输出 WAV；不支持的音色、音高、音量、格式或语速明确拒绝。调用复用现有任务、所有权与统一文件服务。

引擎在 Worker 专用子进程运行，不阻塞心跳。每个模型进程串行推理；加载失败、任务取消、超时会终止该进程，后续请求重新加载。取消等待中的请求不会杀掉其他请求。卸载幂等。引擎 stdout/stderr 被隔离，不记录完整输入、转写或合成文本。

## 本机资产核对（2026-11-06）

已检查 `/disk2/Luna-Aster` 和 `/home/leedow/projects/Luna-Aster`；后者实际配置 SenseVoice ASR + Edge TTS。两处没有上述 ASR/TTS 主模型完整快照。仅找到 Silero VAD 的三个 ONNX 文件，已复制到 `models/vad/silero-vad/`，共 4,897,522 字节，SHA-256 与源文件逐一一致，保留 LICENSE。`models/import-manifest.json` 保存本地复制清单。VAD 不能替代 ASR/TTS 主模型。

`models/` 与常见权重扩展名加入 `.gitignore`、`.dockerignore`；本地资产和生成清单不提交、不进入镜像构建上下文。本次未自动下载主模型或调用付费模型。真实权重推理及生产容器部署须单独验收；测试使用明确的离线夹具。

## English

Speech adapters are optional and run in the single Runtime Worker. The API never imports model frameworks or loads weights. Local engines currently use CPU; GPU monitoring remains simulated.

Administrators can open **Models → Model downloads**, choose a curated ASR/Kokoro snapshot, monitor progress, cancel/retry, and create a disabled CPU model configuration after completion. Downloads run only after an explicit click. They pin a repository commit, verify file sizes and SHA-256/Git blob checksums, and atomically publish complete snapshots. Existing model directories are preserved. Failed/cancelled transfers remove temporary files. Retries restart the download. API keys cannot manage downloads; records belong to their creating administrator.

Install the required `speech-asr`, `speech-tts`, and/or `speech-edge` extras together with `uv sync --frozen`. For Docker, set `MODELDock_SPEECH=true` before building. Downloads do not install runtime libraries. Only the Worker mounts the writable model volume. Local inference disables networking and fails explicitly when weights or language resources are missing.

The source project's active TTS provider was Edge, an online Microsoft service without local weights. ZipVoice was an HTTP client to a separate service whose implementation and weights were absent. Three auxiliary Silero VAD files were found and copied with matching checksums; complete ASR/TTS snapshots were absent. The backend prepares those main models on request. Real inference and production deployment require separate validation.

References: [FunASR API](https://github.com/modelscope/FunASR/blob/main/docs/python_api.md), [Kokoro model](https://github.com/hexgrad/kokoro/blob/main/kokoro/model.py), [Kokoro pipeline](https://github.com/hexgrad/kokoro/blob/main/kokoro/pipeline.py).
