# 第三方模型服务 / Provider adapters

ModelDock 将 aivideo 中的推理协议迁移为可配置 Adapter。默认仅初始化四个 Mock；没有凭据也能运行，不会自动调用远程服务或下载权重。LibLib 网页自动化、YouTube/Reddit 采集和 Copyflow 业务编排不在本次范围。

ModelDock ports aivideo's inference protocols into configurable adapters. Only the four Mock models are seeded. Providers are opt-in; no credentials, model weights, browser profiles, workflow presets or business data are imported.

## 创建与凭据 / Configuration and credentials

管理员在模型管理中选择 Adapter，查看配置 schema，填写配置，先保存为停用模型。配置示例中的模型路径、工作流 ID 和音色必须替换为账号实际有权使用的值。只有启用和提交调用后才执行推理。加载只创建客户端，不提交生成任务。

Standalone Adapter JSON credential fields contain **environment variable names**, never their values. UI-managed chat connections use encrypted storage as described below. Docker Compose injects `.env` into the Worker; for a standalone Worker, export these variables in its shell. Model configuration and API responses contain references only. Restart the Worker after changing its environment. Do not copy aivideo's `.env` or credentials.

远程模型统一使用 `cpu`、`memory_mb=0`。这表示本地客户端资源，不是上游 GPU 监控。卸载只关闭本地客户端。端点由管理员设置，业务请求不能覆盖；只接受 HTTP(S)，URL 中不能包含账号、密码或查询凭据。

## 大语言模型直连 / Direct LLM connections

管理员打开 **厂商连接 → 添加厂商连接**，选择 OpenAI、Claude、Gemini、DeepSeek、通义千问、豆包、Kimi、智谱或自定义兼容服务，填写连接名称、账号对应的 Base URL 和 API Key。保存不会调用推理。厂商连接属于部署级管理配置，只能由管理员登录会话管理；普通用户通过模型授权使用连接背后的模型，业务 API Key 不可管理连接。

先在 API 和 Worker 配置同一个持久化 `MODELDock_PROVIDER_ENCRYPTION_KEY`。可在自己的终端生成一次：

```bash
uv run python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

将输出写入部署环境，勿提交仓库或分享。Compose 会从 `.env` 注入两个进程；修改环境后重建相应容器。主密钥与数据库分开备份，丢失后无法恢复已保存的厂商密钥。已有环境变量接入无需迁移，未设置主密钥不会影响 Mock 或旧接入。

连接的 API Key 用 Fernet 认证加密保存在数据库；读取只返回 `has_api_key`，不返回明文、密文或密钥片段。编辑时留空保留旧密钥，填写新值替换；成功或失败提交后都会清空输入框。密钥提交绕过前端 mutation 缓存。服务器只在 Worker 加载客户端时解密，解密失败会明确报错。

在 **模型管理 → 添加模型** 选择厂商连接、填写账号可用的上游模型 ID。自动匹配 Adapter，使用 CPU/零本地显存，默认停用。一个连接可绑定多个模型，同一家厂商也可以建立多个连接。模型 ID 手动填写，不自动拉取模型列表，也不内置“最新模型”。豆包可填写模型 ID 或推理接入点 ID；通义默认北京兼容入口，可改为地域或业务空间专属地址。预设使用官方 API 地址，不经第三方转发；自定义地址由管理员决定。

启用后使用现有在线测试或 `/v1/chat/completions`。在线测试支持普通响应与 SSE，默认不发送 temperature/top_p；关闭“使用厂商默认采样参数”后发送手动 temperature。实际支持的参数、地区、模型和额度由上游账号决定。只输出最终文本，不将 reasoning/thinking 增量混入回答。

OpenAI、Gemini 和国内兼容厂商复用 `openai_compatible.chat`；Claude 使用 `anthropic.chat` 的 Messages 协议。Claude 仅接收开头的 system/developer 指令和以 user 开始、以 user 结束的文本对话；不支持中途系统指令、assistant 预填充、temperature > 1 或同时指定 temperature 与 top_p。未提供的采样参数不附加到远程请求。OpenAI 连接默认把 Token 上限映射为 `max_completion_tokens`，其他连接映射为 `max_tokens`；模型可显式选择字段以适配具体上游。Claude 统一映射为 Messages 的 max_tokens，未提供时为 256。

修改连接要求所有关联模型卸载，并且没有 queued/running 任务或待处理加载操作；忙碌时返回 `provider_connection_busy`。加载命令提交时即预留 loading 状态；连接、模型绑定和任务提交遵循连接行（按 ID 排序）→ 模型行的锁顺序。已有模型引用时禁止删除连接或切换厂商。Worker 重启放弃历史控制命令，任务沿用既有恢复规则。

**English:** Administrators can create reusable provider connections with write-only API keys. API and Worker must share a persistent Fernet key in `MODELDock_PROVIDER_ENCRYPTION_KEY`. Credentials are encrypted at rest and decrypted only when the Worker loads a client; read APIs expose only `has_api_key`. Blank edits preserve the key. Back up the master key separately from the database. Existing environment-based adapters continue to work without it. All linked models must be unloaded and have no queued/running tasks or pending loads before editing. Referenced connections cannot be deleted or change provider.

Select a connection and manually enter an upstream model/endpoint ID when creating a chat model. Models start disabled. Enable explicitly, then use the playground or existing Chat Completions API. Saving and loading do not perform inference. Streaming and ordinary responses are supported; sampling parameters are omitted unless supplied. Claude uses Messages; other presets use official OpenAI-compatible endpoints. No tool calling, vision, model discovery or provider-specific reasoning controls are exposed. Reasoning deltas are excluded from final text. Ordinary tests use fake upstreams only.

## 对接清单 / Adapters

| 名称 / Name | 能力 / Capability | 操作 / Operations |
|---|---|---|
| openai_compatible.chat | chat | chat；普通响应和 SSE / non-streaming and SSE |
| anthropic.chat | chat | Claude Messages；普通响应和 SSE / non-streaming and SSE |
| runninghub.image | image | generate、edit |
| runninghub.video | video | image_to_video |
| runninghub.workflow.video | video | image_to_video、start_end、lip_sync |
| comfyui.image | image | generate |
| comfyui.video | video | image_to_video、start_end、upscale |
| volcengine.tts | tts | speech；v1 同步或 v3 长文本轮询 / v1 synchronous or v3 async |
| whisperx.subtitle | subtitle | align；音频加原文返回 ASS / audio plus transcript to ASS |

同一模型只对应一个能力；同一工作流模型只对应一个 operation。需要不同工作流时建立不同模型记录。`/api/v1/adapters` 返回配置及输入 schema；模型列表的 `operations` 根据实际配置裁剪。

### OpenAI-compatible chat

```json
{"base_url":"https://openrouter.ai/api/v1","api_key_env":"MODELDock_CHAT_KEY","model":"YOUR_UPSTREAM_MODEL"}
```

使用现有 `/v1/chat/completions`。仅支持 ModelDock 已公开的纯文本参数，不增加工具调用、视觉输入或 Responses API。SSE 支持内容、usage、finish_reason；上游缺少 usage 时报告零计数并在内部任务结果标记 `usage_estimated`，不伪造真实计数。错误流不会发送成功结束。

### RunningHub standard models

```json
{
  "base_url":"https://www.runninghub.cn/openapi/v2",
  "api_key_env":"MODELDock_RUNNINGHUB_KEY",
  "endpoints":{"generate":"YOUR_IMAGE_MODEL/text-to-image","edit":"YOUR_IMAGE_MODEL/image-to-image"},
  "aspect_ratios":["1:1","16:9","9:16"],
  "resolutions":["1k"],
  "defaults":{"aspect_ratio":"1:1","resolution":"1k"},
  "output_hosts":["rh-images.xiaoyaoyou.com"]
}
```

视频使用 `runninghub.video`，endpoints 为 `{"image_to_video":"YOUR_VIDEO_MODEL/image-to-video"}`，并按该模型填写 `resolutions`、`durations`。这些列表是管理员声明的支持范围，不会自动发现上游模型。标准图片接口每次 n=1；使用 `aspect_ratio` 和 `resolution`，不将旧 `size` 字段推测转换为供应商参数。输入文件先通过供应商上传接口转换为临时 URL。

### RunningHub workflow

```json
{
  "base_url":"https://www.runninghub.cn",
  "api_key_env":"MODELDock_RUNNINGHUB_KEY",
  "operation":"start_end",
  "workflow_id":"YOUR_WORKFLOW_ID",
  "bindings":{
    "image_file_id":{"node_id":"START_NODE","field":"image"},
    "end_image_file_id":{"node_id":"END_NODE","field":"image"}
  },
  "output_nodes":["OUTPUT_NODE"],
  "output_hosts":["rh-images.xiaoyaoyou.com"]
}
```

`lip_sync` 必须映射 image_file_id、audio_file_id；可映射 prompt、audio_start、audio_end。同一节点可有多个不同字段绑定。时间输入单位为秒，节点须接受秒数。只有配置的输出节点被读取。

### ComfyUI

填写由自己的 ComfyUI 导出的 **API 格式** workflow JSON；不是编辑器画布 JSON，也不是服务器路径。ModelDock 不启动 ComfyUI、不安装节点或模型。

```json
{
  "base_url":"http://YOUR_COMFYUI_HOST:8188",
  "operation":"upscale",
  "workflow":{"1":{"class_type":"YOUR_INPUT_NODE","inputs":{"file":"","scale":2}},"2":{"class_type":"YOUR_OUTPUT_NODE","inputs":{}}},
  "bindings":{"video_file_id":{"node_id":"1","field":"file"},"scale":{"node_id":"1","field":"scale"}},
  "output_nodes":["2"],
  "targeted_interrupt":false
}
```

示例是配置形状，节点必须替换为实际可执行图。image_to_video 必须映射 image_file_id；start_end 还需 end_image_file_id；generate 必须映射 prompt。图片和视频上传至 `/upload/image`，按配置写入相应节点。只读取指定输出节点中的 image/video 文件。

`targeted_interrupt` 默认关闭：取消只删除自身排队项并停止本地等待。仅当部署端支持带 prompt_id 的定向 `/interrupt` 时才开启。始终不发送全局中断，也不调用全局释放 GPU。

### Volcengine TTS

```json
{
  "base_url":"https://openspeech.bytedance.com",
  "api_key_env":"MODELDock_VOLC_TOKEN",
  "app_id_env":"MODELDock_VOLC_APPID",
  "resource_id":"YOUR_RESOURCE_ID",
  "mode":"v3",
  "voices":["YOUR_VOICE"],
  "formats":["mp3"]
}
```

v1 使用原项目的 AppID/Token/Cluster 鉴权和同步 base64 结果；v3 使用原项目的 AppID/AccessKey/ResourceId 头及 submit/query。这里保留原有旧版账号鉴权，不自动切换为新 API Key 鉴权。

语速 0.5–2，音量 0.5–2，音调 -12–12 半音；不截断超范围值。v1 文本上限 4096；v3 上限 100000。`mix_speakers` 可配置 `{ "VOICE_ALIAS": {"SOURCE_A":0.4,"SOURCE_B":0.6} }`，别名须在 voices 中，正数权重之和须为一，仅用于 v3。

### WhisperX subtitles

```json
{"base_url":"http://YOUR_WHISPERX_HOST:8004"}
```

只调用现有 `/generate-ass`：multipart audio_file + UTF-8 text_file。该接口是原文对齐生成字幕，不是自由语音转写。ModelDock 不自动部署 WhisperX，也不下载其权重。

## 业务调用 / Task API

先通过 `POST /v1/files` 上传文件，再引用返回的 ID：

```json
{"model":"your-image","capability":"image","parameters":{"operation":"edit","image_file_id":"YOUR_FILE_ID","prompt":"Change the background","aspect_ratio":"16:9","resolution":"1k"}}
```

```json
{"model":"your-video","capability":"video","parameters":{"operation":"start_end","image_file_id":"START_FILE_ID","end_image_file_id":"END_FILE_ID","prompt":"Slow camera movement"}}
```

```json
{"model":"your-subtitle","capability":"subtitle","parameters":{"file_id":"AUDIO_FILE_ID","text":"Subtitle transcript"}}
```

字幕原文也可用 text_file_id，必须与 text 二选一。upscale 使用 video_file_id 和 scale；lip_sync 使用 image_file_id、audio_file_id，可加音频截取时间。图像编辑、视频和字幕仅提供异步任务入口。

所有输入文件须属于当前用户；管理员也不绕过隔离。排队和执行期间，全部引用文件受到删除和到期清理保护。输出统一在 result.files，下载仍须登录或 Bearer Key。

## 超时、取消与文件 / Lifecycle and files

- `timeout_seconds` 限制整个任务；连接/读取超时和 poll_interval 在 Adapter 配置中。查询可有限重试，创建推理任务从不自动重试。
- execution_metadata 仅包含提供方、上游任务 ID 和取消状态。不存储原始响应、凭据或下载链接。
- RunningHub 工作流请求上游取消；`upstream_confirmed` 表示接口确认，`upstream_requested` 表示已发定向取消请求，`local_only` 表示只保证本地停止等待。上游生成及计费可能继续。
- Worker 重启：running → failed/worker_lost，保留上游 ID；queued 按原有 Outbox 恢复。不自动恢复远程轮询。显式重试创建新任务，可能再次计费。
- 上传支持 PNG/JPEG/WebP、WAV/MP3/FLAC、MP4 和 UTF-8 文本；生成 ASS 校验基本结构。图像使用 Pillow 检查，音视频使用 ffprobe 检查容器和流。Mock ASR 仍只接受 PCM WAV。
- Docker 镜像包含 ffprobe；本地开发需安装系统 ffmpeg 包。文件上限沿用 MODELDock_UPLOAD_MAX_MB（默认 25 MB）；Adapter 的 max_output_mb 还会限制下载上限，实际保存不能超过全局上限。视频流式下载到临时文件后转入共享存储。
- 输出仅允许 base_url 主机及 output_hosts 明确列出的主机；不跟随重定向，也不向下载主机发送供应商鉴权头。若供应商使用不同 CDN，管理员需将实际 CDN 加入列表。

## 验证边界 / Validation boundary

所有普通测试和浏览器测试使用假上游、临时文件及可丢弃数据库，不访问付费推理。通过这些测试表示协议转换、任务和权限流程符合本地测试合约；不代表供应商账号权限、工作流图、实时 API 或真实生成质量已完成联调。
